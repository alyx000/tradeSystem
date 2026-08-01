"""月线五阳回踩 · 日线指标影子监控 Markdown 渲染。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_STAGE_LABELS = {
    "resonance_observed": "日/周观察条件同时成立（月线见上下文）",
    "daily_reactivated": "日线零轴重回已观察",
    "monthly_seeded": "月线种子成立，等待日线状态",
    "insufficient_history": "日线或动态月线历史不足",
    "blocked": "关键事实缺失",
}


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _macd_line(label: str, payload: Mapping[str, Any]) -> str:
    return (
        f"- {label}：[事实] DIF={_fmt(payload.get('dif'))}；"
        f"DEA={_fmt(payload.get('dea'))}；"
        f"双线零轴上={_fmt(payload.get('above_zero'))}；"
        f"零上向上运行={_fmt(payload.get('bullish_on_zero'))}；"
        f"金叉={_fmt(payload.get('golden_cross'))}"
    )


def _candidate_lines(
    index: int,
    item: Mapping[str, Any],
    *,
    waiting_for_monthly_reclaim: bool = False,
    indeterminate_dynamic_ma5: bool = False,
) -> list[str]:
    monthly = item.get("monthly_evidence") or {}
    daily = item.get("daily_evidence") or {}
    daily_macd = (
        daily.get("daily_macd")
        or daily.get("current_daily")
        or daily.get("daily")
        or {}
    )
    weekly_macd = (
        daily.get("weekly_macd")
        or daily.get("current_weekly")
        or daily.get("weekly")
        or {}
    )
    monthly_macd = monthly.get("monthly_macd") or {}
    dynamic_monthly_ma5 = daily.get("dynamic_monthly_ma5") or {}
    volume = daily.get("volume") or daily.get("volume_confirmation") or {}
    code = item.get("stock_code") or "—"
    name = item.get("stock_name") or ""
    stage = str(item.get("stage") or "unknown")
    mainline_match = item.get("mainline_match")
    if mainline_match is True:
        mainline_text = "命中当前稳定前排代理"
    elif mainline_match is False:
        mainline_text = "未命中当前稳定前排代理"
    else:
        mainline_text = "未知（不作硬剔除）"
    if indeterminate_dynamic_ma5:
        stage_text = "当前动态 5 月线关键事实不足，不进入门内清单"
    elif waiting_for_monthly_reclaim:
        stage_text = "历史完成月种子仍在，目标日动态 5 月线支撑未成立"
    else:
        stage_text = _STAGE_LABELS.get(stage, stage)
    lines = [
        f"### {index}. {name}（{code}）",
        "",
        f"- 观察阶段：[判断] {stage_text}",
        (
            "- 月线种子：[事实] "
            f"连续阳月={_fmt(_first(monthly, 'positive_month_streak', 'bullish_streak'), 0)}；"
            f"回踩月={_first(monthly, 'seed_month', 'pullback_month', 'as_of_month') or '—'}；"
            f"阴月偏好={_fmt(monthly.get('preferred_pullback'))}"
        ),
        (
            "- 完成月种子支撑：[事实] "
            f"low={_fmt(monthly.get('low'))}；"
            f"close={_fmt(monthly.get('close'))}；"
            f"MA5={_fmt(monthly.get('ma5'))}；"
            f"MA10={_fmt(monthly.get('ma10'))}；"
            f"MA20={_fmt(monthly.get('ma20'))}"
        ),
    ]
    if dynamic_monthly_ma5:
        lines.append(
            "- 当前动态 5 月线硬门：[事实·目标日 as-of] "
            f"日期={dynamic_monthly_ma5.get('as_of_date') or '—'}；"
            f"月份={dynamic_monthly_ma5.get('current_month') or '—'}；"
            f"收盘={_fmt(dynamic_monthly_ma5.get('current_close'))}；"
            f"MA5={_fmt(dynamic_monthly_ma5.get('ma5'))}；"
            f"收盘不低于 MA5={_fmt(dynamic_monthly_ma5.get('support_held'))}；"
            f"距离={_fmt_pct(dynamic_monthly_ma5.get('distance_pct'))}；"
            f"连续月份={'、'.join(dynamic_monthly_ma5.get('months') or []) or '—'}；"
            "仅作当前资格门，不新增完成月种子"
        )
        lines.append(
            "- 当前月区间辅助：[事实·固定 T 日 MA5 对照] "
            f"月内最低价={_fmt(dynamic_monthly_ma5.get('current_month_low'))}；"
            "最低价低于本次 T 日动态 MA5="
            f"{_fmt(dynamic_monthly_ma5.get('current_month_low_below_target_asof_ma5'))}"
        )
    lines.extend(
        [
            _macd_line("月线 MACD(12/26/9，完成月)", monthly_macd),
            (
                "- 月线语境：[判断] "
                f"{monthly_macd.get('context') or monthly_macd.get('status') or 'unknown'}"
            ),
            _macd_line("日线 MACD(12/26/9)", daily_macd),
            _macd_line("周线 MACD(12/26/9，严格 as-of)", weekly_macd),
            (
                "- 日线重回事件：[事实] "
                f"首次日期={_first(daily, 'reentry_date', 'first_reentry_date') or '—'}；"
                "当前仍在零轴上="
                f"{_fmt(_first(daily, 'current_above_zero', 'above_zero'))}"
            ),
            (
                "- 量能辅助：[事实·现有系统口径] "
                f"窗口={volume.get('windows') or [5, 13]}；"
                f"阳线={_fmt(_first(volume, 'bullish_bar', 'is_bullish'))}；"
                "同时高于均量线="
                f"{_fmt(_first(volume, 'volume_above_all_prior_mas', 'above_all'))}；"
                f"阳线量能确认={_fmt(volume.get('confirmed'))}；"
                "该窗口不冒充本次转写已确认参数"
            ),
            (
                f"- 板块：{item.get('industry') or '—'}；"
                f"主线代理：[判断] {mainline_text}"
            ),
            "",
        ]
    )
    return lines


def render_monitor(
    summary: Mapping[str, Any],
    *,
    automated_snapshot: bool = False,
) -> str:
    target = summary.get("target_date") or summary.get("requested_date") or "未解析"
    status = str(summary.get("status") or "blocked")
    lines = [
        f"# 月线五阳回踩 · 日线指标影子监控 · {target}",
        "",
        "> [判断] 本报告是只读观察清单，不构成具体买卖建议；"
        "不写数据库状态池、关注池或计划层；"
        + (
            "日频自动任务只推送相对上一完整基线的状态变化。"
            if automated_snapshot
            else "手工 monitor 不自动推送。"
        ),
        "",
        "## 运行收据",
        "",
        f"- 目标交易日：[事实] {target}",
        f"- 月线种子月：[事实] {summary.get('seed_month') or '—'}",
        f"- 运行状态：[事实] {status}",
    ]
    source_status = summary.get("source_status") or {}
    for key, value in source_status.items():
        lines.append(f"- 来源·{key}：[事实] {value}")
    for key, value in (summary.get("counts") or {}).items():
        lines.append(f"- 计数·{key}：[事实] {value}")

    if status == "blocked":
        lines.extend(
            [
                "",
                "## 阻断",
                "",
                f"- [事实] {summary.get('error') or '关键事实不完整'}",
                "- [判断] 该状态不等于真实空候选。",
            ]
        )
    elif status == "partial":
        counts = summary.get("counts") or {}
        reasons = []
        if counts.get("blocked"):
            blocked_parts = []
            if counts.get("blocked_price_shape"):
                blocked_parts.append(
                    f"月内复权形态不可认证 {counts['blocked_price_shape']} 只"
                )
            if counts.get("blocked_month_gap"):
                blocked_parts.append(f"月份缺口 {counts['blocked_month_gap']} 只")
            if counts.get("blocked_missing_latest_month"):
                entire_window = counts.get("blocked_missing_entire_window", 0)
                entire_detail = (
                    f"（其中全窗口无月线 {entire_window} 只）"
                    if entire_window
                    else ""
                )
                blocked_parts.append(
                    "缺最新完成月 "
                    f"{counts['blocked_missing_latest_month']} 只"
                    f"{entire_detail}"
                )
            if counts.get("blocked_other"):
                blocked_parts.append(f"其他月线事实异常 {counts['blocked_other']} 只")
            blocked_detail = (
                f"（{'；'.join(blocked_parts)}）" if blocked_parts else ""
            )
            reasons.append(f"月线不可判 {counts['blocked']} 只{blocked_detail}")
        if counts.get("monthly_seed_truncated"):
            reasons.append(f"校准截断 {counts['monthly_seed_truncated']} 只种子")
        if counts.get("daily_insufficient"):
            reasons.append(
                f"日线或动态月线历史不足 {counts['daily_insufficient']} 只"
            )
        if counts.get("indeterminate_current_month_ma5"):
            reasons.append(
                "动态 5 月线无法判定 "
                f"{counts['indeterminate_current_month_ma5']} 只"
            )
        if summary.get("data_issues"):
            reasons.append(f"目标日/复权异常 {len(summary['data_issues'])} 只")
        lines.extend(
            [
                "",
                "> [事实] 本次覆盖不完整"
                + (f"（{'；'.join(reasons)}）" if reasons else "")
                + "；完整候选仍可展示，但不能把缺失票视为规则未命中，"
                "也不能把其他未知票视为明确不匹配。",
            ]
        )

    context = summary.get("mainline_context") or {}
    lines.extend(
        [
            "",
            "## 板块背景",
            "",
            f"- 稳定前排代理：[判断] {'、'.join(context.get('sectors') or []) or '缺失'}",
            f"- 快照日期：[事实] {'、'.join(context.get('source_dates') or []) or '—'}",
            f"- 行业映射状态：[事实] {context.get('industry_status') or 'unknown'}",
            f"- 行业映射口径：[事实] {context.get('industry_semantics') or '—'}",
            "",
            "## 当前动态 5 月线门内观察清单",
            "",
        ]
    )
    candidates = summary.get("candidates") or []
    waiting_monthly_reclaim = summary.get("waiting_monthly_reclaim") or []
    if candidates:
        for index, item in enumerate(candidates, start=1):
            lines.extend(_candidate_lines(index, item))
    elif status == "complete":
        lines.append(
            "- [事实] certified 月线种子与目标日数据均完整，"
            "本次没有通过当前动态 5 月线硬门的观察项。"
        )
    else:
        lines.append("- [事实] 当前不能确认真实空候选。")

    lines.extend(["", "## 历史种子 · 等待重新站回动态 5 月线", ""])
    if waiting_monthly_reclaim:
        lines.append(
            "- [事实] 以下标的保留完成月种子审计，但目标日收盘低于动态 5 月线，"
            "不计入上方当前观察清单。"
        )
        lines.append("")
        for index, item in enumerate(waiting_monthly_reclaim, start=1):
            lines.extend(
                _candidate_lines(
                    index,
                    item,
                    waiting_for_monthly_reclaim=True,
                )
            )
    elif status == "blocked":
        lines.append("- [事实] 关键事实被阻断，不能判断等待名单。")
    else:
        lines.append("- [事实] 无。")

    lines.extend(["", "## 当前动态 5 月线无法判定", ""])
    indeterminate = summary.get("indeterminate_current_month_ma5") or []
    if indeterminate:
        lines.append(
            "- [事实] 以下标的缺少足够的连续月份或动态 MA5 证据，"
            "不进入门内观察清单，也不解释为已经失守。"
        )
        lines.append("")
        for index, item in enumerate(indeterminate, start=1):
            lines.extend(
                _candidate_lines(
                    index,
                    item,
                    indeterminate_dynamic_ma5=True,
                )
            )
    elif status == "blocked":
        lines.append("- [事实] 关键事实被阻断，不能判断该名单。")
    else:
        lines.append("- [事实] 无。")

    issues = summary.get("data_issues") or []
    if issues:
        lines.extend(["", "## 单票数据异常", ""])
        for item in issues:
            source = item.get("source") or {}
            lines.append(
                f"- {item.get('stock_name') or ''}（{item.get('stock_code') or '—'}）："
                f"[事实] {source.get('status') or item.get('stage') or 'blocked'}；"
                f"{source.get('error') or item.get('error') or '关键字段缺失'}"
            )

    st_excluded = summary.get("st_excluded_items") or []
    if st_excluded:
        lines.extend(["", "## 目标日 ST 身份排除", ""])
        for item in st_excluded:
            lines.append(
                f"- {item.get('stock_name') or ''}"
                f"（{item.get('stock_code') or '—'}）："
                "[事实] 目标日 ST 身份成立，不进入技术观察清单。"
            )

    unresolved = summary.get("unresolved_rules") or []
    lines.extend(["", "## 暂不自动化的原文规则", ""])
    for item in unresolved:
        if isinstance(item, Mapping):
            label = item.get("rule") or item.get("name") or "未解析规则"
            reason = item.get("reason") or item.get("status") or "定义不足"
            lines.append(f"- [老师观点·待核对] {label}：{reason}")
        else:
            lines.append(f"- [老师观点·待核对] {item}")
    if not unresolved:
        lines.append("- [事实] 无")

    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- [老师观点] 前一完成月之前连续至少 5 根阳月，随后完成月回踩并守住月 MA5；"
            "之后价格要重新站回当下 5 月线，日线 MACD 双线重新回到零轴上方后，"
            "再结合量能与周线观察。",
            "- [事实] 五阳、MA5/10/20、日/周 MACD 与量能均由程序计算；"
            "五阳和初始种子支撑只认 certified 完成月；"
            "目标月仅以 T 日前复权坐标下的前四个月月末收盘与 T 日收盘计算动态 MA5，"
            "作为当前资格硬门，不用于新增完成月种子。",
            "- [事实] 等待名单是无状态的同目标月日频快照；"
            "月度翻页后会按新的最新完成月重建种子，"
            "不会在缺少保留期限定义时跨月持久化旧种子。",
            "- [判断] 板块稳定前排、月线反转/主升与多周期共振只是观察标签，"
            "不是操作指令。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
