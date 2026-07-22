"""value-watch 引擎重放纯函数：回撤 episode / 卖出阶梯 / 稀缺周线状态机。

spec v8 核心不变量：状态与事件全集由行情历史确定性重放推导，与运行次数/推送成败无关。
"""
from __future__ import annotations

import datetime

from services.value_watch import engine
from services.value_watch.config import LOGIC_VERSION


def _series(*closes, start="2026-01-05"):
    """按工作日序列造日线（跳过周末），start 须为周一。"""
    d = datetime.date.fromisoformat(start)
    out = []
    for c in closes:
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        out.append({"date": d.isoformat(), "close": float(c)})
        d += datetime.timedelta(days=1)
    return out


# ── ① 回撤 episode ──────────────────────────────────────────────

def test_drawdown_crossing_creates_enter_event():
    closes = _series(*([100] * 5 + [95, 92, 89]))   # dd 至 11%
    snap, events = engine.drawdown_events("801780.SI", closes, [10, 15])
    enters = [e for e in events if e.kind == "enter"]
    assert len(enters) == 1
    ev = enters[0]
    assert ev.key.startswith(f"v{LOGIC_VERSION}:drawdown:801780.SI:10:")
    assert ev.active
    assert snap["current_drawdown_pct"] >= 10


def test_drawdown_two_episodes_after_recross():
    # 16%→12%→8%→11%：修复(8%<10%)后再穿越 → 两个 10 档 episode + 一个 exit
    closes = _series(*([100] * 3 + [84, 88, 92, 89]))
    snap, events = engine.drawdown_events("X", closes, [10])
    enters = [e for e in events if e.kind == "enter"]
    exits = [e for e in events if e.kind == "exit"]
    assert len(enters) == 2 and len(exits) == 1
    assert exits[0].parent_key == enters[0].key
    assert not enters[0].active and enters[1].active   # 第一个已结束,第二个进行中


def test_drawdown_sliding_basis_no_spurious_event():
    # 长期阴跌:每日 -0.3%,超过 BASIS_WINDOW 后 basis 缓降;滑窗不得产生新 episode
    closes = _series(*[100 * (0.997 ** i) for i in range(200)])
    _, events = engine.drawdown_events("X", closes, [10])
    assert len([e for e in events if e.kind == "enter"]) == 1


def test_drawdown_same_day_two_buckets():
    closes = _series(*([100] * 3 + [83]))   # 一日直落 17%:10/15 同日穿越
    _, events = engine.drawdown_events("X", closes, [10, 15])
    keys = {e.key for e in events if e.kind == "enter"}
    assert len(keys) == 2


def test_drawdown_exit_occurred_on_recovery_day():
    closes = _series(*([100] * 3 + [88, 92]))   # 12% → 8% 修复
    _, events = engine.drawdown_events("X", closes, [10])
    ex = [e for e in events if e.kind == "exit"][0]
    assert ex.occurred_date == closes[-1]["date"]
    assert ex.active   # exit 事实已发生,恒 active(通知层再按 parent 判)


# ── ② 卖出阶梯 ─────────────────────────────────────────────────

def test_ladder_rungs_and_pullback():
    closes = _series(*[9.0, 9.5, 10.0, 10.9, 10.5])   # 峰值 +21.1%,当前 +16.7%
    snap, events = engine.ladder_events("601939.SH:2026-01-05", "建设银行", 9.0, closes)
    rungs = sorted(int(e.key.rsplit(":", 1)[-1]) for e in events if e.kind == "enter")
    assert rungs == [10, 15, 20]
    pulls = [e for e in events if e.kind == "exit"]
    assert len(pulls) == 1
    assert pulls[0].parent_key.endswith(":20")
    assert snap["max_rung"] == 20 and snap["current_gain_pct"] < 20


def test_ladder_rungs_permanent_and_no_pullback_without_20():
    closes = _series(*[9.0, 10.5, 9.2])    # 峰值 +16.7% 触 10/15,未触 20;现 +2.2%
    snap, events = engine.ladder_events("K", "N", 9.0, closes)
    enters = [e for e in events if e.kind == "enter"]
    assert sorted(int(e.key.rsplit(":", 1)[-1]) for e in enters) == [10, 15]
    assert all(e.active for e in enters)   # 已触档=持有期事实,永久 active(漏跑不丢)
    assert [e for e in events if e.kind == "exit"] == []   # 未触 20 无回落事件


def test_ladder_same_day_multi_rung_distinct_keys():
    closes = _series(*[9.0, 10.9])         # 一日 +21.1% 连跨三档
    _, events = engine.ladder_events("K", "N", 9.0, closes)
    assert len({e.key for e in events if e.kind == "enter"}) == 3


def test_ladder_ex_dividend_raw_close_semantics():
    """除息日跳空:raw close 口径下涨幅被机械压低——数值如实呈现,不复权修正(spec 取舍②)。"""
    closes = _series(*[10.0, 12.1, 11.0])  # 除息前 +21%,除息后价回 11 → 现 +10%
    snap, events = engine.ladder_events("K", "N", 10.0, closes)
    assert snap["max_rung"] == 20          # 历史触档不回溯修正
    assert 10 <= snap["current_gain_pct"] < 15


# ── ③ 稀缺周线重放 ──────────────────────────────────────────────

def _weeks(closes, start="2025-01-03"):
    base = datetime.date.fromisoformat(start)
    return [{"week_end": (base + datetime.timedelta(weeks=i)).isoformat(),
             "close": float(c), "volume": 1000.0} for i, c in enumerate(closes)]


def _signal_weeks(n_flat=40, n_rise=12):
    """前段横盘(MACD→0 附近)+末段温和抬升:粘合且上零轴。"""
    return _weeks([100.0] * n_flat + [100.0 + (i + 1) * 0.35 for i in range(n_rise)])


def test_scarcity_insufficient_history():
    snap, events = engine.scarcity_replay("600436.SH", _weeks([100] * 10))
    assert snap["state"] == "insufficient_history"
    assert events == []


def test_scarcity_signal_fires_and_key_shape():
    snap, events = engine.scarcity_replay("600436.SH", _signal_weeks())
    signals = [e for e in events if e.kind == "enter"]
    assert signals, "构造序列应触发 signal"
    assert signals[0].key.startswith(f"v{LOGIC_VERSION}:scarcity_signal:600436.SH:")
    assert snap["state"] == "signaled"
    assert signals[-1].active


def test_scarcity_single_week_dip_no_invalidate():
    """signaled 后单周不满足不失效(连续 2 完成周才失效,去抖)。"""
    weeks = _signal_weeks()
    weeks.append({"week_end": "2026-01-30", "close": weeks[-1]["close"] * 0.90,
                  "volume": 1000.0})   # 单周大跌破坏条件
    snap, events = engine.scarcity_replay("600436.SH", weeks)
    assert [e for e in events if e.kind == "exit"] == []
    assert snap["state"] == "signaled"


def test_scarcity_two_week_invalidate_then_resignal_new_key():
    weeks = _signal_weeks()
    last = weeks[-1]["close"]
    weeks.append({"week_end": "2026-01-30", "close": last * 0.90, "volume": 1000.0})
    weeks.append({"week_end": "2026-02-06", "close": last * 0.80, "volume": 1000.0})
    snap, events = engine.scarcity_replay("600436.SH", weeks)
    exits = [e for e in events if e.kind == "exit"]
    assert len(exits) == 1
    assert exits[0].parent_key.startswith(f"v{LOGIC_VERSION}:scarcity_signal:")
    assert snap["state"] == "watching"
