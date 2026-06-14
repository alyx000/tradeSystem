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
    # 11 根历史(无涨停) + 今日涨停 → 首次成立；历史 < 60 标 partial_history
    closes = [round(10 + i * 0.05, 4) for i in range(12)]
    bars = _bars(closes, pcts=[1.0] * 11 + [10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_accelerated=True)
    assert matched is True
    assert detail["prior_limit_in_window"] is False
    assert detail["partial_history"] is True


def test_first_limit_false_when_prior_limit_in_window():
    closes = [round(10 + i * 0.05, 4) for i in range(12)]
    pcts = [1.0] * 11 + [10.0]
    pcts[5] = 9.9  # 历史窗口内有一根涨停
    matched, detail = is_first_limit_up_acceleration(_bars(closes, pcts=pcts), MAIN, today_accelerated=True)
    assert matched is False
    assert detail["prior_limit_in_window"] is True


def test_first_limit_false_when_today_not_limit():
    matched, _ = is_first_limit_up_acceleration(_bars([10] * 12), MAIN, today_accelerated=False)
    assert matched is False


def test_first_limit_insufficient_when_history_below_min():
    # 历史 5 根(<MIN_BARS_FOR_SIGNAL) → 无法断言"近 60 日首次" → insufficient
    bars = _bars([10] * 6, pcts=[1.0] * 5 + [10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_accelerated=True)
    assert matched is False and detail["insufficient_history"] is True


def test_first_limit_insufficient_when_no_history():
    bars = _bars([11.3], pcts=[10.0])
    matched, detail = is_first_limit_up_acceleration(bars, MAIN, today_accelerated=True)
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


def test_gentle_rise_true_at_42_pct_within_45_cap():
    """校准锚点：上限 40→45 后，42% 累涨落带内（真实样本 605358 立昂微 0508 42.6%）。"""
    closes = [round(10.0 + i * 4.2 / 19, 4) for i in range(20)]  # 累计 +42%
    matched, detail = is_gentle_rise(_bars(closes), MAIN)
    assert matched is True
    assert detail["cum_pct"] == pytest.approx(42.0, abs=0.5)


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


# ---- GAP A: board-aware 加速阈值（双创 15%，鞠磊「20cm 涨15%+」）----

DUAL_CYB = "300750.SZ"  # 创业板 20cm
DUAL_KCB = "688512.SH"  # 科创板 20cm


def test_accel_threshold_dual_board_is_15():
    from services.trend_leader.detectors import accel_threshold
    assert accel_threshold(DUAL_CYB) == pytest.approx(15.0)
    assert accel_threshold(DUAL_KCB) == pytest.approx(15.0)


def test_accel_threshold_main_board_keeps_limit():
    from services.trend_leader.detectors import accel_threshold
    assert accel_threshold(MAIN) == pytest.approx(9.8)  # 10% × 0.98 容差，主板不变


def test_first_accel_dual_board_15pct_no_prior():
    """双创今日 +16%（非全 20% 涨停）+ 窗口内无 15%+ → 首次加速成立。"""
    closes = [round(10 + i * 0.05, 4) for i in range(12)]
    bars = _bars(closes, pcts=[1.0] * 11 + [16.0])
    matched, detail = is_first_limit_up_acceleration(bars, DUAL_KCB, today_accelerated=True)
    assert matched is True
    assert detail["prior_limit_in_window"] is False


def test_first_accel_dual_board_prior_15pct_disqualifies():
    """双创窗口内已有 15.5% 加速日 → 非首次。"""
    closes = [round(10 + i * 0.05, 4) for i in range(12)]
    pcts = [1.0] * 11 + [16.0]
    pcts[5] = 15.5
    matched, detail = is_first_limit_up_acceleration(_bars(closes, pcts=pcts), DUAL_KCB, today_accelerated=True)
    assert matched is False and detail["prior_limit_in_window"] is True


def test_first_accel_dual_board_prior_14pct_allowed():
    """双创窗口内 14%（< 15% 加速阈值）不算先前加速 → 仍首次。"""
    closes = [round(10 + i * 0.05, 4) for i in range(12)]
    pcts = [1.0] * 11 + [16.0]
    pcts[5] = 14.0
    matched, detail = is_first_limit_up_acceleration(_bars(closes, pcts=pcts), DUAL_KCB, today_accelerated=True)
    assert matched is True and detail["prior_limit_in_window"] is False


def test_gentle_rise_dual_board_excludes_15pct_window_day():
    """双创缓涨窗口内有 15.5% 加速日 → 算已加速 → 非缓涨。"""
    closes = [round(10.0 + i * 1.5 / 19, 4) for i in range(20)]  # +15% 累计
    pcts = [0.0] * 20
    pcts[5] = 15.5
    matched, detail = is_gentle_rise(_bars(closes, pcts=pcts), DUAL_KCB)
    assert matched is False and detail["has_limit_in_window"] is True


def test_gentle_rise_dual_board_allows_10pct_window_day():
    """双创窗口内 10%（< 15% 双创加速阈值）不算加速 → 仍缓涨（主板口径下 10% 会被算涨停）。"""
    closes = [round(10.0 + i * 1.5 / 19, 4) for i in range(20)]
    pcts = [0.0] * 20
    pcts[5] = 10.0
    matched, detail = is_gentle_rise(_bars(closes, pcts=pcts), DUAL_KCB)
    assert matched is True and detail["has_limit_in_window"] is False
