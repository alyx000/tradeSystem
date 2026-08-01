"""宏观日历预取与 SQLite 幂等同步回归。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import yaml

from collectors.market import CalendarPrefetchResult, prefetch_calendar_result
from db.schema import init_schema
from services.calendar_sync import coverage_receipt, sync_calendar_events


class _Registry:
    def __init__(self, data, *, success: bool = True):
        self.data = data
        self.success = success

    def call(self, name, *args):
        assert name == "get_macro_calendar_range"
        assert args == ("2026-07-29", "2026-08-11")
        return SimpleNamespace(success=self.success, data=self.data, error=None)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_prefetch_merges_and_atomically_writes_yaml(tmp_path):
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    (tracking / "calendar_auto.yaml").write_text(
        "events:\n"
        "  - date: '2026-07-28'\n"
        "    event: 旧事件\n"
        "    importance: low\n",
        encoding="utf-8",
    )
    registry = _Registry([
        {
            "date": "2026-07-30",
            "time": "20:30",
            "event": "美国GDP",
            "importance": "high",
            "region": "US",
        },
        {"date": "", "event": "无效事件"},
    ])

    result = prefetch_calendar_result(
        registry,
        days=14,
        from_date="2026-07-29",
        base_dir=tmp_path,
    )

    assert result.fetched_count == 1
    assert result.total_count == 2
    assert result.events[0]["event"] == "美国GDP"
    raw = yaml.safe_load((tracking / "calendar_auto.yaml").read_text(encoding="utf-8"))
    assert [event["event"] for event in raw["events"]] == ["旧事件", "美国GDP"]
    assert not list(tracking.glob("*.tmp"))


def test_sync_is_idempotent_updates_auto_and_preserves_manual():
    conn = _conn()
    events = ({
        "date": "2026-07-30",
        "time": "20:30",
        "event": "美国GDP",
        "importance": "high",
        "region": "US",
        "expected": "2.5%",
    },)

    first = sync_calendar_events(conn, events, input_by="pytest")
    second = sync_calendar_events(conn, events, input_by="pytest")
    assert first["inserted"] == 1
    assert second["unchanged"] == 1

    changed = ({**events[0], "expected": "2.6%"},)
    third = sync_calendar_events(conn, changed, input_by="pytest")
    assert third["updated"] == 1
    row = conn.execute(
        "SELECT source, country, impact, expected, note FROM calendar_events"
    ).fetchone()
    assert tuple(row) == ("auto_prefetch", "US", "high", "2.6%", "input_by=pytest")

    conn.execute(
        "UPDATE calendar_events SET source = 'manual', expected = '人工值' WHERE event = '美国GDP'"
    )
    conn.commit()
    preserved = sync_calendar_events(conn, changed, input_by="pytest")
    assert preserved["manual_preserved"] == 1
    assert conn.execute(
        "SELECT expected FROM calendar_events WHERE event = '美国GDP'"
    ).fetchone()[0] == "人工值"


def test_coverage_receipt_distinguishes_complete_and_partial():
    complete = CalendarPrefetchResult(
        from_date="2026-07-29",
        to_date="2026-08-11",
        fetched_count=3,
        total_count=3,
        coverage_end="2026-08-06",
        events=(),
    )
    partial = CalendarPrefetchResult(
        from_date="2026-07-29",
        to_date="2026-08-11",
        fetched_count=1,
        total_count=1,
        coverage_end="2026-07-30",
        events=(),
    )

    assert coverage_receipt(complete)["status"] == "complete"
    assert coverage_receipt(partial)["status"] == "partial"


def test_sync_requires_input_by():
    conn = _conn()
    try:
        sync_calendar_events(conn, [], input_by="")
    except ValueError as exc:
        assert "input_by" in str(exc)
    else:
        raise AssertionError("空 input_by 应拒绝")


def test_manual_duplicate_takes_precedence_over_older_auto_row():
    conn = _conn()
    conn.execute(
        "INSERT INTO calendar_events(date,event,source,expected) VALUES (?,?,?,?)",
        ("2026-07-30", "美国GDP", "auto_prefetch", "旧自动值"),
    )
    conn.execute(
        "INSERT INTO calendar_events(date,event,source,expected) VALUES (?,?,?,?)",
        ("2026-07-30", "美国GDP", "manual", "人工值"),
    )
    conn.commit()

    receipt = sync_calendar_events(
        conn,
        [{"date": "2026-07-30", "event": "美国GDP", "expected": "新自动值"}],
        input_by="pytest",
    )

    assert receipt["manual_preserved"] == 1
    rows = conn.execute(
        "SELECT source, expected FROM calendar_events ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("auto_prefetch", "旧自动值"),
        ("manual", "人工值"),
    ]
