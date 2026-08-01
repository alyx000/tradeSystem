"""月线指标观察纯函数测试：无网络、DB、文件或推送副作用。"""
from __future__ import annotations

import calendar
import datetime as dt

import pytest

from services.monthly_pattern.indicator_watch import (
    MACD_MIN_OBSERVATIONS,
    UNRESOLVED_RULES,
    aggregate_weekly_bars_as_of,
    daily_weekly_macd_states,
    detect_monthly_seed,
    evaluate_daily_monitor,
    macd_state_series,
)
from services.monthly_pattern.models import MonthlyBar


def _month_end(index: int) -> str:
    year = 2024 + index // 12
    month = index % 12 + 1
    return dt.date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _seed_bars(
    *,
    latest_bearish: bool = True,
    history: int = 20,
) -> list[MonthlyBar]:
    bars: list[MonthlyBar] = []
    closes = [10.0 + index for index in range(history - 1)]
    closes.append(closes[-1] + 0.5)
    for index, close in enumerate(closes):
        if index == len(closes) - 1:
            open_ = close + 0.5 if latest_bearish else close - 0.5
            low = close - 2.5
        else:
            open_ = close - 0.5
            low = open_ - 0.4
        end_date = _month_end(index)
        bars.append(
            MonthlyBar(
                month=end_date[:7],
                end_date=end_date,
                open=open_,
                high=max(open_, close) + 0.5,
                low=low,
                close=close,
                volume=100.0,
                amount=close * 100.0,
                is_complete=True,
                trading_days=20,
                price_shape_valid=True,
            )
        )
    return bars


def _business_dates(start: dt.date, count: int) -> list[str]:
    result: list[str] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += dt.timedelta(days=1)
    return result


def _daily_bars(
    closes: list[float],
    *,
    start: dt.date = dt.date(2024, 1, 2),
    last_volume: float = 300.0,
) -> list[dict]:
    dates = _business_dates(start, len(closes))
    bars = []
    for index, (date, close) in enumerate(zip(dates, closes)):
        open_ = close - 0.2
        bars.append(
            {
                "trade_date": date,
                "open": open_,
                "high": close + 0.4,
                "low": open_ - 0.4,
                "close": close,
                "volume": last_volume if index == len(closes) - 1 else 100.0,
            }
        )
    return bars


def _down_then_up(down: int, up: int) -> list[float]:
    falling = [200.0 - index * 0.3 for index in range(down)]
    floor = falling[-1]
    rising = [floor + (index + 1) * 0.8 for index in range(up)]
    return falling + rising


def _month_level_bars(levels: dict[str, float]) -> list[dict]:
    bars: list[dict] = []
    for month, close in levels.items():
        year, month_number = (int(part) for part in month.split("-"))
        last_day = calendar.monthrange(year, month_number)[1]
        for day in range(1, last_day + 1):
            current = dt.date(year, month_number, day)
            if current.weekday() >= 5:
                continue
            bars.append(
                {
                    "trade_date": current.isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 100.0,
                }
            )
    return bars


def _last_bar_in_month(bars: list[dict], month: str) -> str:
    return max(
        bar["trade_date"]
        for bar in bars
        if bar["trade_date"].startswith(month)
    )


def test_monthly_seed_matches_and_bearish_pullback_is_only_preferred_evidence():
    bearish = detect_monthly_seed(_seed_bars(latest_bearish=True))
    bullish = detect_monthly_seed(_seed_bars(latest_bearish=False))

    assert bearish.status == "matched"
    assert bearish.matched is True
    assert bearish.evidence["preferred_pullback"] is True
    assert bearish.evidence["conditions"]["preferred_pullback"]["hard_gate"] is False
    assert bearish.evidence["positive_month_streak"] >= 5
    assert bearish.evidence["open"] == pytest.approx(29.0)
    assert bearish.evidence["low"] <= bearish.evidence["ma5"]
    assert bearish.evidence["close"] == pytest.approx(28.5)

    assert bullish.status == "matched"
    assert bullish.matched is True
    assert bullish.evidence["preferred_pullback"] is False
    assert bullish.evidence["bullish_streak_before_pullback"] >= 5


