"""研报覆盖统计逻辑的单元测试。"""
from __future__ import annotations

from collections import Counter
from unittest.mock import MagicMock

import pytest

from providers.base import DataResult


def _aggregate_research_coverage(report_data: list[dict]) -> list[dict]:
    """从 collect_post_market 中提取的研报聚合逻辑（与业务代码一致）。"""
    stock_counts: Counter[str] = Counter()
    stock_names: dict[str, str] = {}
    for r in report_data:
        code = r.get("stock_code", "")
        if code:
            stock_counts[code] += 1
            stock_names[code] = r.get("stock_name", "")
    return [
        {"stock_code": code, "stock_name": stock_names.get(code, ""), "report_count": count}
        for code, count in stock_counts.most_common(20)
    ]


class TestResearchCoverageAggregation:

    def test_basic_aggregation(self):
        data = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "A"},
            {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "B"},
            {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "C"},
            {"stock_code": "300750", "stock_name": "宁德时代", "institution": "A"},
            {"stock_code": "300750", "stock_name": "宁德时代", "institution": "B"},
            {"stock_code": "601318", "stock_name": "中国平安", "institution": "A"},
        ]
        result = _aggregate_research_coverage(data)
        assert len(result) == 3
        assert result[0]["stock_code"] == "600519"
        assert result[0]["report_count"] == 3
        assert result[1]["stock_code"] == "300750"
        assert result[1]["report_count"] == 2
        assert result[2]["stock_code"] == "601318"
        assert result[2]["report_count"] == 1

    def test_top_20_limit(self):
        data = [
            {"stock_code": f"{600000 + i}", "stock_name": f"stock_{i}", "institution": "X"}
            for i in range(30)
        ]
        result = _aggregate_research_coverage(data)
        assert len(result) == 20

    def test_empty_data(self):
        assert _aggregate_research_coverage([]) == []

    def test_missing_stock_code_skipped(self):
        data = [
            {"stock_code": "", "stock_name": "无代码", "institution": "A"},
            {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "B"},
        ]
        result = _aggregate_research_coverage(data)
        assert len(result) == 1
        assert result[0]["stock_code"] == "600519"

    def test_stock_name_preserved(self):
        data = [
            {"stock_code": "300750", "stock_name": "宁德时代", "institution": "A"},
        ]
        result = _aggregate_research_coverage(data)
        assert result[0]["stock_name"] == "宁德时代"

    def test_ordering_by_count_desc(self):
        data = (
            [{"stock_code": "A", "stock_name": "sa"} for _ in range(2)]
            + [{"stock_code": "B", "stock_name": "sb"} for _ in range(5)]
            + [{"stock_code": "C", "stock_name": "sc"} for _ in range(3)]
        )
        result = _aggregate_research_coverage(data)
        counts = [r["report_count"] for r in result]
        assert counts == sorted(counts, reverse=True)
        assert result[0]["stock_code"] == "B"


class TestProviderBaseDefault:
    """基类默认返回 not implemented。"""

    def test_base_returns_not_implemented(self):
        from providers.base import DataProvider

        class Dummy(DataProvider):
            def initialize(self): return True
            def get_capabilities(self): return []

        d = Dummy()
        r = d.get_research_report_list("2026-04-10")
        assert not r.success
        assert "not implemented" in r.error


class TestCollectorIntegration:
    """验证 collect_post_market 正确将研报覆盖数据写入 result。"""

    def test_research_coverage_in_result(self):
        registry = MagicMock()
        call_count = 0

        def call_side(method: str, *args, **kwargs):
            if method == "get_research_report_list":
                return DataResult(
                    data=[
                        {"stock_code": "600519", "stock_name": "贵州茅台"},
                        {"stock_code": "600519", "stock_name": "贵州茅台"},
                        {"stock_code": "300750", "stock_name": "宁德时代"},
                    ],
                    source="mock",
                )
            return DataResult(data=None, source="mock", error="skip")

        registry.call.side_effect = call_side

        from collectors.market import MarketCollector
        collector = MarketCollector(registry)
        result = collector.collect_post_market("2026-04-10")

        assert "research_coverage_top" in result
        top = result["research_coverage_top"]
        assert top[0]["stock_code"] == "600519"
        assert top[0]["report_count"] == 2

    def test_research_coverage_missing_when_provider_fails(self):
        registry = MagicMock()
        registry.call.return_value = DataResult(data=None, source="mock", error="fail")

        from collectors.market import MarketCollector
        collector = MarketCollector(registry)
        result = collector.collect_post_market("2026-04-10")

        assert "research_coverage_top" not in result


