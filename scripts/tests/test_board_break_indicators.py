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

    def test_mid_window_zero_factor_returns_none(self):
        # 窗口中部一日 adj_factor=0（脏值）：若不挡住会把该日 OHLC 整体乘 0 清零，
        # 污染 250 日区间分位（range_low 拉到 0）与减持位置极性（恒判低位、方向打反）
        bars = [{"trade_date": "20260701", "close": 20.0, "low": 19.0, "high": 21.0},
                {"trade_date": "20260702", "close": 15.0, "low": 14.0, "high": 16.0},
                {"trade_date": "20260703", "close": 10.5, "low": 10.0, "high": 11.0}]
        factors = [{"trade_date": "20260701", "adj_factor": 1.0},
                   {"trade_date": "20260702", "adj_factor": 0.0},  # 历史日因子污染
                   {"trade_date": "20260703", "adj_factor": 2.0}]
        assert I.apply_qfq(bars, factors) is None

    def test_mid_window_non_finite_factor_returns_none(self):
        bars = [{"trade_date": "20260701", "close": 20.0, "low": 19.0, "high": 21.0},
                {"trade_date": "20260702", "close": 15.0, "low": 14.0, "high": 16.0}]
        factors = [{"trade_date": "20260701", "adj_factor": float("nan")},
                   {"trade_date": "20260702", "adj_factor": 2.0}]
        assert I.apply_qfq(bars, factors) is None


class TestQfqDirtyBars:
    """脏历史 OHLC 守卫（门2 S2 R1）：任一价格缺失/非有限 → 整体 None，防 _ema TypeError / 伪分位。"""

    def _factors(self, bars):
        return [{"trade_date": b["trade_date"], "adj_factor": 1.0} for b in bars]

    @pytest.mark.parametrize("key,bad", [("close", None), ("low", None), ("high", None),
                                         ("close", float("nan")), ("close", "abc")])
    def test_dirty_historical_price_returns_none(self, key, bad):
        bars = [{"trade_date": f"2026-01-{i+1:02d}", "close": 10.0, "low": 9.8, "high": 10.2}
                for i in range(20)]
        bars[5] = {**bars[5], key: bad}
        assert I.apply_qfq(bars, self._factors(bars)) is None

    def test_overflow_ratio_returns_none(self):
        """极端因子比值/复权后溢出 → 整体 None（门2 S2 R2）。"""
        bars = [{"trade_date": f"2026-01-{i+1:02d}", "close": 1e308, "low": 1e308, "high": 1e308}
                for i in range(3)]
        factors = [{"trade_date": b["trade_date"], "adj_factor": 1e300} for b in bars]
        factors[-1]["adj_factor"] = 1e-300  # T 日极小正因子 → ratio 溢出
        assert I.apply_qfq(bars, factors) is None


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


class TestMacdHandCalc:
    def test_matches_independent_ema_recurrence(self):
        """用与 indicators._ema 写法不同的第二套 EMA 递推手算对照，验证 DIF 数值口径一致
        （等价于 pandas `ewm(adjust=False)` 语义，但不引入 pandas 依赖）。
        """
        closes = [float(100 + i) for i in range(130)]  # >=120 根等差序列，MACD warm-up 足够

        def _ema_independent(values: list[float], n: int) -> float:
            k = 2.0 / (n + 1)
            ema = values[0]
            for v in values[1:]:
                ema += (v - ema) * k  # 写法与 indicators._ema 的 ema*(1-k)+v*k 不同，独立验证
            return ema

        expected = _ema_independent(closes, 12) - _ema_independent(closes, 26)
        assert I.macd_dif(closes) == pytest.approx(expected, rel=1e-9)