def test_monthly_seed_ignores_incomplete_future_month():
    bars = _seed_bars()
    future_end = _month_end(len(bars))
    bars.append(
        MonthlyBar(
            month=future_end[:7],
            end_date=future_end,
            open=1.0,
            high=1.2,
            low=0.8,
            close=0.9,
            volume=1.0,
            amount=1.0,
            is_complete=False,
            trading_days=1,
            price_shape_valid=False,
        )
    )

    result = detect_monthly_seed(bars)

    assert result.status == "matched"
    assert result.evidence["seed_month_end"] == bars[-2].end_date
    assert result.evidence["excluded_incomplete_months"] == 1


def test_monthly_seed_separates_insufficient_blocked_and_not_matched():
    insufficient = detect_monthly_seed(_seed_bars()[:19])
    assert insufficient.status == "insufficient_history"
    assert insufficient.evidence["reason"] == "monthly_ma_history_insufficient"

    gap = _seed_bars()
    del gap[-8]
    blocked_gap = detect_monthly_seed(gap)
    assert blocked_gap.status == "blocked"
    assert "non_consecutive_completed_months" in blocked_gap.evidence["reason"]

    invalid_shape = _seed_bars()
    invalid_shape[-4] = MonthlyBar(
        **{**invalid_shape[-4].to_dict(), "price_shape_valid": False}
    )
    blocked_shape = detect_monthly_seed(invalid_shape)
    assert blocked_shape.status == "blocked"
    assert "price_shape_invalid" in blocked_shape.evidence["reason"]

    short_streak = _seed_bars()
    short_streak[-3] = MonthlyBar(
        **{
            **short_streak[-3].to_dict(),
            "open": short_streak[-3].close + 0.2,
            "high": short_streak[-3].close + 0.5,
        }
    )
    not_matched = detect_monthly_seed(short_streak)
    assert not_matched.status == "not_matched"
    assert "prior_consecutive_bullish_months" in not_matched.evidence["failed_conditions"]


@pytest.mark.parametrize(
    "definitive_false",
    [
        "prior_consecutive_bullish_months",
        "monthly_ma_alignment",
        "ma5_support",
    ],
)
def test_monthly_seed_known_false_short_circuits_unknown_shape(
    definitive_false: str,
) -> None:
    """AND 三态：已有硬门为 false 时，另一硬门 unknown 不得把结果抬成 blocked。"""
    bars = _seed_bars()

    if definitive_false == "prior_consecutive_bullish_months":
        bearish = bars[-2]
        bars[-2] = MonthlyBar(
            **{
                **bearish.to_dict(),
                "open": bearish.close + 0.2,
                "high": bearish.close + 0.5,
            }
        )
        unknown_index = -6
    elif definitive_false == "monthly_ma_alignment":
        for index, bar in enumerate(bars):
            bars[index] = MonthlyBar(
                **{
                    **bar.to_dict(),
                    "open": 20.0,
                    "high": 20.5,
                    "low": 19.5,
                    "close": 20.0,
                }
            )
        unknown_index = -4
    else:
        latest = bars[-1]
        bars[-1] = MonthlyBar(
            **{
                **latest.to_dict(),
                "open": 29.0,
                "high": 29.5,
                "low": 28.0,
                "close": 28.5,
            }
        )
        unknown_index = -4

    bars[unknown_index] = MonthlyBar(
        **{**bars[unknown_index].to_dict(), "price_shape_valid": False}
    )

    result = detect_monthly_seed(bars)

    assert result.status == "not_matched"
    assert result.matched is False
    assert definitive_false in result.evidence["failed_conditions"]


def test_monthly_seed_unknown_without_known_false_remains_blocked() -> None:
    """AND 三态：没有已知 false、但存在 unknown 时必须 fail-closed，且绝不 matched。"""
    bars = _seed_bars()
    bars[-4] = MonthlyBar(
        **{**bars[-4].to_dict(), "price_shape_valid": False}
    )

    result = detect_monthly_seed(bars)

    assert result.status == "blocked"
    assert result.matched is False
    assert result.evidence["reason"] == f"price_shape_invalid: {bars[-4].month}"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("open", float("nan"), "must be finite"),
        ("high", -1.0, "must be positive"),
        ("low", None, "must be finite"),
    ],
)
def test_monthly_seed_invalid_shape_still_blocks_structurally_dirty_prices(
    field: str,
    value,
    reason: str,
) -> None:
    """Unknown 只代表形态不可认证，不能掩盖字段缺失、非有限或非正的源损坏。"""
    bars = _seed_bars()
    dirty = bars[-4].to_dict()
    dirty["price_shape_valid"] = False
    dirty[field] = value
    bars[-4] = dirty

    # 同时制造一个已知 false，证明结构损坏仍优先于三态短路。
    latest = bars[-1]
    bars[-1] = MonthlyBar(
        **{
            **latest.to_dict(),
            "open": 29.0,
            "high": 29.5,
            "low": 28.0,
            "close": 28.5,
        }
    )

    result = detect_monthly_seed(bars)

    assert result.status == "blocked"
    assert result.matched is False
    assert reason in result.evidence["reason"]


