"""value-watch 条件引擎：确定性重放（历史 → 状态快照 + 事件全集）。

spec v8 核心不变量：
- 状态与"截至当日应存在的事件全集"只由行情历史/持仓行决定，与运行次数、推送成败无关；
  键确定（重跑同历史 → 同键集合）。
- 事件分两类：enter（进入档位/信号成立/阶梯首触）——`active` 表示条件当前仍成立，通知层
  只推 active 的 enter；exit（回撤修复/信号失效/冲高回落）——事实已发生即 `active=True`，
  通知层按 "parent enter 已发出" 决定是否推（迟到必补）。
- 回撤 episode 模型：事件身份 = 穿越日；必须先退出档位再真实再穿越才有新事件，
  基准滑窗只影响 dd 数值（长期阴跌不刷屏）。

**调用方契约（键确定性）**：closes/weeks 历史必须从固定锚日（config.HISTORY_ANCHOR_DATE）
起取，不得用"目标日往前 N 天"滚动窗口——窗口前移会让头部截断处的 dd/EMA 值漂移，
episode crossing_date 与 scarcity 临界周随之漂移 → 键变 → 账本去重失效重复推送。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    BASIS_WINDOW,
    INVALIDATE_WEEKS,
    LADDER_RUNGS,
    LOGIC_VERSION,
    MA_SPREAD_MAX,
    WARMUP_WEEKS,
)
from .weekly import weekly_ma, weekly_macd


@dataclass
class Event:
    key: str
    kind: str                       # "enter" | "exit"
    parent_key: "str | None"
    occurred_date: str
    active: bool
    title: str
    facts: dict = field(default_factory=dict)


def _v(suffix: str) -> str:
    return f"v{LOGIC_VERSION}:{suffix}"


# ── ① 红利买入触发：回撤 episode ────────────────────────────────

def drawdown_events(code: str, closes: list[dict],
                    buckets: list[int],
                    basis_window: int = BASIS_WINDOW) -> tuple[dict, list[Event]]:
    """closes：升序 [{"date","close"}]。返回 (状态快照, 事件列表)。

    对每档 B：episode = 从穿越日（dd>=B 且前日 dd<B）到首个 dd<B 日的连续区间。
    enter 键 = drawdown:{code}:{B}:{crossing_date}，active=episode 至序列末未关闭；
    exit 键 = drawdown_recovered:... 同 crossing_date，episode 至多一次。
    """
    n = len(closes)
    dd = [0.0] * n
    basis_val = [0.0] * n
    basis_date = [""] * n
    for i in range(n):
        lo = max(0, i - basis_window + 1)
        window = closes[lo:i + 1]
        bmax = max(window, key=lambda b: b["close"])
        basis_val[i], basis_date[i] = bmax["close"], bmax["date"]
        dd[i] = (basis_val[i] - closes[i]["close"]) / basis_val[i] * 100 if basis_val[i] else 0.0

    events: list[Event] = []
    bucket_state: dict[str, dict] = {}   # str 键:json round-trip 后 int 键会变字符串,统一 str 消歧
    for b in sorted(buckets):
        open_enter: "Event | None" = None   # 持有对象引用,exit 时直接置位,免全量扫描
        for i in range(n):
            above = dd[i] >= b
            prev_above = dd[i - 1] >= b if i > 0 else False
            if above and not prev_above and open_enter is None:
                crossing = closes[i]["date"]
                open_enter = Event(
                    key=_v(f"drawdown:{code}:{b}:{crossing}"),
                    kind="enter", parent_key=None, occurred_date=crossing,
                    active=True,   # 先置 True，episode 关闭时改 False
                    title=f"{code} 回撤触及 {b}% 档",
                    facts={"bucket": b, "crossing_date": crossing},
                )
                events.append(open_enter)
            elif not above and open_enter is not None:
                open_enter.active = False
                crossing = open_enter.facts["crossing_date"]
                events.append(Event(
                    key=_v(f"drawdown_recovered:{code}:{b}:{crossing}"),
                    kind="exit", parent_key=open_enter.key,
                    occurred_date=closes[i]["date"], active=True,
                    title=f"{code} 回撤修复至 {b}% 档内",
                    facts={"bucket": b, "crossing_date": crossing,
                           "recovered_date": closes[i]["date"]},
                ))
                open_enter = None
        bucket_state[str(b)] = {
            "in_episode": open_enter is not None,
            "crossing_date": open_enter.facts["crossing_date"] if open_enter else None,
        }

    snap = {
        "code": code,
        "current_close": closes[-1]["close"] if closes else None,
        "current_drawdown_pct": round(dd[-1], 2) if closes else None,
        "basis": basis_val[-1] if closes else None,
        "basis_date": basis_date[-1] if closes else None,
        "buckets": bucket_state,
    }
    return snap, events


# ── ② 卖出阶梯 ─────────────────────────────────────────────────

def ladder_events(position_key: str, name: str, entry_price: float,
                  closes_since_entry: list[dict],
                  rungs: "list[int] | None" = None) -> tuple[dict, list[Event]]:
    """closes_since_entry：自 entry_date 起升序日线。涨幅 = close/entry − 1（raw close
    "价格涨幅"口径，未含分红未复权——除息日机械压低如实呈现，spec 取舍②）。

    enter：各档首触日（持有期事实，永久 active，漏跑从历史补算不丢档）；
    exit：已触 20 档且当前涨幅 < 20%（每 position_key 至多一次）。
    """
    rungs = rungs or LADDER_RUNGS
    gains = [(bar["close"] / entry_price - 1) * 100 for bar in closes_since_entry]
    events: list[Event] = []
    max_rung = 0
    for r in sorted(rungs):
        first_touch = next(
            (closes_since_entry[i]["date"] for i, g in enumerate(gains) if g >= r), None)
        if first_touch is None:
            continue
        max_rung = max(max_rung, r)
        events.append(Event(
            key=_v(f"ladder:{position_key}:{r}"),
            kind="enter", parent_key=None, occurred_date=first_touch,
            active=True,
            title=f"{name} 价格涨幅触及 +{r}% 档",
            facts={"rung": r, "first_touch_date": first_touch,
                   "entry_price": entry_price},
        ))
    current_gain = gains[-1] if gains else 0.0
    top = max(rungs)
    if max_rung >= top and current_gain < top:
        pull_date = None
        seen_top = False
        for i, g in enumerate(gains):
            if g >= top:
                seen_top = True
            elif seen_top:   # 首个回落日即身份日(再冲高再回落仍同一键,至多一次)
                pull_date = closes_since_entry[i]["date"]
                break
        events.append(Event(
            key=_v(f"ladder_pullback:{position_key}"),
            kind="exit", parent_key=_v(f"ladder:{position_key}:{top}"),
            occurred_date=pull_date or closes_since_entry[-1]["date"], active=True,
            title=f"{name} 冲高回落至 +{top}% 档内",
            facts={"top_rung": top, "current_gain_pct": round(current_gain, 2)},
        ))
    snap = {
        "position_key": position_key, "name": name, "entry_price": entry_price,
        "current_gain_pct": round(current_gain, 2),
        "max_rung": max_rung,
    }
    return snap, events


# ── ③ 稀缺价值：完成周重放状态机 ────────────────────────────────

def scarcity_replay(code: str, weeks: list[dict]) -> tuple[dict, list[Event]]:
    """weeks：完成周升序 [{"week_end","close","volume"}]（weekly.aggregate_completed_weeks
    输出）。状态机 watching ⇄ signaled 由周线历史确定性重放，与推送成败无关。

    signal：粘合（MA5/10/20 (max−min)/min ≤ 3% 且 MA5 > 上周 MA5）∧ 上零轴
    （DIF>0 且 DEA>0）同一完成周首次同时满足。
    invalidate：signaled 后连续 INVALIDATE_WEEKS 完成周不满足（单周抖动不失效）。
    """
    if len(weeks) < WARMUP_WEEKS:
        return {"code": code, "state": "insufficient_history",
                "completed_weeks": len(weeks)}, []

    closes = [w["close"] for w in weeks]
    ma5, ma10, ma20 = weekly_ma(closes, 5), weekly_ma(closes, 10), weekly_ma(closes, 20)
    dif, dea = weekly_macd(closes)

    def cond(i: int) -> "tuple[bool, float | None]":
        """返回 (满足?, spread_pct)——facts 与判定同源,免重复展开表达式。"""
        if ma20[i] is None or ma5[i - 1] is None:
            return False, None
        mas = [ma5[i], ma10[i], ma20[i]]
        spread = (max(mas) - min(mas)) / min(mas)
        upward = ma5[i] > ma5[i - 1]          # 向上仅约束 MA5(spec 显式声明)
        above_zero = dif[i] > 0 and dea[i] > 0
        return (spread <= MA_SPREAD_MAX and upward and above_zero), round(spread * 100, 2)

    state = "watching"
    fail_streak = 0
    open_signal: "Event | None" = None   # 持有对象引用,失效时直接置位
    events: list[Event] = []
    for i in range(WARMUP_WEEKS - 1, len(weeks)):
        ok, spread_pct = cond(i)
        if state == "watching" and ok:
            state = "signaled"
            fail_streak = 0
            open_signal = Event(
                key=_v(f"scarcity_signal:{code}:{weeks[i]['week_end']}"),
                kind="enter", parent_key=None,
                occurred_date=weeks[i]["week_end"], active=True,
                title=f"{code} 周线波段进入条件成立（粘合+MACD 上零轴）",
                facts={"week_end": weeks[i]["week_end"], "ma_spread_pct": spread_pct,
                       "dif": round(dif[i], 4), "dea": round(dea[i], 4)},
            )
            events.append(open_signal)
        elif state == "signaled":
            if ok:
                fail_streak = 0
            else:
                fail_streak += 1
                if fail_streak >= INVALIDATE_WEEKS:
                    state = "watching"
                    fail_streak = 0
                    open_signal.active = False
                    events.append(Event(
                        key=_v(f"scarcity_invalidated:{code}:{weeks[i]['week_end']}"),
                        kind="exit", parent_key=open_signal.key,
                        occurred_date=weeks[i]["week_end"], active=True,
                        title=f"{code} 周线条件失效（连续 {INVALIDATE_WEEKS} 周不满足）",
                        facts={"week_end": weeks[i]["week_end"]},
                    ))
                    open_signal = None

    snap = {"code": code, "state": state, "completed_weeks": len(weeks),
            "last_week_end": weeks[-1]["week_end"]}
    return snap, events
