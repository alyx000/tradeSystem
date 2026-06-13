"""趋势主升检测器纯函数单测（Stage 1）。

bar 约定：list[dict]，按 trade_date **升序**（最旧→最新），最后一根=今日。
每个检测器返回 (matched: bool, detail: dict)，detail 含计算值 + insufficient_history 标记。
"""
from __future__ import annotations

import pytest

from services.trend_leader.detectors import (
    ma,
    is_near_ma5,
    is_far_from_ma5,
    is_first_limit_up_acceleration,
    is_gentle_rise,
    is_volume_shrink_pullback,
    is_trend_broken,
)

MAIN = "600552.SH"  # 主板，涨停阈值 10%


def _bars(closes, vols=None, opens=None, pcts=None) -> list[dict]:
    """从序列造升序 bar。vols/opens/pcts 不给则用默认（open=close、vol=1000、pct=0）。"""
    n = len(closes)
    vols = vols or [1000.0] * n
    opens = opens or list(closes)
    pcts = pcts or [0.0] * n
    out = []
    for i in range(n):
        c = closes[i]
        out.append({
            "trade_date": f"2026-06-{i + 1:02d}",
            "open": opens[i], "high": max(opens[i], c), "low": min(opens[i], c),
            "close": c, "pre_close": closes[i - 1] if i > 0 else c,
            "vol": vols[i], "amount": (c or 0) * vols[i], "pct_chg": pcts[i],
        })
    return out


# ---- ma ----

def test_ma_returns_trailing_mean():
    assert ma([1, 2, 3, 4, 5, 6], 5) == pytest.approx((2 + 3 + 4 + 5 + 6) / 5)


def test_ma_returns_none_when_insufficient():
    assert ma([1, 2, 3], 5) is None


# ---- is_near_ma5 ----

def test_is_near_ma5_true_within_threshold():
    matched, detail = is_near_ma5(_bars([10.0, 10.1, 9.9, 10.2, 10.05]))
    assert matched is True
    assert detail["ma5"] == pytest.approx(10.05)
    assert detail["deviation"] == pytest.approx(0.0, abs=1e-9)


def test_is_near_ma5_false_when_far():
    matched, detail = is_near_ma5(_bars([10.0, 10.0, 10.0, 10.0, 11.0]))
    assert matched is False
    assert detail["deviation"] == pytest.approx((11.0 - 10.2) / 10.2)


# ---- is_far_from_ma5 ----

def test_is_far_from_ma5_true_when_above_threshold():
    matched, detail = is_far_from_ma5(_bars([10.0, 10.0, 10.0, 10.0, 11.5]))
    assert matched is True
    assert detail["deviation"] == pytest.approx((11.5 - 10.3) / 10.3)


def test_is_far_from_ma5_false_when_below_threshold():
    matched, _ = is_far_from_ma5(_bars([10.0, 10.0, 10.0, 10.0, 11.0]))
    assert matched is False


def test_ma5_detectors_insufficient_history():
    bars = _bars([10.0, 10.1, 9.9])
    m_near, d_near = is_near_ma5(bars)
    m_far, d_far = is_far_from_ma5(bars)
    assert m_near is False and d_near["insufficient_history"] is True
    assert m_far is False and d_far["insufficient_history"] is True


# ---- is_first_limit_up_acceleration ----

