"""volume_concentration aggregator 纯函数单测:行业聚合 / 集中度 / 未分类 / 覆盖率。"""
from __future__ import annotations

import pytest

from services.volume_concentration import aggregator


def test_groups_by_industry_with_share_and_sorted():
    """按行业聚合 count/amount/codes,share_in_top_n=行业额/总额,按额降序。"""
    stocks = [
        {"code": "300750.SZ", "industry": "电池", "amount_billion": 60.0},
        {"code": "002594.SZ", "industry": "电池", "amount_billion": 40.0},
        {"code": "600519.SH", "industry": "白酒Ⅱ", "amount_billion": 90.0},
    ]

    result = aggregator.aggregate_sectors(stocks)

    assert result["top_n"] == 3
    assert result["total_amount_billion"] == 190.0
    ss = result["sector_summary"]
    assert [s["industry"] for s in ss] == ["电池", "白酒Ⅱ"]  # 按 amount 降序
    assert ss[0]["count"] == 2
    assert ss[0]["amount_billion"] == 100.0
    assert ss[0]["share_in_top_n"] == pytest.approx(100.0 / 190.0)
    assert ss[0]["codes"] == ["300750.SZ", "002594.SZ"]
    assert result["industry_coverage"] == 1.0  # 全部已归类


def test_unclassified_bucket_and_coverage():
    """缺成分股进「未分类」桶;coverage = 已归类数 / 总数。"""
    stocks = [
        {"code": "A", "industry": "电池", "amount_billion": 50.0},
        {"code": "B", "industry": "未分类", "amount_billion": 30.0},
        {"code": "C", "industry": "未分类", "amount_billion": 20.0},
    ]

    result = aggregator.aggregate_sectors(stocks)

    industries = {s["industry"] for s in result["sector_summary"]}
    assert "未分类" in industries  # 未分类作为正常桶进 summary
    unc = next(s for s in result["sector_summary"] if s["industry"] == "未分类")
    assert unc["count"] == 2
    assert unc["amount_billion"] == 50.0
    assert result["industry_coverage"] == pytest.approx(1 / 3)  # 仅 1/3 归类


def test_industry_none_falls_into_unclassified():
    """industry 字段缺失/None 也归「未分类」。"""
    stocks = [{"code": "X", "amount_billion": 10.0}, {"code": "Y", "industry": None, "amount_billion": 5.0}]

    result = aggregator.aggregate_sectors(stocks)

    assert result["sector_summary"][0]["industry"] == "未分类"
    assert result["sector_summary"][0]["count"] == 2
    assert result["industry_coverage"] == 0.0


def test_empty_stocks_returns_zeroed():
    """空输入 → 全 0,不报错(非交易日/无数据由上层不写库)。"""
    result = aggregator.aggregate_sectors([])

    assert result["top_n"] == 0
    assert result["total_amount_billion"] == 0.0
    assert result["sector_summary"] == []
    assert result["industry_coverage"] == 0.0


def test_shares_sum_to_one():
    """各行业 share_in_top_n 合计 ≈ 1。"""
    stocks = [
        {"code": "A", "industry": "电池", "amount_billion": 60.0},
        {"code": "B", "industry": "白酒Ⅱ", "amount_billion": 90.0},
        {"code": "C", "industry": "证券Ⅱ", "amount_billion": 50.0},
    ]

    result = aggregator.aggregate_sectors(stocks)

    assert sum(s["share_in_top_n"] for s in result["sector_summary"]) == pytest.approx(1.0)


def test_tie_break_by_industry_name_deterministic():
    """同成交额行业 → 按行业名升序二级排序,保证报告/测试稳定。"""
    stocks = [
        {"code": "A", "industry": "BBB", "amount_billion": 50.0},
        {"code": "B", "industry": "AAA", "amount_billion": 50.0},
    ]

    result = aggregator.aggregate_sectors(stocks)

    assert [s["industry"] for s in result["sector_summary"]] == ["AAA", "BBB"]