class TestLabelIndustry:
    """label_industry：给 coverage items 附申万一级行业（三级降级）。"""

    def _reg(self, sw_map, *, success=True, error=None):
        reg = MagicMock()
        if success:
            reg.call.return_value = DataResult(data=sw_map, source="mock")
        else:
            reg.call.return_value = DataResult(data=None, source="mock", error=error or "fail")
        return reg

    def test_hits_sw_l1_via_six_digit_prefix(self):
        from services.research_digest.collector import label_industry
        sw_map = {"600519.SH": {"name": "贵州茅台", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}
        items = [{"stock_code": "600519", "report_count": 3}]
        out = label_industry(items, self._reg(sw_map))
        assert out[0]["industry"] == "食品饮料"
        assert out[0]["report_count"] == 3  # 原字段保留

    def test_missing_member_falls_back_to_unclassified(self):
        from services.research_digest.collector import label_industry, UNCLASSIFIED
        items = [{"stock_code": "688635", "report_count": 1}]
        out = label_industry(items, self._reg({}))  # 空 map → 缺成分
        assert out[0]["industry"] == UNCLASSIFIED

    def test_map_failure_all_unclassified(self):
        from services.research_digest.collector import label_industry, UNCLASSIFIED
        items = [{"stock_code": "600519", "report_count": 2}]
        out = label_industry(items, self._reg(None, success=False, error="dns"))
        assert out[0]["industry"] == UNCLASSIFIED

    def test_empty_l1_value_falls_back_to_unclassified(self):
        from services.research_digest.collector import label_industry, UNCLASSIFIED
        sw_map = {"600519.SH": {"name": "x", "sw_l1": "", "sw_l2": "白酒Ⅱ"}}
        out = label_industry([{"stock_code": "600519", "report_count": 1}], self._reg(sw_map))
        assert out[0]["industry"] == UNCLASSIFIED

    def test_does_not_mutate_input(self):
        from services.research_digest.collector import label_industry
        sw_map = {"600519.SH": {"name": "x", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}
        items = [{"stock_code": "600519", "report_count": 1}]
        label_industry(items, self._reg(sw_map))
        assert "industry" not in items[0]  # 返回新 dict，不改原列表

    def test_empty_or_missing_stock_code_unclassified(self):
        from services.research_digest.collector import label_industry, UNCLASSIFIED
        sw_map = {"600519.SH": {"name": "x", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}
        out = label_industry([{"report_count": 1}, {"stock_code": "", "report_count": 1}], self._reg(sw_map))
        assert out[0]["industry"] == UNCLASSIFIED  # 缺 stock_code
        assert out[1]["industry"] == UNCLASSIFIED  # 空 stock_code


class TestAggregateByIndustry:
    """aggregate_by_industry：纯函数按行业聚合 + 排序。"""

    def test_aggregates_and_sorts_by_report_count_desc(self):
        from services.research_digest.collector import aggregate_by_industry
        items = [
            {"stock_code": "600519", "industry": "食品饮料", "report_count": 2},
            {"stock_code": "601318", "industry": "银行", "report_count": 6},
            {"stock_code": "600036", "industry": "银行", "report_count": 1},
        ]
        out = aggregate_by_industry(items)
        assert out[0] == {"industry": "银行", "stock_count": 2, "report_count": 7}
        assert out[1] == {"industry": "食品饮料", "stock_count": 1, "report_count": 2}

    def test_tie_breaks_by_stock_count_then_name(self):
        from services.research_digest.collector import aggregate_by_industry
        items = [
            {"industry": "机械设备", "report_count": 3},
            {"industry": "机械设备", "report_count": 0},
            {"industry": "机械设备", "report_count": 0},
            {"industry": "电子", "report_count": 3},
        ]
        out = aggregate_by_industry(items)
        # 同篇数 3：机械设备(3只) 排 电子(1只) 前
        assert [b["industry"] for b in out] == ["机械设备", "电子"]

    def test_tie_breaks_by_name_when_both_counts_equal(self):
        from services.research_digest.collector import aggregate_by_industry
        # report_count 与 stock_count 全并列 → 末级按行业名升序
        items = [
            {"industry": "银行", "report_count": 2},
            {"industry": "电子", "report_count": 2},
        ]
        out = aggregate_by_industry(items)
        assert [b["industry"] for b in out] == ["电子", "银行"]  # 电 < 银（Unicode 升序）

    def test_unclassified_always_last(self):
        from services.research_digest.collector import aggregate_by_industry, UNCLASSIFIED
        items = [
            {"industry": UNCLASSIFIED, "report_count": 99},  # 篇数最高也排最后
            {"industry": "银行", "report_count": 1},
        ]
        out = aggregate_by_industry(items)
        assert out[-1]["industry"] == UNCLASSIFIED

    def test_empty_input(self):
        from services.research_digest.collector import aggregate_by_industry
        assert aggregate_by_industry([]) == []

    def test_missing_report_count_treated_as_zero(self):
        from services.research_digest.collector import aggregate_by_industry
        out = aggregate_by_industry([{"industry": "银行"}])
        assert out[0] == {"industry": "银行", "stock_count": 1, "report_count": 0}


class TestCollectCnReportCount:
    """collect_cn item 须带 report_count（篇数=源行数，钉钉行业聚合用）。"""

    def test_collect_cn_item_has_report_count_equals_row_count(self):
        from services.research_digest import collector
        reg = MagicMock()

        def call_side(method, *a, **k):
            if method == "get_research_report_list":
                return DataResult(
                    data=[
                        {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "A"},
                        {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "B"},
                    ],
                    source="mock",
                )
            return DataResult(data=[], source="mock")  # 补观点网络调用返空

        reg.call.side_effect = call_side
        items = collector.collect_cn(reg, "2026-04-10")
        assert items[0]["stock_code"] == "600519"
        assert items[0]["report_count"] == 2
