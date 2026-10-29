"""TushareProvider 初始化不得把凭据持久化到用户目录。"""
from __future__ import annotations

import tushare as ts
import pandas as pd
import pytest
from providers import tushare_transport

from providers.tushare_provider import TushareProvider


class _FakePro:
    def __init__(self) -> None:
        self.trade_cal_calls: list[dict] = []

    def trade_cal(self, **kwargs):
        self.trade_cal_calls.append(kwargs)
        return pd.DataFrame([{"exchange": "SSE", "cal_date": "20260101", "is_open": 0}])


def test_initialize_passes_token_directly_without_set_token(monkeypatch) -> None:
    fake_pro = _FakePro()
    seen: dict[str, str] = {}

    def forbidden_set_token(_token: str) -> None:
        raise AssertionError("initialize must not persist token via ts.set_token")

    def fake_pro_api(token: str):
        seen["token"] = token
        return fake_pro

    monkeypatch.setattr(ts, "set_token", forbidden_set_token)
    monkeypatch.setattr(tushare_transport, "TushareMirrorClient", fake_pro_api)

    provider = TushareProvider({"token": "token-from-config"})

    assert provider.initialize() is True
    assert seen == {"token": "token-from-config"}
    assert provider.pro is fake_pro
    assert provider._initialized is True
    assert fake_pro.trade_cal_calls == [
        {"exchange": "SSE", "start_date": "20260101", "end_date": "20260101"},
    ]


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame([{"is_open": 1}]),
    pd.DataFrame([{"exchange": "SSE", "cal_date": "20260102", "is_open": 1}])])
def test_initialize_rejects_empty_or_wrong_calendar(monkeypatch, frame):
    fake = _FakePro()
    fake.trade_cal = lambda **kwargs: frame
    monkeypatch.setattr(tushare_transport, "TushareMirrorClient", lambda token: fake)
    provider = TushareProvider({"token": "fake"})
    assert provider.initialize() is False
    assert provider.pro is None
    assert not provider._initialized
    result = provider.get_stock_universe_as_of("2026-10-29")
    assert not result.success
    assert "初始化日历响应" in result.error


def test_initialize_error_preserved_without_account_link(monkeypatch):
    def fail(token):
        raise RuntimeError("Token已过期 https://example.invalid/private-account")
    monkeypatch.setattr(tushare_transport, "TushareMirrorClient", fail)
    provider = TushareProvider({"token": "fake"})
    assert not provider.initialize()
    result = provider.get_stock_universe_as_of("2026-10-29")
    assert "Token已过期" in result.error
    assert "private-account" not in result.error
