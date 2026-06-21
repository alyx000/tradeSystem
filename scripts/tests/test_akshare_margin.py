"""AkshareProvider.get_margin_data：tushare 全失败时的两融余额降级源（沪深，无北交所）。

mock self.ak.stock_margin_sse / stock_margin_szse。注意量纲：SSE 单位元、SZSE 单位亿元。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak_provider():
    p = object.__new__(AkshareProvider)
    p.name = "akshare"
    p.config = {}
    p.ak = MagicMock()
    return p


def _sse_df():
    # SSE 单位为元，含日期列「信用交易日期」
    return pd.DataFrame(
        [
            {"信用交易日期": "20260617", "融资余额": 1_000_00000000, "融券余量金额": 2_00000000, "融资融券余额": 1_002_00000000},
            {"信用交易日期": "20260616", "融资余额": 990_00000000, "融券余量金额": 2_00000000, "融资融券余额": 992_00000000},
        ]
    )


def _szse_df():
    # SZSE 单位为亿元，无日期列（date 作为入参）
    return pd.DataFrame(
        [{"融资余额": 900.0, "融券余额": 10.0, "融资融券余额": 910.0}]
    )


def test_capability_declared(ak_provider):
    assert "get_margin_data" in ak_provider.get_capabilities()


def test_sse_szse_unit_normalization(ak_provider):
    """SSE 元 → /1e8、SZSE 亿元直用；两所齐全才 success。"""
    ak_provider.ak.stock_margin_sse.return_value = _sse_df()
    ak_provider.ak.stock_margin_szse.return_value = _szse_df()

    r = AkshareProvider.get_margin_data(ak_provider, "2026-06-17")
    assert r.success
    assert r.source.startswith("akshare")
    by_ex = {e["exchange_id"]: e for e in r.data["exchanges"]}
    # SSE 1000 亿，SZSE 900 亿
    assert by_ex["SSE"]["rzye_yi"] == pytest.approx(1000.0)
    assert by_ex["SZSE"]["rzye_yi"] == pytest.approx(900.0)
    # 合计 1900 亿
    assert r.data["total_rzye_yi"] == pytest.approx(1900.0)
    assert r.data["trade_date"] == "2026-06-17"
    assert r.data["market_scope"] == "SSE+SZSE"  # 降级口径，不含北交所


def test_szse_failure_returns_error(ak_provider):
    """SZSE SSL 抖动失败 → 整体 error（半额不冒充全市场总额，让 registry 继续降级/最终失败）。"""
    ak_provider.ak.stock_margin_sse.return_value = _sse_df()
    ak_provider.ak.stock_margin_szse.side_effect = RuntimeError("SSLError")

    r = AkshareProvider.get_margin_data(ak_provider, "2026-06-17")
    assert not r.success
    assert "SZSE" in r.error


def test_both_failure_errors(ak_provider):
    """两所都取不到 → error，触发 registry 继续降级/最终失败。"""
    ak_provider.ak.stock_margin_sse.side_effect = RuntimeError("net")
    ak_provider.ak.stock_margin_szse.side_effect = RuntimeError("net")

    r = AkshareProvider.get_margin_data(ak_provider, "2026-06-17")
    assert not r.success
