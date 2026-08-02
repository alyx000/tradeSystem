from datetime import datetime
from zoneinfo import ZoneInfo

from services.intraday_monitor.guards import is_intraday_session
from services.intraday_monitor.rules import MonitorRule, should_emit


def test_below_is_strict_and_equality_does_not_trigger():
    rule = MonitorRule("r", "指数", "000001.SH", 1572.0)
    assert rule.is_active(1571.99) is True
    assert rule.is_active(1572.0) is False
    assert rule.is_active(1572.01) is False


def test_transition_only_emits_on_entry():
    assert should_emit(previous_active=None, current_active=True) is True
    assert should_emit(previous_active=False, current_active=True) is True
    assert should_emit(previous_active=True, current_active=True) is False
    assert should_emit(previous_active=True, current_active=False) is False


def test_shanghai_session_boundaries_are_explicit():
    tz = ZoneInfo("Asia/Shanghai")
    assert is_intraday_session(datetime(2026, 8, 3, 9, 30, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 11, 30, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 11, 31, tzinfo=tz)) is False
    assert is_intraday_session(datetime(2026, 8, 3, 13, 0, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 15, 0, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 15, 1, tzinfo=tz)) is False
