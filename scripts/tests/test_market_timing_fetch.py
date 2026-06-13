"""market-timing 指数日线取数路由单测（mock registry，证明 avg_price 不被 tushare 遮蔽）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from services.market_timing import fetch


def test_avg_price_routes_to_call_specific_tdx():
    """平均股价必须直连 tdx（call_specific），绕开 tushare 对未知 code 的空成功遮蔽。"""
    reg = MagicMock()
    fetch.fetch_index_daily(reg, "avg_price", "2026-05-01", "2026-06-13")
    reg.call_specific.assert_called_once_with(
        "tdx", "get_index_daily_range", "avg_price", "2026-05-01", "2026-06-13"
    )
    reg.call.assert_not_called()


def test_standard_index_routes_to_registry_call():
    """标准指数走 registry.call 按优先级降级。"""
    reg = MagicMock()
    fetch.fetch_index_daily(reg, "000001.SH", "2026-05-01", "2026-06-13")
    reg.call.assert_called_once_with(
        "get_index_daily_range", "000001.SH", "2026-05-01", "2026-06-13"
    )
    reg.call_specific.assert_not_called()


def test_avg_price_sentinel_normalizes_case_and_whitespace():
    """大小写/空格变体也须命中专路并传规范 code，避免漏到 tushare 被遮蔽。"""
    for variant in ("AVG_PRICE", " avg_price ", "Avg_Price"):
        reg = MagicMock()
        fetch.fetch_index_daily(reg, variant, "2026-05-01", "2026-06-13")
        reg.call_specific.assert_called_once_with(
            "tdx", "get_index_daily_range", "avg_price", "2026-05-01", "2026-06-13"
        )
        reg.call.assert_not_called()
