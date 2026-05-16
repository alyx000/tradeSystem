"""Markdown 渲染：把 AggregateResult + LLM 点评编排成钉钉 markdown。

按 TDD 增量构建：F1 LLM 成功 → F2 LLM None 降级 → F3 红线命中过滤。
"""
from __future__ import annotations

import logging

from .aggregator import AggregateResult

logger = logging.getLogger(__name__)

# 红线关键词：LLM 输出中任一命中 → 整段 LLM 输出丢弃，formatter 回退 F2 路径
REDLINE_KEYWORDS = ("买入", "卖出", "目标价", "必涨", "满仓", "建仓", "止损位", "加仓", "空仓")


def _scan_redline(text: str) -> str | None:
    """扫描文本是否含红线关键词；返回命中的关键词，未命中返回 None。"""
    for kw in REDLINE_KEYWORDS:
        if kw in text:
            return kw
    return None


def render_daily(result: AggregateResult, commentary: str | None) -> tuple[str, str]:
    """渲染日报 markdown。返回 (title, markdown_body)。"""
    # 红线扫描：命中即丢弃 LLM 段，回退 F2 ⚠️ 路径
    if commentary is not None:
        hit = _scan_redline(commentary)
        if hit:
            logger.warning("LLM commentary 命中红线关键词 '%s'，整段丢弃", hit)
            commentary = None

    title = f"📊 行业推荐 · {result.window_end}（近 {result.lookback_days} 日 Top {len(result.sectors)}）"

    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"**窗口**：{result.window_start} → {result.window_end}")
    lines.append("")
    lines.append("### 热度排行")
    lines.append("")
    for idx, s in enumerate(result.sectors, start=1):
        lines.append(f"**{idx}. {s.sector_name}** · 提及 {s.mentions} 次 · score `{s.score:.2f}`")
        for snippet in s.snippets[:3]:
            lines.append(f"  - {snippet}")
        lines.append("")

    # LLM 点评段（不可用时降级到 ⚠️ 标签）
    if commentary is None:
        lines.append("### AI 点评")
        lines.append("")
        lines.append("⚠️ AI 点评暂不可用，本次仅规则版排行。")
    else:
        lines.append("### AI 点评")
        lines.append("")
        lines.append(commentary)

    return title, "\n".join(lines)