def test_monthly_seed_known_false_does_not_hide_declared_valid_invalid_ohlc() -> None:
    """声明形态有效时，OHLC 结构损坏必须先于任一已知硬门失败返回 blocked。"""
    bars = _seed_bars()
    dirty = bars[-4]
    bars[-4] = MonthlyBar(
        **{
            **dirty.to_dict(),
            "open": dirty.close + 1.0,
            "high": dirty.close,
        }
    )
    latest = bars[-1]
    bars[-1] = MonthlyBar(
        **{
            **latest.to_dict(),
            "open": 29.0,
            "high": 29.5,
            "low": 28.0,
            "close": 28.5,
        }
    )

    result = detect_monthly_seed(bars)

    assert result.status == "blocked"
    assert result.matched is False
    assert "invalid OHLC" in result.evidence["reason"]


def test_monthly_seed_ignores_old_gap_and_old_invalid_shape_outside_20_month_suffix():
    bars = _seed_bars(history=30)
    bars[2] = MonthlyBar(
        **{**bars[2].to_dict(), "price_shape_valid": False}
    )
    del bars[5]

    result = detect_monthly_seed(bars)

    assert result.status == "matched"
    assert result.evidence["required_suffix_months"] == 20


def test_monthly_seed_requires_ma_alignment_and_ma5_touch_support():
    unaligned = _seed_bars()
    for index, bar in enumerate(unaligned):
        unaligned[index] = MonthlyBar(
            **{
                **bar.to_dict(),
                "open": 20.0,
                "high": 20.5,
                "low": 19.5,
                "close": 20.0,
            }
        )
    unaligned_result = detect_monthly_seed(unaligned)
    assert unaligned_result.status == "not_matched"
    assert "monthly_ma_alignment" in unaligned_result.evidence["failed_conditions"]

    no_touch = _seed_bars()
    latest = no_touch[-1]
    no_touch[-1] = MonthlyBar(
        **{
            **latest.to_dict(),
            "open": 29.0,
            "high": 29.5,
            "low": 28.0,
            "close": 28.5,
        }
    )
    no_touch_result = detect_monthly_seed(no_touch)
    assert no_touch_result.status == "not_matched"
    assert "ma5_support" in no_touch_result.evidence["failed_conditions"]


def test_macd_series_separates_above_zero_from_bullish_on_zero():
    closes = [100.0 + index for index in range(60)]
    closes += [160.0 - index * 0.2 for index in range(12)]

    states = macd_state_series(closes)

    assert states[MACD_MIN_OBSERVATIONS - 2]["ready"] is False
    assert states[MACD_MIN_OBSERVATIONS - 2]["above_zero"] is None
    assert states[MACD_MIN_OBSERVATIONS - 1]["ready"] is True
    assert any(
        point["above_zero"] is True and point["bullish_on_zero"] is False
        for point in states
    )
    assert all(
        point["above_zero"] is True
        for point in states
        if point["bullish_on_zero"] is True
    )


def test_weekly_as_of_aggregation_never_reads_after_target():
    bars = _daily_bars([100.0 + index * 0.1 for index in range(50)])
    target = bars[39]["trade_date"]
    future = [
        {
            **bar,
            "open": 999.0,
            "high": 1001.0,
            "low": 998.0,
            "close": 1000.0,
        }
        for bar in bars[40:]
    ]
    with_future = bars[:40] + future

    expected = aggregate_weekly_bars_as_of(bars[:40], target)
    actual = aggregate_weekly_bars_as_of(with_future, target)

    assert actual == expected
    assert actual[-1]["trade_date"] == target
    assert actual[-1]["close"] != 1000.0

    states = daily_weekly_macd_states(with_future, target)
    assert states["excluded_after_target_count"] == 10
    assert states["daily"][-1]["date"] == target
    assert states["weekly"][-1]["date"] == target


