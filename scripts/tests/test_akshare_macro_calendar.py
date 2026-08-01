"""AkShare 宏观日历：空日与来源失败不可混淆。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from providers.akshare_provider import AkshareProvider
from providers.base import DataResult


def _provider() -> AkshareProvider:
    provider = AkshareProvider({})
    provider._initialized = True
    provider.ak = MagicMock()
    return provider


def test_single_day_exception_is_failure_not_healthy_empty():
    provider = _provider()
    provider.ak.news_economic_baidu.side_effect = RuntimeError("source down")

    result = provider.get_macro_calendar("2026-07-29")

    assert result.success is False
    assert result.data is None
    assert "source down" in result.error


def test_single_day_empty_dataframe_is_healthy_empty():
    provider = _provider()
    provider.ak.news_economic_baidu.return_value = pd.DataFrame()

    result = provider.get_macro_calendar("2026-07-29")

    assert result.success is True
    assert result.data == []


def test_range_fails_closed_when_any_day_source_fails():
    provider = _provider()
    provider.get_macro_calendar = MagicMock(side_effect=[
        DataResult(data=[], source="pytest"),
        DataResult(data=None, source="pytest", error="source down"),
        DataResult(
            data=[{"event": "美国GDP", "time": "20:30"}],
            source="pytest",
        ),
    ])

    result = provider.get_macro_calendar_range("2026-07-29", "2026-07-31")

    assert result.success is False
    assert result.data is None
    assert "2026-07-30" in result.error
