"""market_timing_signal 表读写（一指数一天一行快照）。

upsert 按 PK(trade_date, index_code)：同日重跑刷新(refreshed)、不产生重复行。
底分型生命周期推进需读「上一交易日同指数行」，故提供 get_prior_signal。
"""
from __future__ import annotations

import sqlite3

# upsert 写入列（updated_at 由 SQL 单独写 datetime('now')；created_at 用表默认）
_COLUMNS = (
    "trade_date", "index_code", "index_name",
    "swing_pivot_date", "swing_pivot_type", "swing_pivot_price",
    "fib_day_count", "fib_hit", "fib_near",
    "fractal_status", "fractal_low_date", "fractal_low_price", "fractal_confirm_date", "fractal_json",
    "resonance_count", "market_amount_yi", "amount_pctile_20d",
    "limit_down_count", "advance", "decline", "data_source",
)


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def upsert_signal(conn: sqlite3.Connection, row: dict) -> None:
    """按 (trade_date, index_code) upsert。冲突即刷新所有列(refreshed)。"""
    placeholders = ",".join("?" for _ in _COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in ("trade_date", "index_code"))
    sql = (
        f"INSERT INTO market_timing_signal ({','.join(_COLUMNS)}, updated_at) "
        f"VALUES ({placeholders}, datetime('now')) "
        f"ON CONFLICT(trade_date, index_code) DO UPDATE SET {updates}, updated_at=datetime('now')"
    )
    conn.execute(sql, [row.get(c) for c in _COLUMNS])


def list_signals(
    conn: sqlite3.Connection, *, date: str | None = None, index_code: str | None = None, limit: int = 50
) -> list[dict]:
    """只读查询：指定 date 返回当日全部指数；否则返回最近 limit 行。"""
    where, params = [], []
    if date:
        where.append("trade_date=?")
        params.append(date)
    if index_code:
        where.append("index_code=?")
        params.append(index_code)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM market_timing_signal{clause} ORDER BY trade_date DESC, index_code"
    if not date:
        sql += f" LIMIT {int(limit)}"
    return _rows(conn.execute(sql, params))
