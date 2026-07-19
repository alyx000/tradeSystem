"""趋势主升观察清单 Markdown 渲染（盘后只读）。

输入 = scanner.run_daily 的 summary + 一个 conn（回查 pool 富化名称/在池天数/退出原因）。
输出 = 移动端友好的观察清单：**全部标 [判断]、守红线**——不出价位、不给买卖建议、不写计划层。
临盘买点（低开/T+0）留给用户手工，渲染层只呈现「客观信号命中与否」的判断标记。
"""
from __future__ import annotations

import sqlite3

from services.trend_leader import pool

# 在池信号 → 展示标签（顺序即列顺序）。
# 红线：只描述客观技术状态，禁用可操作买卖动作词（低吸/买点/了结/见顶/止盈…）；
# 是否买卖由用户自行判断，渲染层不暗示动作。
_SIGNAL_LABELS = [
    ("shrink_pullback_buy", "缩量阴线回踩"),
    ("near_ma5", "贴MA5"),
    ("overheat", "远离MA5(乖离过大)"),
]

_REDLINE = ("> 盘后只读观察清单 · 全部为 [判断] · "
            "不构成买卖建议、不含价位、不预测点位、不写交易计划层。")


def _hit(flag) -> str:
    return "✅" if flag else "—"


def _llm_status_text(meta: dict) -> str | None:
    status = meta.get("status")
    if status == "fallback_l2":
        reason = meta.get("reason") or "unknown"
        if reason in {"invalid_output", "parse_failed"}:
            return f"LLM输出非法，概念分支已关闭（原因：{reason}）"
        return f"LLM调用失败，概念分支已关闭（原因：{reason}）"
    if status == "fallback":
        return "LLM不可用，已回退机械概念分支（旧状态）"
    if status == "disabled":
        return "人工禁用 LLM，使用机械概念分支"
    if status == "ok" and not (meta.get("accepted_concepts") or []):
        return "LLM未确认概念分支"
    if status == "ok":
        return "LLM概念过滤成功"
    if status == "skipped_empty_concepts":
        return "无可供 LLM 过滤的机械概念"
    return None


def _main_sector_label(summary: dict) -> str:
    metadata_keys = {
        "main_sector_window",
        "main_sector_required_hits",
        "main_sector_snapshot_count",
        "main_sector_source_date",
        "main_sector_status",
    }
    if metadata_keys.isdisjoint(summary):
        degraded = "（当日主线缺失，已回退最近一日）" if summary.get("degraded_main") else ""
        return f"申万二级 Top-K∪手工{degraded}"

    window = summary.get("main_sector_window") or 3
    required = summary.get("main_sector_required_hits") or 1
    label = f"申万二级近{window}个有效快照 Top-K 至少{required}次∪手工"

    status = summary.get("main_sector_status")
    if status is None:  # 兼容旧 summary，避免历史调用方立刻失去降级提示。
        status = "fallback" if summary.get("degraded_main") else "exact"
    source_date = summary.get("main_sector_source_date")
    snapshot_count = summary.get("main_sector_snapshot_count")
    notes = []
    if status == "exact":
        notes.append("目标日快照可用")
    elif status == "fallback":
        if source_date:
            notes.append(f"目标日不可用，使用{source_date}快照（已回退）")
        else:
            notes.append("目标日不可用，已回退最近有效快照")
    elif status == "missing":
        notes.append("无可用集中度快照，仅保留手工板块")
    if (
        status != "missing"
        and isinstance(snapshot_count, int)
        and snapshot_count < window
    ):
        notes.append(f"历史仅{snapshot_count}条有效快照")
    return f"{label}{''.join(f'（{note}）' for note in notes)}"


def render_daily(conn: sqlite3.Connection, summary: dict) -> str:
    """渲染当日趋势主升观察清单。"""
    active = {r["code"]: r for r in pool.list_pool(conn, status="active")}
    exited = {r["code"]: r for r in pool.list_pool(conn, status="exited")}
    date = summary.get("date", "")

    lines: list[str] = [f"# 趋势主升观察清单 · {date}  [判断]", "", _REDLINE, ""]

    # 漏斗概览
    main_sectors = summary.get("main_sectors") or []
    main_concepts = summary.get("main_concepts") or []
    main_line = summary.get("main_line") or "l2"
    llm_meta = summary.get("mainline_llm") or {}
    main_sector_label = _main_sector_label(summary)
    # entered=首见入池；refreshed=同日重跑 / 推送失败重试时同一票再命中（pool 已 active，仍是今日入池）。
    # 概览计数与下方表格统一用合并集，否则重试报告会出现「今日新入池：0」却列出该票的自相矛盾。
    todays = (summary.get("entered") or []) + (summary.get("refreshed") or [])
    lines += [
        "## 漏斗概览",
        f"- 当日涨停：{summary.get('limit_up', 0)}",
        f"- 主线口径：{main_line}",
        f"- 主线板块（{main_sector_label}）：{'、'.join(main_sectors) or '（无）'}",
        f"- 主线分支（同花顺概念）：{'、'.join(main_concepts) or '（无）'}",
        f"- 加速∩主线候选（涨停∪双创15%）：{summary.get('candidates', 0)}",
        f"- 今日新入池：{len(todays)} · 在池退出：{len(summary.get('exited') or [])}",
    ]
    llm_status_text = _llm_status_text(llm_meta) if main_line == "hybrid" else None
    if llm_status_text:
        lines.append(f"- LLM主线过滤：{llm_status_text}")
    lines.append("")

    # 今日新入池
    lines += ["## 今日新入池（首次加速 + 主线缓涨）[判断]"]
    if not todays:
        lines += ["今日无新入池。", ""]
    else:
        main_l2 = set(summary.get("main_sectors") or [])
        lines += ["| 代码 | 名称 | 申万二级 | 首次加速日 | 触发 |", "| --- | --- | --- | --- | --- |"]
        for code in todays:
            r = active.get(code, {})
            sig = r.get("last_signal") or {}
            sw_l2 = r.get("sw_l2", "")
            branch = sig.get("branch_concepts") or []
            # 经概念分支入主线（二级不在主线 Top-K）时标注命中概念，说明它为何算主线。
            sw_disp = sw_l2 if (sw_l2 in main_l2 or not branch) else f"{sw_l2}·分支:{'/'.join(branch)}"
            # 触发区分：涨停 vs 双创15%加速（双创涨15%+未到全涨停）；老数据无此字段默认涨停。
            lines.append(
                f"| {code} | {r.get('name', '')} | {sw_disp} | "
                f"{r.get('first_limit_date', '')} | {sig.get('entry_trigger', '涨停')} |")
        lines.append("")

    # 在池信号（回踩/见顶提示）
    lines += ["## 在池信号（缩量回踩 / 贴MA5 / 乖离状态）[判断]"]
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
    lines += ["## 今日退池（趋势破坏）[判断]"]
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
