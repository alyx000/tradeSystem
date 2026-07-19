"""sector_crowding_daily 读写 — JSON 编解码封装在此，UPSERT 幂等、保留首次 created_at。"""
from __future__ import annotations

import json
import sqlite3


def save_snapshot(conn: sqlite3.Connection, record: dict) -> None:
    if record.get("date") is None or record.get("sectors") is None:
        raise ValueError("save_snapshot: 缺少必填字段 date/sectors")
    conn.execute(
        """
        INSERT INTO sector_crowding_daily (
            date, market_total_billion, sectors_json, proxy_json, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            market_total_billion = excluded.market_total_billion,
            sectors_json = excluded.sectors_json,
            proxy_json = excluded.proxy_json,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            record.get("market_total_billion"),
            json.dumps(record["sectors"], ensure_ascii=False),
            json.dumps(record["proxy"], ensure_ascii=False) if record.get("proxy") is not None else None,
            json.dumps(record["meta"], ensure_ascii=False) if record.get("meta") is not None else None,
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    def _j(col):
        return json.loads(row[col]) if row[col] else None

    return {
        "date": row["date"],
        "market_total_billion": row["market_total_billion"],
        "sectors": _j("sectors_json") or [],
        "proxy": _j("proxy_json"),
        "meta": _j("meta_json"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_snapshot(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sector_crowding_daily WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    """取 <= end_date 的最近 days 行快照，按日期升序（供分位现算/trend）。"""
    rows = conn.execute(
        "SELECT * FROM sector_crowding_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]


def get_latest_market_total_before(conn: sqlite3.Connection, date: str) -> float | None:
    """date 之前最近一个非 NULL 两市总额（骤降告警基准）。"""
    row = conn.execute(
        """SELECT market_total_billion FROM sector_crowding_daily
           WHERE date < ? AND market_total_billion IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (date,),
    ).fetchone()
    return row["market_total_billion"] if row else None
