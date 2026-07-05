"""MA4 拐头 + 成交额均线突破扫描编排。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import re
import sqlite3

from services.ma_breakout import constants as C
from services.ma_breakout import detectors
from utils.price_limit import limit_pct_for


def _bare_code(code) -> str:
    return str(code or "").split(".")[0].strip()


def _is_bare_stock_code(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _extract_embedded_stock_code(value: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    if match:
        return match.group(1)
    return None


def _stock_basic_name_map(registry, date: str) -> tuple[dict[str, str], str | None]:
    if registry is None:
        return {}, "stock_basic_registry_missing"
    try:
        result = registry.call("get_stock_basic_list", date)
    except Exception as exc:
        return {}, f"stock_basic_failed:{exc}"
    if not getattr(result, "success", False) or not isinstance(result.data, list):
        error = getattr(result, "error", "") or "malformed"
        return {}, f"stock_basic_failed:{error}"
    buckets: dict[str, set[str]] = defaultdict(set)
    for row in result.data:
        name = str(row.get("name") or "").strip()
        code = _bare_code(row.get("ts_code") or row.get("symbol") or row.get("code"))
        if name and _is_bare_stock_code(code):
            buckets[name].add(code)
    return {name: next(iter(codes)) for name, codes in buckets.items() if len(codes) == 1}, None


def _resolve_leader_tracking_code(row, name_map: dict[str, str]) -> str | None:
    raw_code = str(row["stock_code"] or "").strip()
    if _is_bare_stock_code(_bare_code(raw_code)):
        return _bare_code(raw_code)
    for value in (raw_code, str(row["stock_name"] or "").strip()):
        embedded = _extract_embedded_stock_code(value)
        if embedded:
            return embedded
    for key in (raw_code, str(row["stock_name"] or "").strip()):
        if key in name_map:
            return name_map[key]
    return None


def _is_rejected_leader(code: str | None, name: str | None) -> bool:
    bare = _bare_code(code)
    clean_name = str(name or "").strip()
    return bare in C.REJECTED_LEADER_CODES or clean_name in C.REJECTED_LEADER_NAMES


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _merge_leader_meta(universe: dict[str, dict], code: str, source: str, **meta) -> None:
    bare = _bare_code(code)
    if not bare:
        return
    entry = universe.setdefault(bare, {"sources": []})
    if source not in entry["sources"]:
        entry["sources"].append(source)
    for key, value in meta.items():
        if value in (None, ""):
            continue
        if key == "first_seen_date":
            old = entry.get(key)
            if old is None or value < old:
                entry[key] = value
        elif key == "last_seen_date":
            old = entry.get(key)
            if old is None or value > old:
                entry[key] = value
        else:
            entry.setdefault(key, value)


def _lookback_start(date: str, lookback_days: int) -> str:
    end = datetime.strptime(date, "%Y-%m-%d").date()
    return (end - timedelta(days=lookback_days)).isoformat()


def load_former_leader_universe(
    conn: sqlite3.Connection,
    date: str,
    *,
    lookback_days: int = C.DEFAULT_LEADER_LOOKBACK_DAYS,
    registry=None,
    stats: dict | None = None,
    include_auto_trend_pool: bool = False,
) -> dict[str, dict]:
    """读取目标日前已经出现过的历史龙头/最票宇宙。"""
    start_date = _lookback_start(date, lookback_days)
    stats = stats if stats is not None else {}
    stats.setdefault("unresolved_leader_tracking", 0)
    universe: dict[str, dict] = {}
    if include_auto_trend_pool and _table_exists(conn, "trend_leader_pool"):
        for row in conn.execute(
            """
            SELECT code, name, sw_l2, entered_date, last_seen_date, status
            FROM trend_leader_pool
            WHERE entered_date < ?
              AND (entered_date >= ? OR last_seen_date >= ?)
            """,
            (date, start_date, start_date),
        ):
            _merge_leader_meta(
                universe,
                row["code"],
                "trend_leader_pool",
                name=row["name"],
                sw_l2=row["sw_l2"],
                first_seen_date=row["entered_date"],
                last_seen_date=row["last_seen_date"],
                status=row["status"],
            )
    if _table_exists(conn, "leader_tracking"):
        name_map, name_map_error = _stock_basic_name_map(registry, date)
        for row in conn.execute(
            """
            SELECT stock_code, stock_name, sector, attribute_type, first_seen_date, last_seen_date, is_active
            FROM leader_tracking
            WHERE first_seen_date < ?
              AND (first_seen_date >= ? OR last_seen_date >= ?)
            """,
            (date, start_date, start_date),
        ):
            resolved_code = _resolve_leader_tracking_code(row, name_map)
            if not resolved_code:
                raw_code = str(row["stock_code"] or "").strip()
                raw_name = str(row["stock_name"] or "").strip()
                if name_map_error and not _is_bare_stock_code(_bare_code(raw_code)) and raw_name:
                    stats["leader_resolution_error"] = name_map_error
                stats["unresolved_leader_tracking"] += 1
                continue
            if _is_rejected_leader(resolved_code, row["stock_name"]):
                continue
            _merge_leader_meta(
                universe,
                resolved_code,
                "leader_tracking",
                name=row["stock_name"],
                sw_l2=row["sector"],
                role=row["attribute_type"],
                first_seen_date=row["first_seen_date"],
                last_seen_date=row["last_seen_date"],
                is_active=row["is_active"],
            )
    return universe


def _date_range_desc(end_date: str, max_days: int):
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    for i in range(max_days):
        yield (end - timedelta(days=i)).isoformat()


def _registry_is_trade_day(registry, date: str) -> bool | None:
    try:
        result = registry.call("is_trade_day", date)
    except Exception:
        return None
    if getattr(result, "success", False) and result.data is not None:
        return bool(result.data)
    return None


def _is_weekday(date: str) -> bool:
    return datetime.strptime(date, "%Y-%m-%d").weekday() < 5


def _fetch_recent_quotes(registry, target_date: str, required_days: int) -> tuple[list[tuple[str, list[dict]]], list[str], bool]:
    quote_days: list[tuple[str, list[dict]]] = []
    source_errors: list[str] = []
    target_failed = False
    for date in _date_range_desc(target_date, C.MAX_LOOKBACK_CALENDAR_DAYS):
        result = registry.call("get_market_daily_quotes", date)
        if not getattr(result, "success", False):
            source_errors.append(f"market_daily_quotes:{date}")
            if date == target_date:
                target_failed = True
                break
            continue
        rows = result.data if isinstance(result.data, list) else []
        if rows:
            quote_days.append((date, rows))
        elif date == target_date:
            source_errors.append("target_quotes_missing")
            target_failed = True
            break
        else:
            is_trade_day = _registry_is_trade_day(registry, date)
            if is_trade_day is True:
                source_errors.append(f"market_daily_quotes_empty:{date}")
            elif is_trade_day is None and _is_weekday(date):
                source_errors.append(f"market_daily_quotes_empty_calendar_unknown:{date}")
        if len(quote_days) >= required_days:
            break
    quote_days.sort(key=lambda item: item[0])
    return quote_days, source_errors, target_failed


def _sw_lookup(registry) -> dict[str, dict]:
    result = registry.call("get_stock_sw_industry_map")
    if getattr(result, "success", False) and isinstance(result.data, dict):
        out = {}
        for key, value in result.data.items():
            out[str(key)] = value
            out[_bare_code(key)] = value
        return out
    return {}


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_today_limit_up(code: str, bar: dict, name: str = "") -> bool:
    pct = bar.get("pct_chg")
    limit_pct = limit_pct_for(code, bar.get("name") or name or "")
    return pct is not None and limit_pct is not None and pct >= limit_pct * C.LIMIT_UP_DETECT_FACTOR


def _bars_by_code(quote_days: list[tuple[str, list[dict]]]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for date, rows in quote_days:
        for row in rows:
            code = row.get("ts_code") or row.get("code")
            bare = _bare_code(code)
            if not bare:
                continue
            grouped[bare].append({
                "trade_date": date,
                "ts_code": code,
                "name": row.get("name") or "",
                "close": _num(row.get("close")),
                "amount": _num(row.get("amount")),
                "pct_chg": _num(row.get("pct_chg")),
            })
    return dict(grouped)


def _flatten_candidate(code: str, bars: list[dict], sw: dict[str, dict], detail: dict, former_meta: dict | None = None) -> dict:
    today = bars[-1]
    sw_entry = sw.get(str(today.get("ts_code"))) or sw.get(code) or {}
    former_meta = former_meta or {}
    amount_detail = detail.get("amount") or {}
    ma_detail = detail.get("ma") or {}
    amount_mas = {
        key.replace("amount_ma", ""): value
        for key, value in amount_detail.items()
        if key.startswith("amount_ma")
    }
    return {
        "code": code,
        "ts_code": today.get("ts_code"),
        "name": sw_entry.get("name") or former_meta.get("name") or today.get("name") or "",
        "sw_l2": sw_entry.get("sw_l2") or former_meta.get("sw_l2") or "未分类",
        "pct_chg": today.get("pct_chg"),
        "today_amount": today.get("amount"),
        "amount_mas": amount_mas,
        "amount_ma5": amount_detail.get("amount_ma5"),
        "amount_ma10": amount_detail.get("amount_ma10"),
        "ma4_today": ma_detail.get("ma_today"),
        "ma4_prev": ma_detail.get("ma_prev"),
        "former_leader_sources": former_meta.get("sources") or [],
        "former_leader_first_seen": former_meta.get("first_seen_date"),
        "former_leader_last_seen": former_meta.get("last_seen_date"),
        "former_leader_role": former_meta.get("role"),
    }


def run_daily(
    registry,
    date: str,
    *,
    windows: tuple[int, ...] = C.DEFAULT_AMOUNT_WINDOWS,
    top_n: int = C.DEFAULT_TOP_N,
    min_target_quote_rows: int = C.MIN_TARGET_QUOTE_ROWS,
    former_leaders: dict[str, dict] | None = None,
) -> dict:
    """扫描目标日全市场，返回当日命中清单。"""
    required_days = max(max(windows), C.MA_PERIOD + 2)
    quote_days, source_errors, target_failed = _fetch_recent_quotes(registry, date, required_days)
    target_rows = []
    if quote_days and quote_days[-1][0] == date:
        target_rows = quote_days[-1][1]
    if target_failed or source_errors:
        return {
            "status": "source_failed",
            "date": date,
            "candidates": [],
            "source_errors": source_errors,
            "scanned_count": 0,
            "matched_count": 0,
            "insufficient_count": 0,
            "truncated": False,
        }
    below_floor_errors = []
    for quote_date, rows in quote_days:
        if len(rows) < min_target_quote_rows:
            below_floor_errors.append(f"quote_rows_below_floor:{quote_date}")
            if quote_date == date:
                below_floor_errors.append("target_quote_rows_below_floor")
    if below_floor_errors:
        return {
            "status": "source_failed",
            "date": date,
            "candidates": [],
            "source_errors": below_floor_errors,
            "scanned_count": len(target_rows),
            "matched_count": 0,
            "insufficient_count": 0,
            "truncated": False,
        }
    if len(quote_days) < required_days:
        return {
            "status": "source_failed",
            "date": date,
            "candidates": [],
            "source_errors": ["insufficient_market_history"],
            "scanned_count": len(target_rows),
            "matched_count": 0,
            "insufficient_count": 0,
            "truncated": False,
        }

    grouped = _bars_by_code(quote_days)
    sw = _sw_lookup(registry)
    candidates = []
    insufficient = 0
    scanned = 0
    for code, bars in grouped.items():
        former_meta = None
        if former_leaders is not None:
            former_meta = former_leaders.get(code)
            if former_meta is None:
                continue
        if not bars or bars[-1].get("trade_date") != date:
            continue
        scanned += 1
        matched, detail = detectors.match_pattern(bars, target_date=date, windows=windows)
        limit_name = (former_meta or {}).get("name") or ""
        if matched and not _is_today_limit_up(code, bars[-1], limit_name):
            candidates.append(_flatten_candidate(code, bars, sw, detail, former_meta))
        elif (
            detail.get("insufficient_history")
            or (detail.get("ma") or {}).get("insufficient_history")
            or (detail.get("amount") or {}).get("insufficient_history")
        ):
            insufficient += 1

    candidates.sort(key=lambda item: (item.get("today_amount") or 0.0), reverse=True)
    matched_count = len(candidates)
    limited = candidates[:top_n]
    return {
        "status": "ok",
        "date": date,
        "windows": list(windows),
        "candidates": limited,
        "source_errors": source_errors,
        "leader_universe_count": len(former_leaders) if former_leaders is not None else None,
        "scanned_count": scanned,
        "matched_count": matched_count,
        "insufficient_count": insufficient,
        "truncated": matched_count > len(limited),
    }
