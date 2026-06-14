"""market-timing 盘后只读观察清单渲染（Markdown）。

守红线：全部为 [判断] 派生信号；客观取数标 [事实]；**不预判方向、不出买卖价位、
不给买卖建议**。底分型结构低点/前高作为「已发生的结构事实」呈现，不包装成入场价。
"""
from __future__ import annotations

_FRACTAL_LABEL = {
    "forming": "🟡 底分型成型",
    "confirmed": "🟢 底分型确认(放量中阳突破)",
    "invalid": "⚪ 结构破坏",
    "none": "—",
}


def _fib_cell(sig: dict) -> str:
    hit, near, dc = sig.get("fib_hit"), sig.get("fib_near"), sig.get("fib_day_count")
    if hit is not None:
        return f"🎯 变盘窗口·第{hit}交易日"
    if near is not None:
        return f"⏳ 临近变盘窗口（斐波那契{near}，差{abs((dc or 0) - near)}日）"
    if dc is not None:
        return f"未到变盘窗口（第{dc}日）"
    return "—"


def _pivot_cell(sig: dict) -> str:
    d, t, p = sig.get("swing_pivot_date"), sig.get("swing_pivot_type"), sig.get("swing_pivot_price")
    if not d:
        return "—"
    label = "高点" if t == "high" else ("低点" if t == "low" else t or "")
    return f"{label} {d}（{p}）"


def render_daily(result: dict) -> str:
    """从 scanner.run_daily 结果渲染盘后观察清单 Markdown。"""
    date = result.get("date")
    sigs = result.get("signals", [])
    ctx = result.get("context", {})
    skipped = result.get("skipped", [])

    lines = [f"# 大盘择时观察 · {date} [判断]", ""]
    lines.append("> 以下全部为 **[判断]** 派生信号，**不构成买卖建议、不预测方向、不出价位**。")
    lines.append("> 时间周期=斐波那契变盘窗口；底分型=三 K 结构 + 放量中阳确认。仅供复盘参考。")
    lines.append("")

    if not sigs:
        lines.append(f"_{date} 无可用指数数据（{len(skipped)} 个指数当日无数据），跳过。_")
        return "\n".join(lines)

    # 市场上下文（客观取数）
    lines.append("## 市场上下文 [事实]")
    amt, pct = ctx.get("market_amount_yi"), ctx.get("amount_pctile_20d")
    if amt is not None:
        tail = ""
        if pct is not None:
            tail = f"（近20日分位 {pct:.0%}{'，地量' if pct <= 0.2 else ''}）"
        lines.append(f"- 两市成交额：**{amt:.0f} 亿**{tail}")
    adv, dec = ctx.get("advance"), ctx.get("decline")
    if adv is not None and dec is not None:
        lines.append(f"- 涨跌家数：涨 {adv} / 跌 {dec}")
    ld = ctx.get("limit_down_count")
    if ld is not None:
        lines.append(f"- 跌停家数：{ld}")
    res = result.get("resonance_count", 0)
    lines.append(f"- 共振变盘点：**{res}** 个指数同日命中（共振越多，变盘信号越强 [判断]）")
    lines.append("")

    # 时间周期
    lines.append("## 时间周期 · 变盘点 [判断]")
    lines.append("| 指数 | 起算拐点 [事实] | 距今交易日 | 变盘点判断 |")
    lines.append("|---|---|---|---|")
    for s in sigs:
        lines.append(f"| {s.get('index_name')} | {_pivot_cell(s)} | "
                     f"{s.get('fib_day_count') if s.get('fib_day_count') is not None else '—'} | {_fib_cell(s)} |")
    lines.append("")
    lines.append("> 变盘点=从最近拐点起算的交易日数命中斐波那契数(5/8/13/21/34/55)，时间上大概率方向转折的窗口；"
                 "只标「时间到位」，不预判涨跌，需结合共振/底分型/成交额综合看。")
    lines.append("")

    # 底分型
    lines.append("## 底分型 [判断]")
    lines.append("| 指数 | 状态 | 结构低点 [事实] | 确认日 |")
    lines.append("|---|---|---|---|")
    for s in sigs:
        status = s.get("fractal_status", "none")
        low = (f"{s.get('fractal_low_date')}（{s.get('fractal_low_price')}）"
               if s.get("fractal_low_date") else "—")
        lines.append(f"| {s.get('index_name')} | {_FRACTAL_LABEL.get(status, status)} | {low} | "
                     f"{s.get('fractal_confirm_date') or '—'} |")
    if skipped:
        lines.append("")
        lines.append(f"_当日无数据跳过：{', '.join(i.get('name', i.get('code')) for i in skipped)}_")
    return "\n".join(lines)


def render_signals(rows: list[dict]) -> str:
    """signals 只读查询渲染（紧凑表）。"""
    if not rows:
        return "无 market-timing 信号记录。"
    lines = ["| 日期 | 指数 | 距今日数 | 变盘点 | 底分型 |", "|---|---|---|---|---|"]
    for s in rows:
        fib = (f"🎯{s['fib_hit']}" if s.get("fib_hit") is not None
               else (f"…{s['fib_near']}" if s.get("fib_near") is not None else "—"))
        lines.append(f"| {s.get('trade_date')} | {s.get('index_name')} | "
                     f"{s.get('fib_day_count') if s.get('fib_day_count') is not None else '—'} | "
                     f"{fib} | {_FRACTAL_LABEL.get(s.get('fractal_status'), s.get('fractal_status'))} |")
    return "\n".join(lines)
