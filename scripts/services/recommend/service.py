"""行业推荐服务编排：aggregator → llm_commentary → formatter → pusher。

CLI / API / scheduler 都调这一层。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from .aggregator import AggregateResult, aggregate
from .formatter import render_daily
from .llm_commentary import comment

logger = logging.getLogger(__name__)


@dataclass
class RenderedRecommendation:
    title: str
    markdown: str
    result: AggregateResult


def run_recommend(
    conn: sqlite3.Connection,
    *,
    lookback_days: int,
    top_k: int,
    skip_llm: bool = False,
) -> RenderedRecommendation:
    """执行完整推荐流程，返回 (title, markdown, raw aggregate)。

    skip_llm=True 用于 --dry-run，不调 gemini 直接走规则版 + ⚠️。
    """
    result = aggregate(conn, lookback_days=lookback_days, top_k=top_k)

    commentary: str | None = None
    if not skip_llm and result.sectors:
        payload = _build_llm_payload(result)
        commentary = comment(payload)
        if commentary is None:
            logger.info("LLM 不可用，走规则版降级")

    title, markdown = render_daily(result, commentary)
    return RenderedRecommendation(title=title, markdown=markdown, result=result)


def _build_llm_payload(result: AggregateResult) -> str:
    """把 Top K 排行 + 摘要塞成 JSON prompt 给 gemini。"""
    top_k_data = [
        {
            "sector": s.sector_name,
            "mentions": s.mentions,
            "avg_confidence": round(s.avg_confidence, 2),
            "score": round(s.score, 2),
            "snippets": s.snippets[:3],
        }
        for s in result.sectors[:3]   # 仅 Top 3 进 LLM 节省 token
    ]
    return (
        "你是 A 股短线交易体系的助理。下面是最近"
        f"{result.lookback_days}天用户录入的行业热度排行与原始观点摘要。\n\n"
        "请对 Top 3 行业，每个给一段 50-80 字的「为什么现在值得看 + 核心逻辑」点评。\n"
        "红线：不得给出具体买卖建议、不预测价格目标、不得在数据外做主观推断。\n\n"
        f"数据：\n{json.dumps(top_k_data, ensure_ascii=False, indent=2)}"
    )
