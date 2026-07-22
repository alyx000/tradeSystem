"""value-watch 渲染层：报告 markdown + 推送消息（钉钉手机端列表式，不用表格）。

红线（spec v8）：数字标 [事实]；条件/档位解读标 [判断]·出处 teacher_notes#391；
推送文案不用祈使句——"卖出一半/全部清仓"只能出现在"原笔记框架含义"引用行并标
"非操作指令"；脚注"不构成投资建议"。
"""
from __future__ import annotations

from .config import TEACHER_NOTE_REF
from .engine import Event

_FOOTER = (f"\n> 出处：{TEACHER_NOTE_REF}（鞠磊·价值投资年课）。"
           "以上为条件监控事实与框架引用，[判断] 不构成投资建议。")

# 事件键前缀 → 原笔记框架含义（引用区文案，非操作指令）
_MEANINGS = [
    ("drawdown:", "原笔记框架：银行板块指数/长电回撤 10%-15% 为红利价值买入观察区间（非操作指令）"),
    ("drawdown_recovered:", "原笔记框架：回撤修复离开观察档位，周期性等待下一轮（非操作指令）"),
    ("ladder_pullback:", "原笔记框架：冲高回落跌回 +20% 档内对应「全部清仓锁定利润」（非操作指令）"),
    ("ladder:", "原笔记框架：涨幅档位对应「分批减持一半」（非操作指令）"),
    ("scarcity_signal:", "原笔记框架：周线粘合+MACD 上零轴为稀缺价值波段进入条件（非操作指令）"),
    ("scarcity_invalidated:", "原笔记框架：周线条件失效，回到观察状态（非操作指令）"),
]


def _meaning(key: str) -> str:
    for prefix, text in _MEANINGS:
        if f":{prefix}" in key or key.split(":", 1)[-1].startswith(prefix):
            return text
    return ""


def render_push_messages(candidates: list[Event],
                         payload: dict) -> list[tuple[str, str, list[str]]]:
    """候选事件 → [(title, markdown, keys)]。同标的合并一条；同轮 parent+exit
    （如首跑历史触档且已回落）合并为一条"曾触及且已回落"消息、两键同录。"""
    groups: dict[str, list[Event]] = {}
    for ev in candidates:
        # 键形如 v1:<type>:<subject>:...，subject 作分组轴
        parts = ev.key.split(":")
        subject = parts[2] if len(parts) > 2 else ev.key
        groups.setdefault(subject, []).append(ev)

    out = []
    for subject, evs in groups.items():
        lines = [f"### 价值观察 · {subject}"]
        for ev in evs:
            tag = "[事实]" if ev.kind == "enter" else "[事实·解除]"
            lines.append(f"- {tag} {ev.title}（发生日 {ev.occurred_date}）")
            facts = ", ".join(f"{k}={v}" for k, v in ev.facts.items() if v is not None)
            if facts:
                lines.append(f"  - {facts}")
            meaning = _meaning(ev.key)
            if meaning:
                lines.append(f"  - [判断] {meaning}")
        lines.append(_FOOTER)
        out.append((f"价值观察 · {subject}", "\n".join(lines), [e.key for e in evs]))
    return out


def render_report(payload: dict, *, date: str, logic_version: int,
                  candidates: "list[Event] | None" = None) -> str:
    lines = [f"# 价值投资条件监控 · {date}",
             f"- snapshot_date: {date} / holdings_as_of: {payload.get('holdings_as_of')}"
             f" / logic_version: {logic_version}", ""]

    lines.append("## ① 红利买入触发（回撤观察）")
    for code, snap in (payload.get("drawdown") or {}).items():
        if snap is None:
            lines.append(f"- {code}: source_failed（当日跳过，不落假值）")
            continue
        lines.append(
            f"- [事实] {code}: 收盘 {snap.get('current_close')} / "
            f"回撤 {snap.get('current_drawdown_pct')}%（基准 {snap.get('basis')} "
            f"@ {snap.get('basis_date')}）")
        for b, st in (snap.get("buckets") or {}).items():
            state = "档内" if st.get("in_episode") else "档外"
            lines.append(f"  - [判断] {b}% 档：{state}"
                         + (f"（穿越日 {st.get('crossing_date')}）" if st.get("crossing_date") else ""))

    lines.append("")
    lines.append("## ② 卖出阶梯（持仓）")
    ladder = payload.get("ladder") or []
    if not ladder:
        lines.append("- 持仓池无监控标的")
    for p in ladder:
        if p.get("insufficient_identity"):
            lines.append(f"- [事实] {p.get('name')}({p.get('code')}): 缺成本价/建仓日期，"
                         "无法追踪阶梯（用 db holdings-add --entry-date 补录）")
            continue
        if p.get("state") == "insufficient_data":
            lines.append(f"- [事实] {p.get('name')}: 行情数据不足，本日不评估")
            continue
        lines.append(f"- [事实] {p.get('name')}: 价格涨幅 {p.get('current_gain_pct')}%"
                     f"（已达最高档 +{p.get('max_rung')}%；raw close 未含分红未复权，"
                     "高分红标的除息日会机械压低涨幅）")

    lines.append("")
    lines.append("## ③ 稀缺价值（周线观察）")
    for code, snap in (payload.get("scarcity") or {}).items():
        if snap is None:
            lines.append(f"- {code}: source_failed")
            continue
        lines.append(f"- [判断] {code}: 状态 {snap.get('state')}"
                     f"（完成周 {snap.get('completed_weeks')}，"
                     f"最近周末交易日 {snap.get('last_week_end')}）")

    if candidates is not None:
        lines.append("")
        lines.append("## 本次候选事件")
        if candidates:
            for ev in candidates:
                lines.append(f"- {ev.kind}: {ev.title}（{ev.occurred_date}）key={ev.key}")
        else:
            lines.append("- 无（状态无变化或均已通知）")

    lines.append(_FOOTER)
    return "\n".join(lines)
