"""趋势主升观察清单 Markdown 渲染（盘后只读）。

输入 = scanner.run_daily 的 summary + 一个 conn（回查 pool 富化名称/在池天数/退出原因）。
输出 = 移动端友好的观察清单：**全部标 [判断]、守红线**——不出价位、不给买卖建议、不写计划层。
临盘买点（低开/T+0）留给用户手工，渲染层只呈现「客观信号命中与否」的判断标记。
"""
from __future__ import annotations

import sqlite3

from services.trend_leader import pool

# 在池信号 → 展示标签（顺序即列顺序）
_SIGNAL_LABELS = [
    ("shrink_pullback_buy", "缩量阴线回踩"),
    ("near_ma5", "贴MA5"),
    ("overheat", "远离MA5(了结提示)"),
]

_REDLINE = ("> 盘后只读观察清单 · 全部为 [判断] · "
            "不构成买卖建议、不含价位、不写交易计划层；临盘买点请自行判断。")


def _hit(flag) -> str:
    return "✅" if flag else "—"


def render_daily(conn: sqlite3.Connection, summary: dict) -> str:
    """渲染当日趋势主升观察清单。"""
    active = {r["code"]: r for r in pool.list_pool(conn, status="active")}
    exited = {r["code"]: r for r in pool.list_pool(conn, status="exited")}
    date = summary.get("date", "")

    lines: list[str] = [f"# 趋势主升观察清单 · {date}  [判断]", "", _REDLINE, ""]

    # 漏斗概览
    main_sectors = summary.get("main_sectors") or []
    degraded = "（当日主线缺失,已回退最近一日）" if summary.get("degraded_main") else ""
    lines += [
        "## 漏斗概览",
        f"- 当日涨停：{summary.get('limit_up', 0)}",
        f"- 主线板块（Top-K∪手工）{degraded}：{'、'.join(main_sectors) or '（无）'}",
        f"- 涨停∩主线候选：{summary.get('candidates', 0)}",
        f"- 今日新入池：{len(summary.get('entered') or [])} · "
        f"在池退出：{len(summary.get('exited') or [])}",
        "",
    ]

    # 今日新入池
    lines += ["## 今日新入池（首次涨停加速 + 主线缓涨）[判断]"]
    entered = summary.get("entered") or []
    if not entered:
        lines += ["今日无新入池。", ""]
    else:
        lines += ["| 代码 | 名称 | 申万二级 | 首次涨停日 |", "| --- | --- | --- | --- |"]
        for code in entered:
            r = active.get(code, {})
            lines.append(
                f"| {code} | {r.get('name', '')} | {r.get('sw_l2', '')} | "
                f"{r.get('first_limit_date', '')} |")
        lines.append("")

    # 在池信号（回踩/见顶提示）
    lines += ["## 在池信号（回踩低吸 / 见顶了结提示）[判断]"]
    signals = summary.get("in_pool_signals") or []
    if not signals:
        lines += ["在池无新信号。", ""]
    else:
        header = "| 代码 | 名称 | 在池天数 | " + " | ".join(lbl for _, lbl in _SIGNAL_LABELS) + " |"
        lines += [header, "| " + " | ".join(["---"] * (3 + len(_SIGNAL_LABELS))) + " |"]
        for sig in signals:
            code = sig.get("code", "")
            r = active.get(code, {})
            marks = " | ".join(_hit(sig.get(key)) for key, _ in _SIGNAL_LABELS)
            lines.append(
                f"| {code} | {r.get('name', '')} | {r.get('days_in_pool', '')} | {marks} |")
        lines.append("")

    # 今日退池（趋势破坏）
    lines += ["## 今日退池（趋势破坏）"]
    exited_codes = summary.get("exited") or []
    if not exited_codes:
        lines += ["今日无退池。", ""]
    else:
        lines += ["| 代码 | 名称 | 退出原因 |", "| --- | --- | --- |"]
        for code in exited_codes:
            r = exited.get(code, {})
            lines.append(f"| {code} | {r.get('name', '')} | {r.get('exit_reason', '')} |")
        lines.append("")

    # 运营提示（仅在有失败时出现，区分「链路断」与「今日无候选」）
    source_errors = summary.get("source_errors") or []
    data_errors = summary.get("data_errors") or []
    if source_errors or data_errors:
        lines += ["## 运营提示"]
        if source_errors:
            lines.append(f"- 数据源失败：{'、'.join(source_errors)}（发现链路受影响，非「今日无候选」）")
        if data_errors:
            lines.append(f"- 个股行情缺失：{'、'.join(data_errors)}（未推进/未退池，待补采）")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_pool(rows: list[dict]) -> str:
    """渲染观察池只读清单（trend-leader pool）。"""
    if not rows:
        return "（观察池为空）\n"
    lines = ["| 代码 | 名称 | 申万二级 | 入池日 | 在池天数 | 状态 | 退出原因 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(
            f"| {r.get('code', '')} | {r.get('name', '')} | {r.get('sw_l2', '')} | "
            f"{r.get('entered_date', '')} | {r.get('days_in_pool', '')} | "
            f"{r.get('status', '')} | {r.get('exit_reason') or '—'} |")
    return "\n".join(lines) + "\n"
