from services.new_high import aggregator


def test_groups_by_industry_and_keeps_full_stock_list():
    stocks = [
        {"code": "1", "name": "A", "industry": "半导体", "amount": 300},
        {"code": "2", "name": "B", "industry": "半导体", "amount": 100},
        {"code": "3", "name": "C", "industry": "通信设备", "amount": 200},
    ]

    sectors = aggregator.aggregate_by_sector(stocks)

    assert sectors[0]["industry"] == "半导体"
    assert sectors[0]["count"] == 2
    assert [s["code"] for s in sectors[0]["stocks"]] == ["1", "2"]
    assert sectors[1]["industry"] == "通信设备"


def test_unclassified_sorts_after_classified_when_counts_tie():
    stocks = [
        {"code": "1", "name": "A", "industry": "未分类", "amount": 300},
        {"code": "2", "name": "B", "industry": "银行", "amount": 100},
    ]

    sectors = aggregator.aggregate_by_sector(stocks)

    assert [s["industry"] for s in sectors] == ["银行", "未分类"]
