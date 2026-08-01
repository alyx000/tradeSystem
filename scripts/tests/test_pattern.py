"""形态篇纯函数单测（utils/pattern.py）。

出处：teacher_notes#444 鞠磊《形态篇（第一节技术课程）》，认知 cog_3b32e660。
三个函数各自独立可证伪，样本不足一律 None 不硬算（沿用 ma_position / position_250d 契约）。
"""
from __future__ import annotations

import pytest

from utils import pattern as P


# ──────────────────────────────────────────────────────────
# ma_alignment：5/10/20/30/55 多头排列
# ──────────────────────────────────────────────────────────
class TestMaAlignment:
    def test_perfect_bullish_alignment(self):
        """单调上升序列 → 短均线全部高于长均线，aligned=True。"""
        closes = [10.0 + i * 0.1 for i in range(60)]
        r = P.ma_alignment(closes)
        assert r["state"] == "ok"
        assert r["aligned"] is True
        assert r["broken_at"] is None
        assert r["values"]["ma5"] > r["values"]["ma55"]

    def test_downtrend_not_aligned(self):
        """单调下跌 → 短均线低于长均线，aligned=False 且断点在最短的一对。"""
        closes = [50.0 - i * 0.1 for i in range(60)]
        r = P.ma_alignment(closes)
        assert r["state"] == "ok"
        assert r["aligned"] is False
        assert r["broken_at"] == "ma5>ma10"

    def test_broken_at_reports_first_failing_pair(self):
        """短端排列成立但长端断裂时，broken_at 必须指向真正断裂的那一对。

        构造：前 30 根横盘在高位 30.0、后 30 根从 20.0 缓慢上行 → 短端 ma5>ma10>ma20>ma30
        全部成立，但 ma55 仍被前段高位抬到 26.1（高于 ma30 的 22.9）→ 断点落在 ma30>ma55。
        """
        closes = [30.0] * 30 + [20.0 + i * 0.2 for i in range(30)]
        r = P.ma_alignment(closes)
        assert r["aligned"] is False
        assert r["broken_at"] == "ma30>ma55"
        assert r["values"]["ma5"] > r["values"]["ma30"]  # 短端确实成立，断的是长端

    def test_flat_series_ties_are_not_aligned(self):
        """全平盘时各均线恰相等——「排列」要求严格递减，恰等于不算（与 ma_position「站上」同口径）。"""
        r = P.ma_alignment([12.0] * 60)
        assert r["aligned"] is False
        assert r["broken_at"] == "ma5>ma10"

    def test_insufficient_history_returns_none(self):
        """不足最长窗口 → aligned=None，不得退化成 False。"""
        r = P.ma_alignment([10.0 + i * 0.1 for i in range(40)])
        assert r["state"] == "insufficient"
        assert r["aligned"] is None
        assert r["broken_at"] is None
        assert r["values"]["ma5"] is not None      # 短窗口仍给值供展示
        assert r["values"]["ma55"] is None

    def test_none_values_in_window_are_insufficient(self):
        closes = [10.0 + i * 0.1 for i in range(60)]
        closes[-3] = None
        r = P.ma_alignment(closes)
        assert r["state"] == "insufficient"
        assert r["aligned"] is None

    def test_custom_windows(self):
        closes = [10.0 + i * 0.1 for i in range(30)]
        r = P.ma_alignment(closes, windows=(5, 10, 20))
        assert r["aligned"] is True
        assert set(r["values"]) == {"ma5", "ma10", "ma20"}

    def test_empty_input(self):
        r = P.ma_alignment([])
        assert r["state"] == "insufficient"
        assert r["aligned"] is None


# ──────────────────────────────────────────────────────────
# macd_state：DIF / DEA / 零轴 / 金叉
# ──────────────────────────────────────────────────────────
def _rising(n: int, start: float = 10.0, step: float = 0.05) -> list[float]:
    return [start + i * step for i in range(n)]


