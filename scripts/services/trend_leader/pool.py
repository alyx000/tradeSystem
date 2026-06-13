"""趋势主升观察池状态机（trend_leader_pool 读写）。

状态：active ⇄ exited。PK=(code, entered_date)。
- record：命中趋势主升 → 无 active 则入池(entered)、有 active 则刷新(refreshed)
- touch：在池股每日维护，更新 last_seen/days/signal（不新建；无 active 则忽略）
- mark_exited：active → exited（趋势破坏触发）
- days_in_pool：按「出现的扫描日数」计，同日重扫不重复 +1（去掉对交易日历的依赖）
"""
from __future__ import annotations

import json
import sqlite3


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        raw = d.get("last_signal_json")
        d["last_signal"] = json.loads(raw) if raw else None
        out.append(d)
    return out


def _dump(signal_json) -> str | None:
    return json.dumps(signal_json, ensure_ascii=False) if signal_json is not None else None


def get_active(conn: sqlite3.Connection, code: str) -> dict | None:
    rows = _rows(conn.execute(
        "SELECT * FROM trend_leader_pool WHERE code=? AND status='active' "
        "ORDER BY entered_date DESC", (code,)))
    return rows[0] if rows else None


def list_pool(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        return _rows(conn.execute(
            "SELECT * FROM trend_leader_pool WHERE status=? ORDER BY entered_date, code", (status,)))
    return _rows(conn.execute(
        "SELECT * FROM trend_leader_pool ORDER BY entered_date, code"))


def _active_row(conn: sqlite3.Connection, code: str):
    return conn.execute(
        "SELECT entered_date, last_seen_date, days_in_pool FROM trend_leader_pool "
        "WHERE code=? AND status='active'", (code,)).fetchone()


def _refresh(conn, code, entered_date, last_seen, days, date, sig, *, keep_sig_if_none: bool) -> None:
    new_days = days if last_seen == date else days + 1  # 同日重扫不重复计
    sig_expr = "COALESCE(?, last_signal_json)" if keep_sig_if_none else "?"
    conn.execute(
        f"UPDATE trend_leader_pool SET last_seen_date=?, days_in_pool=?, "
        f"last_signal_json={sig_expr}, updated_at=datetime('now') "
        f"WHERE code=? AND entered_date=?",
        (date, new_days, sig, code, entered_date))
    conn.commit()


def record(conn: sqlite3.Connection, *, code: str, name: str, sw_l2: str,
           first_limit_date: str, date: str, signal_json=None) -> str:
    """命中趋势主升入池或刷新。返回 'entered'（新建 active）/ 'refreshed'（已有 active）。"""
    active = _active_row(conn, code)
    sig = _dump(signal_json)
    if active is None:
        # ON CONFLICT 重新激活：极端下同一 (code, entered_date) 已有 exited 行（同日退池后再命中）
        # 时不报 PK 冲突而是复用该行重置为 active。consistent 涨停数据下本不可达，纯防御。
        conn.execute(
            "INSERT INTO trend_leader_pool "
            "(code, name, sw_l2, first_limit_date, entered_date, last_seen_date, "
            " days_in_pool, status, last_signal_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'active', ?, datetime('now')) "
            "ON CONFLICT(code, entered_date) DO UPDATE SET "
            " status='active', last_seen_date=excluded.last_seen_date, days_in_pool=1, "
            " exit_date=NULL, exit_reason=NULL, last_signal_json=excluded.last_signal_json, "
            " name=excluded.name, sw_l2=excluded.sw_l2, first_limit_date=excluded.first_limit_date, "
            " updated_at=datetime('now')",
            (code, name, sw_l2, first_limit_date, date, date, sig))
        conn.commit()
        return "entered"
    entered_date, last_seen, days = active
    _refresh(conn, code, entered_date, last_seen, days, date, sig, keep_sig_if_none=False)
    return "refreshed"


def touch(conn: sqlite3.Connection, code: str, date: str, signal_json=None) -> None:
    """在池股每日维护：刷新 last_seen/days/signal；无 active 行则忽略（signal=None 时保留原值）。"""
    active = _active_row(conn, code)
    if active is None:
        return
    entered_date, last_seen, days = active
    _refresh(conn, code, entered_date, last_seen, days, date, _dump(signal_json), keep_sig_if_none=True)


def mark_exited(conn: sqlite3.Connection, code: str, date: str, reason: str) -> None:
    """active → exited（趋势破坏）。"""
    conn.execute(
        "UPDATE trend_leader_pool SET status='exited', exit_date=?, exit_reason=?, "
        "last_seen_date=?, updated_at=datetime('now') WHERE code=? AND status='active'",
        (date, reason, date, code))
    conn.commit()
