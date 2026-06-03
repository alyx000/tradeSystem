"""market.py 研报覆盖采集接线：复用 research_digest.build_coverage_panel 产出富化面板数据。"""
from __future__ import annotations

from types import SimpleNamespace

from collectors.market import MarketCollector


def _res(data, success=True, error=None):
    return SimpleNamespace(success=success, data=data, error=error)


class _Reg:
    def __init__(self, mapping):
        self.mapping = mapping

    def call(self, method, *args):
        return self.mapping.get(method, _res([]))


def test_collect_research_coverage_enriches_via_panel():
    """接 build_coverage_panel：高信号标的带 expanded + 评级方向 + 观点。"""
    rows = [{"stock_code": "300999", "stock_name": "新覆盖股", "institution": "中信", "rating_change": "首次"}]
    reports = [{"date": "2026-05-29", "institution": "东吴", "title": "再融资落地，业务全面高增"}]
    reg = _Reg({"get_research_report_list": _res(rows), "get_research_reports": _res(reports)})
    out = MarketCollector(reg)._collect_research_coverage("2026-05-29")
    assert out[0]["stock_code"] == "300999"
    assert out[0]["expanded"] is True
    assert out[0]["rating_direction"] == "首次覆盖"
    assert out[0]["viewpoint"] == "再融资落地，业务全面高增"


def test_collect_research_coverage_preserves_pill_count():
    """长尾药丸仍带篇数（向后兼容口径不变）。"""
    rows = [
        {"stock_code": "000001", "stock_name": "平安", "institution": "广发", "rating_change": "维持"},
        {"stock_code": "000001", "stock_name": "平安", "institution": "中信", "rating_change": "维持"},
    ]
    reg = _Reg({"get_research_report_list": _res(rows), "get_research_reports": _res([])})
    out = MarketCollector(reg)._collect_research_coverage("2026-05-29")
    assert out[0]["stock_code"] == "000001"
    assert out[0]["report_count"] == 2


def test_collect_research_coverage_empty_on_failure():
    """get_research_report_list 失败 → 返空（不致命）。"""
    reg = _Reg({"get_research_report_list": _res(None, success=False, error="dns")})
    assert MarketCollector(reg)._collect_research_coverage("2026-05-29") == []


def test_collect_research_coverage_attaches_industry():
    """每条覆盖项带 industry；命中申万成分 → 一级行业名。"""
    rows = [{"stock_code": "600519", "stock_name": "贵州茅台", "institution": "中信", "rating_change": "维持"}]
    sw_map = {"600519.SH": {"name": "贵州茅台", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}
    reg = _Reg({
        "get_research_report_list": _res(rows),
        "get_research_reports": _res([]),
        "get_stock_sw_industry_map": _res(sw_map),
    })
    out = MarketCollector(reg)._collect_research_coverage("2026-05-29")
    assert out[0]["stock_code"] == "600519"
    assert out[0]["industry"] == "食品饮料"


def test_collect_research_coverage_unclassified_when_no_member():
    """缺申万成分 → 未分类（_Reg 未配 sw map → 空 → 未分类）。"""
    rows = [{"stock_code": "688635", "stock_name": "C长进", "institution": "中信"}]
    reg = _Reg({"get_research_report_list": _res(rows), "get_research_reports": _res([])})
    out = MarketCollector(reg)._collect_research_coverage("2026-05-29")
    assert out[0]["industry"] == "未分类"
