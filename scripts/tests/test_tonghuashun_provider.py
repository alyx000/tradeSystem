from __future__ import annotations

import json

import pytest

from providers.tonghuashun_provider import (
    REALHEAD_URL,
    TonghuashunProvider,
    _normalize_code,
    _parse_realhead,
)


def _body(**overrides) -> str:
    items = {
        "5": "883421",
        "6": "1396.447",
        "7": "1385.763",
        "8": "1407.727",
        "9": "1385.354",
        "10": "1407.455",
        "13": "123440769000.000",
        "19": "2130562300000.000",
        "name": "同花顺全A(沪深)",
        "updateTime": "2026-08-31 14:11",
    }
    items.update(overrides)
    return (
        "quotebridge_v6_realhead_bk_883421_last("
        + json.dumps({"items": items}, ensure_ascii=False)
        + ")"
    )


def test_code_validation_is_fail_closed():
    assert _normalize_code("883421") == "883421.THS"
    assert _normalize_code(" 883421.ths ") == "883421.THS"
    assert _normalize_code("000001.SH") is None
    assert _normalize_code("883421,883422") is None


def test_realhead_jsonp_requires_matching_callback_and_code():
    assert _parse_realhead(_body(), "883421")["10"] == "1407.455"
    with pytest.raises(ValueError, match="布局异常"):
        _parse_realhead("callback({})", "883421")
    with pytest.raises(ValueError, match="代码错位"):
        _parse_realhead(_body(**{"5": "883422"}), "883421")


def test_realtime_quote_parses_point_pre_close_and_market_update_time(monkeypatch):
    provider = TonghuashunProvider()
    provider.initialize()
    monkeypatch.setattr(provider, "_fetch_one", lambda session, code: _body())

    result = provider.get_realtime_quotes(["883421.THS"])

    assert result.success
    assert result.source == "tonghuashun:realhead_v6"
    assert result.source_url == REALHEAD_URL.format(code="883421")
    quote = result.data[0]
    assert quote["code"] == "883421.THS"
    assert quote["name"] == "同花顺全A(沪深)"
    assert quote["price"] == 1407.455
    assert quote["pre_close"] == 1396.447
    assert quote["pct_chg"] == pytest.approx(0.7883, abs=1e-4)
    assert quote["quote_date"] == "2026-08-31"
    assert quote["quote_time"] == "14:11:00"


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"10": "nan"}, "数值非有限"),
        ({"6": "0"}, "最新点位或昨收非法"),
        ({"updateTime": "bad"}, "not enough values"),
    ],
)
def test_dirty_quote_fails_closed(monkeypatch, overrides, expected):
    provider = TonghuashunProvider()
    provider.initialize()
    monkeypatch.setattr(
        provider,
        "_fetch_one",
        lambda session, code: _body(**overrides),
    )

    result = provider.get_realtime_quotes(["883421.THS"])

    assert not result.success
    assert expected in result.note


def test_fetch_retries_transient_http_failure_three_times(monkeypatch):
    class Response:
        text = "ok"

        def raise_for_status(self):
            raise RuntimeError("502")

    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout):
            self.calls += 1
            return Response()

    provider = TonghuashunProvider()
    session = Session()
    with pytest.raises(RuntimeError, match="连续 3 次"):
        provider._fetch_one(session, "883421")
    assert session.calls == 3


def test_setup_providers_registers_tonghuashun():
    import main as main_module

    registry = main_module.setup_providers(
        {
            "providers": {
                "sina": {"enabled": False},
                "tonghuashun": {"enabled": True, "priority": 4},
                "tdx": {"enabled": False},
            }
        }
    )
    provider = registry.get_provider("tonghuashun")
    assert provider is not None
    assert provider.priority == 4
    assert provider.supports("get_realtime_quotes")
