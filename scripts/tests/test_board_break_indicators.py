"""scripts/tests/test_board_break_indicators.py"""
import math
import pytest
from services.board_break import indicators as I


def _bars(n, close=10.0, low=None, high=None, start_day=1):
    out = []
    for i in range(n):
        out.append({"trade_date": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                    "close": close, "low": low or close * 0.98, "high": high or close * 1.02})
    return out


class TestQfq:
    def test_pre_adjust_normalized_to_T(self):
        bars = [{"trade_date": "20260701", "close": 20.0, "low": 19.0, "high": 21.0},
                {"trade_date": "20260702", "close": 10.5, "low": 10.0, "high": 11.0}]
        factors = [{"trade_date": "20260701", "adj_factor": 1.0},
                   {"trade_date": "20260702", "adj_factor": 2.0}]  # 除权日翻倍
        out = I.apply_qfq(bars, factors)
        assert out[0]["close"] == pytest.approx(10.0)   # 20 * 1.0 / 2.0
        assert out[1]["close"] == pytest.approx(10.5)   # T 日不变

    def test_factor_missing_returns_none(self):
        bars = [{"trade_date": "20260702", "close": 10.0, "low": 9.8, "high": 10.2}]
        assert I.apply_qfq(bars, []) is None


class TestMacd:
    def test_known_series_hand_check(self):
        closes = [float(i) for i in range(1, 131)]  # 单调升 → DIF > 0
        assert I.macd_dif(closes) > 0

    def test_monotonic_down_negative(self):
        closes = [float(200 - i) for i in range(130)]
        assert I.macd_dif(closes) < 0

    def test_insufficient_bars_none(self):
        assert I.macd_dif([10.0] * 119) is None


class TestGain10:
    def test_formula_includes_T(self):
        closes = [10.0] * 10 + [10.0, 13.0]  # close[-11]=10 → +30%
        assert I.gain_10d(closes) == pytest.approx(30.0)

    def test_insufficient_none(self):
        assert I.gain_10d([10.0] * 10) is None


class TestPosition250:
    def test_low_high_percentile(self):
        bars = _bars(250, close=10.0)
        bars[-1] = {**bars[-1], "close": 10.0, "low": 9.8, "high": 10.2}
        bars[0] = {**bars[0], "low": 5.0}
        bars[10] = {**bars[10], "high": 25.0}
        pos = I.position_250d(bars)
        assert pos["state"] == "full"
        assert pos["value"] == pytest.approx((10.0 - 5.0) / (25.0 - 5.0))

    def test_flat_range_missing(self):
        bars = [{"trade_date": "20260101", "close": 10.0, "low": 10.0, "high": 10.0}] * 250
        assert I.position_250d(bars)["state"] == "missing"

    @pytest.mark.parametrize("n,state", [(119, "missing"), (120, "degraded"), (249, "degraded"), (250, "full")])
    def test_sample_states(self, n, state):
        assert I.position_250d(_bars(n))["state"] == state
