"""
ETF 净申购（get_etf_flow）单元测试
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider
from providers.tushare_provider import TushareProvider


# =====================================================================
# TushareProvider.get_etf_flow —— 走 fund_share（份额变动），主源
# =====================================================================

class TestTushareEtfFlow:
    def _provider(self):
        p = TushareProvider.__new__(TushareProvider)
        p.config = {}
        p.pro = MagicMock()
        return p

    def _fund_share_df(self, prev_share: float, last_share: float):
        return pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20260527", "20260528"],
            "fd_share": [prev_share, last_share],
        })

    def test_share_change_from_fund_share(self):
        """fd_share(万份) 末两日差 → shares_change_billion(亿份)，单位 /1e4"""
        p = self._provider()
        # 同一 df 喂给所有 watchlist code，只验 510300 的数学
        p.pro.fund_share = MagicMock(
            return_value=self._fund_share_df(2789688.77, 2796888.77))
        r = p.get_etf_flow("2026-05-28")
        assert r.success
        assert "tushare" in r.source
        e = next((x for x in r.data if x["code"] == "510300"), None)
        assert e is not None
        assert e["name"]  # 带名称
        assert e["shares_change_billion"] == pytest.approx((2796888.77 - 2789688.77) / 1e4, abs=0.001)
        assert e["total_shares_billion"] == pytest.approx(2796888.77 / 1e4, abs=0.001)

    def test_net_redemption_negative(self):
        """份额减少 → 净赎回为负"""
        p = self._provider()
        p.pro.fund_share = MagicMock(
            return_value=self._fund_share_df(2796888.77, 2789688.77))
        r = p.get_etf_flow("2026-05-28")
        e = next((x for x in r.data if x["code"] == "510300"), None)
        assert e["shares_change_billion"] < 0

    def test_insufficient_rows_skipped(self):
        """某 ETF 仅 1 行历史 → 跳过该只，不计入结果"""
        p = self._provider()
        p.pro.fund_share = MagicMock(return_value=pd.DataFrame({
            "ts_code": ["510300.SH"], "trade_date": ["20260528"], "fd_share": [2796888.77],
        }))
        r = p.get_etf_flow("2026-05-28")
        # 全部只有 1 行 → 无有效项 → success=False
        assert not r.success

    def test_all_fail_returns_error(self):
        p = self._provider()
        p.pro.fund_share = MagicMock(side_effect=Exception("镜像不支持"))
        r = p.get_etf_flow("2026-05-28")
        assert not r.success

    def test_nan_fd_share_skipped(self):
        """fd_share 为 NaN 时跳过该只，不输出 nan 亿份（提主源健壮性守卫）"""
        import numpy as np
        p = self._provider()
        p.pro.fund_share = MagicMock(return_value=pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20260527", "20260528"],
            "fd_share": [np.nan, 2796888.77],
        }))
        r = p.get_etf_flow("2026-05-28")
        # 所有 watchlist 都喂同一含 NaN 的 df → 无有效项 → success=False
        assert not r.success

    def test_inf_fd_share_skipped(self):
        """fd_share 为 inf 时同样被 math.isfinite 守卫剔除"""
        p = self._provider()
        p.pro.fund_share = MagicMock(return_value=pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20260527", "20260528"],
            "fd_share": [float("inf"), 2796888.77],
        }))
        r = p.get_etf_flow("2026-05-28")
        assert not r.success

    def test_capability_declared_for_registry_routing(self):
        """tushare 必须在 get_capabilities 声明 get_etf_flow，否则 registry.supports 跳过它→落回坏的 akshare"""
        p = self._provider()
        assert "get_etf_flow" in p.get_capabilities()

    def test_etf_code_exchange_suffix(self):
        """ts_code 后缀映射正确：5xxxxx→.SH，1xxxxx→.SZ"""
        p = self._provider()
        called = []

        def _fs(ts_code, start_date, end_date):
            called.append(ts_code)
            return self._fund_share_df(100.0, 200.0)

        p.pro.fund_share = MagicMock(side_effect=_fs)
        p.get_etf_flow("2026-05-28")
        assert "510300.SH" in called
        assert "159919.SZ" in called


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