def test_daily_monitor_reports_daily_reactivation_when_weekly_is_not_ready():
    closes = _down_then_up(70, 30)
    bars = _daily_bars(closes)
    seed = bars[69]["trade_date"]
    target = bars[-1]["trade_date"]

    result = evaluate_daily_monitor(bars, target, seed)

    assert result["status"] == "daily_reactivated"
    assert result["daily"]["first_above_zero_flip_after_seed"] > seed
    assert result["daily"]["current"]["above_zero"] is True
    assert result["weekly"]["current"]["ready"] is False
    assert result["volume_auxiliary"]["classification"] == "existing_system_auxiliary"
    assert result["volume_auxiliary"]["hard_gate"] is False
    assert result["volume_auxiliary"]["confirmed"] is True
    assert set(result["volume_auxiliary"]["prior_volume_mas"]) == {"5", "13"}
    assert result["stage"] == result["status"]
    assert result["evidence"]["daily_macd"] == result["daily"]["current"]
    assert result["evidence"]["reentry_date"] == (
        result["daily"]["first_above_zero_flip_after_seed"]
    )
    assert UNRESOLVED_RULES


def test_daily_monitor_reports_resonance_without_using_volume_as_a_gate():
    closes = _down_then_up(180, 80)
    bars = _daily_bars(closes, last_volume=50.0)
    seed = bars[179]["trade_date"]
    target = bars[-1]["trade_date"]

    result = evaluate_daily_monitor(bars, target, seed)

    assert result["status"] == "resonance_observed"
    assert result["daily"]["current"]["bullish_on_zero"] is True
    assert result["weekly"]["current"]["bullish_on_zero"] is True
    assert result["volume_auxiliary"]["confirmed"] is False


@pytest.mark.parametrize(
    ("current_close", "support_held"),
    [
        (11.0, True),
        (10.0, True),
        (9.0, False),
    ],
)
def test_dynamic_monthly_ma5_uses_inclusive_current_close_hard_gate(
    current_close: float,
    support_held: bool,
) -> None:
    bars = _month_level_bars(
        {
            "2026-01": 10.0,
            "2026-02": 10.0,
            "2026-03": 10.0,
            "2026-04": 10.0,
            "2026-05": current_close,
        }
    )
    target = _last_bar_in_month(bars, "2026-05")
    seed = _last_bar_in_month(bars, "2026-04")

    result = evaluate_daily_monitor(bars, target, seed)
    dynamic = result["evidence"]["dynamic_monthly_ma5"]

    assert dynamic["status"] == "complete"
    assert dynamic["operator"] == "current_close >= dynamic_month_ma5"
    assert dynamic["months"] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ]
    assert dynamic["month_closes"] == [10.0, 10.0, 10.0, 10.0, current_close]
    assert dynamic["month_last_dates"][-1] == target
    assert dynamic["ma5"] == pytest.approx((40.0 + current_close) / 5.0)
    assert dynamic["support_held"] is support_held
    assert dynamic["used_for_monthly_seed"] is False
    if support_held:
        assert result["reason"] != "current_close_below_dynamic_month_ma5"
    else:
        assert result["status"] == "monthly_seeded"
        assert result["reason"] == "current_close_below_dynamic_month_ma5"


def test_dynamic_monthly_ma5_blocks_internal_calendar_month_gap() -> None:
    bars = _month_level_bars(
        {
            "2026-01": 10.0,
            "2026-02": 10.0,
            "2026-04": 10.0,
            "2026-05": 10.0,
            "2026-06": 10.0,
        }
    )
    target = _last_bar_in_month(bars, "2026-06")
    seed = _last_bar_in_month(bars, "2026-05")

    result = evaluate_daily_monitor(bars, target, seed)

    assert result["status"] == "blocked"
    assert result["reason"] == "dynamic_month_ma5_month_gap: 2026-02->2026-04"
    assert result["evidence"]["dynamic_monthly_ma5"]["support_held"] is None


def test_dynamic_monthly_ma5_reports_insufficient_five_month_history() -> None:
    bars = _month_level_bars(
        {
            "2026-01": 10.0,
            "2026-02": 10.0,
            "2026-03": 10.0,
            "2026-04": 10.0,
        }
    )
    target = _last_bar_in_month(bars, "2026-04")
    seed = _last_bar_in_month(bars, "2026-03")

    result = evaluate_daily_monitor(bars, target, seed)

    assert result["status"] == "insufficient_history"
    assert result["reason"] == "dynamic_month_ma5_history_insufficient"
    assert result["evidence"]["dynamic_monthly_ma5"]["available_months"] == 4


