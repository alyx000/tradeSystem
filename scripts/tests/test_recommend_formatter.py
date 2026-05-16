"""TDD: 行业推荐 markdown formatter 单测

按 plan 第 ⑪ 节 F1-F3 三场景顺序驱动 formatter 实现。
使用直接构造 AggregateResult 的方式，不依赖 SQL（fixture 形状稳定）。
"""
from __future__ import annotations

import pytest

from services.recommend.aggregator import AggregateResult, SectorScore


def _sample_result(*, sectors: list[tuple[str, int, float]] = None) -> AggregateResult:
    """构造一个 AggregateResult 用于 formatter 测试。
    sectors: [(sector_name, mentions, score), ...] 按 score 倒序。
    """
    if sectors is None:
        sectors = [("半导体", 4, 3.76), ("军工", 3, 1.90), ("AI", 1, 1.00)]
    return AggregateResult(
        sectors=[
            SectorScore(
                sector_name=name,
                mentions=mentions,
                avg_confidence=0.5,
                recency_decay=1.0,
                score=score,
                latest_date="2026-05-16",
                snippets=[f"{name} 摘要 1", f"{name} 摘要 2"],
            )
            for name, mentions, score in sectors
        ],
        window_start="2026-05-13",
        window_end="2026-05-16",
        lookback_days=3,
    )


# ─────────────────────────────────────────────────────────────
# F1: LLM 成功 → 输出含 Top K + LLM 段，无 ⚠️
# ─────────────────────────────────────────────────────────────
def test_f1_render_daily_with_llm_commentary(tmp_path):
    result = _sample_result()
    commentary = "半导体逻辑：周期回升。军工逻辑：订单兑现。AI 逻辑：算力溢出。"

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary)

    # 标题含日期窗口与 Top K
    assert "2026-05-16" in title or "推荐" in title
    # markdown 含每个行业名
    for name in ["半导体", "军工", "AI"]:
        assert name in markdown
    # markdown 含 LLM 段（点评原文）
    assert "周期回升" in markdown
    # 无降级警告
    assert "⚠" not in markdown
    assert "AI 点评暂不可用" not in markdown


# ─────────────────────────────────────────────────────────────
# F2: LLM 不可用 (commentary=None) → 含 ⚠️ 标签，仍有 Top K
# ─────────────────────────────────────────────────────────────
def test_f2_render_daily_with_llm_unavailable():
    result = _sample_result()

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary=None)

    # 仍含 Top K 各行业
    for name in ["半导体", "军工", "AI"]:
        assert name in markdown
    # 含 ⚠️ 警告与降级文案
    assert "⚠" in markdown
    assert "AI 点评暂不可用" in markdown
    # 不应直接拼出字符串 "None"
    assert "None" not in markdown


# ─────────────────────────────────────────────────────────────
# F3: 红线命中 → 丢弃 LLM 段，回退 F2 行为；logger 记关键词
# ─────────────────────────────────────────────────────────────
def test_f3_render_daily_with_redline_hit(caplog):
    import logging

    result = _sample_result()
    # 模拟 LLM 输出含"建议买入"红线词
    bad_commentary = "半导体周期回升，建议买入低估标的。"

    from services.recommend.formatter import render_daily

    with caplog.at_level(logging.WARNING):
        title, markdown = render_daily(result, bad_commentary)

    # 红线段不出现在 markdown 中（整段丢弃）
    assert "建议买入" not in markdown
    assert "低估标的" not in markdown
    # 回退到 F2 同款 ⚠️ 标签
    assert "⚠" in markdown
    assert "AI 点评暂不可用" in markdown
    # logger 记到红线关键词
    assert any("买入" in rec.message for rec in caplog.records)
