"""月线模式纯逻辑检测器测试。

全部使用内存样本，覆盖完成月聚合、指标、三类模式和观察状态；不触网络、DB 或推送。
"""
from __future__ import annotations

import calendar
from datetime import date

import pytest

from services.monthly_pattern import detectors as D
from services.monthly_pattern.models import MonthlyBar


def _month_end(index: int) -> str:
    year = 2020 + index // 12
    month = index % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _monthly_bars(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    complete: list[bool] | None = None,
    shape_valid: list[bool] | None = None,
) -> list[MonthlyBar]:
    bars: list[MonthlyBar] = []
    for i, close in enumerate(closes):
        open_ = opens[i] if opens else close
        high = highs[i] if highs else max(open_, close) + 0.1
        low = lows[i] if lows else min(open_, close) - 0.1
        end_date = _month_end(i)
        bars.append(
            MonthlyBar(
                month=end_date[:7],
                end_date=end_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volumes[i] if volumes else 100.0,
                amount=(volumes[i] if volumes else 100.0) * close,
                is_complete=complete[i] if complete else True,
                trading_days=20,
                price_shape_valid=shape_valid[i] if shape_valid else True,
            )
        )
    return bars


def _zero_axis_cross_closes() -> list[float]:
    # 先长期上行，再连续两个月调整，最后一月反弹：
    # 末月恰好形成 DIF 在零轴上方由下向上穿 DEA。
    return [10.0 + i * 0.5 for i in range(36)] + [26.9, 26.3, 28.3]


def test_aggregate_completed_monthly_bars_uses_qfq_ohlcv_consistently():
    # 输入字段均已是前复权口径；raw_* 只用于钉死聚合器不得混入未复权价格。
    daily = [
        {
            "trade_date": "2026-01-02",
            "open": 5.0,
            "high": 6.0,
            "low": 4.0,
            "close": 5.5,
            "volume": 10.0,
            "amount": 55.0,
            "raw_open": 10.0,
            "raw_high": 12.0,
            "raw_low": 8.0,
            "raw_close": 11.0,
        },
        {
            "trade_date": "2026-01-30",
            "open": 5.6,
            "high": 7.0,
            "low": 5.0,
            "close": 6.5,
            "volume": 20.0,
            "amount": 130.0,
        },
        {
            "trade_date": "2026-02-02",
            "open": 6.6,
            "high": 7.2,
            "low": 6.3,
            "close": 7.0,
            "volume": 30.0,
            "amount": 210.0,
        },
    ]

    result = D.aggregate_completed_monthly_bars(daily)

    assert len(result) == 1
    january = result[0]
    assert january.month == "2026-01"
    assert january.open == 5.0
    assert january.high == 7.0
    assert january.low == 4.0
    assert january.close == 6.5
    assert january.volume == 30.0
    assert january.amount == 185.0
    assert january.trading_days == 2
    assert january.is_complete is True


def test_aggregate_excludes_last_month_unless_calendar_attests_completion():
    daily = [
        {
            "trade_date": "2026-01-30",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "vol": 100.0,
            "amount": 1000.0,
        },
        {
            "trade_date": "2026-02-27",
            "open": 11.0,
            "high": 12.0,
            "low": 10.0,
            "close": 11.5,
            "vol": 120.0,
            "amount": 1200.0,
        },
    ]

    assert [bar.month for bar in D.aggregate_completed_monthly_bars(daily)] == ["2026-01"]
    assert [
        bar.month
        for bar in D.aggregate_completed_monthly_bars(daily, last_month_complete=True)
    ] == ["2026-01", "2026-02"]


def test_aggregate_rejects_duplicate_or_non_finite_daily_prices():
    duplicate = [
        {
            "trade_date": "2026-01-02",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 100.0,
            "amount": 1000.0,
        },
        {
            "trade_date": "2026-01-02",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 100.0,
            "amount": 1000.0,
        },
    ]
    with pytest.raises(ValueError, match="duplicate trade_date"):
        D.aggregate_completed_monthly_bars(duplicate)

    dirty = [{**duplicate[0], "close": float("nan")}]
    with pytest.raises(ValueError, match="finite"):
        D.aggregate_completed_monthly_bars(dirty, last_month_complete=True)


def test_monthly_indicators_compute_sma_and_zero_axis_golden_cross():
    closes = _zero_axis_cross_closes()
    indicators = D.compute_monthly_indicators(_monthly_bars(closes))
    latest = indicators[-1]

    assert latest.ma5 == pytest.approx(sum(closes[-5:]) / 5)
    assert latest.ma10 == pytest.approx(sum(closes[-10:]) / 10)
    assert latest.ma20 == pytest.approx(sum(closes[-20:]) / 20)
    assert latest.macd_dif > 0
    assert latest.macd_dea > 0
    assert latest.macd_golden_cross is True
    assert latest.macd_zero_axis_golden_cross is True


