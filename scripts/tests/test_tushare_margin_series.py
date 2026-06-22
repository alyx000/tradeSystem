"""TushareProvider.get_margin_series：两融余额区间时间序列（供联动性相关分析）。

复用 get_margin_data 的完整性逻辑（应到交易所集合判定），但返回区间内**所有**完整
交易日的升序序列，而非单日快照。mock pro.margin，无 token。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


def _row(trade_date, ex, rzye, rqye, rzrqye):
    return {
        "trade_date": trade_date,
        "exchange_id": ex,
        "rzye": rzye,
        "rqye": rqye,
        "rzrqye": rzrqye,
    }


@pytest.fixture
def provider_with_margin_df():
    ts = object.__new__(TushareProvider)
    ts.name = "tushare"
    ts.config = {}
    ts.pro = MagicMock()
    return ts


def test_capability_declared():
    """get_margin_series 必须在 capabilities 声明，否则 registry 静默跳过降级链。"""
    caps = TushareProvider.get_capabilities(object.__new__(TushareProvider))
    assert "get_margin_series" in caps


def test_complete_days_returned_ascending(provider_with_margin_df):
    """三所齐全的多日 → 升序序列，每日三市合计 + 沪深分项。"""
    df = pd.DataFrame(
        [
            _row("20260617", "SSE", 1.0e11, 2.0e9, 1.02e11),
            _row("20260617", "SZSE", 9.0e10, 1.0e9, 9.1e10),
            _row("20260617", "BSE", 1.0e9, 1.0e7, 1.01e9),
            _row("20260616", "SSE", 9.9e10, 2.0e9, 1.01e11),
            _row("20260616", "SZSE", 8.9e10, 1.0e9, 9.0e10),
            _row("20260616", "BSE", 0.9e9, 1.0e7, 0.91e9),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_series(provider_with_margin_df, "2026-06-10", "2026-06-17")
    assert r.success
    series = r.data
    assert [d["trade_date"] for d in series] == ["2026-06-16", "2026-06-17"]
    last = series[-1]
    # 三市合计 rzrqye：(1.02e11+9.1e10+1.01e9)/1e8 = 1020+910+10.1 = 1940.1 亿
    assert last["total_rzrqye_yi"] == pytest.approx(1940.1)
    assert last["sse_rzrqye_yi"] == pytest.approx(1020.0)
    assert last["szse_rzrqye_yi"] == pytest.approx(910.0)
    assert last["bse_rzrqye_yi"] == pytest.approx(10.1)
    assert last["market_scope"] == "BSE+SSE+SZSE"
    # 区间查询：start/end 透传
    _, kwargs = provider_with_margin_df.pro.margin.call_args
    assert kwargs["start_date"] == "20260610"
    assert kwargs["end_date"] == "20260617"


def test_incomplete_days_dropped(provider_with_margin_df):
    """某日仅沪市（深/北滞后）→ 该日剔除，不污染序列；完整日保留。"""
    df = pd.DataFrame(
        [
            # 最新日仅 SSE → 不完整，剔除
            _row("20260618", "SSE", 1.5e11, 2.0e9, 1.52e11),
            # 06-17 三所齐全 → 保留
            _row("20260617", "SSE", 1.0e11, 2.0e9, 1.02e11),
            _row("20260617", "SZSE", 9.0e10, 1.0e9, 9.1e10),
            _row("20260617", "BSE", 1.0e9, 1.0e7, 1.01e9),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_series(provider_with_margin_df, "2026-06-10", "2026-06-18")
    assert r.success
    assert [d["trade_date"] for d in r.data] == ["2026-06-17"]


def test_empty_df_returns_error(provider_with_margin_df):
    provider_with_margin_df.pro.margin.return_value = pd.DataFrame()
    r = TushareProvider.get_margin_series(provider_with_margin_df, "2026-06-10", "2026-06-18")
    assert not r.success


def test_no_complete_day_returns_error(provider_with_margin_df):
    """整窗只返沪市 → 应到集合以沪深为下限不塌缩 → 无完整日 → error。"""
    df = pd.DataFrame(
        [
            _row("20260618", "SSE", 1.5e11, 2.0e9, 1.52e11),
            _row("20260617", "SSE", 1.49e11, 2.0e9, 1.51e11),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df
    r = TushareProvider.get_margin_series(provider_with_margin_df, "2026-06-10", "2026-06-18")
    assert not r.success


def test_exception_returns_error(provider_with_margin_df):
    provider_with_margin_df.pro.margin.side_effect = RuntimeError("network")
    r = TushareProvider.get_margin_series(provider_with_margin_df, "2026-06-10", "2026-06-18")
    assert not r.success
    assert "network" in r.error
