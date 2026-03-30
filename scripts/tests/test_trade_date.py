"""utils.trade_date：上一交易日推算（mock registry，无外网）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from providers.base import DataResult
from utils.trade_date import get_prev_trade_date


def test_get_prev_trade_date_finds_nearest_open_day():
    """today 为周一且周六为休市时，应返回上周五。"""
    registry = MagicMock()

    def call_side(method: str, date: str):
        # 2026-03-30 周一；向前 2026-03-29 日、2026-03-28 六 休市；2026-03-27 五 开市
        open_days = {"2026-03-27", "2026-03-26"}
        if method == "is_trade_day":
            ok = date in open_days
            return DataResult(data=ok, source="mock")
        return DataResult(data=None, source="mock", error="unexpected")

    registry.call.side_effect = call_side
    assert get_prev_trade_date(registry, "2026-03-30") == "2026-03-27"


def test_get_prev_trade_date_fallback_to_yesterday():
    """7 天内 is_trade_day 均失败或均休市时回退昨天。"""
    registry = MagicMock()
    registry.call.return_value = DataResult(data=False, source="mock")
    assert get_prev_trade_date(registry, "2026-03-30") == "2026-03-29"


def test_get_prev_trade_date_respects_is_trade_day_failure():
    """is_trade_day 返回 error（success=False）时视为非交易日继续向前。"""
    registry = MagicMock()
    calls = []

    def call_side(method: str, date: str):
        calls.append(date)
        if method != "is_trade_day":
            return DataResult(data=None, source="mock", error="bad")
        if date == "2026-03-27":
            return DataResult(data=True, source="mock")
        return DataResult(data=None, source="mock", error="api down")

    registry.call.side_effect = call_side
    assert get_prev_trade_date(registry, "2026-03-30") == "2026-03-27"
