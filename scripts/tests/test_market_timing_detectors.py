"""market-timing 检测器纯函数单测（无 I/O）。

覆盖：ma / swing 双向拐点 / 斐波那契日数 / 变盘点命中-临近 / 共振 /
底分型成型 / 放量中阳确认（各失败分支）/ 历史不足兜底。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services.market_timing import detectors as D


def _d(i: int) -> str:
    return (datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")


def _hl(i: int, price: float, vol: float = 1000.0) -> dict:
    """high=low=close=open=price 的「点」K 线，便于 swing 极值口径清晰。"""
    return {"trade_date": _d(i), "open": price, "high": price, "low": price, "close": price, "vol": vol}


def _b(i: int, o: float, c: float, vol: float = 1000.0) -> dict:
    return {"trade_date": _d(i), "open": o, "high": max(o, c), "low": min(o, c), "close": c, "vol": vol}


def _series(closes, opens=None, vols=None) -> list[dict]:
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        v = vols[i] if vols else 1000.0
        out.append(_b(i, o, c, v))
    return out


# ── ma ──

def test_ma_basic():
    assert D.ma([1, 2, 3, 4, 5], 3) == 4.0


def test_ma_insufficient():
    assert D.ma([1, 2], 3) is None


# ── find_swing_pivot（双向）──

def test_swing_finds_recent_high():
    bars = [_hl(i, p) for i, p in enumerate([10, 11, 12, 15, 12, 11, 10, 9])]
    piv = D.find_swing_pivot(bars, window=2, min_reversal=0.05)
    assert piv["type"] == "high"
    assert piv["price"] == 15
    assert piv["date"] == _d(3)


def test_swing_finds_recent_low():
    bars = [_hl(i, p) for i, p in enumerate([15, 14, 12, 10, 12, 14, 15, 16])]
    piv = D.find_swing_pivot(bars, window=2, min_reversal=0.05)
    assert piv["type"] == "low"
    assert piv["price"] == 10
    assert piv["date"] == _d(3)


def test_swing_monotonic_returns_none():
    bars = [_hl(i, p) for i, p in enumerate([10, 11, 12, 13, 14, 15, 16])]
    assert D.find_swing_pivot(bars, window=2, min_reversal=0.05) is None


def test_swing_insufficient_bars_returns_none():
    bars = [_hl(i, 100) for i in range(4)]
    assert D.find_swing_pivot(bars, window=2) is None


def test_swing_reversal_too_small_returns_none():
    # 峰值后只回落 1%，< min_reversal 5% → 不算有效 swing
    bars = [_hl(i, p) for i, p in enumerate([100, 101, 102, 105, 104.5, 104, 103.9, 103.8])]
    assert D.find_swing_pivot(bars, window=2, min_reversal=0.05) is None


# ── fib_day_count ──

def test_fib_day_count_from_pivot():
    bars = [_hl(i, 100) for i in range(22)]
    assert D.fib_day_count(bars, _d(0)) == 21
    assert D.fib_day_count(bars, _d(21)) == 0


def test_fib_day_count_pivot_not_found():
    bars = [_hl(i, 100) for i in range(5)]
    assert D.fib_day_count(bars, "2099-12-31") is None


# ── fib_turning_point ──

def test_fib_exact_hit():
    tp = D.fib_turning_point(21, tolerance=1)
    assert tp["hit"] == 21 and tp["near"] is None and tp["distance"] == 0


def test_fib_near_within_tolerance():
    tp = D.fib_turning_point(20, tolerance=1)
    assert tp["hit"] is None and tp["near"] == 21 and tp["distance"] == 1


def test_fib_no_signal_outside_tolerance():
    tp = D.fib_turning_point(25, tolerance=1)
    assert tp["hit"] is None and tp["near"] is None and tp["distance"] == 4


def test_fib_zero_and_none():
    assert D.fib_turning_point(0)["hit"] is None
    assert D.fib_turning_point(None)["hit"] is None


# ── count_resonance ──

def test_count_resonance_counts_exact_hits_only():
    tps = [
        {"hit": 21, "near": None},
        {"hit": None, "near": 8},
        {"hit": 8, "near": None},
        {"hit": None, "near": None},
    ]
    assert D.count_resonance(tps) == 2


# ── is_bottom_fractal ──

def test_bottom_fractal_middle_lowest_true():
    bars = [_b(0, 12, 12.5), _b(1, 11, 10), _b(2, 10.5, 11.5)]
    # 显式压低中间低点
    bars[1]["low"] = 10.0
    bars[0]["low"] = 12.0
    bars[2]["low"] = 11.0
    ok, info = D.is_bottom_fractal(bars)
    assert ok is True
    assert info["low_price"] == 10.0
    assert info["low_date"] == _d(1)


def test_bottom_fractal_middle_not_lowest_false():
    bars = [_b(0, 10, 10), _b(1, 11, 11), _b(2, 12, 12)]
    ok, _ = D.is_bottom_fractal(bars)
    assert ok is False


def test_bottom_fractal_insufficient():
    ok, info = D.is_bottom_fractal([_b(0, 10, 10), _b(1, 11, 11)])
    assert ok is False
    assert info["reason"] == "insufficient_history"


# ── is_breakout_confirm ──

_BASE_CLOSES = [100, 100, 100, 100, 100, 98, 98, 99, 100, 103]
_BASE_OPENS = [100, 100, 100, 100, 100, 98, 98, 99, 100, 100]
_BASE_VOLS = [1000] * 9 + [2000]


def test_breakout_confirm_all_true():
    bars = _series(_BASE_CLOSES, _BASE_OPENS, _BASE_VOLS)
    ok, det = D.is_breakout_confirm(bars)
    assert ok is True
    assert det["mid_yang"] and det["volume_up"] and det["above_ma5"] and det["ma5_turning"]


def test_breakout_confirm_fails_on_shrink_volume():
    vols = [1000] * 10  # 今日不放量
    bars = _series(_BASE_CLOSES, _BASE_OPENS, vols)
    ok, det = D.is_breakout_confirm(bars)
    assert ok is False
    assert det["volume_up"] is False


def test_breakout_confirm_fails_on_yin_line():
    closes = _BASE_CLOSES[:-1] + [99]      # 今日收阴
    opens = _BASE_OPENS[:-1] + [101]
    bars = _series(closes, opens, _BASE_VOLS)
    ok, det = D.is_breakout_confirm(bars)
    assert ok is False
    assert det["mid_yang"] is False


def test_breakout_confirm_fails_below_ma5():
    closes = [110, 110, 110, 110, 110, 110, 110, 110, 100, 101.5]
    opens = closes[:-1] + [100]
    bars = _series(closes, opens, _BASE_VOLS)
    ok, det = D.is_breakout_confirm(bars)
    assert ok is False
    assert det["above_ma5"] is False


def test_breakout_confirm_insufficient():
    bars = _series([100, 101, 102], [100, 100, 100])
    ok, info = D.is_breakout_confirm(bars)
    assert ok is False
    assert info["reason"] == "insufficient_history"


# ── is_fractal_confirmed（消费前高 + 结构未破）──

def test_fractal_confirmed_all_true():
    bars = _series(_BASE_CLOSES, _BASE_OPENS, _BASE_VOLS)  # 今日 close=103，放量中阳破MA5拐头
    fractal = {"low_price": 98.0, "left_high": 101.0, "right_high": 101.0}
    ok, det = D.is_fractal_confirmed(bars, fractal)
    assert ok is True
    assert det["broke_prior_high"] and det["structure_held"]


def test_fractal_confirmed_fails_when_prior_high_not_broken():
    bars = _series(_BASE_CLOSES, _BASE_OPENS, _BASE_VOLS)  # 放量中阳全真，但前高定在 110
    fractal = {"low_price": 98.0, "left_high": 110.0, "right_high": 110.0}
    ok, det = D.is_fractal_confirmed(bars, fractal)
    assert ok is False
    assert det["broke_prior_high"] is False


def test_fractal_confirmed_fails_when_breakout_fails():
    vols = [1000] * 10  # 今日不放量 → breakout 不成立，确认整体 False
    bars = _series(_BASE_CLOSES, _BASE_OPENS, vols)
    fractal = {"low_price": 98.0, "left_high": 101.0, "right_high": 101.0}
    ok, det = D.is_fractal_confirmed(bars, fractal)
    assert ok is False
    assert det["volume_up"] is False
