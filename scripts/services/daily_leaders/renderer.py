from __future__ import annotations

from typing import Any

from services.daily_leaders.models import (
    BOARD_TYPES,
    LEADER_ROLES,
    MAX_CONFIRMATION_CANDIDATES,
)
from services.daily_leaders.llm import (
    MAX_RISK_FLAGS,
    is_safe_llm_reason,
    is_safe_llm_risk_flag,
)
from services.daily_leaders.selection import stock_identity_key


KNOWN_LLM_FAILURE_REASONS = {
    "auth_required",
    "disabled",
    "empty_mapping",
    "empty_output",
    "exception",
    "incomplete_mapping",
    "invalid_json",
    "invalid_mapping",
    "missing_status",
    "nonzero_exit",
    "quota_exhausted",
    "timeout",
    "unknown_error",
}
CANDIDATE_LIMIT_FIELDS = {
    "deduped_count",
    "duplicate_trimmed_count",
    "final_count",
    "max_candidates",
    "original_count",
    "review_pool_count",
    "review_pool_trimmed_count",
    "sector_role_trimmed_count",
    "stock_duplicate_trimmed_count",
    "trimmed_count",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _count(value: Any) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _render_candidate_limit(candidate_limit: dict[str, Any]) -> str:
    role_aware_keys = {
        "review_pool_count",
        "sector_role_trimmed_count",
        "stock_duplicate_trimmed_count",
        "final_count",
    }
    if role_aware_keys.intersection(candidate_limit):
        parts: list[str] = []
        fields = (
            ("original_count", "原始候选 {} 条"),
            ("deduped_count", "按股票去重后 {} 条"),
            ("review_pool_count", "LLM 复核池 {} 条"),
            ("sector_role_trimmed_count", "同板块同属性收敛 {} 条"),
            ("stock_duplicate_trimmed_count", "股票重复收敛 {} 条"),
            ("final_count", "最终候选 {} 条"),
            ("max_candidates", "硬上限 {} 条"),
        )
        for key, template in fields:
            count = _count(candidate_limit.get(key)) if key in candidate_limit else None
            if count is not None:
                parts.append(template.format(count))
        return f"> 候选收敛：[判断] {'，'.join(parts)}。" if parts else ""

    trimmed_count = _count(candidate_limit.get("trimmed_count"))
    if not trimmed_count:
        return ""
    parts = []
    original_count = _count(candidate_limit.get("original_count"))
    deduped_count = _count(candidate_limit.get("deduped_count"))
    max_candidates = _count(candidate_limit.get("max_candidates"))
    if original_count is not None:
        parts.append(f"原始候选 {original_count} 条")
    if deduped_count is not None:
        parts.append(f"按股票去重后 {deduped_count} 条")
    if max_candidates is not None:
        parts.append(f"展示前 {max_candidates} 条")
    parts.append(f"已折叠 {trimmed_count} 条")
    return f"> 候选收敛：[判断] {'，'.join(parts)}。"


def _has_verified_role_aware_metadata(proposal: dict[str, Any], leaders: Any) -> bool:
    if not isinstance(leaders, list):
        return False
    candidate_limit = proposal.get("candidate_limit")
    if not isinstance(candidate_limit, dict):
        return False
    if not CANDIDATE_LIMIT_FIELDS.issubset(candidate_limit):
        return False
    if any(
        type(candidate_limit[field]) is not int or candidate_limit[field] < 0
        for field in CANDIDATE_LIMIT_FIELDS
    ):
        return False
    llm_status = proposal.get("llm_status")
    if (
        not isinstance(llm_status, dict)
        or type(llm_status.get("ok")) is not bool
        or llm_status.get("ok") is not False
    ):
        return False
    max_candidates = candidate_limit.get("max_candidates")
    original_count = candidate_limit.get("original_count")
    deduped_count = candidate_limit.get("deduped_count")
    review_pool_count = candidate_limit.get("review_pool_count")
    final_count = candidate_limit.get("final_count")
    if not 1 <= max_candidates <= MAX_CONFIRMATION_CANDIDATES:
        return False
    if final_count != len(leaders):
        return False
    if final_count > max_candidates or final_count > MAX_CONFIRMATION_CANDIDATES:
        return False
    if not original_count >= deduped_count >= review_pool_count >= final_count:
        return False
    if candidate_limit.get("duplicate_trimmed_count") != original_count - deduped_count:
        return False
    if candidate_limit.get("review_pool_trimmed_count") != original_count - review_pool_count:
        return False
    if candidate_limit.get("trimmed_count") != original_count - final_count:
        return False

    seen_stocks: set[str] = set()
    seen_sector_roles: set[tuple[str, str]] = set()
    for item in leaders:
        if not isinstance(item, dict):
            return False
        leader_role = item.get("leader_role")
        if leader_role not in LEADER_ROLES:
            return False
        if item.get("attribute_type") != leader_role:
            return False
        if type(item.get("is_new")) is not bool:
            return False
        if item.get("selection_basis") != "deterministic_fallback":
            return False
        if "board_type" in item and item.get("board_type") not in BOARD_TYPES:
            return False

        stock_key = stock_identity_key(item)
        if not stock_key or stock_key in seen_stocks:
            return False
        seen_stocks.add(stock_key)

        sector = " ".join(str(item.get("sector") or "").split())
        sector_role = (sector, leader_role)
        if not sector or sector_role in seen_sector_roles:
            return False
        seen_sector_roles.add(sector_role)
    return True


def render_markdown(proposal: dict[str, Any]) -> str:
    date = _text(proposal.get("date")).strip() or "未注明日期"
    raw_leaders = proposal.get("top_leaders")
    leaders = raw_leaders if isinstance(raw_leaders, list) else []

    lines = [
        f"# 每日最票候选确认稿 · {date}",
        "",
        "> 风险提示：以下内容仅用于复盘确认与资料整理，不构成买卖建议，不提供价格目标或仓位建议；是否录入由用户人工确认。",
    ]
    llm_status = proposal.get("llm_status") if isinstance(proposal.get("llm_status"), dict) else {}
    if llm_status and not llm_status.get("ok"):
        raw_reason = _text(llm_status.get("reason")).strip()
        reason = raw_reason if raw_reason in KNOWN_LLM_FAILURE_REASONS else "unknown_error"
        verified_metadata = _has_verified_role_aware_metadata(proposal, raw_leaders)
        suffix = (
            "已按确定性板块/属性规则兜底收敛，仍需人工确认。"
            if verified_metadata
            else "旧候选稿未验证新收敛规则，需人工复核。"
        )
        if reason == "disabled":
            warning = f"> LLM 复核已主动跳过：[判断] disabled；{suffix}"
        else:
            warning = f"> LLM 复核未完成：[判断] {reason}；{suffix}"
        lines.extend(
            [
                "",
                warning,
            ]
        )
    candidate_filters = proposal.get("candidate_filters") if isinstance(proposal.get("candidate_filters"), dict) else {}
    min_amount_yi = candidate_filters.get("min_amount_yi")
    try:
        min_amount_yi_float = float(min_amount_yi)
    except (TypeError, ValueError):
        min_amount_yi_float = None
    if min_amount_yi_float is not None:
        lines.extend(
            [
                "",
                f"> 候选过滤：[判断] 已过滤成交额低于 {min_amount_yi_float:.1f} 亿或缺少可验证成交额的个股。",
            ]
        )
    candidate_limit = proposal.get("candidate_limit") if isinstance(proposal.get("candidate_limit"), dict) else {}
    summary = _render_candidate_limit(candidate_limit)
    if summary:
        lines.extend(
            [
                "",
                summary,
            ]
        )
    lines.extend(["", "## 候选条目"])

    skipped = proposal.get("skipped") if isinstance(proposal.get("skipped"), dict) else {}
    if skipped:
        reason = _text(skipped.get("reason")).strip() or "unknown"
        prev_trade_date = _text(skipped.get("prev_trade_date")).strip()
        lines.append("")
        lines.append(f"- 跳过原因：[判断] {reason}")
        if prev_trade_date:
            lines.append(f"- 上一交易日：{prev_trade_date}")

    if not leaders:
        lines.append("")
        lines.append("- 暂无候选。")

    for index, item in enumerate(leaders, start=1):
        stock = _text(item.get("stock")).strip() or "未命名标的"
        sector = _text(item.get("sector")).strip() or "未分类"
        attribute_type = _text(item.get("attribute_type")).strip()
        attribute = _text(item.get("attribute")).strip() or "未注明属性"
        llm_role = _text(item.get("llm_role")).strip()
        leader_role = next(
            (
                role
                for role in (
                    _text(item.get("leader_role")).strip(),
                    llm_role,
                    attribute_type,
                )
                if role in LEADER_ROLES
            ),
            "未注明",
        )
        board_type = next(
            (
                board
                for board in (
                    _text(item.get("board_type")).strip(),
                    attribute_type,
                    llm_role,
                )
                if board in BOARD_TYPES
            ),
            "未注明",
        )
        legacy_attribute = (
            attribute_type
            if attribute_type and attribute_type not in LEADER_ROLES | BOARD_TYPES
            else ""
        )
        selection_basis = _text(item.get("selection_basis")).strip()
        selection_basis_label = {
            "llm": "LLM复核",
            "deterministic_fallback": "确定性兜底",
        }.get(selection_basis, selection_basis or "未注明")
        clarity = _text(item.get("clarity")).strip() or "未注明"
        position = _text(item.get("position")).strip() or "未注明"
        is_new = "是" if item.get("is_new") else "否"
        teacher_alignment = _text(item.get("teacher_alignment")).strip() or "未提及"
        raw_llm_reason = item.get("llm_reason")
        llm_reason = (
            raw_llm_reason.strip()
            if is_safe_llm_reason(raw_llm_reason)
            else ""
        )
        unsafe_llm_reason = bool(raw_llm_reason) and not llm_reason
        llm_rank = _text(item.get("llm_rank")).strip()
        llm_display_role = llm_role if llm_role in LEADER_ROLES else ""
        raw_risk_flags = item.get("risk_flags") or []
        risk_flags_valid = (
            isinstance(raw_risk_flags, list)
            and len(raw_risk_flags) <= MAX_RISK_FLAGS
            and all(is_safe_llm_risk_flag(flag) for flag in raw_risk_flags)
        )
        risk_flags = (
            [flag.strip() for flag in raw_risk_flags]
            if risk_flags_valid
            else []
        )
        unsafe_risk_flags = bool(raw_risk_flags) and not risk_flags_valid

        lines.extend(
            [
                "",
                f"### {index}. {stock}｜{sector}",
                f"- 最票属性：{leader_role}",
                f"- 板型：{board_type}",
                f"- 入选方式：[判断] {selection_basis_label}",
                f"- 属性说明：{attribute}",
                f"- 清晰度：{clarity}",
                f"- 位置：{position}",
                f"- 新候选：{is_new}",
                f"- 老师观点对照：{teacher_alignment}",
            ]
        )
        if legacy_attribute:
            lines.append(f"- 历史属性：{legacy_attribute}")
        if llm_rank and llm_display_role:
            lines.append(f"- LLM裁判：[判断] 排序 {llm_rank} / 最票属性 {llm_display_role}")
        elif llm_rank:
            lines.append(f"- LLM裁判：[判断] 排序 {llm_rank}")
        elif llm_display_role:
            lines.append(f"- LLM裁判：[判断] 最票属性 {llm_display_role}")
        if risk_flags:
            flags = "、".join(risk_flags)
            if flags:
                lines.append(f"- 风险标签：[判断] {flags}")
        elif unsafe_risk_flags:
            lines.append("- 风险标签：[判断] 旧稿内容格式不安全，已隐藏。")
        if llm_reason:
            lines.append(f"- LLM 辅助理由：[判断] {llm_reason}")
        elif unsafe_llm_reason:
            lines.append("- LLM 辅助理由：[判断] 旧稿内容格式不安全，已隐藏。")

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
