"""TushareProvider.get_margin_data：mock pro.margin，无 token。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


@pytest.fixture
def provider_with_margin_df():
    """绕过 initialize，仅注入 mock pro。"""
    ts = object.__new__(TushareProvider)
    ts.name = "tushare"
    ts.config = {}
    ts.pro = MagicMock()
    return ts


def test_get_margin_data_sums_exchanges(provider_with_margin_df):
    df = pd.DataFrame(
        [
            {"exchange_id": "SSE", "rzye": 1.0e11, "rqye": 2.0e9, "rzrqye": 1.02e11},
            {"exchange_id": "SZSE", "rzye": 9.0e10, "rqye": 1.0e9, "rzrqye": 9.1e10},
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-03-27")
    assert r.success
    assert r.data["trade_date"] == "2026-03-27"
    assert r.data["total_rzye_yi"] == pytest.approx(1900.0)  # 1.9e11 / 1e8
    assert r.data["total_rqye_yi"] == pytest.approx(30.0)  # 3e9 / 1e8
    assert len(r.data["exchanges"]) == 2
    assert r.data["exchanges"][0]["exchange_id"] == "SSE"
    provider_with_margin_df.pro.margin.assert_called_once_with(trade_date="20260327")


def test_get_margin_data_empty_df(provider_with_margin_df):
    provider_with_margin_df.pro.margin.return_value = pd.DataFrame()
    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-03-27")
    assert not r.success
    assert "无融资融券汇总" in r.error


def test_get_margin_data_exception(provider_with_margin_df):
    provider_with_margin_df.pro.margin.side_effect = RuntimeError("network")
    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-03-27")
    assert not r.success
    assert "network" in r.error
