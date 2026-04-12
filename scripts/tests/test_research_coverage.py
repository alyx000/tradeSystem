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