def test_fundamental_monthly_trend_requires_alignment_and_close_at_or_above_ma5():
    closes = [10.0 + i * 0.5 for i in range(20)]

    matched = D.detect_fundamental_monthly_trend(_monthly_bars(closes))

    assert matched.matched is True
    conditions = matched.evidence["conditions"]
    assert conditions["ma_bullish_alignment"]["met"] is True
    assert conditions["close_at_or_above_ma5"]["met"] is True
    assert matched.status == "matched"


def test_fundamental_monthly_trend_keeps_close_ma_signal_when_shape_is_invalid():
    closes = [10.0 + i * 0.5 for i in range(20)]
    shape_valid = [True] * 19 + [False]

    matched = D.detect_fundamental_monthly_trend(
        _monthly_bars(closes, shape_valid=shape_valid)
    )

    assert matched.matched is True


def test_fundamental_monthly_trend_reports_insufficient_history():
    result = D.detect_fundamental_monthly_trend(_monthly_bars([10.0] * 19))

    assert result.matched is False
    assert result.status == "insufficient_history"
    assert result.evidence["sample"] == {
        "available_months": 19,
        "required_months": 20,
        "excluded_incomplete_months": 0,
    }


def test_theme_monthly_attack_near_high_is_evidence_not_hard_gate():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    highs = [value + 0.1 for value in closes]
    lows = [value - 0.1 for value in closes]
    volumes = [100.0] * len(closes)
    opens[-1] = 20.0  # 月阳线实体从三条均线下方穿到三条均线上方
    lows[-1] = 19.5
    highs[-1] = 40.0  # 故意远离月高，证明该项只做证据
    volumes[-1] = 300.0

    result = D.detect_theme_monthly_attack(
        _monthly_bars(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    )

    assert result.matched is True
    conditions = result.evidence["conditions"]
    assert conditions["bullish_body_crosses_three_mas"]["met"] is True
    assert conditions["zero_axis_golden_cross"]["met"] is True
    assert conditions["volume_above_ma5_or_ma10"]["met"] is True
    assert conditions["close_near_month_high"]["met"] is False
    assert conditions["close_near_month_high"]["hard_gate"] is False


def test_theme_monthly_attack_requires_strict_volume_break():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    opens[-1] = 20.0
    result = D.detect_theme_monthly_attack(
        _monthly_bars(closes, opens=opens, volumes=[100.0] * len(closes))
    )

    assert result.matched is False
    assert result.evidence["conditions"]["volume_above_ma5_or_ma10"]["met"] is False


def test_theme_monthly_attack_fails_closed_when_latest_month_shape_is_invalid():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    opens[-1] = 20.0
    volumes = [100.0] * (len(closes) - 1) + [300.0]
    shape_valid = [True] * (len(closes) - 1) + [False]

    result = D.detect_theme_monthly_attack(
        _monthly_bars(
            closes,
            opens=opens,
            volumes=volumes,
            shape_valid=shape_valid,
        )
    )

    assert result.matched is False
    conditions = result.evidence["conditions"]
    assert conditions["price_shape_valid"]["met"] is False
    assert conditions["bullish_body_crosses_three_mas"]["met"] is False
    assert conditions["close_near_month_high"]["met"] is False


def test_formal_detectors_ignore_uncompleted_last_month():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    opens[-1] = 20.0
    complete = [True] * (len(closes) - 1) + [False]
    volumes = [100.0] * (len(closes) - 1) + [300.0]

    result = D.detect_theme_monthly_attack(
        _monthly_bars(closes, opens=opens, volumes=volumes, complete=complete)
    )

    assert result.matched is False
    assert result.evidence["sample"]["excluded_incomplete_months"] == 1
    assert result.evidence["as_of_month"] == _month_end(len(closes) - 2)[:7]


def test_monthly_reacceleration_matches_prior_high_volume_yin_setup():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    volumes = [100.0] * len(closes)
    opens[-2] = closes[-2] + 1.0
    volumes[-2] = 200.0
    opens[-1] = closes[-1] - 1.0
    volumes[-1] = 300.0

    result = D.detect_monthly_reacceleration(
        _monthly_bars(closes, opens=opens, volumes=volumes)
    )

    assert result.matched is True
    conditions = result.evidence["conditions"]
    assert conditions["prior_setup"]["met"] is True
    assert "high_volume_bearish_month" in conditions["prior_setup"]["setup_kinds"]
    assert conditions["zero_axis_golden_cross"]["met"] is True
    assert conditions["high_volume_bullish_month"]["met"] is True


def test_monthly_reacceleration_matches_prior_ma_convergence_setup():
    closes = [10.0 + i * 0.5 for i in range(25)] + [22.0] * 11 + [20.9, 27.9]
    opens = closes.copy()
    opens[-1] = 22.0
    volumes = [100.0] * (len(closes) - 1) + [200.0]

    result = D.detect_monthly_reacceleration(
        _monthly_bars(closes, opens=opens, volumes=volumes)
    )

    assert result.matched is True
    setup = result.evidence["conditions"]["prior_setup"]
    assert setup["met"] is True
    assert "ma_convergence" in setup["setup_kinds"]


def test_reacceleration_ignores_shape_invalid_prior_bearish_body():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    volumes = [100.0] * len(closes)
    opens[-2] = closes[-2] + 1.0
    volumes[-2] = 200.0
    shape_valid = [True] * len(closes)
    shape_valid[-2] = False
    bars = _monthly_bars(
        closes,
        opens=opens,
        volumes=volumes,
        shape_valid=shape_valid,
    )

    setup = D._prior_setup_evidence(bars, D.compute_monthly_indicators(bars))

    assert setup["high_volume_bearish_months"] == []
    assert bars[-2].month in setup["shape_invalid_months"]


def test_reacceleration_fails_closed_when_latest_bullish_body_is_invalid():
    closes = _zero_axis_cross_closes()
    opens = closes.copy()
    volumes = [100.0] * len(closes)
    opens[-2] = closes[-2] + 1.0
    volumes[-2] = 200.0
    opens[-1] = closes[-1] - 1.0
    volumes[-1] = 300.0
    shape_valid = [True] * (len(closes) - 1) + [False]

    result = D.detect_monthly_reacceleration(
        _monthly_bars(
            closes,
            opens=opens,
            volumes=volumes,
            shape_valid=shape_valid,
        )
    )

    assert result.matched is False
    current = result.evidence["conditions"]["high_volume_bullish_month"]
    assert current["met"] is False
    assert current["price_shape_valid"] is False


def test_reacceleration_thresholds_are_named_and_explainable():
    assert D.REACCEL_SETUP_LOOKBACK_MONTHS == 12
    assert D.PRIOR_HIGH_VOLUME_YIN_RATIO == pytest.approx(1.2)
    assert D.REACCEL_VOLUME_RATIO == pytest.approx(1.2)
    assert D.MA_CONVERGENCE_MAX_SPREAD_RATIO == pytest.approx(0.03)
    assert D.CLOSE_NEAR_HIGH_MAX_RANGE_RATIO == pytest.approx(0.10)


def test_pool_state_active_includes_close_equal_to_ma5():
    result = D.evaluate_pool_state(_monthly_bars([10.0] * 6))

    assert result.state == "active"
    assert result.evidence["conditions"]["close_at_or_above_ma5"]["met"] is True


def test_pool_state_risk_when_completed_month_closes_below_ma5():
    result = D.evaluate_pool_state(_monthly_bars([10.0] * 5 + [5.0]))

    assert result.state == "risk"
    assert result.evidence["conditions"]["close_at_or_above_ma5"]["met"] is False


def test_pool_state_reentry_when_price_stands_back_above_ma5():
    result = D.evaluate_pool_state(_monthly_bars([10.0, 10.0, 10.0, 10.0, 5.0, 10.0]))

    assert result.state == "reentry"
    assert result.evidence["conditions"]["stood_back_above_ma5"]["met"] is True


def test_pool_state_reentry_on_high_volume_reclaim_of_half_bearish_body():
    closes = [8.0, 8.0, 8.0, 8.0, 8.0, 10.5]
    opens = closes.copy()
    opens[-2] = 12.0
    opens[-1] = 9.0
    volumes = [100.0] * 5 + [200.0]

    result = D.evaluate_pool_state(
        _monthly_bars(closes, opens=opens, volumes=volumes)
    )

    reclaim = result.evidence["conditions"]["high_volume_half_body_reclaim"]
    assert result.state == "reentry"
    assert reclaim["met"] is True
    assert reclaim["half_body_price"] == pytest.approx(10.0)


@pytest.mark.parametrize("invalid_index", [-2, -1])
def test_pool_state_half_body_reclaim_fails_closed_for_invalid_shape(
    invalid_index: int,
):
    closes = [8.0, 8.0, 8.0, 8.0, 8.0, 10.5]
    opens = closes.copy()
    opens[-2] = 12.0
    opens[-1] = 9.0
    volumes = [100.0] * 5 + [200.0]
    shape_valid = [True] * len(closes)
    shape_valid[invalid_index] = False

    result = D.evaluate_pool_state(
        _monthly_bars(
            closes,
            opens=opens,
            volumes=volumes,
            shape_valid=shape_valid,
        )
    )

    reclaim = result.evidence["conditions"]["high_volume_half_body_reclaim"]
    assert result.state == "active"
    assert reclaim["met"] is False
    assert reclaim["price_shape_valid"] is False


def test_pool_state_excludes_uncompleted_month_before_evaluation():
    bars = _monthly_bars(
        [10.0] * 5 + [5.0],
        complete=[True, True, True, True, True, False],
    )

    result = D.evaluate_pool_state(bars)

    assert result.state == "active"
    assert result.evidence["sample"]["excluded_incomplete_months"] == 1
    assert result.evidence["as_of_month"] == bars[-2].month
