"""TushareProvider.get_stock_daily_range：mock pro.query，断言返回完整 OHLCV。

Stage 0（趋势主升 scanner）：原方法只回 {trade_date, pct_chg}，缺 close/open/vol，
导致贴 MA5 / 缩量阴线 / 远离 MA5 检测器无米下锅。本测试钉死「区间日线必须含 OHLCV」契约。
底层 Tushare daily 接口本就返回全列，扩字段向后兼容（消费方 regulatory.py 仅读 trade_date+pct_chg）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


@pytest.fixture
def provider():
    """绕过 initialize，仅注入 mock pro（同 test_tushare_margin 模式）。"""
    ts = object.__new__(TushareProvider)
    ts.name = "tushare"
    ts.config = {}
    ts.pro = MagicMock()
    return ts


def _daily_df() -> pd.DataFrame:
    """Tushare daily 接口返回列：ts_code/trade_date/open/high/low/close/pre_close/change/pct_chg/vol/amount。"""
    return pd.DataFrame(
        [
            {"ts_code": "600552.SH", "trade_date": "20260609", "open": 12.00, "high": 12.50,
             "low": 11.90, "close": 12.35, "pre_close": 11.97, "change": 0.38,
             "pct_chg": 3.17, "vol": 380000.0, "amount": 470000.0},
            {"ts_code": "600552.SH", "trade_date": "20260608", "open": 11.80, "high": 12.00,
             "low": 11.70, "close": 11.97, "pre_close": 11.80, "change": 0.17,
             "pct_chg": 1.44, "vol": 300000.0, "amount": 360000.0},
        ]
    )


def test_get_stock_daily_range_includes_ohlcv(provider):
    """区间日线每根 bar 必须含 open/high/low/close/vol/amount（不只 pct_chg）。"""
    provider.pro.query.return_value = _daily_df()

    r = TushareProvider.get_stock_daily_range(provider, "600552", "2026-06-08", "2026-06-09")

    assert r.success
    assert isinstance(r.data, list) and len(r.data) == 2
    row = next(b for b in r.data if b["trade_date"] == "2026-06-09")
    for key in ("open", "high", "low", "close", "vol", "amount"):
        assert key in row, f"区间日线缺字段 {key}"
    assert row["close"] == pytest.approx(12.35)
    assert row["open"] == pytest.approx(12.00)
    assert row["vol"] == pytest.approx(380000.0)
    assert row["amount"] == pytest.approx(470000.0)


def test_get_stock_daily_range_keeps_legacy_fields(provider):
    """向后兼容：trade_date / pct_chg 仍在（regulatory.py 依赖）。"""
    provider.pro.query.return_value = _daily_df()

    r = TushareProvider.get_stock_daily_range(provider, "600552", "2026-06-08", "2026-06-09")

    assert r.success
    row = r.data[0]
    assert "trade_date" in row and "pct_chg" in row
    assert row["trade_date"].count("-") == 2  # 归一为 YYYY-MM-DD
