"""sector_correlation_daily 读写 — JSON 列序列化/反序列化在此封装。

record 以 python 对象进出（windows/sectors/sector_index/pair_* 都是 list/dict），
JSON 编解码是存储细节，不外泄给 collector/formatter。UPSERT 幂等、保留首次 created_at。
"""
from __future__ import annotations

import json
import sqlite3

_REQUIRED = ("date", "windows", "top_n", "base_index", "indices")


def save_correlation(conn: sqlite3.Connection, record: dict) -> None:
    """写入/覆盖某交易日的相关性快照（UPSERT，保留 created_at 刷 updated_at）。"""
    for key in _REQUIRED:
        if record.get(key) is None:
            raise ValueError(f"save_correlation: 缺少必填字段 {key}")
    conn.execute(
        """
        INSERT INTO sector_correlation_daily (
            date, windows_json, top_n, activity_days, sample_days_json,
            base_index, indices_json, sectors_json,
            sector_index_json, pair_raw_json, pair_excess_json, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            windows_json = excluded.windows_json,
            top_n = excluded.top_n,
            activity_days = excluded.activity_days,
            sample_days_json = excluded.sample_days_json,
            base_index = excluded.base_index,
            indices_json = excluded.indices_json,
            sectors_json = excluded.sectors_json,
            sector_index_json = excluded.sector_index_json,
            pair_raw_json = excluded.pair_raw_json,
            pair_excess_json = excluded.pair_excess_json,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            json.dumps(record["windows"], ensure_ascii=False),
            int(record["top_n"]),
            record.get("activity_days"),
            json.dumps(record.get("sample_days", {}), ensure_ascii=False),
            record["base_index"],
            json.dumps(record["indices"], ensure_ascii=False),
            json.dumps(record.get("sectors", []), ensure_ascii=False),
            json.dumps(record.get("sector_index", {}), ensure_ascii=False),
            json.dumps(record.get("pair_raw", {}), ensure_ascii=False),
            json.dumps(record.get("pair_excess", {}), ensure_ascii=False),
            json.dumps(record["meta"], ensure_ascii=False) if record.get("meta") is not None else None,
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    def _j(col, default):
        return json.loads(row[col]) if row[col] else default

    return {
        "date": row["date"],
        "windows": _j("windows_json", []),
        "top_n": row["top_n"],
        "activity_days": row["activity_days"],
        "sample_days": _j("sample_days_json", {}),
        "base_index": row["base_index"],
        "indices": _j("indices_json", []),
        "sectors": _j("sectors_json", []),
        "sector_index": _j("sector_index_json", {}),
        "pair_raw": _j("pair_raw_json", {}),
        "pair_excess": _j("pair_excess_json", {}),
        "meta": _j("meta_json", None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_correlation(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sector_correlation_daily WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent_correlation(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    """取 <= end_date 的最近 days 天快照，按日期正序（供 trend）。"""
    rows = conn.execute(
        "SELECT * FROM sector_correlation_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]