def test_daily_monitor_stays_monthly_seeded_without_post_seed_flip():
    closes = [200.0 - index * 0.25 for index in range(220)]
    bars = _daily_bars(closes)
    seed = bars[179]["trade_date"]

    result = evaluate_daily_monitor(bars, bars[-1]["trade_date"], seed)

    assert result["status"] == "monthly_seeded"
    assert result["daily"]["first_above_zero_flip_after_seed"] is None
    assert result["daily"]["reactivated"] is False


def test_daily_monitor_excludes_future_rows_without_changing_result():
    closes = _down_then_up(70, 30)
    bars = _daily_bars(closes)
    seed = bars[69]["trade_date"]
    target = bars[-1]["trade_date"]
    future_date = _business_dates(
        dt.date.fromisoformat(target) + dt.timedelta(days=1), 1
    )[0]
    future = {
        "trade_date": future_date,
        "open": 999.0,
        "high": 1001.0,
        "low": 998.0,
        "close": 1000.0,
        "volume": 999999.0,
    }

    baseline = evaluate_daily_monitor(bars, target, seed)
    with_future = evaluate_daily_monitor(bars + [future], target, seed)

    assert with_future["status"] == baseline["status"]
    assert with_future["daily"] == baseline["daily"]
    assert with_future["weekly"] == baseline["weekly"]
    assert with_future["evidence"]["dynamic_monthly_ma5"] == (
        baseline["evidence"]["dynamic_monthly_ma5"]
    )
    assert with_future["data_quality"]["excluded_after_target_count"] == 1


def test_daily_monitor_ignores_dirty_or_duplicate_rows_after_target():
    bars = _daily_bars(_down_then_up(70, 30))
    seed = bars[69]["trade_date"]
    target = bars[-1]["trade_date"]
    future_date = _business_dates(
        dt.date.fromisoformat(target) + dt.timedelta(days=1), 1
    )[0]
    dirty_future = {
        "trade_date": future_date,
        "open": float("nan"),
        "high": float("nan"),
        "low": float("nan"),
        "close": float("nan"),
        "volume": float("nan"),
    }

    baseline = evaluate_daily_monitor(bars, target, seed)
    result = evaluate_daily_monitor(
        bars + [dirty_future, {**dirty_future}],
        target,
        seed,
    )

    assert result["status"] == baseline["status"]
    assert result["daily"] == baseline["daily"]
    assert result["weekly"] == baseline["weekly"]
    assert result["data_quality"]["excluded_after_target_count"] == 2


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda bars: bars + [{**bars[-1]}],
            "duplicate trade_date",
        ),
        (
            lambda bars: [
                *bars[:-1],
                {**bars[-1], "high": bars[-1]["close"] - 1.0},
            ],
            "invalid OHLC",
        ),
        (
            lambda bars: [
                *bars[:-1],
                {**bars[-1], "volume": float("nan")},
            ],
            "must be finite",
        ),
    ],
)
def test_daily_monitor_blocks_duplicate_or_dirty_ohlcv(mutator, message):
    bars = _daily_bars(_down_then_up(70, 30))
    result = evaluate_daily_monitor(
        mutator(bars),
        bars[-1]["trade_date"],
        bars[69]["trade_date"],
    )

    assert result["status"] == "blocked"
    assert message in result["reason"]


def test_daily_monitor_blocks_missing_target_and_future_seed():
    bars = _daily_bars(_down_then_up(70, 30))
    missing_target = _business_dates(
        dt.date.fromisoformat(bars[-1]["trade_date"]) + dt.timedelta(days=1), 1
    )[0]
    stale = evaluate_daily_monitor(bars, missing_target, bars[69]["trade_date"])
    assert stale["status"] == "blocked"
    assert "target_bar_missing" in stale["reason"]

    future_seed = evaluate_daily_monitor(
        bars,
        bars[-1]["trade_date"],
        missing_target,
    )
    assert future_seed["status"] == "blocked"
    assert "seed_month_end must not be after target_date" in future_seed["reason"]


def test_daily_monitor_distinguishes_insufficient_pre_seed_history():
    bars = _daily_bars(_down_then_up(20, 20))
    result = evaluate_daily_monitor(
        bars,
        bars[-1]["trade_date"],
        bars[10]["trade_date"],
    )

    assert result["status"] == "insufficient_history"
    assert result["reason"] == "pre_seed_macd_history_insufficient"
