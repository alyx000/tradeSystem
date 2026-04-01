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


def _extract_market_row(date_str: str, data: dict) -> dict:
    """从 post-market YAML 数据提取 daily_market 行。"""
    indices = data.get("indices", {})
    emotion = data.get("emotion", {})
    style = data.get("style_analysis", data.get("style", {}))
    capital = data.get("capital_flow", data.get("capital", {}))
    breadth = data.get("market_breadth", {})

    return {
        "date": date_str,
        "sh_index_close": indices.get("sh_close"),
        "sh_index_change_pct": indices.get("sh_change_pct"),
        "sz_index_close": indices.get("sz_close"),
        "sz_index_change_pct": indices.get("sz_change_pct"),
        "total_amount": data.get("total_amount") or data.get("total_volume"),
        "advance_count": breadth.get("advance_count") or breadth.get("up_count"),
        "decline_count": breadth.get("decline_count") or breadth.get("down_count"),
        "limit_up_count": emotion.get("limit_up_count"),
        "limit_down_count": emotion.get("limit_down_count"),
        "seal_rate": emotion.get("seal_rate"),
        "broken_rate": emotion.get("broken_rate"),
        "highest_board": emotion.get("highest_board"),
        "continuous_board_counts": emotion.get("continuous_board_counts"),
        "premium_10cm": style.get("premium_10cm"),
        "premium_20cm": style.get("premium_20cm"),
        "premium_second_board": style.get("premium_second_board"),
        "northbound_net": capital.get("northbound_net"),
        "margin_balance": capital.get("margin_balance"),
        "market_breadth": breadth if breadth else None,
        "raw_data": data,
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
