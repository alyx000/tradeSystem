"""Markdown 渲染：把 AggregateResult + LLM 大盘判断编排成钉钉 markdown（三段结构）。

三段各归各位，避免大盘观点冒充行业逻辑：
- 📌 近期大盘判断：LLM 提炼的 bullets；LLM 不可用 / 命中红线 → 降级展示最近 3 条原始 core_view。
- 🔥 行业热度榜：teacher_notes 提及次数排名（仅名次 + 提及数）。
- 💡 有具体催化的行业：industry_info 的真行业逻辑。

红线扫描只作用于 LLM 输出（生成内容）；原始 core_view / content 是用户录入的事实，不扫。
"""
from __future__ import annotations

import logging

from .aggregator import AggregateResult

logger = logging.getLogger(__name__)

# 红线关键词：LLM 输出中任一命中 → 整段 LLM 输出丢弃，降级到原始 core_view
REDLINE_KEYWORDS = ("买入", "卖出", "目标价", "必涨", "满仓", "建仓", "止损位", "加仓", "空仓")

MARKET_VIEW_FALLBACK_MAX = 3   # 降级展示原始大盘观点的最大条数


def _scan_redline(text: str) -> str | None:
    """扫描文本是否含红线关键词；返回命中的关键词，未命中返回 None。"""
    for kw in REDLINE_KEYWORDS:
        if kw in text:
            return kw
    return None


def render_daily(result: AggregateResult, commentary: str | None) -> tuple[str, str]:
    """渲染日报 / 周报 markdown（daily、weekly 共用此函数）。返回 (title, markdown_body)。

    红线扫描范围（产品决策，勿擅自扩大）：**只扫 LLM 生成的 commentary**，
    不扫降级展示的原始 core_view、也不扫 catalysts 的 industry_info.content。
    原因：原文是用户录入的老师观点 / 新闻事实（事实层），不是 AI 生成的买卖建议；
    对其做红线词匹配会误杀审慎表述（如「右肩风险，不宜建仓」会因含「建仓」被丢）。
    红线约束的对象是 AI 的『生成』，不是用户记录的事实。
    （codex review 曾建议对降级原文也扫描，经评估按上述理由不采纳。）
    """
    # 红线扫描：仅作用于 LLM 输出；命中即丢弃 LLM 段，降级到原始大盘观点
    if commentary is not None:
        hit = _scan_redline(commentary)
        if hit:
            logger.warning("LLM 大盘判断命中红线关键词 '%s'，整段丢弃，降级原始观点", hit)
            commentary = None

    title = f"📊 行业推荐 · {result.window_end}（近 {result.lookback_days} 日 Top {len(result.sectors)}）"

    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"**窗口**：{result.window_start} → {result.window_end}")
    lines.append("")

    # ── 📌 近期大盘判断 ──
    lines.append("### 📌 近期大盘判断")
    lines.append("")
    if commentary:
        lines.append(commentary)
    elif result.market_views:
        for mv in result.market_views[:MARKET_VIEW_FALLBACK_MAX]:
            lines.append(f"- {mv.text}")
    else:
        lines.append(f"近 {result.lookback_days} 日暂无大盘观点录入。")
    lines.append("")

    # ── 🔥 行业热度榜（按老师提及） ──
    lines.append("### 🔥 行业热度榜（按老师提及）")
    lines.append("")
    if result.sectors:
        for idx, s in enumerate(result.sectors, start=1):
            lines.append(f"{idx}. {s.sector_name} · 提及 {s.mentions} 次")
    else:
        lines.append(f"近 {result.lookback_days} 日无板块提及。")
    lines.append("")

    # ── 💡 有具体催化的行业 ──
    lines.append("### 💡 有具体催化的行业")
    lines.append("")
    if result.catalysts:
        for c in result.catalysts:
            conf = f"（{c.confidence}）" if c.confidence else ""
            lines.append(f"- {c.sector_name}{conf}：{c.content}")
    else:
        lines.append(f"近 {result.lookback_days} 日无具体催化录入。")

    return title, "\n".join(lines)
