"""TDD: 行业推荐 markdown formatter 单测（三段结构）

三段：📌 大盘判断 / 🔥 热度榜 / 💡 催化行业。
直接构造 AggregateResult，不依赖 SQL（fixture 形状稳定）。
"""
from __future__ import annotations

from services.recommend.aggregator import (
    AggregateResult,
    Catalyst,
    MarketView,
    SectorHeat,
)


def _sample_result(*, market_views=None, sectors=None, catalysts=None) -> AggregateResult:
    """构造 AggregateResult 用于 formatter 测试。
    market_views: [text, ...]；sectors: [(name, mentions), ...]；catalysts: [(name, conf, content), ...]
    """
    if market_views is None:
        market_views = ["大盘观点一", "大盘观点二"]
    if sectors is None:
        sectors = [("半导体", 4), ("军工", 3), ("AI", 1)]
    if catalysts is None:
        catalysts = [("六氟化钨", "高", "日企停产"), ("MLCC", "中", "现货涨 40%")]
    return AggregateResult(
        market_views=[MarketView(date="2026-05-16", text=t) for t in market_views],
        sectors=[
            SectorHeat(sector_name=n, mentions=m, recency_decay=1.0, score=float(m),
                       latest_date="2026-05-16")
            for n, m in sectors
        ],
        catalysts=[
            Catalyst(date="2026-05-16", sector_name=n, confidence=cf, content=ct)
            for n, cf, ct in catalysts
        ],
        window_start="2026-05-13",
        window_end="2026-05-16",
        lookback_days=3,
    )


# ─────────────────────────────────────────────────────────────
# F1: LLM 大盘判断成功 → 大盘判断段用 LLM bullets，原始 core_view 不出现
# ─────────────────────────────────────────────────────────────
def test_f1_render_daily_with_llm_commentary():
    result = _sample_result()
    commentary = "- 趋势未改，属低吸观察窗\n- 成交额未站上 3 万亿，看补量"

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary)

    assert "2026-05-16" in title
    # 三段标题都在
    assert "近期大盘判断" in markdown
    assert "行业热度榜" in markdown
    assert "有具体催化的行业" in markdown
    # LLM bullets 出现在大盘判断段
    assert "低吸观察窗" in markdown
    # commentary 在场时，原始 core_view 不出现（LLM 取代降级原文）
    assert "大盘观点一" not in markdown
    # 热度榜各板块名 + 提及次数
    for name in ["半导体", "军工", "AI"]:
        assert name in markdown
    assert "提及 4 次" in markdown
    # 催化行业来自 industry_info
    assert "六氟化钨" in markdown
    assert "日企停产" in markdown
    assert "（高）" in markdown
    # 无降级警告 / None 字面量
    assert "⚠" not in markdown
    assert "None" not in markdown


# ─────────────────────────────────────────────────────────────
# F2: LLM 不可用 (commentary=None) → 大盘判断降级展示原始 core_view
# ─────────────────────────────────────────────────────────────
def test_f2_render_daily_with_llm_unavailable():
    result = _sample_result()

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary=None)

    # 大盘判断降级展示原始观点
    assert "大盘观点一" in markdown
    assert "大盘观点二" in markdown
    # 三段仍在
    for name in ["半导体", "军工", "AI"]:
        assert name in markdown
    assert "六氟化钨" in markdown
    # 不应拼出 "None"
    assert "None" not in markdown


# ─────────────────────────────────────────────────────────────
# F3: 红线命中 → 丢弃 LLM 段，降级原始 core_view；logger 记关键词
# ─────────────────────────────────────────────────────────────
def test_f3_render_daily_with_redline_hit(caplog):
    import logging

    result = _sample_result(market_views=["观点A", "观点B", "观点C"])
    bad_commentary = "- 半导体可建仓低吸，建议买入"

    from services.recommend.formatter import render_daily

    with caplog.at_level(logging.WARNING):
        title, markdown = render_daily(result, bad_commentary)

    # 红线段整段丢弃
    assert "建议买入" not in markdown
    assert "建仓" not in markdown
    # 降级到原始 core_view，且保持 market_views 顺序
    for v in ["观点A", "观点B", "观点C"]:
        assert v in markdown
    assert markdown.find("观点A") < markdown.find("观点B") < markdown.find("观点C")
    # logger 记到红线关键词
    assert any("买入" in rec.message or "建仓" in rec.message for rec in caplog.records)


# ─────────────────────────────────────────────────────────────
# F4: 三段空集 → 各自占位文案，不留空段
# ─────────────────────────────────────────────────────────────
def test_f4_empty_sections_show_placeholders():
    result = _sample_result(market_views=[], sectors=[], catalysts=[])

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary=None)

    assert "暂无大盘观点录入" in markdown
    assert "无板块提及" in markdown
    assert "无具体催化录入" in markdown
    assert "None" not in markdown


# ─────────────────────────────────────────────────────────────
# F5: confidence 为 None 的催化条目 → 不渲染空括号
# ─────────────────────────────────────────────────────────────
def test_f5_catalyst_without_confidence_omits_parens():
    result = _sample_result(catalysts=[("某板块", None, "某催化")])

    from services.recommend.formatter import render_daily

    title, markdown = render_daily(result, commentary=None)

    assert "某板块：某催化" in markdown
    assert "（None）" not in markdown
    assert "（）" not in markdown
