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
    """把成功发送的事件键合并进当日行（只增不删、去重）。行不存在则静默跳过——
    调用方总是先 upsert_daily 再 append。"""
    if not keys:
        return
    row = conn.execute(
        "SELECT sent_events_json FROM value_watch_daily WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        return
    existing = set(json.loads(row[0] or "[]"))
    merged = sorted(existing | set(keys))
    conn.execute(
        "UPDATE value_watch_daily SET sent_events_json = ?, "
        "updated_at = datetime('now','localtime') WHERE date = ?",
        (json.dumps(merged, ensure_ascii=False), date),
    )
    conn.commit()


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
