"""双写支持：YAML 写入后同步到 SQLite，失败记入 pending_writes。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .connection import get_db

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PENDING_WRITES_PATH = PROJECT_ROOT / "data" / "pending_writes.json"


def _load_pending() -> list[dict]:
    if not PENDING_WRITES_PATH.exists():
        return []
    with open(PENDING_WRITES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_pending(items: list[dict]) -> None:
    PENDING_WRITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_WRITES_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def record_pending(table: str, data: dict, error: str) -> None:
    """DB 写失败时记录到 pending_writes.json。"""
    items = _load_pending()
    items.append({"table": table, "data": data, "error": error})
    _save_pending(items)
    logger.warning("Recorded pending write for %s: %s", table, error)


def retry_pending(db_path: str | Path | None = None) -> tuple[int, int]:
    """重试 pending_writes 中的失败记录。返回 (成功数, 失败数)。"""
    items = _load_pending()
    if not items:
        return 0, 0

    from . import queries as Q

    succeeded, failed = 0, 0
    remaining = []

    with get_db(db_path) as conn:
        for item in items:
            table = item["table"]
            data = item["data"]
            try:
                if table == "daily_market":
                    Q.upsert_daily_market(conn, data)
                elif table == "teacher_notes":
                    teacher_name = data.get("teacher_name", "unknown")
                    note_data = {k: v for k, v in data.items() if k != "teacher_name"}
                    tid = Q.get_or_create_teacher(conn, teacher_name)
                    Q.insert_teacher_note(conn, teacher_id=tid, **note_data)
                elif table == "calendar_events":
                    Q.insert_calendar_event(conn, **data)
                elif table == "holdings":
                    Q.upsert_holding(conn, **data)
                elif table == "watchlist":
                    Q.insert_watchlist(conn, **data)
                else:
                    logger.warning("Unknown table in pending writes: %s", table)
                    remaining.append(item)
                    failed += 1
                    continue
                succeeded += 1
            except Exception as e:
                item["error"] = str(e)
                remaining.append(item)
                failed += 1

    _save_pending(remaining)
    logger.info("Pending writes retry: %d succeeded, %d failed", succeeded, failed)
    return succeeded, failed


def sync_daily_market_to_db(date_str: str, yaml_data: dict,
                            db_path: str | Path | None = None) -> bool:
    """将盘后行情 YAML 数据同步写入 DB。返回是否成功。"""
    from . import queries as Q

    row = _extract_market_row(date_str, yaml_data)
    try:
        with get_db(db_path) as conn:
            Q.upsert_daily_market(conn, row)
        return True
    except Exception as e:
        logger.error("Failed to sync daily_market for %s: %s", date_str, e)
        record_pending("daily_market", row, str(e))
        return False


def _normalize_stock_code_for_match(code: str | None) -> str:
    """用于匹配持仓代码：忽略交易所后缀与大小写。"""
    if not code:
        return ""
    s = str(code).strip().upper()
    for suf in (".SZ", ".SH", ".BJ"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def parse_post_market_envelope(raw_data: str | dict | None) -> dict | None:
    """解析 daily_market.raw_data（dict 或 JSON 字符串）为盘后信封 dict。"""
    if raw_data is None:
        return None
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        try:
            d = json.loads(raw_data)
            return d if isinstance(d, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


def holdings_quote_details_from_envelope(envelope: dict | None) -> dict[str, dict[str, float | None]]:
    """从信封顶层 holdings_data 提取：归一化代码 -> {close, pnl_pct}。"""
    if not envelope or not isinstance(envelope, dict):
        return {}
    holdings_data = envelope.get("holdings_data")
    if not isinstance(holdings_data, list):
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for item in holdings_data:
        if not isinstance(item, dict) or "error" in item:
            continue
        raw_code = item.get("code")
        close = item.get("close")
        if raw_code is None or close is None:
            continue
        try:
            key = _normalize_stock_code_for_match(str(raw_code))
            if not key:
                continue
            rec: dict[str, float | None] = {"close": float(close), "pnl_pct": None}
            if item.get("pnl_pct") is not None:
                try:
                    rec["pnl_pct"] = float(item["pnl_pct"])
                except (TypeError, ValueError):
                    pass
            out[key] = rec
        except (TypeError, ValueError):
            continue
    return out


def sync_holdings_quotes_from_post_market(
    date_str: str,
    envelope: dict,
    db_path: str | Path | None = None,
) -> int:
    """从 post-market.yaml 的 holdings_data 将收盘价写入 holdings.current_price。

    按归一化代码匹配（忽略 .SZ/.SH/.BJ）。返回成功更新的持仓条数。
    """
    details = holdings_quote_details_from_envelope(envelope)
    quotes = {k: v["close"] for k, v in details.items() if v.get("close") is not None}

    if not quotes:
        return 0

    from . import queries as Q

    try:
        with get_db(db_path) as conn:
            updated = 0
            rows = Q.get_holdings(conn, status="active")
            for row in rows:
                nid = _normalize_stock_code_for_match(row.get("stock_code"))
                if nid in quotes:
                    Q.update_holding(conn, int(row["id"]), current_price=quotes[nid])
                    updated += 1
    except Exception as e:
        logger.error("sync_holdings_quotes_from_post_market(%s): %s", date_str, e)
        return 0

    if updated:
        logger.info(
            "持仓现价已同步: date=%s updated=%d (YAML 中有效行情 %d 只)",
            date_str, updated, len(quotes),
        )
    return updated


def _post_market_collector_source(envelope: dict) -> dict:
    """盘后 YAML 信封：采集结果在 raw_data 内；旧版扁平结构则整包即 source。"""
    inner = envelope.get("raw_data")
    if isinstance(inner, dict) and inner:
        return inner
    return envelope


def _safe_sub(d: dict | None, key: str) -> dict:
    x = (d or {}).get(key)
    return x if isinstance(x, dict) else {}


def _first_non_none(*values):
    """依次取第一个非 None 的值（保留 0 / False 等合法值）。"""
    for x in values:
        if x is not None:
            return x
    return None


def _extract_market_row(date_str: str, envelope: dict) -> dict:
    """从 post-market YAML（信封或扁平）提取 daily_market 行；raw_data 列存完整信封。"""
    source = _post_market_collector_source(envelope)
    indices = _safe_sub(source, "indices")
    vol = _safe_sub(source, "total_volume")
    breadth = _safe_sub(source, "breadth")
    mb = _safe_sub(source, "market_breadth")
    if not mb:
        mb = _safe_sub(envelope, "market_breadth")
    limit_up = _safe_sub(source, "limit_up")
    limit_down = _safe_sub(source, "limit_down")
    emotion = _safe_sub(source, "emotion")
    if not emotion:
        emotion = _safe_sub(envelope, "emotion")
    style = source.get("style_analysis") or source.get("style") or {}
    if not isinstance(style, dict):
        style = {}
    if not style:
        style = envelope.get("style_analysis") or envelope.get("style") or {}
        if not isinstance(style, dict):
            style = {}
    northbound = _safe_sub(source, "northbound")
    capital = _safe_sub(source, "capital_flow")
    if not capital:
        capital = _safe_sub(source, "capital")
    if not capital:
        capital = _safe_sub(envelope, "capital_flow") or _safe_sub(envelope, "capital")
    margin = _safe_sub(source, "margin_data")
    ma_sh = _safe_sub(_safe_sub(source, "moving_averages"), "shanghai")
    ma_all = source.get("moving_averages") if isinstance(source.get("moving_averages"), dict) else {}

    def _idx_close_pct(nest: str, legacy_close: str, legacy_pct: str):
        block = indices.get(nest)
        if isinstance(block, dict) and "error" not in block:
            return block.get("close"), block.get("change_pct")
        return indices.get(legacy_close), indices.get(legacy_pct)

    sh_c, sh_p = _idx_close_pct("shanghai", "sh_close", "sh_change_pct")
    sz_c, sz_p = _idx_close_pct("shenzhen", "sz_close", "sz_change_pct")

    total_amount = source.get("total_amount")
    if total_amount is None and isinstance(source.get("total_volume"), (int, float)):
        total_amount = source.get("total_volume")
    if total_amount is None:
        total_amount = vol.get("total_billion")
    if total_amount is None:
        total_amount = envelope.get("total_amount")

    advance = _first_non_none(
        breadth.get("advance"),
        mb.get("advance_count"),
        mb.get("up_count"),
    )
    decline = _first_non_none(
        breadth.get("decline"),
        mb.get("decline_count"),
        mb.get("down_count"),
    )

    lu_bad = "error" in limit_up
    ld_bad = "error" in limit_down

    limit_up_count = None if lu_bad else limit_up.get("count")
    if limit_up_count is None:
        limit_up_count = emotion.get("limit_up_count")

    limit_down_count = None if ld_bad else limit_down.get("count")
    if limit_down_count is None:
        limit_down_count = emotion.get("limit_down_count")

    seal_rate = None if lu_bad else limit_up.get("seal_rate_pct")
    if seal_rate is None:
        seal_rate = emotion.get("seal_rate")

    broken_rate = None if lu_bad else limit_up.get("broken_rate_pct")
    if broken_rate is None:
        broken_rate = emotion.get("broken_rate")

    highest_board = None if lu_bad else limit_up.get("highest_board")
    if highest_board is None:
        highest_board = emotion.get("highest_board")

    ladder = None if lu_bad else limit_up.get("board_ladder")
    if isinstance(ladder, dict) and ladder:
        continuous_board_counts = json.dumps(ladder, ensure_ascii=False)
    else:
        continuous_board_counts = emotion.get("continuous_board_counts")

    sf = source.get("style_factors") if isinstance(source.get("style_factors"), dict) else {}
    snap = sf.get("premium_snapshot") if isinstance(sf.get("premium_snapshot"), dict) else {}

    def _snap_premium_med(*keys: str):
        for k in keys:
            g = snap.get(k)
            if isinstance(g, dict) and g.get("premium_median") is not None:
                return g.get("premium_median")
        return None

    # 各列只对应同口径快照键，避免 10/20/30cm 混用；缺省时再读 YAML style 显式字段
    premium_10cm = _snap_premium_med("first_board_10cm")
    premium_20cm = _snap_premium_med("first_board_20cm")
    premium_30cm = _snap_premium_med("first_board_30cm")
    premium_second_board = _snap_premium_med("second_board")
    if premium_10cm is None:
        premium_10cm = style.get("premium_10cm")
    if premium_20cm is None:
        premium_20cm = style.get("premium_20cm")
    if premium_30cm is None:
        premium_30cm = style.get("premium_30cm")
    if premium_second_board is None:
        premium_second_board = style.get("premium_second_board")

    northbound_net = northbound.get("net_buy_billion")
    if northbound_net is None:
        northbound_net = capital.get("northbound_net")

    margin_balance = margin.get("total_rzrqye_yi")
    if margin_balance is None:
        margin_balance = margin.get("total_rzye_yi")
    if margin_balance is None:
        margin_balance = capital.get("margin_balance")

    market_breadth_out = breadth if breadth else (mb if mb else None)

    def _above_ma5w(key: str):
        sub = ma_all.get(key)
        return sub.get("above_ma5w") if isinstance(sub, dict) else None

    def _json_col(key: str):
        v = source.get(key)
        if v is None:
            return None
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return v

    return {
        "date": date_str,
        "sh_index_close": sh_c,
        "sh_index_change_pct": sh_p,
        "sz_index_close": sz_c,
        "sz_index_change_pct": sz_p,
        "total_amount": total_amount,
        "advance_count": advance,
        "decline_count": decline,
        "sh_above_ma5w": ma_sh.get("above_ma5w"),
        "sz_above_ma5w": _above_ma5w("shenzhen"),
        "chinext_above_ma5w": _above_ma5w("chinext"),
        "star50_above_ma5w": _above_ma5w("star50"),
        "avg_price_above_ma5w": _first_non_none(
            _above_ma5w("avg_price"),
            _above_ma5w("equally_weighted"),
        ),
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "seal_rate": seal_rate,
        "broken_rate": broken_rate,
        "highest_board": highest_board,
        "continuous_board_counts": continuous_board_counts,
        "premium_10cm": premium_10cm,
        "premium_20cm": premium_20cm,
        "premium_30cm": premium_30cm,
        "premium_second_board": premium_second_board,
        "northbound_net": northbound_net,
        "margin_balance": margin_balance,
        "market_breadth": market_breadth_out,
        "raw_data": envelope,
        "node_signals": _json_col("node_signals"),
        "top_volume_stocks": _json_col("top_volume_stocks"),
        "etf_flow": _json_col("etf_flow"),
        "hk_indices": _json_col("hk_indices"),
    }


def reconcile_daily_market(db_path: str | Path | None = None,
                           daily_dir: Path | None = None) -> list[dict]:
    """对账：比对 daily/ YAML 和 daily_market 表的关键字段，报告差异。"""
    import yaml as _yaml
    from . import queries as Q

    base = daily_dir or PROJECT_ROOT / "daily"
    if not base.exists():
        return []

    diffs = []
    with get_db(db_path) as conn:
        for day_dir in sorted(base.iterdir()):
            if not day_dir.is_dir():
                continue
            pm_path = day_dir / "post-market.yaml"
            if not pm_path.exists():
                continue
            with open(pm_path, "r", encoding="utf-8") as f:
                yaml_data = _yaml.safe_load(f) or {}

            date_str = day_dir.name
            db_row = Q.get_daily_market(conn, date_str)

            if not db_row:
                diffs.append({"date": date_str, "issue": "missing_in_db"})
                continue

            yaml_row = _extract_market_row(date_str, yaml_data)
            for key in ("sh_index_close", "total_amount", "limit_up_count"):
                yaml_val = yaml_row.get(key)
                db_val = db_row.get(key)
                if yaml_val is not None and db_val is not None and yaml_val != db_val:
                    diffs.append({
                        "date": date_str, "field": key,
                        "yaml": yaml_val, "db": db_val,
                    })

    return diffs
