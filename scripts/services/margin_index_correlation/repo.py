"""margin_index_correlation_daily 读写 — JSON 列序列化/反序列化在此封装。

record 以 python 对象进出（windows/indices/lag/sync_corr/divergence/balance 等都是
list/dict），JSON 编解码是存储细节，不外泄给 collector/formatter。UPSERT 幂等、
单日单行、保留首次 created_at 刷 updated_at。
"""
from __future__ import annotations

import json
import sqlite3

_REQUIRED = ("date", "windows", "indices", "base_index")


def save(conn: sqlite3.Connection, record: dict) -> None:
    """写入/覆盖某交易日的联动性快照（UPSERT，保留 created_at 刷 updated_at）。"""
    for key in _REQUIRED:
        if record.get(key) is None:
            raise ValueError(f"save: 缺少必填字段 {key}")

    def _j(key, default):
        return json.dumps(record.get(key, default), ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO margin_index_correlation_daily (
            date, data_trade_date, windows_json, indices_json, base_index,
            lag_json, sync_corr_json, divergence_json, balance_json,
            sample_days_json, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            data_trade_date = excluded.data_trade_date,
            windows_json = excluded.windows_json,
            indices_json = excluded.indices_json,
            base_index = excluded.base_index,
            lag_json = excluded.lag_json,
            sync_corr_json = excluded.sync_corr_json,
            divergence_json = excluded.divergence_json,
            balance_json = excluded.balance_json,
            sample_days_json = excluded.sample_days_json,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            record.get("data_trade_date"),
            _j("windows", []),
            _j("indices", []),
            record["base_index"],
            _j("lag", {}),
            _j("sync_corr", {}),
            _j("divergence", {}),
            _j("balance", {}),
            _j("sample_days", {}),
            json.dumps(record["meta"], ensure_ascii=False) if record.get("meta") is not None else None,
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    def _j(col, default):
        return json.loads(row[col]) if row[col] else default

    return {
        "date": row["date"],
        "data_trade_date": row["data_trade_date"],
        "windows": _j("windows_json", []),
        "indices": _j("indices_json", []),
        "base_index": row["base_index"],
        "lag": _j("lag_json", {}),
        "sync_corr": _j("sync_corr_json", {}),
        "divergence": _j("divergence_json", {}),
        "balance": _j("balance_json", {}),
        "sample_days": _j("sample_days_json", {}),
        "meta": _j("meta_json", None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM margin_index_correlation_daily WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    """取 <= end_date 的最近 days 天快照，按日期正序（供 signals 只读回看）。"""
    rows = conn.execute(
        "SELECT * FROM margin_index_correlation_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]
