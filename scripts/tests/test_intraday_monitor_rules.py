from datetime import datetime
from zoneinfo import ZoneInfo

from services.intraday_monitor.guards import is_intraday_session
from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    SSE_COMPOSITE_RECLAIM_3955,
    MonitorRule,
    should_emit,
)


def test_below_is_strict_and_equality_does_not_trigger():
    rule = MonitorRule("r", "指数", "000001.SH", 1572.0)
    assert rule.is_active(1571.99) is True
    assert rule.is_active(1572.0) is False
    assert rule.is_active(1572.01) is False


def test_reclaim_is_inclusive_and_does_not_emit_on_initial_match():
    rule = MonitorRule(
        "reclaim",
        "指数",
        "000001.SH",
        1582.0,
        direction="above",
        inclusive=True,
        emit_on_initial_match=False,
        action_label="收复",
    )

    assert rule.is_active(1581.99) is False
    assert rule.is_active(1582.0) is True
    assert rule.is_active(1582.01) is True
    assert rule.action_text == "收复"
    assert should_emit(
        previous_active=None,
        current_active=True,
        emit_on_initial_match=rule.emit_on_initial_match,
    ) is False
    assert should_emit(
        previous_active=False,
        current_active=True,
        emit_on_initial_match=rule.emit_on_initial_match,
    ) is True


def test_only_sse_composite_reclaim_3955_is_enabled():
    assert DEFAULT_RULES == (SSE_COMPOSITE_RECLAIM_3955,)
    assert SSE_COMPOSITE_RECLAIM_3955.rule_id == "sse-composite-reclaim-3955"
    assert SSE_COMPOSITE_RECLAIM_3955.instrument_name == "上证指数"
    assert SSE_COMPOSITE_RECLAIM_3955.code == "000001.SH"
    assert SSE_COMPOSITE_RECLAIM_3955.threshold == 3955.0
    assert SSE_COMPOSITE_RECLAIM_3955.direction == "above"
    assert SSE_COMPOSITE_RECLAIM_3955.inclusive is True
    assert SSE_COMPOSITE_RECLAIM_3955.emit_on_initial_match is False
    assert SSE_COMPOSITE_RECLAIM_3955.action_text == "站上"
    assert SSE_COMPOSITE_RECLAIM_3955.is_active(3954.99) is False
    assert SSE_COMPOSITE_RECLAIM_3955.is_active(3955.0) is True


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
