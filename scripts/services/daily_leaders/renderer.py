from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def render_markdown(proposal: dict[str, Any]) -> str:
    date = _text(proposal.get("date")).strip() or "未注明日期"
    leaders = proposal.get("top_leaders") or []

    lines = [
        f"# 每日最票候选确认稿 · {date}",
        "",
        "> 风险提示：以下内容仅用于复盘确认与资料整理，不构成买卖建议，不提供价格目标或仓位建议；是否录入由用户人工确认。",
    ]
    llm_status = proposal.get("llm_status") if isinstance(proposal.get("llm_status"), dict) else {}
    if llm_status and not llm_status.get("ok"):
        reason = _text(llm_status.get("reason")).strip() or "unknown"
        lines.extend(
            [
                "",
                f"> LLM裁判未生效：[判断] {reason}；当前排序仅为数据候选层，请勿按 LLM 结果确认。",
            ]
        )
    lines.extend(["", "## 候选条目"])

    if not leaders:
        lines.append("")
        lines.append("- 暂无候选。")

    for index, item in enumerate(leaders, start=1):
        stock = _text(item.get("stock")).strip() or "未命名标的"
        sector = _text(item.get("sector")).strip() or "未分类"
        attribute_type = _text(item.get("attribute_type")).strip() or "未注明类型"
        attribute = _text(item.get("attribute")).strip() or "未注明属性"
        clarity = _text(item.get("clarity")).strip() or "未注明"
        position = _text(item.get("position")).strip() or "未注明"
        is_new = "是" if item.get("is_new") else "否"
        teacher_alignment = _text(item.get("teacher_alignment")).strip() or "未提及"
        llm_reason = _text(item.get("llm_reason")).strip()
        llm_rank = _text(item.get("llm_rank")).strip()
        llm_role = _text(item.get("llm_role")).strip()
        risk_flags = item.get("risk_flags") or []

        lines.extend(
            [
                "",
                f"### {index}. {stock}｜{sector}",
                f"- 属性：{attribute_type} / {attribute}",
                f"- 清晰度：{clarity}",
                f"- 位置：{position}",
                f"- 新候选：{is_new}",
                f"- 老师观点对照：{teacher_alignment}",
            ]
        )
        if llm_rank or llm_role:
            lines.append(f"- LLM裁判：[判断] 排序 {llm_rank or '-'} / 角色 {llm_role or '-'}")
        if risk_flags:
            flags = "、".join(_text(flag).strip() for flag in risk_flags if _text(flag).strip())
            if flags:
                lines.append(f"- 风险标签：[判断] {flags}")
        if llm_reason:
            lines.append(f"- LLM 辅助理由：[判断] {llm_reason}")

        lines.append("- 证据：")
        evidence = item.get("evidence") or []
        if not evidence:
            lines.append("  - [判断] 暂无结构化证据，需人工复核。")
        for evidence_item in evidence:
            label = _text(evidence_item.get("label")).strip() or "[判断]"
            text = _text(evidence_item.get("text")).strip() or "未填写"
            lines.append(f"  - {label} {text}")

    lines.extend(
        [
            "",
            "## 确认方式",
            "",
            "- 可回复：确认，全部录入",
            "- 可回复：确认录入第 1 条",
            "- 可回复：跳过，并说明原因",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