def test_first_limit_true_when_today_limit_and_no_prior():
    bars = _bars([10, 10.1, 10.2, 10.3, 11.3], pcts=[1.0, 1.0, 1.0, 1.0, 10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_limit_up=True)
    assert matched is True
    assert detail["prior_limit_in_window"] is False


def test_first_limit_false_when_prior_limit_in_window():
    bars = _bars([10, 11, 11.1, 11.2, 12.3], pcts=[1.0, 9.9, 0.9, 0.9, 10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_limit_up=True)
    assert matched is False
    assert detail["prior_limit_in_window"] is True


def test_first_limit_false_when_today_not_limit():
    bars = _bars([10, 10.1, 10.2, 10.3, 10.4], pcts=[1.0, 1.0, 1.0, 1.0, 1.0])
    matched, _ = is_first_limit_up_acceleration(bars, MAIN, today_limit_up=False)
    assert matched is False


def test_first_limit_insufficient_when_no_history():
    bars = _bars([11.3], pcts=[10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_limit_up=True)
    assert matched is False and detail["insufficient_history"] is True


# ---- is_gentle_rise ----

def test_gentle_rise_true_in_range_no_limit():
    closes = [round(10.0 + i * 1.5 / 19, 4) for i in range(20)]  # 累计 +15%
    matched, detail = is_gentle_rise(_bars(closes), MAIN)
    assert matched is True
    assert detail["cum_pct"] == pytest.approx(15.0, abs=0.5)


def test_gentle_rise_false_when_too_steep():
    closes = [round(10.0 + i * 6.0 / 19, 4) for i in range(20)]  # 累计 +60%
    matched, _ = is_gentle_rise(_bars(closes), MAIN)
    assert matched is False


def test_gentle_rise_false_when_has_limit_in_window():
    closes = [round(10.0 + i * 1.5 / 19, 4) for i in range(20)]
    pcts = [0.0] * 20
    pcts[5] = 10.0  # 窗口内（非今日）有一根涨停
    matched, detail = is_gentle_rise(_bars(closes, pcts=pcts), MAIN)
    assert matched is False and detail["has_limit_in_window"] is True


def test_gentle_rise_insufficient_history():
    matched, detail = is_gentle_rise(_bars([10, 10, 10]), MAIN)
    assert matched is False and detail["insufficient_history"] is True


# ---- is_volume_shrink_pullback ----

def test_volume_shrink_pullback_true():
    bars = _bars([10, 10, 10, 10, 9.5], vols=[1000, 1000, 1000, 1000, 500],
                 opens=[10, 10, 10, 10, 10.0])
    matched, detail = is_volume_shrink_pullback(bars)
    assert matched is True
    assert detail["is_yin"] is True and detail["shrink"] is True


def test_volume_shrink_pullback_false_when_not_yin():
    bars = _bars([10, 10, 10, 10, 9.5], vols=[1000, 1000, 1000, 1000, 500],
                 opens=[10, 10, 10, 10, 9.0])  # open<close → 阳线
    matched, _ = is_volume_shrink_pullback(bars)
    assert matched is False


def test_volume_shrink_pullback_false_when_not_shrink():
    bars = _bars([10, 10, 10, 10, 9.5], vols=[1000, 1000, 1000, 1000, 1000],
                 opens=[10, 10, 10, 10, 10.0])
    matched, _ = is_volume_shrink_pullback(bars)
    assert matched is False


def test_volume_shrink_pullback_insufficient():
    matched, detail = is_volume_shrink_pullback(_bars([10, 10, 9.5]))
    assert matched is False and detail["insufficient_history"] is True


# ---- is_trend_broken ----

def test_trend_broken_below_ma10():
    bars = _bars([10, 10, 10, 10, 10, 10, 10, 10, 10, 8])
    matched, detail = is_trend_broken(bars)
    assert matched is True and detail["below_ma10"] is True


def test_trend_broken_two_day_break_widening():
    bars = _bars([10, 10, 10, 10, 10, 10, 10, 10, 9.5, 9.0],
                 pcts=[0, 0, 0, 0, 0, 0, 0, 0, -5.0, -5.3])
    matched, detail = is_trend_broken(bars)
    assert matched is True and detail["two_day_break"] is True


def test_trend_broken_false_when_flat():
    bars = _bars([10] * 10)
    matched, _ = is_trend_broken(bars)
    assert matched is False


def test_trend_broken_insufficient():
    matched, detail = is_trend_broken(_bars([10] * 9))
    assert matched is False and detail["insufficient_history"] is True
