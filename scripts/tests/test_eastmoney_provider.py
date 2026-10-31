"""东方财富中证2000报价契约；所有行情均为合成数据。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from providers.eastmoney_provider import EastmoneyProvider, _parse_quote


def payload(**changes):
    data = {"f43": 2585.34, "f44": 2610.0, "f45": 2580.0, "f46": 2600.0,
            "f47": 1234, "f48": 5000000, "f57": "932000", "f58": "中证2000",
            "f59": 2, "f60": 2590.0,
            "f86": int(datetime(2026, 10, 30, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())}
    data.update(changes)
    return {"rc": 0, "data": data}


def test_parse_identity_unscaled_price_timestamp_and_units():
    q = _parse_quote(payload(), "932000.CSI")
    assert (q["code"], q["name"], q["price"]) == ("932000.CSI", "中证2000", 2585.34)
    assert (q["quote_date"], q["quote_time"]) == ("2026-10-30", "10:00:00")
    assert q["volume"] == 123400 and q["amount"] == 5000000
    assert q["pre_close"] == 2590.0


@pytest.mark.parametrize("changes", [
    {"f57": "000001"}, {"f58": "上证指数"}, {"f58": None},
    {"f43": "-"}, {"f43": float("nan")}, {"f43": float("inf")}, {"f43": 0},
    {"f60": 0}, {"f43": True}, {"f47": -1}, {"f48": -1},
    {"f86": None}, {"f86": True}, {"f86": 0}, {"f86": 1.5}, {"f86": 1e30},
    {"f59": True}, {"f59": 2.5}, {"f59": 99},
])
def test_dirty_quotes_rejected(changes):
    with pytest.raises((ValueError, OverflowError, OSError)):
        _parse_quote(payload(**changes), "932000.CSI")


@pytest.mark.parametrize("body", [None, {}, {"rc": 1, "data": {}}, {"rc": False, "data": {}}, {"rc": 0, "data": None}])
def test_empty_or_error_envelope_rejected(body):
    with pytest.raises(ValueError):
        _parse_quote(body, "932000.CSI")


def test_provider_returns_source_and_rejects_unknown_codes_before_network(monkeypatch):
    p = EastmoneyProvider()
    assert p.initialize() and p.get_capabilities() == ["get_realtime_quotes"]
    calls = []
    monkeypatch.setattr(p, "_fetch_one", lambda session, secid: calls.append(secid) or payload())
    r = p.get_realtime_quotes(["932000.CSI", "932000.CSI"])
    assert r.success and len(r.data) == 1 and calls == ["2.932000"]
    assert r.source == "eastmoney:quote" and r.data[0]["price"] == 2585.34
    calls.clear()
    r = p.get_realtime_quotes(["000001.SH", "932000.SH", "932000.CSI,000001.SH"])
    assert not r.success and not calls


def test_http_retry_is_bounded_and_keeps_exact_query():
    class Response:
        def raise_for_status(self):
            raise RuntimeError("502")

    class Session:
        calls = []
        def get(self, url, *, params, timeout):
            self.calls.append((url, params, timeout))
            return Response()

    s = Session()
    with pytest.raises(RuntimeError):
        EastmoneyProvider()._fetch_one(s, "2.932000")
    assert len(s.calls) == 2
    assert all(c[1]["secid"] == "2.932000" and c[1]["fltt"] == "2" and c[2] == 8 for c in s.calls)


def test_production_registration_respects_enabled_flag():
    from main import setup_providers
    config = {"providers": {name: {"enabled": False} for name in
                            ("tushare", "akshare", "sina", "tonghuashun", "tdx", "eastmoney")}}
    assert setup_providers(config).get_provider("eastmoney") is None
    config["providers"]["eastmoney"] = {"enabled": True, "priority": 5}
    p = setup_providers(config).get_provider("eastmoney")
    assert isinstance(p, EastmoneyProvider) and p.priority == 5


def test_same_priority_keeps_existing_sina_ahead_of_new_source():
    from main import setup_providers
    registry = setup_providers({"providers": {"sina": {"priority": 3}, "eastmoney": {"priority": 3},
                                             "tdx": {"enabled": False}}})
    names = [p.name for p in registry.providers]
    assert names.index("sina") < names.index("eastmoney")


def test_source_failure_has_no_successful_empty_quote(monkeypatch):
    p = EastmoneyProvider()
    def fail(session, secid):
        raise RuntimeError("source failed")
    monkeypatch.setattr(p, "_fetch_one", fail)
    result = p.get_realtime_quotes(["932000.CSI"])
    assert not result.success and result.data is None and "source failed" in result.note
