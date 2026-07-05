from __future__ import annotations

import pytest

from services.ma_breakout import detectors


def _bars(closes, amounts):
    return [
        {"trade_date": f"2026-06-{i + 1:02d}", "close": close, "amount": amount}
        for i, (close, amount) in enumerate(zip(closes, amounts))
    ]


def test_ma4_turning_up_requires_today_up_after_prior_flat_or_down():
    bars = _bars(
        closes=[10.0, 10.0, 10.0, 10.0, 9.0, 9.0, 11.0],
        amounts=[100.0] * 7,
    )

    matched, detail = detectors.is_ma_turning_up(bars, period=4)

    assert matched is True
    assert detail["ma_today"] == pytest.approx(9.75)
    assert detail["ma_prev"] == pytest.approx(9.5)
    assert detail["ma_prev2"] == pytest.approx(9.75)
    assert detail["ma_prev3"] == pytest.approx(10.0)
    assert detail["insufficient_history"] is False


def test_ma4_turning_up_rejects_already_rising_ma():
    bars = _bars(
        closes=[8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0],
        amounts=[100.0] * 7,
    )

    matched, detail = detectors.is_ma_turning_up(bars, period=4)

    assert matched is False
    assert detail["ma_today"] > detail["ma_prev"] > detail["ma_prev2"]


def test_ma4_turning_up_requires_prior_local_trough_not_one_day_zigzag():
    bars = _bars(
        closes=[257.11, 264.18, 258.0, 261.0, 271.77, 259.7, 281.41],
        amounts=[100.0] * 7,
    )

    matched, detail = detectors.is_ma_turning_up(bars, period=4)

    assert matched is False
    assert detail["ma_today"] > detail["ma_prev"]
    assert detail["ma_prev"] <= detail["ma_prev2"]
    assert detail["ma_prev"] > detail["ma_prev3"]


def test_ma4_turning_up_requires_prior_ma_downtrend_not_one_day_pullback():
    bars = _bars(
        closes=[43.4, 45.6, 47.79, 43.67, 43.47, 47.02, 46.05, 50.04],
        amounts=[100.0] * 8,
    )

    matched, detail = detectors.is_ma_turning_up(bars, period=4)

    assert matched is False
    assert detail["ma_today"] > detail["ma_prev"]
    assert detail["ma_prev"] < detail["ma_prev2"]
    assert detail["ma_prev2"] > detail["ma_prev3"]


def test_ma4_turning_up_rejects_insufficient_history():
    matched, detail = detectors.is_ma_turning_up(_bars([10.0] * 6, [100.0] * 6), period=4)

    assert matched is False
    assert detail["insufficient_history"] is True


def test_amount_breaks_both_average_lines():
    bars = _bars(
        closes=[10.0] * 10,
        amounts=[100.0, 105.0, 98.0, 102.0, 99.0, 101.0, 103.0, 104.0, 106.0, 180.0],
    )

    matched, detail = detectors.is_amount_breakout(bars, windows=(5, 10))

    assert matched is True
    assert detail["today_amount"] == pytest.approx(180.0)
    assert detail["amount_ma5"] == pytest.approx((101.0 + 103.0 + 104.0 + 106.0 + 180.0) / 5)
    assert detail["amount_ma10"] == pytest.approx(sum([100.0, 105.0, 98.0, 102.0, 99.0, 101.0, 103.0, 104.0, 106.0, 180.0]) / 10)
    assert detail["insufficient_history"] is False


def test_amount_breakout_requires_breaking_every_configured_window():
    bars = _bars(
        closes=[10.0] * 10,
        amounts=[100.0, 300.0, 300.0, 300.0, 300.0, 80.0, 80.0, 80.0, 80.0, 120.0],
    )

    matched, detail = detectors.is_amount_breakout(bars, windows=(5, 10))

    assert matched is False
    assert detail["today_amount"] > detail["amount_ma5"]
    assert detail["today_amount"] < detail["amount_ma10"]


def test_match_pattern_requires_fresh_target_date():
    bars = _bars(
        closes=[10.0, 10.0, 10.0, 10.0, 9.0, 11.0, 11.2, 11.3, 11.4, 11.5],
        amounts=[100.0] * 9 + [180.0],
    )
    bars[-1]["trade_date"] = "2026-06-09"

    matched, detail = detectors.match_pattern(bars, target_date="2026-06-10", windows=(5, 10))

    assert matched is False
    assert detail["stale_last_bar"] is True
