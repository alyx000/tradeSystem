"""AkshareProvider.get_margin_series：两融余额区间序列降级源（仅沪深，无北交所）。

sse 走 stock_margin_sse 区间，szse 按 sse 返回的真实交易日逐日 stock_margin_szse；
沪深任一日缺则该日跳过；全空返 error。单位归一：沪市元/1e8、深市已是亿。mock，无网络。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def provider_with_ak():
    ak_provider = object.__new__(AkshareProvider)
    ak_provider.name = "akshare"
    ak_provider.config = {}
    ak_provider.ak = MagicMock()
    return ak_provider


def _sse_df(rows):
    # 信用交易日期 / 融资余额(元) / 融券余量金额(元) / 融资融券余额(元)
    return pd.DataFrame(rows)


def test_capability_declared():
    caps = AkshareProvider.get_capabilities(object.__new__(AkshareProvider))
    assert "get_margin_series" in caps


def test_sse_szse_merged_series_ascending(provider_with_ak):
    """沪市区间 + 深市逐日 → 升序合并序列，单位归一，market_scope=SSE+SZSE。"""
    provider_with_ak.ak.stock_margin_sse.return_value = _sse_df([
        {"信用交易日期": "20260616", "融资余额": 1.0e12, "融券余量金额": 2.0e10, "融资融券余额": 1.02e12},
        {"信用交易日期": "20260617", "融资余额": 1.01e12, "融券余量金额": 2.0e10, "融资融券余额": 1.03e12},
    ])

    def _szse(date):
        m = {
            "20260616": pd.DataFrame([{"融资余额": 7000.0, "融券余额": 50.0, "融资融券余额": 7050.0}]),
            "20260617": pd.DataFrame([{"融资余额": 7100.0, "融券余额": 50.0, "融资融券余额": 7150.0}]),
        }
        return m[date]

    provider_with_ak.ak.stock_margin_szse.side_effect = _szse

    r = AkshareProvider.get_margin_series(provider_with_ak, "2026-06-10", "2026-06-17")
    assert r.success
    series = r.data
    assert [d["trade_date"] for d in series] == ["2026-06-16", "2026-06-17"]
    last = series[-1]
    # sse rzrqye 1.03e12/1e8 = 10300 亿；szse 7150 亿；合计 17450
    assert last["sse_rzrqye_yi"] == pytest.approx(10300.0)
    assert last["szse_rzrqye_yi"] == pytest.approx(7150.0)
    assert last["total_rzrqye_yi"] == pytest.approx(17450.0)
    assert last["market_scope"] == "SSE+SZSE"
    assert last["bse_rzrqye_yi"] == 0.0


def test_day_missing_szse_skipped(provider_with_ak):
    """某日深市缺 → 该日跳过（半额不冒充全市场），其余日保留。"""
    provider_with_ak.ak.stock_margin_sse.return_value = _sse_df([
        {"信用交易日期": "20260616", "融资余额": 1.0e12, "融券余量金额": 2.0e10, "融资融券余额": 1.02e12},
        {"信用交易日期": "20260617", "融资余额": 1.01e12, "融券余量金额": 2.0e10, "融资融券余额": 1.03e12},
    ])

    def _szse(date):
        if date == "20260617":
            return pd.DataFrame([{"融资余额": 7100.0, "融券余额": 50.0, "融资融券余额": 7150.0}])
        return pd.DataFrame()  # 06-16 深市缺

    provider_with_ak.ak.stock_margin_szse.side_effect = _szse

    r = AkshareProvider.get_margin_series(provider_with_ak, "2026-06-10", "2026-06-17")
    assert r.success
    assert [d["trade_date"] for d in r.data] == ["2026-06-17"]


def test_no_complete_day_returns_error(provider_with_ak):
    """全窗深市都缺 → 无完整日 → error。"""
    provider_with_ak.ak.stock_margin_sse.return_value = _sse_df([
        {"信用交易日期": "20260617", "融资余额": 1.01e12, "融券余量金额": 2.0e10, "融资融券余额": 1.03e12},
    ])
    provider_with_ak.ak.stock_margin_szse.return_value = pd.DataFrame()
    r = AkshareProvider.get_margin_series(provider_with_ak, "2026-06-10", "2026-06-17")
    assert not r.success


def test_sse_empty_returns_error(provider_with_ak):
    provider_with_ak.ak.stock_margin_sse.return_value = pd.DataFrame()
    r = AkshareProvider.get_margin_series(provider_with_ak, "2026-06-10", "2026-06-17")
    assert not r.success


def test_fallback_call_count_bounded_by_max_days(provider_with_ak):
    """降级成本封顶（codex #4）：sse 返回 200 日，深市调用数 ≤ max_days，不随全区间线性膨胀。"""
    days = [f"2026{m:02d}{d:02d}" for m in range(1, 8) for d in range(1, 29)][:200]
    provider_with_ak.ak.stock_margin_sse.return_value = _sse_df([
        {"信用交易日期": d, "融资余额": 1.0e12, "融券余量金额": 2.0e10, "融资融券余额": 1.02e12}
        for d in days
    ])
    provider_with_ak.ak.stock_margin_szse.return_value = pd.DataFrame(
        [{"融资余额": 7000.0, "融券余额": 50.0, "融资融券余额": 7050.0}])

    r = AkshareProvider.get_margin_series(provider_with_ak, "2026-01-01", "2026-07-28", max_days=90)
    assert r.success
    assert len(r.data) == 90  # 只取最近 90 个完整日
    assert provider_with_ak.ak.stock_margin_szse.call_count <= 90  # 深市调用封顶，不到 200
    # 升序返回，且取的是最新 90 日（末日为区间内最新）
    assert r.data[-1]["trade_date"] > r.data[0]["trade_date"]
