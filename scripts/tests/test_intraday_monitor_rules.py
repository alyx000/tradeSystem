from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services.intraday_monitor.guards import (
    is_close_finalization_window,
    is_intraday_session,
)
from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    JINGLIANG_HOLDINGS_BOARD_BREAK_20260819_20,
    JINJIAN_RICE_BOARD_BREAK_20260819_20,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    RED_SIFANG_BOARD_BREAK_20260819_20,
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


def test_sse_litong_and_two_day_board_break_rules_are_enabled():
    assert DEFAULT_RULES == (
        SSE_COMPOSITE_RECLAIM_3955,
        LITONG_ELECTRONICS_BELOW_123_92_20260811,
        JINJIAN_RICE_BOARD_BREAK_20260819_20,
        RED_SIFANG_BOARD_BREAK_20260819_20,
        JINGLIANG_HOLDINGS_BOARD_BREAK_20260819_20,
    )
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

    litong = LITONG_ELECTRONICS_BELOW_123_92_20260811
    assert litong.rule_id == "litong-electronics-below-123-92-20260811"
    assert litong.instrument_name == "利通电子"
    assert litong.code == "603629.SH"
    assert litong.threshold == 123.92
    assert litong.inclusive is False
    assert litong.emit_on_initial_match is True
    assert litong.value_label == "价格"
    assert litong.value_unit == "元"
    assert litong.is_active(123.91) is True
    assert litong.is_active(123.92) is False
    assert litong.is_effective_on(date(2026, 8, 10)) is False
    assert litong.is_effective_on(date(2026, 8, 11)) is True
    assert litong.is_effective_on(date(2026, 8, 12)) is False

    expected = (
        (JINJIAN_RICE_BOARD_BREAK_20260819_20, "金健米业", "600127.SH", 7.11, 7.82),
        (RED_SIFANG_BOARD_BREAK_20260819_20, "红四方", "603395.SH", 26.36, 29.0),
        (JINGLIANG_HOLDINGS_BOARD_BREAK_20260819_20, "京粮控股", "000505.SZ", 6.79, 7.47),
    )
    for rule, name, code, pre_close, up_limit in expected:
        assert rule.instrument_name == name
        assert rule.code == code
        assert rule.threshold is None
        assert rule.threshold_mode == "daily_up_limit"
        assert rule.threshold_label == "当日涨停价"
        assert rule.direction == "below"
        assert rule.inclusive is False
        assert rule.emit_on_initial_match is True
        assert rule.resolve_threshold({"pre_close": pre_close, "name": name}) == up_limit
        assert rule.is_active(up_limit, threshold=up_limit) is False
        assert rule.is_active(up_limit - 0.01, threshold=up_limit) is True
        assert rule.is_effective_on(date(2026, 8, 18)) is False
        assert rule.is_effective_on(date(2026, 8, 19)) is True
        assert rule.is_effective_on(date(2026, 8, 20)) is True
        assert rule.is_effective_on(date(2026, 8, 21)) is False


def test_rule_rejects_reversed_validity_window():
    with pytest.raises(ValueError, match="valid_from"):
        MonitorRule(
            "invalid-window",
            "测试标的",
            "600000.SH",
            10.0,
            valid_from=date(2026, 8, 12),
            valid_until=date(2026, 8, 11),
        )


def test_dynamic_limit_rule_rejects_ambiguous_or_missing_threshold_inputs():
    with pytest.raises(ValueError, match="不得同时提供固定"):
        MonitorRule(
            "ambiguous",
            "测试标的",
            "600000.SH",
            11.0,
            threshold_mode="daily_up_limit",
        )
    with pytest.raises(ValueError, match="必须提供"):
        MonitorRule("missing", "测试标的", "600000.SH", None)
    with pytest.raises(ValueError, match="必须先解析"):
        JINJIAN_RICE_BOARD_BREAK_20260819_20.is_active(7.81)
    with pytest.raises(ValueError, match="无法根据前收盘价"):
        JINJIAN_RICE_BOARD_BREAK_20260819_20.resolve_threshold({"pre_close": None})


def test_transition_only_emits_on_entry():
    assert should_emit(previous_active=None, current_active=True) is True
    assert should_emit(previous_active=False, current_active=True) is True
    assert should_emit(previous_active=True, current_active=True) is False
    assert should_emit(previous_active=True, current_active=False) is False


def test_shanghai_session_boundaries_are_explicit():
    tz = ZoneInfo("Asia/Shanghai")
    assert is_intraday_session(datetime(2026, 8, 3, 9, 30, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 11, 30, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 11, 30, 59, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 11, 31, tzinfo=tz)) is False
    assert is_intraday_session(datetime(2026, 8, 3, 13, 0, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 15, 0, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 15, 0, 59, tzinfo=tz)) is True
    assert is_intraday_session(datetime(2026, 8, 3, 15, 1, tzinfo=tz)) is False
    assert is_close_finalization_window(datetime(2026, 8, 3, 15, 0, tzinfo=tz)) is True
    assert is_close_finalization_window(datetime(2026, 8, 3, 15, 5, 59, tzinfo=tz)) is True
    assert is_close_finalization_window(datetime(2026, 8, 3, 15, 6, tzinfo=tz)) is False
