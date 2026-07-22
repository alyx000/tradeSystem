"""通知候选集判定（spec v8 通知层）。

- enter 进入候选 ⇔ 条件当前仍成立（active）且键 ∉ effective ledger；
- exit 进入候选 ⇔ parent enter 键 ∈ effective ledger（曾成功发出，或本轮已入选）
  且本键 ∉ ledger——不要求发生在当日：恢复日/失效周漏跑后任意一次运行迟到补推，
  已提醒必有解除；parent 从未发出的 exit 不推（没有提醒需要解除）；
- effective ledger = 历史账本 ∪ 本轮已入选键；enter 先于其 exit 处理（同轮
  parent+exit 场景由 formatter 合并为一条消息，两键同录账本）。
- 推送闸门只比较 run_target_date（本次运行快照日）与日历确认的最新已收盘交易日；
  事件发生日不参与闸门。
"""
from __future__ import annotations

from .engine import Event


def select_candidates(events: list[Event], ledger: set[str]) -> list[Event]:
    eff = set(ledger)
    out: list[Event] = []
    for ev in sorted(events, key=lambda e: 0 if e.kind == "enter" else 1):
        if ev.key in eff:
            continue
        if ev.kind == "enter" and not ev.active:
            continue   # 从未提醒过的瞬态不补推(spec 非目标,报告脚注声明)
        if ev.kind == "exit" and (ev.parent_key not in eff):
            continue
        out.append(ev)
        eff.add(ev.key)
    return out


def allow_push(run_target_date: str, latest_closed: "str | None") -> bool:
    """latest_closed=None 表示日历 blocked——本次运行不推送任何事件（落库照常）。"""
    return latest_closed is not None and run_target_date == latest_closed
