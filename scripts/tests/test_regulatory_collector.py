"""RegulatoryCollector 降级行为单测。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from collectors.regulatory import RegulatoryCollector
from providers.base import DataResult


class _RateLimitedRegistry:
    def __init__(self, error: str):
        self.error = error
        self.calls: list[str] = []

    def call(self, method: str, *args, **kwargs):
        self.calls.append(method)
        if method == "get_stock_st":
            return DataResult(data=[], source="stub")
        if method == "get_trade_calendar":
            start = date(2026, 4, 1)
            rows = []
            for i in range(45):
                d = start + timedelta(days=i)
                rows.append({"cal_date": d.isoformat(), "is_open": 1})
            return DataResult(data=rows, source="stub")
        if method == "get_stk_shock":
            return DataResult(data=None, source="tushare", error=self.error)
        raise AssertionError(f"unexpected fallback call after rate limit: {method}")


def _assert_collect_potential_skips_self_calculation(error: str):
    registry = _RateLimitedRegistry(error)
    collector = RegulatoryCollector(registry)

    with sqlite3.connect(":memory:") as conn:
        result = collector.collect_potential(conn, "2026-05-15")

    assert result == []
    assert "get_market_daily_changes" not in registry.calls
    assert "get_index_daily_range" not in registry.calls


def test_collect_potential_skips_self_calculation_after_rate_limit():
    _assert_collect_potential_skips_self_calculation("HTTP 429: Too Many Requests")


def test_collect_potential_skips_self_calculation_after_connection_failure():
    _assert_collect_potential_skips_self_calculation(
        "Max retries exceeded with url: / (Caused by NewConnectionError: Connection refused)"
    )
