"""
ETF 净申购（get_etf_flow）单元测试
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


def _make_provider():
    p = AkshareProvider()
    p._initialized = True
    p.ak = MagicMock()
    return p


def _make_etf_df(shares_last: float, shares_prev: float, size: float = 500.0) -> pd.DataFrame:
    """构造两行份额历史 DataFrame"""
    return pd.DataFrame({
        "净值日期": ["2026-03-31", "2026-04-01"],
        "基金份额(万份)": [shares_prev, shares_last],
        "基金规模(亿元)": [size, size + 10],
    })


class TestGetEtfFlow:
    def test_returns_list(self):
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(return_value=_make_etf_df(100000, 95000))
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        assert isinstance(r.data, list)
        assert len(r.data) > 0

    def test_net_subscription_positive(self):
        """份额增加 → shares_change_billion 为正"""
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(return_value=_make_etf_df(110000, 100000))
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        entry = next((e for e in r.data if e["code"] == "510300"), None)
        assert entry is not None
        assert entry.get("shares_change_billion", 0) > 0

    def test_net_redemption_negative(self):
        """份额减少 → shares_change_billion 为负"""
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(return_value=_make_etf_df(90000, 100000))
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        entry = next((e for e in r.data if e["code"] == "510300"), None)
        assert entry is not None
        assert entry.get("shares_change_billion", 0) < 0

    def test_partial_failure_returns_available(self):
        """部分 ETF 失败时，成功项仍返回"""
        p = _make_provider()
        call_count = {"n": 0}

        def _side_effect(fund, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_etf_df(100000, 95000)
            raise Exception("模拟网络异常")

        p.ak.fund_etf_fund_info_em = MagicMock(side_effect=_side_effect)
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        assert len(r.data) >= 1

    def test_all_fail_returns_error(self):
        """全部 ETF 失败 → success=False"""
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(side_effect=Exception("全失败"))
        r = p.get_etf_flow("2026-04-01")
        assert not r.success

    def test_empty_dataframe_skipped(self):
        """空 DataFrame 应被跳过，不抛异常"""
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(return_value=pd.DataFrame())
        r = p.get_etf_flow("2026-04-01")
        assert not r.success

    def test_entry_has_required_fields(self):
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(return_value=_make_etf_df(100000, 95000))
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        for entry in r.data:
            assert "code" in entry
            assert "name" in entry

    def test_shares_calculation_precision(self):
        """份额 10000 万份 = 1 亿份；变化 5000 万份 = 0.5 亿份"""
        p = _make_provider()
        p.ak.fund_etf_fund_info_em = MagicMock(
            return_value=_make_etf_df(shares_last=15000, shares_prev=10000)
        )
        r = p.get_etf_flow("2026-04-01")
        assert r.success
        entry = next((e for e in r.data if e["code"] == "510300"), None)
        assert entry is not None
        assert entry.get("shares_change_billion") == pytest.approx(0.5, abs=0.001)
