from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services.intraday_monitor.guards import (
    is_close_finalization_window,
    is_intraday_session,
)
from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    GUOCI_MATERIALS_BELOW_67_22_20260831,
    KAILAIYING_BREAKOUT_172_26_20260821_24,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    SSE_COMPOSITE_RECLAIM_3955,
    STAR50_BREAKOUT_1700_20260821_24,
    ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
    THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
    MonitorRule,
    should_emit,
)


BOARD_BREAK_RULE = MonitorRule(
    "test-board-break",
    "测试股票",
    "600127.SH",
    None,
    direction="below",
    inclusive=False,
    emit_on_initial_match=True,
    action_label="低于",
    valid_from=date(2026, 8, 19),
    valid_until=date(2026, 8, 20),
    value_label="价格",
    value_unit="元",
    threshold_mode="daily_up_limit",
    threshold_label="当日涨停价",
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


def test_fixed_and_ma_temporary_rules_are_registered():
    assert DEFAULT_RULES == (
        SSE_COMPOSITE_RECLAIM_3955,
        LITONG_ELECTRONICS_BELOW_123_92_20260811,
        STAR50_BREAKOUT_1700_20260821_24,
        KAILAIYING_BREAKOUT_172_26_20260821_24,
        GUOCI_MATERIALS_BELOW_67_22_20260831,
        ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
        THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
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

    star50 = STAR50_BREAKOUT_1700_20260821_24
    assert star50.rule_id == "star50-breakout-1700-20260821-24"
    assert star50.instrument_name == "科创50"
    assert star50.code == "000688.SH"
    assert star50.threshold == 1700.0
    assert star50.direction == "above"
    assert star50.inclusive is False
    assert star50.emit_on_initial_match is True
    assert star50.action_text == "突破"
    assert star50.is_active(1700.0) is False
    assert star50.is_active(1700.01) is True
    assert star50.is_effective_on(date(2026, 8, 20)) is False
    assert star50.is_effective_on(date(2026, 8, 21)) is True
    assert star50.is_effective_on(date(2026, 8, 24)) is True
    assert star50.is_effective_on(date(2026, 8, 25)) is False

    kailaiying = KAILAIYING_BREAKOUT_172_26_20260821_24
    assert kailaiying.rule_id == "kailaiying-breakout-172-26-20260821-24"
    assert kailaiying.instrument_name == "凯莱英"
    assert kailaiying.code == "002821.SZ"
    assert kailaiying.threshold == 172.26
    assert kailaiying.direction == "above"
    assert kailaiying.inclusive is False
    assert kailaiying.emit_on_initial_match is True
    assert kailaiying.action_text == "突破"
    assert kailaiying.value_label == "价格"
    assert kailaiying.value_unit == "元"
    assert kailaiying.is_active(172.26) is False
    assert kailaiying.is_active(172.27) is True
    assert kailaiying.is_effective_on(date(2026, 8, 20)) is False
    assert kailaiying.is_effective_on(date(2026, 8, 21)) is True
    assert kailaiying.is_effective_on(date(2026, 8, 24)) is True
    assert kailaiying.is_effective_on(date(2026, 8, 25)) is False

    guoci = GUOCI_MATERIALS_BELOW_67_22_20260831
    assert guoci.rule_id == "guoci-materials-below-67-22-20260831"
    assert guoci.instrument_name == "国瓷材料"
    assert guoci.code == "300285.SZ"
    assert guoci.threshold == 67.22
    assert guoci.is_active(67.21) is True
    assert guoci.is_active(67.22) is False
    assert guoci.is_effective_on(date(2026, 8, 30)) is False
    assert guoci.is_effective_on(date(2026, 8, 31)) is True
    assert guoci.is_effective_on(date(2026, 9, 1)) is False

    zhongke = ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902
    assert zhongke.rule_id == "zhongke-feice-below-previous-ma5-20260831-0902"
    assert zhongke.instrument_name == "中科飞测"
    assert zhongke.code == "688361.SH"
    assert zhongke.threshold is None
    assert zhongke.threshold_mode == "previous_close_ma"
    assert zhongke.threshold_window == 5
    assert zhongke.threshold_provider == "tushare"
    assert zhongke.resolve_threshold(
        {}, historical_closes=[100, 101, 102, 103, 104]
    ) == 102.0
    assert zhongke.is_active(101.99, threshold=102.0) is True
    assert zhongke.is_active(102.0, threshold=102.0) is False
    assert zhongke.is_effective_on(date(2026, 8, 30)) is False
    assert zhongke.is_effective_on(date(2026, 8, 31)) is True
    assert zhongke.is_effective_on(date(2026, 9, 2)) is True
    assert zhongke.is_effective_on(date(2026, 9, 3)) is False

    ths_all_a = THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT
    assert ths_all_a.code == "883421.THS"
    assert ths_all_a.provider == "tonghuashun"
    assert ths_all_a.threshold == -4.0
    assert ths_all_a.value_mode == "daily_pct_change"
    assert ths_all_a.resolve_value({"price": 96, "pre_close": 100}) == -4.0
    assert ths_all_a.is_active(-4.0) is False
    assert ths_all_a.is_active(-4.0001) is True


def test_daily_pct_change_uses_price_and_pre_close_not_provider_pct_field():
    rule = THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT
    assert rule.resolve_value({"price": 95, "pre_close": 100, "pct_chg": 9.9}) == -5.0
    with pytest.raises(ValueError, match="前收盘价"):
        rule.resolve_value({"price": 95, "pre_close": 0})
    with pytest.raises(ValueError, match="前收盘价"):
        rule.resolve_value({"price": 95})


def test_negative_threshold_only_allowed_for_non_price_value_mode():
    with pytest.raises(ValueError, match="价格阈值必须为正数"):
        MonitorRule("negative-price", "指数", "000001.SH", -4.0)


@pytest.mark.parametrize(
    ("threshold_mode", "extra"),
    (
        ("daily_up_limit", {}),
        (
            "previous_close_ma",
            {"threshold_window": 5, "threshold_provider": "tushare"},
        ),
    ),
)
def test_daily_pct_change_rejects_dynamic_price_threshold_modes(threshold_mode, extra):
    with pytest.raises(ValueError, match="仅支持固定百分比阈值"):
        MonitorRule(
            "invalid-pct-dynamic",
            "测试指数",
            "883421.THS",
            None,
            threshold_mode=threshold_mode,
            value_mode="daily_pct_change",
            **extra,
        )



def test_dynamic_board_break_capability_remains_available():
    rule = BOARD_BREAK_RULE
    assert rule.threshold is None
    assert rule.threshold_mode == "daily_up_limit"
    assert rule.threshold_label == "当日涨停价"
    assert rule.resolve_threshold({"pre_close": 7.11, "name": "测试股票"}) == 7.82
    assert rule.is_active(7.82, threshold=7.82) is False
    assert rule.is_active(7.81, threshold=7.82) is True
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
        BOARD_BREAK_RULE.is_active(7.81)
    with pytest.raises(ValueError, match="无法根据前收盘价"):
        BOARD_BREAK_RULE.resolve_threshold({"pre_close": None})


def test_previous_close_ma_rule_rejects_ambiguous_or_incomplete_inputs():
    with pytest.raises(ValueError, match="不得同时提供固定"):
        MonitorRule(
            "ambiguous-ma",
            "测试标的",
            "688001.SH",
            10.0,
            threshold_mode="previous_close_ma",
            threshold_window=5,
            threshold_provider="tushare",
        )
    with pytest.raises(ValueError, match="正整数"):
        MonitorRule(
            "missing-window",
            "测试标的",
            "688001.SH",
            None,
            threshold_mode="previous_close_ma",
            threshold_provider="tushare",
        )
    with pytest.raises(ValueError, match="threshold_provider"):
        MonitorRule(
            "missing-provider",
            "测试标的",
            "688001.SH",
            None,
            threshold_mode="previous_close_ma",
            threshold_window=5,
        )
    rule = ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902
    with pytest.raises(ValueError, match="需要 5 个"):
        rule.resolve_threshold({}, historical_closes=[100, 101, 102, 103])
    with pytest.raises(ValueError, match="非有限或非正"):
        rule.resolve_threshold({}, historical_closes=[100, 101, 0, 103, 104])


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
