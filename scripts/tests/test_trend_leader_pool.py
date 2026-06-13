"""趋势主升观察池 trend_leader_pool 的 schema + 状态机单测（Stage 2a/2b）。

PK=(code, entered_date)：exited 再命中=新建一行保留历史；active 每 code 至多一行。
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.trend_leader import pool


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row  # 与生产 db.connection 一致
    init_schema(c)
    return c


def _rec(conn, date, **over):
    kw = dict(code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
              first_limit_date="2026-06-09", date=date)
    kw.update(over)
    return pool.record(conn, **kw)


# ---- 2a schema ----

def test_schema_creates_trend_leader_pool(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trend_leader_pool)")}
    assert {
        "code", "name", "sw_l2", "first_limit_date", "entered_date", "last_seen_date",
        "days_in_pool", "status", "exit_date", "exit_reason", "last_signal_json",
    } <= cols


# ---- 2b 状态机 ----

def test_enter_creates_active_days_1(conn):
    assert _rec(conn, "2026-06-09") == "entered"
    a = pool.get_active(conn, "600552")
    assert a["status"] == "active" and a["days_in_pool"] == 1 and a["entered_date"] == "2026-06-09"


def test_refresh_next_day_increments_days(conn):
    _rec(conn, "2026-06-09")
    assert _rec(conn, "2026-06-10") == "refreshed"
    a = pool.get_active(conn, "600552")
    assert a["days_in_pool"] == 2 and a["last_seen_date"] == "2026-06-10"


def test_same_day_rescan_does_not_double_count(conn):
    _rec(conn, "2026-06-09")
    _rec(conn, "2026-06-09")
    assert pool.get_active(conn, "600552")["days_in_pool"] == 1


def test_mark_exited_clears_active_and_records_reason(conn):
    _rec(conn, "2026-06-09")
    pool.mark_exited(conn, "600552", date="2026-06-12", reason="跌破MA10")
    assert pool.get_active(conn, "600552") is None
    exited = pool.list_pool(conn, status="exited")
    assert len(exited) == 1 and exited[0]["exit_reason"] == "跌破MA10" and exited[0]["exit_date"] == "2026-06-12"


def test_reentry_after_exit_new_row_keeps_history(conn):
    _rec(conn, "2026-06-09")
    pool.mark_exited(conn, "600552", date="2026-06-12", reason="跌破MA10")
    assert _rec(conn, "2026-06-20") == "entered"
    a = pool.get_active(conn, "600552")
    assert a["entered_date"] == "2026-06-20" and a["days_in_pool"] == 1
    assert len(pool.list_pool(conn)) == 2  # 1 exited 历史 + 1 active


def test_reentry_same_date_after_exit_reactivates(conn):
    # 极端：同日入池→退池→同日再命中。ON CONFLICT 重新激活，不崩、不新增行。
    _rec(conn, "2026-06-09")
    pool.mark_exited(conn, "600552", date="2026-06-09", reason="盘中破位")
    assert _rec(conn, "2026-06-09") == "entered"
    a = pool.get_active(conn, "600552")
    assert a is not None and a["status"] == "active" and a["exit_reason"] is None
    assert len(pool.list_pool(conn)) == 1  # 同 (code,entered_date) 复用


def test_touch_updates_signal_without_entering(conn):
    _rec(conn, "2026-06-09")
    pool.touch(conn, "600552", date="2026-06-10", signal_json={"overheat": True, "deviation": 0.09})
    a = pool.get_active(conn, "600552")
    assert a["days_in_pool"] == 2 and a["last_signal"]["overheat"] is True


def test_touch_noop_when_no_active(conn):
    pool.touch(conn, "000001", date="2026-06-10", signal_json={"x": 1})
    assert pool.list_pool(conn) == []


def test_signal_json_roundtrip(conn):
    _rec(conn, "2026-06-09", signal_json={"near_ma5": True, "deviation": 0.012})
    a = pool.get_active(conn, "600552")
    assert a["last_signal"]["near_ma5"] is True and a["last_signal"]["deviation"] == pytest.approx(0.012)
