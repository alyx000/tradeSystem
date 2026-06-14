"""趋势主升 signal_hits 重建 helper 单测。

signal_hits(last_signal) 把池里已存的「嵌套明细」(scanner Pass2 落库)重建成「布尔命中」，
供 web 渲染信号 chip。重建逻辑必须与 detectors 的 matched 语义一致——靠对账用例锁死防漂移。
"""
from __future__ import annotations

import pytest

from services.trend_leader import constants as C
from services.trend_leader.detectors import (
    is_near_ma5,
    is_far_from_ma5,
    is_volume_shrink_pullback,
    is_trend_broken,
)
from services.trend_leader.signals import signal_hits


def _bars(closes, vols=None, opens=None, pcts=None) -> list[dict]:
    """从序列造升序 bar（与 test_trend_leader_detectors 同口径）。"""
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


def _pass2_signal(bars: list[dict], *, entry_trigger="涨停", branch_concepts=None) -> dict:
    """复刻 scanner Pass2 写入 last_signal_json 的结构（3 信号基线）。"""
    _, sd = is_volume_shrink_pullback(bars)
    _, nd = is_near_ma5(bars)
    _, fdd = is_far_from_ma5(bars)
    _, bd = is_trend_broken(bars)
    return {
        "shrink_pullback": sd, "near_ma5": nd, "overheat": fdd, "trend": bd,
        "entry_trigger": entry_trigger, "branch_concepts": branch_concepts or [],
    }


# ── Pass1 / 空值 → None ────────────────────────────────────────

def test_none_signal_returns_none():
    assert signal_hits(None) is None


def test_empty_signal_returns_none():
    assert signal_hits({}) is None


@pytest.mark.parametrize("garbage", ["not-a-dict", 5, ["shrink_pullback"], True])
def test_non_dict_signal_returns_none(garbage):
    """DB 脏值反序列化成非 dict 时回退「待维护」，不抛 TypeError。"""
    assert signal_hits(garbage) is None


@pytest.mark.parametrize("bad", [
    {"shrink_pullback": True, "near_ma5": [], "overheat": "x"},  # 嵌套全是 truthy 非 dict
    {"shrink_pullback": {}, "near_ma5": True, "overheat": 5},
])
def test_non_dict_nested_detail_no_crash(bad):
    """嵌套明细是 truthy 非 dict（脏值/漂移）时不抛 AttributeError，按未命中处理。"""
    hits = signal_hits(bad)
    assert hits == {"shrink_pullback_buy": False, "near_ma5": False, "overheat": False}


@pytest.mark.parametrize("dirty", ["true", "false", 1, [1], {"a": 1}])
def test_dirty_boolean_not_false_positive_hit(dirty):
    """is_yin/shrink 脏成 truthy 非 bool（字符串/对象）时不得误报命中（严格 is True）。"""
    sig = {"shrink_pullback": {"is_yin": dirty, "shrink": dirty, "insufficient_history": False}}
    assert signal_hits(sig)["shrink_pullback_buy"] is False


@pytest.mark.parametrize("dev", ["0.01", "0.09", [], {"x": 1}, True, float("nan"), float("inf")])
def test_non_numeric_deviation_no_crash(dev):
    """deviation 漂移成字符串/对象/bool/NaN/Inf 时不抛 TypeError，按未命中处理。"""
    sig = {
        "shrink_pullback": {"is_yin": False, "shrink": False, "insufficient_history": False},
        "near_ma5": {"deviation": dev, "insufficient_history": False},
        "overheat": {"deviation": dev, "insufficient_history": False},
    }
    hits = signal_hits(sig)
    assert hits["near_ma5"] is False and hits["overheat"] is False


def test_pass1_signal_returns_none():
    """入池(Pass1) last_signal 只有 first_limit/gentle，无 Pass2 信号 → None（待维护）。"""
    pass1 = {"first_limit": {"today_accelerated": True}, "gentle": {"cum_pct": 12.0},
             "entry_trigger": "涨停", "branch_concepts": []}
    assert signal_hits(pass1) is None


# ── 三布尔逐项 ─────────────────────────────────────────────────

def test_keys_are_exactly_three_signals():
    hits = signal_hits(_pass2_signal(_bars([10.0, 10.1, 9.9, 10.2, 10.05])))
    assert set(hits.keys()) == {"shrink_pullback_buy", "near_ma5", "overheat"}


def test_near_ma5_hit_true():
    hits = signal_hits(_pass2_signal(_bars([10.0, 10.1, 9.9, 10.2, 10.05])))
    assert hits["near_ma5"] is True
    assert hits["overheat"] is False


def test_overheat_hit_true():
    hits = signal_hits(_pass2_signal(_bars([10.0, 10.0, 10.0, 10.0, 11.5])))
    assert hits["overheat"] is True
    assert hits["near_ma5"] is False


def test_shrink_pullback_buy_hit_true():
    # 收阴(close<open) + 缩量(vol 远低于昨量与 MA5 量)
    bars = _bars(
        closes=[10.0, 10.2, 10.4, 10.6, 10.3],
        vols=[2000.0, 2000.0, 2000.0, 2000.0, 200.0],
        opens=[10.0, 10.2, 10.4, 10.6, 10.6],  # 末根 open>close → 阴线
    )
    hits = signal_hits(_pass2_signal(bars))
    assert hits["shrink_pullback_buy"] is True


# ── insufficient_history 守卫（不抛 KeyError） ─────────────────

def test_insufficient_history_all_false():
    """历史不足时各 detail 只有 insufficient_history，无 deviation 等键 → 全 False，不抛错。"""
    hits = signal_hits(_pass2_signal(_bars([10.0, 10.1])))
    assert hits == {"shrink_pullback_buy": False, "near_ma5": False, "overheat": False}


# ── 对账：signal_hits 必须 == detectors 的 matched（锁死防漂移） ──

@pytest.mark.parametrize("closes,vols,opens", [
    ([10.0, 10.1, 9.9, 10.2, 10.05], None, None),            # 贴 MA5
    ([10.0, 10.0, 10.0, 10.0, 11.5], None, None),            # 远离 MA5
    ([10.0, 10.2, 10.4, 10.6, 10.3],                          # 缩量阴线
     [2000.0, 2000.0, 2000.0, 2000.0, 200.0],
     [10.0, 10.2, 10.4, 10.6, 10.6]),
    ([10.0, 10.0, 10.0, 10.0, 10.0], None, None),            # 全平：均不命中
    ([10.0, 10.1], None, None),                               # 历史不足
])
def test_reconciliation_matches_detectors(closes, vols, opens):
    bars = _bars(closes, vols=vols, opens=opens)
    shrink, _ = is_volume_shrink_pullback(bars)
    near, _ = is_near_ma5(bars)
    far, _ = is_far_from_ma5(bars)
    hits = signal_hits(_pass2_signal(bars))
    assert hits["shrink_pullback_buy"] == shrink
    assert hits["near_ma5"] == near
    assert hits["overheat"] == far


def test_thresholds_sourced_from_constants():
    """重建直接复用 constants 阈值，不写死魔法数（守 deviation 边界口径）。"""
    assert C.NEAR_MA5_MAX_DEVIATION == 0.03
    assert C.FAR_FROM_MA5_MIN_DEVIATION == 0.08
