"""研报速读 renderer 行业覆盖热度段 + service 接线。"""
from __future__ import annotations

from services.research_digest.renderer import render_md


def test_industry_section_present_and_positioned():
    """行业段在 Top3 之后、A股段之前；展示「N只/M篇」。"""
    cn_industry = [
        {"industry": "银行", "stock_count": 2, "report_count": 6},
        {"industry": "机械设备", "stock_count": 3, "report_count": 3},
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "## 📊 行业覆盖热度" in md
    assert "银行 2只/6篇" in md
    assert "机械设备 3只/3篇" in md
    # 位置：行业段在 Top3 之后、A股段之前
    assert md.index("## 🏆 Top3") < md.index("## 📊 行业覆盖热度") < md.index("## 🇨🇳 A股机构评级")


def test_industry_section_caps_at_display_cap():
    """超过 INDUSTRY_DISPLAY_CAP 折叠为「…还有 N 个」。"""
    from services.research_digest.collector import INDUSTRY_DISPLAY_CAP
    cn_industry = [
        {"industry": f"行业{i}", "stock_count": 1, "report_count": 1}
        for i in range(INDUSTRY_DISPLAY_CAP + 3)
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "…还有 3 个" in md


def test_no_fold_marker_when_exactly_at_cap():
    """恰好 = INDUSTRY_DISPLAY_CAP → 全展示，无「…还有 N 个」。"""
    from services.research_digest.collector import INDUSTRY_DISPLAY_CAP
    cn_industry = [
        {"industry": f"行业{i}", "stock_count": 1, "report_count": 1}
        for i in range(INDUSTRY_DISPLAY_CAP)
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "## 📊 行业覆盖热度" in md
    assert "…还有" not in md


def test_exact_separator_string():
    """精确分隔串：「A N只/M篇 · B N只/M篇」。"""
    cn_industry = [
        {"industry": "银行", "stock_count": 2, "report_count": 6},
        {"industry": "机械设备", "stock_count": 3, "report_count": 3},
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "银行 2只/6篇 · 机械设备 3只/3篇" in md


def test_no_industry_section_when_empty():
    """无行业数据 → 不出现行业段（默认 None 与显式 [] 都不渲染）。"""
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[])
    assert "## 📊 行业覆盖热度" not in md
    _, md_empty = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=[])
    assert "## 📊 行业覆盖热度" not in md_empty


def test_service_computes_cn_industry():
    from unittest.mock import MagicMock
    from providers.base import DataResult
    from services.research_digest import service

    reg = MagicMock()

    def call_side(method, *a, **k):
        if method == "get_research_report_list":
            return DataResult(data=[
                {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "中信"},
            ], source="mock")
        if method == "get_stock_sw_industry_map":
            return DataResult(data={"600519.SH": {"name": "贵州茅台", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}, source="mock")
        if method == "get_us_rating_changes":
            return DataResult(data=[], source="mock")
        return DataResult(data=[], source="mock")

    reg.call.side_effect = call_side
    out = service.run_daily_digest(reg, "2026-05-29", no_llm=True)
    assert out.cn_industry
    assert out.cn_industry[0]["industry"] == "食品饮料"
    assert "## 📊 行业覆盖热度" in out.markdown