class TestMacdState:
    def test_zero_axis_golden_cross(self):
        """长期上涨把 DIF/DEA 推到零上 → 两根浅回调压 DIF 下穿 → 两根反弹重新上穿。"""
        base = _rising(130)
        closes = base + [base[-1] - (i + 1) * 0.25 for i in range(2)]
        closes = closes + [closes[-1] + (i + 1) * 1.0 for i in range(2)]
        r = P.macd_state(closes)
        assert r["state"] == "ok"
        assert r["dif"] > 0 and r["dea"] > 0
        assert r["above_zero"] is True
        assert r["golden_cross"] is True
        assert r["zero_axis_bullish"] is True

    def test_below_zero_golden_cross_is_not_bullish(self):
        """零下金叉：课程明确「零下金叉也会涨但纠结、难成主升浪」→ 不得算达标。"""
        closes = [30.0 - i * 0.12 for i in range(130)] + [14.6]
        r = P.macd_state(closes)
        assert r["state"] == "ok"
        assert r["dif"] < 0 and r["dea"] < 0
        assert r["above_zero"] is False
        assert r["golden_cross"] is True
        assert r["zero_axis_bullish"] is False

    def test_dif_positive_but_dea_still_below_zero_is_not_above_zero(self):
        """DIF 刚翻正、DEA 仍在零下的纠结段不算零上——这是 above_zero 要求两者同时为正的理由。

        只看 DIF 会把这段判成零上，与课程「零下金叉难成主升浪」的本意相悖。
        """
        closes = [30.0 - i * 0.12 for i in range(130)] + [14.6 + i * 0.5 for i in range(8)]
        r = P.macd_state(closes)
        assert r["dif"] > 0
        assert r["dea"] < 0
        assert r["above_zero"] is False
        assert r["zero_axis_bullish"] is False

    def test_zero_axis_running_without_cross_still_bullish(self):
        """课程原话「不管是金叉还是运行，都要在零轴上方」——零上运行（非当日金叉）同样达标。"""
        r = P.macd_state(_rising(140))
        assert r["above_zero"] is True
        assert r["golden_cross"] is False
        assert r["zero_axis_bullish"] is True

    def test_dead_cross_above_zero_not_bullish(self):
        """零上但 DIF 已下穿 DEA → 动能转弱，不算达标。"""
        closes = _rising(130) + [16.5 - i * 0.25 for i in range(6)]
        r = P.macd_state(closes)
        assert r["above_zero"] is True
        assert r["zero_axis_bullish"] is False

    def test_insufficient_history_returns_none(self):
        r = P.macd_state(_rising(80))
        assert r["state"] == "insufficient"
        assert r["dif"] is None and r["dea"] is None
        assert r["above_zero"] is None
        assert r["golden_cross"] is None
        assert r["zero_axis_bullish"] is None

    def test_none_in_series_is_insufficient(self):
        closes = _rising(130)
        closes[50] = None
        r = P.macd_state(closes)
        assert r["state"] == "insufficient"
        assert r["dif"] is None

    def test_dif_matches_board_break_implementation(self):
        """回归保护：board_break.macd_dif 改为本模块薄封装后，DIF 数值必须与历史口径一致。

        board_break 原实现 = EMA12 - EMA26，seed=首根 close（pandas adjust=False 语义）。
        """
        from services.board_break.indicators import macd_dif

        closes = _rising(150, start=8.0, step=0.07)
        assert macd_dif(closes) == pytest.approx(P.macd_state(closes)["dif"])

    def test_board_break_min_bars_contract_unchanged(self):
        """board_break 的 <120 根标缺失契约不能因下沉而改变。"""
        from services.board_break.indicators import macd_dif

        assert macd_dif(_rising(119)) is None
        assert macd_dif(_rising(120)) is not None


# ──────────────────────────────────────────────────────────
# volume_ma_rhythm：5/13 均量线 + 阳放阴缩
# ──────────────────────────────────────────────────────────
def _bar(o: float, c: float, v: float, date: str = "20260701") -> dict:
    return {"open": o, "close": c, "vol": v, "trade_date": date}


def _flat_bars(n: int, vol: float = 1000.0) -> list[dict]:
    """温和上行的底噪 bar，用于把均量线预热到已知水平。"""
    return [_bar(10.0 + i * 0.01, 10.0 + i * 0.01, vol) for i in range(n)]


