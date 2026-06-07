"""Antigravity 建议合成 + 红线三级护栏。复用 build_antigravity_runner 与 REDLINE_KEYWORDS。

红线只扫 LLM 生成内容，不扫认知事实（对齐红线约束『生成』非『事实』）。
L1 调用/解析失败 → 模板兜底；L2 逐条 bullet 命中红线丢弃、空段兜底；L3 no_llm 纯结构化。
"""
from __future__ import annotations

import logging
from collections import Counter

from services.recommend.formatter import REDLINE_KEYWORDS  # 红线词单一真源

logger = logging.getLogger(__name__)


def _scan_redline(text: str) -> str | None:
    for kw in REDLINE_KEYWORDS:
        if kw in (text or ""):
            return kw
    return None


def _build_prompt() -> str:
    return (
        "你是交易认知复盘助理。下面 JSON 数组每条是一条**真实**沉淀的交易认知"
        "（title 标题 / category 分类 / pattern 复用范式 / heat 近期印证次数 / "
        "consensus 老师共识数 / is_new 是否本期新捕获）。"
        "请基于这些认知，输出两类**体系级**建议，返回严格 JSON："
        '{"system_suggestions": ["...","..."], "direction_suggestions": ["...","..."]}。'
        "system_suggestions：对交易体系/框架的建议（认知结构、验证机制、复盘节奏等），2-3 条，每条≤40 字；"
        "direction_suggestions：下一步研究/跟踪方向（聚焦哪类认知、补哪类验证），2-3 条，每条≤40 字。"
        "**严禁出现具体标的买卖点、价格目标、仓位操作词（买入/卖出/加仓/满仓/建仓/止损位/空仓/必涨）；"
        "只到体系与方向层，不做个股操作建议。严禁臆造输入中未出现的认知或事实。**"
    )


def _payload(scored) -> list[dict]:
    return [
        {"title": s.title, "category": s.category, "pattern": s.pattern or "",
         "heat": s.heat, "consensus": s.consensus, "is_new": s.is_new}
        for s in scored
    ]


def _clean_bullets(raw) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        # 只接受字符串；LLM 可能返 [None] / [{...}] 等，str(item) 会渲染出 "None"/dict 串
        # 且绕过红线护栏（codex 中项）→ 非字符串直接丢弃
        if not isinstance(item, str):
            logger.warning("[cognition-digest] 建议条目非字符串(%s)，丢弃", type(item).__name__)
            continue
        text = item.strip()
        if not text:
            continue
        hit = _scan_redline(text)
        if hit:
            logger.warning("[cognition-digest] 建议命中红线 '%s'，丢弃该条", hit)
            continue
        out.append(text)
    return out


def _template_suggestions(scored) -> dict:
    if not scored:
        return {"system_suggestions": [], "direction_suggestions": [], "_llm_used": False}
    cat_heat: Counter = Counter()
    for s in scored:
        cat_heat[s.category] += s.heat
    top_cat = cat_heat.most_common(1)[0][0] if cat_heat else "—"
    new_cnt = sum(1 for s in scored if s.is_new)
    return {
        "system_suggestions": [
            f"本期热度最高方向为「{top_cat}」，建议在该方向加强认知验证与复盘沉淀",
            f"本期新捕获 {new_cnt} 条候选认知，建议安排盘后验证补足事实源",
        ],
        "direction_suggestions": [
            f"优先跟踪「{top_cat}」相关认知的后续印证与失效边界",
            "对仅单老师提出（共识=1）的认知保持观察，等待跨老师共识再升级",
        ],
        "_llm_used": False,
    }


def generate_suggestions(scored, *, no_llm: bool = False, llm_runner=None) -> dict:
    """返回 {system_suggestions, direction_suggestions, _llm_used}。任何降级走模板兜底。"""
    if no_llm or llm_runner is None or not scored:
        return _template_suggestions(scored)
    try:
        result = llm_runner(_build_prompt(), _payload(scored))
    except Exception as e:  # noqa: BLE001  与 research_digest narrator 一致：任何异常全量降级
        logger.warning("[cognition-digest] narrator LLM 异常，模板兜底: %s", e)
        return _template_suggestions(scored)
    # L1 结构严格校验：必须 dict 且两键都在且都是 list；任一不符 → 整段模板兜底（不部分采纳）
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("system_suggestions"), list)
        or not isinstance(result.get("direction_suggestions"), list)
    ):
        logger.info("[cognition-digest] narrator 结构不符(L1：缺键/非列表)，模板兜底")
        return _template_suggestions(scored)

    # L2 逐条红线清洗；某段全被清空 → 该段模板兜底
    system = _clean_bullets(result["system_suggestions"])
    direction = _clean_bullets(result["direction_suggestions"])
    tmpl = _template_suggestions(scored)
    # _llm_used 为整体单标记：任一段保留了 LLM bullet 即 True。混合段（一段 LLM + 一段红线清空后
    # 走模板）不细分 per-section source —— 有意取舍：per-section 审计收益边际，徒增 renderer/契约复杂度
    # （YAGNI；codex 轻微项，defer）。如未来需要精确审计，再加 system_llm_used/direction_llm_used。
    return {
        "system_suggestions": system or tmpl["system_suggestions"],
        "direction_suggestions": direction or tmpl["direction_suggestions"],
        "_llm_used": bool(system or direction),
    }
