from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from providers.tushare_provider import TushareProvider


def test_market_daily_quotes_include_amount_fields_for_ma_breakout():
    provider = TushareProvider(config={"token": "x"})
    provider.pro = MagicMock()
    provider._initialized = True
    provider.pro.daily.return_value = pd.DataFrame([
        {
            "ts_code": "600001.SH",
            "trade_date": "20260612",
            "open": 10.0,
            "high": 11.0,
            "low": 9.8,
            "close": 10.8,
            "pre_close": 10.0,
            "pct_chg": 8.0,
            "vol": 10000.0,
            "amount": 120000.0,
        }
    ])

    result = provider.get_market_daily_quotes("2026-06-12")

    _, kwargs = provider.pro.daily.call_args
    fields = kwargs["fields"].split(",")
    assert "trade_date" in fields
    assert "pct_chg" in fields
    assert "vol" in fields
    assert "amount" in fields
    assert result.success
    assert result.data[0]["trade_date"] == "20260612"
    assert result.data[0]["pct_chg"] == 8.0
    assert result.data[0]["vol"] == 10000.0
    assert result.data[0]["amount"] == 120000.0