class TestVolumeMaRhythm:
    def test_yang_above_and_yin_shrink(self):
        """标准阳放阴缩：阳线量站上 5/13 均量线，阴线量落回均量线下。"""
        bars = _flat_bars(20)
        bars += [
            _bar(10.0, 10.5, 3000.0),   # 放量阳
            _bar(10.5, 10.2, 500.0),    # 缩量阴
            _bar(10.2, 10.8, 3200.0),   # 放量阳
            _bar(10.8, 10.6, 600.0),    # 缩量阴
        ]
        r = P.volume_ma_rhythm(bars, lookback=4)
        assert r["state"] == "ok"
        assert r["yang_total"] == 2
        assert r["yang_above_count"] == 2
        assert r["yin_total"] == 2
        assert r["yin_shrink_count"] == 2
        assert r["groups"] == 2

    def test_yang_below_volume_ma_not_counted(self):
        """上涨但不放量 → 不计入 yang_above，课程要求阳线站上均量线才算资金进场。"""
        bars = _flat_bars(20) + [_bar(10.0, 10.5, 300.0)]
        r = P.volume_ma_rhythm(bars, lookback=1)
        assert r["yang_total"] == 1
        assert r["yang_above_count"] == 0
        assert r["groups"] == 0

    def test_yin_expanding_volume_not_shrink(self):
        """放量阴线 = 节奏翻转，不得计入缩量。"""
        bars = _flat_bars(20) + [_bar(10.5, 10.0, 4000.0)]
        r = P.volume_ma_rhythm(bars, lookback=1)
        assert r["yin_total"] == 1
        assert r["yin_shrink_count"] == 0

    def test_doji_counted_in_neither_side(self):
        """平盘（close==open）既不是阳也不是阴，不计入任何一边。"""
        bars = _flat_bars(20) + [_bar(10.0, 10.0, 3000.0)]
        r = P.volume_ma_rhythm(bars, lookback=1)
        assert r["yang_total"] == 0
        assert r["yin_total"] == 0

    def test_expanding_yin_breaks_pending_yang_group(self):
        """放量阳 → 放量阴 → 缩量阴 不成组：中间的放量阴线已经是节奏翻转。

        门2 review 复现：不重置 pending_yang 会把这段变脏的节奏报成一组完整「阳放阴缩」。
        """
        bars = _flat_bars(20) + [
            _bar(10.0, 10.5, 3000.0),   # 放量阳
            _bar(10.5, 10.1, 4000.0),   # 放量阴 → 节奏已破
            _bar(10.1, 9.9, 400.0),     # 缩量阴（迟到，不该补成组）
        ]
        r = P.volume_ma_rhythm(bars, lookback=3)
        assert r["yang_above_count"] == 1
        assert r["yin_shrink_count"] == 1
        assert r["groups"] == 0

    def test_doji_does_not_break_pending_yang_group(self):
        """平盘不打断待成组的放量阳——十字星不是方向性的翻转证据。"""
        bars = _flat_bars(20) + [
            _bar(10.0, 10.5, 3000.0),   # 放量阳
            _bar(10.5, 10.5, 2000.0),   # 平盘
            _bar(10.5, 10.2, 400.0),    # 缩量阴
        ]
        assert P.volume_ma_rhythm(bars, lookback=3)["groups"] == 1

    def test_weak_yang_does_not_break_pending_group(self):
        """不放量的阳线不打断——课程只要求「大部分阳线」站上均量线。"""
        bars = _flat_bars(20) + [
            _bar(10.0, 10.5, 3000.0),   # 放量阳
            _bar(10.5, 10.7, 300.0),    # 缩量阳（未站上均量线）
            _bar(10.7, 10.4, 400.0),    # 缩量阴
        ]
        r = P.volume_ma_rhythm(bars, lookback=3)
        assert r["yang_total"] == 2 and r["yang_above_count"] == 1
        assert r["groups"] == 1

    def test_groups_require_yang_then_yin_order(self):
        """组 = 放量阳在前、缩量阴在后；只有阴线不成组。"""
        bars = _flat_bars(20) + [_bar(10.5, 10.2, 500.0), _bar(10.2, 10.0, 480.0)]
        r = P.volume_ma_rhythm(bars, lookback=2)
        assert r["yin_shrink_count"] == 2
        assert r["groups"] == 0

    def test_insufficient_history(self):
        """窗口内任一根算不出均量线 → insufficient，计数不得当作事实。"""
        r = P.volume_ma_rhythm(_flat_bars(8), lookback=20)
        assert r["state"] == "insufficient"
        assert r["groups"] is None
        assert r["yang_above_count"] is None

    def test_none_volume_is_insufficient(self):
        bars = _flat_bars(20) + [_bar(10.0, 10.5, None)]
        r = P.volume_ma_rhythm(bars, lookback=1)
        assert r["state"] == "insufficient"

    def test_ratios_reported(self):
        bars = _flat_bars(20) + [
            _bar(10.0, 10.5, 3000.0),
            _bar(10.5, 10.2, 500.0),
        ]
        r = P.volume_ma_rhythm(bars, lookback=2)
        assert r["yang_above_ratio"] == pytest.approx(1.0)
        assert r["yin_shrink_ratio"] == pytest.approx(1.0)

    def test_zero_denominator_ratio_is_none(self):
        """窗口内没有阳线时比率为 None，不得算成 0.0 冒充「一根都没站上」。"""
        bars = _flat_bars(20) + [_bar(10.5, 10.2, 500.0)]
        r = P.volume_ma_rhythm(bars, lookback=1)
        assert r["yang_total"] == 0
        assert r["yang_above_ratio"] is None
