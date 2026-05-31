"""volume_concentration formatter 单测:Markdown 片段断言。"""
from __future__ import annotations

from services.volume_concentration import formatter


def _record():
    return {
        "date": "2026-05-29",
        "top_n": 4,
        "total_amount_billion": 200.0,
        "market_total_billion": 10000.0,
        "stocks": [],
        "sector_summary": [
            {"industry": "电池", "count": 2, "amount_billion": 100.0, "share_in_top_n": 0.5, "codes": []},
            {"industry": "白酒Ⅱ", "count": 1, "amount_billion": 60.0, "share_in_top_n": 0.3, "codes": []},
            {"industry": "未分类", "count": 1, "amount_billion": 40.0, "share_in_top_n": 0.2, "codes": []},
        ],
        "source": {"industry_source": "tushare:index_member_all", "industry_coverage": 0.75,
                   "market_total_source": "tushare:index_daily"},
    }


def _trend(sufficient=True):
    if not sufficient:
        return {"days": 1, "sufficient": False, "sector_rotation": {"new": [], "dropped": [], "持续": []},
                "amount_trend": {"latest": 200.0, "previous": None, "change_pct": None}, "stock_retention": []}
    return {
        "days": 30, "sufficient": True,
        "sector_rotation": {"new": ["证券Ⅱ"], "dropped": ["白酒Ⅱ"], "持续": ["电池"]},
        "amount_trend": {"latest": 200.0, "previous": 180.0, "change_pct": 11.11},
        "stock_retention": [{"code": "A", "name": "甲", "streak": 3}, {"code": "B", "name": "乙", "streak": 1}],
    }


def test_header_total_and_coverage():
    out = formatter.format_daily_report(_record(), _trend())
    assert "2026-05-29" in out
    assert "200.0" in out            # 合计成交额
    assert "覆盖率" in out and "75" in out
    assert "2.0%" in out             # 占两市 200/10000


def test_top3_excludes_unclassified_but_lists_it():
    out = formatter.format_daily_report(_record(), _trend())
    # 前3行业集中度 = 电池0.5 + 白酒0.3 = 80%(未分类不计)
    assert "80" in out
    assert "电池" in out and "白酒Ⅱ" in out
    # 未分类单列,标注不计入
    assert "未分类" in out and "不计入" in out


def test_trend_sufficient_renders_three_sections():
    out = formatter.format_daily_report(_record(), _trend(sufficient=True))
    assert "证券Ⅱ" in out      # 新进
    assert "环比" in out and "11.11" in out
    assert "甲" in out          # 留存 streak>=2
    assert "乙" not in out      # streak 1 不展示


def test_trend_insufficient_fallback_text():
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))
    assert "累积" in out and "1 天" in out


def test_negative_change_pct_sign():
    """环比负增长:显示 -X%,不出现 +- 双符号。"""
    t = _trend()
    t["amount_trend"] = {"latest": 90.0, "previous": 100.0, "change_pct": -10.0}

    out = formatter.format_daily_report(_record(), t)

    assert "-10.0%" in out
    assert "+-" not in out
