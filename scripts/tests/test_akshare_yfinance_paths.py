"""AkShareProvider：亚太 yfinance 与 get_us_tickers_overnight，mock yfinance，无东财。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    p._df_global_spot_em = None
    p._df_futures_global_spot_em = None
    return p


def _hist_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-03-20", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


@patch("yfinance.Ticker")
def test_get_global_index_hsi_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _hist_df([24000.0, 24500.0])
    r = ak.get_global_index("hsi")
    assert r.success
    assert r.data["close"] == 24500.0
    assert r.data["change_pct"] == pytest.approx(2.08, abs=0.02)
    assert "yfinance" in r.source


@patch("yfinance.Ticker")
def test_get_global_index_hstech_second_ticker_if_first_empty(mock_ticker, ak: AkshareProvider):
    t1 = MagicMock()
    t1.history.return_value = pd.DataFrame()
    t2 = MagicMock()
    t2.history.return_value = _hist_df([4600.0, 4700.0])
    mock_ticker.side_effect = [t1, t2]

    r = ak.get_global_index("hstech")
    assert r.success
    assert r.data["close"] == 4700.0
    assert mock_ticker.call_count == 2


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight(mock_ticker, ak: AkshareProvider):
    def make_ticker(closes):
        m = MagicMock()
        m.history.return_value = _hist_df(closes)
        return m

    mock_ticker.side_effect = [
        make_ticker([27.0, 28.0]),
        make_ticker([35.0, 34.5]),
    ]

    r = ak.get_us_tickers_overnight(["KWEB", "FXI"])
    assert r.success
    assert r.data["KWEB"]["close"] == 28.0
    assert r.data["KWEB"]["change_pct"] > 0
    assert r.data["FXI"]["close"] == 34.5
    assert r.data["FXI"]["change_pct"] < 0


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight_empty_history(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = pd.DataFrame()
    r = ak.get_us_tickers_overnight(["KWEB"])
    assert r.success
    assert "error" in r.data["KWEB"]
