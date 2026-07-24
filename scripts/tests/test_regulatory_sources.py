"""监管异动三个 Tushare range 接口的 provider 契约。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


@pytest.fixture
def provider():
    instance = object.__new__(TushareProvider)
    instance.name = "tushare"
    instance.config = {}
    instance.pro = MagicMock()
    return instance


def test_regulatory_range_methods_are_advertised_as_capabilities(provider):
    capabilities = set(provider.get_capabilities())
    assert {
        "get_stk_alert_range",
        "get_stk_shock_range",
        "get_stk_high_shock_range",
    } <= capabilities


def test_get_stk_alert_range_passes_range_and_normalizes_dates(provider):
    provider.pro.stk_alert.return_value = pd.DataFrame(
        [
            {
                "ts_code": "600664.SH",
                "name": "哈药股份",
                "start_date": "20260724",
                "end_date": "20260806",
                "type": "监管期证券",
            }
        ]
    )

    result = provider.get_stk_alert_range("2026-04-24", "2026-07-24")

    assert result.success
    provider.pro.stk_alert.assert_called_once_with(
        start_date="20260424",
        end_date="20260724",
    )
    assert result.source == "tushare:stk_alert"
    assert result.data[0]["code"] == "600664.SH"
    assert result.data[0]["start_date_norm"] == "2026-07-24"
    assert result.data[0]["end_date_norm"] == "2026-08-06"
    assert result.data[0]["source"] == "tushare:stk_alert"


@pytest.mark.parametrize(
    ("method_name", "pro_method", "source"),
    [
        ("get_stk_shock_range", "stk_shock", "tushare:stk_shock"),
        ("get_stk_high_shock_range", "stk_high_shock", "tushare:stk_high_shock"),
    ],
)
def test_shock_range_passes_range_and_parses_period(
    provider, method_name, pro_method, source
):
    getattr(provider.pro, pro_method).return_value = pd.DataFrame(
        [
            {
                "ts_code": "300534.SZ",
                "trade_date": "20260722",
                "name": "陇神戎发",
                "trade_market": "创业板",
                "reason": "连续十个交易日内涨幅偏离值达到规定阈值",
                "period": "2026070920260722",
            }
        ]
    )

    result = getattr(provider, method_name)("2026-05-24", "2026-07-23")

    assert result.success
    getattr(provider.pro, pro_method).assert_called_once_with(
        start_date="20260524",
        end_date="20260723",
    )
    assert result.source == source
    row = result.data[0]
    assert row["code"] == "300534.SZ"
    assert row["trade_date_norm"] == "2026-07-22"
    assert row["period_start"] == "2026-07-09"
    assert row["period_end"] == "2026-07-22"
    assert row["period_norm"] == "2026-07-09/2026-07-22"
    assert row["source"] == source


@pytest.mark.parametrize(
    ("method_name", "pro_method", "source"),
    [
        ("get_stk_alert_range", "stk_alert", "tushare:stk_alert"),
        ("get_stk_shock_range", "stk_shock", "tushare:stk_shock"),
        ("get_stk_high_shock_range", "stk_high_shock", "tushare:stk_high_shock"),
    ],
)
def test_regulatory_range_empty_is_successful_empty(
    provider, method_name, pro_method, source
):
    getattr(provider.pro, pro_method).return_value = pd.DataFrame()

    result = getattr(provider, method_name)("2026-05-24", "2026-07-23")

    assert result.success
    assert result.data == []
    assert result.source == source


@pytest.mark.parametrize(
    ("method_name", "pro_method"),
    [
        ("get_stk_alert_range", "stk_alert"),
        ("get_stk_shock_range", "stk_shock"),
        ("get_stk_high_shock_range", "stk_high_shock"),
    ],
)
def test_regulatory_range_provider_failure_is_not_disguised_as_empty(
    provider, method_name, pro_method
):
    getattr(provider.pro, pro_method).side_effect = RuntimeError("network unavailable")

    result = getattr(provider, method_name)("2026-05-24", "2026-07-23")

    assert not result.success
    assert result.data is None
    assert "network unavailable" in (result.error or "")


def test_regulatory_range_recursively_splits_when_provider_hits_row_cap(provider):
    def _response(*, start_date: str, end_date: str):
        if start_date == "20260701" and end_date == "20260710":
            return pd.DataFrame(
                [
                    {
                        "ts_code": f"{index:06d}.SZ",
                        "trade_date": "20260701",
                    }
                    for index in range(1000)
                ]
            )
        return pd.DataFrame(
            [
                {
                    "ts_code": "300534.SZ",
                    "trade_date": start_date,
                    "reason": "异常波动",
                    "period": f"{start_date}{end_date}",
                }
            ]
        )

    provider.pro.stk_shock.side_effect = _response

    result = provider.get_stk_shock_range("2026-07-01", "2026-07-10")

    assert result.success
    assert len(result.data) == 2
    assert provider.pro.stk_shock.call_count == 3


def test_regulatory_range_fails_closed_when_single_day_reaches_row_cap(provider):
    provider.pro.stk_alert.return_value = pd.DataFrame(
        [
            {
                "ts_code": f"{index:06d}.SZ",
                "start_date": "20260723",
                "end_date": "20260724",
            }
            for index in range(1000)
        ]
    )

    result = provider.get_stk_alert_range("2026-07-23", "2026-07-23")

    assert not result.success
    assert result.data is None
    assert "1000 行上限" in (result.error or "")
