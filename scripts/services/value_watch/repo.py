"""value_watch_daily 快照 + sent_events 通知账本存取层。

契约（spec v8）：
- 一天一行；同日重跑 UPSERT 刷新 payload/logic_version/updated_at，sent_events_json 只增不删。
- 已发事件全集 = 全表 sent_events_json 并集（每交易日 1 行，全表扫可接受）。
- payload 落库 json.dumps(allow_nan=False)：NaN 会写成非标 JSON token，严格消费端直接炸。
"""
from __future__ import annotations

import json
import sqlite3


def upsert_daily(conn: sqlite3.Connection, date: str, payload: dict, logic_version: int) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    conn.execute(
        """
        INSERT INTO value_watch_daily (date, payload_json, logic_version)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            payload_json = excluded.payload_json,
            logic_version = excluded.logic_version,
            updated_at = datetime('now','localtime')
        """,
        (date, payload_json, logic_version),
    )
    conn.commit()


def append_sent_events(conn: sqlite3.Connection, date: str, keys: list[str]) -> None:
    """把成功发送的事件键合并进当日行（只增不删、去重）。

    读-合并-写包在 BEGIN IMMEDIATE 写事务里（门2 G2 high-2）：两个连接并发追加时
    非原子读改写会互相覆盖对方的新键——丢键 = 已发送事件下轮重推，破坏账本只增不删。
    行不存在抛错（调用序契约：必须先 upsert_daily）。"""
    if not keys:
        return
    if conn.in_transaction:
        conn.commit()   # 结束残留事务，保证 BEGIN IMMEDIATE 无条件生效
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT sent_events_json FROM value_watch_daily WHERE date = ?", (date,)
        ).fetchone()
        if row is None:
            raise ValueError(f"value_watch_daily 无 {date} 行；须先 upsert_daily 再 append")
        merged = sorted(set(json.loads(row[0] or "[]")) | set(keys))
        conn.execute(
            "UPDATE value_watch_daily SET sent_events_json = ?, "
            "updated_at = datetime('now','localtime') WHERE date = ?",
            (json.dumps(merged, ensure_ascii=False), date),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def load_sent_ledger(conn: sqlite3.Connection) -> set[str]:
    ledger: set[str] = set()
    for (raw,) in conn.execute("SELECT sent_events_json FROM value_watch_daily").fetchall():
        ledger |= set(json.loads(raw or "[]"))
    return ledger


def get_snapshot(conn: sqlite3.Connection, date: str | None) -> dict | None:
    """读单日快照；date=None 取最新。无行返回 None。"""
    if date is None:
        row = conn.execute(
            "SELECT date, payload_json, sent_events_json, logic_version, created_at, updated_at "
            "FROM value_watch_daily ORDER BY date DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT date, payload_json, sent_events_json, logic_version, created_at, updated_at "
            "FROM value_watch_daily WHERE date = ?",
            (date,),
        ).fetchone()
    if row is None:
        return None
    return {
        "date": row[0],
        "payload": json.loads(row[1]),
        "sent_events": json.loads(row[2] or "[]"),
        "logic_version": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }
