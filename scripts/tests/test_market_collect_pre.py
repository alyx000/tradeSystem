"""MarketCollector.collect_pre_market：mock registry，无网络。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from collectors.market import MarketCollector
from providers.base import DataResult


def _ok_index(name: str):
    return DataResult(data={"name": name, "close": 100.0, "change_pct": 0.5}, source="mock")


def _ok_commodity(name: str):
    return DataResult(data={"name": name, "close": 50.0, "change_pct": 0.0}, source="mock")


def test_collect_pre_market_shapes_and_margin(tmp_path, monkeypatch):
    """校验返回 dict 结构、news 使用 target_date、融资融券走 prev_trade_date。"""
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)
    (tmp_path / "tracking").mkdir(parents=True)
    (tmp_path / "tracking" / "calendar.yaml").write_text("events: []\n", encoding="utf-8")
    (tmp_path / "tracking" / "calendar_auto.yaml").write_text("events: []\n", encoding="utf-8")

    calls = []

    def fake_call(method: str, *args, **kwargs):
        calls.append((method, args))
        if method == "get_global_index":
            return _ok_index(args[0])
        if method == "get_us_tickers_overnight":
            return DataResult(
                data={
                    "KWEB": {"name": "KWEB", "close": 10.0, "change_pct": 1.0},
                    "FXI": {"name": "FXI", "close": 20.0, "change_pct": -1.0},
                },
                source="mock",
            )
        if method == "get_commodity":
            return _ok_commodity(args[0])
        if method == "get_forex":
            return DataResult(data={"name": args[0], "close": 7.0, "change_pct": 0.0}, source="mock")
        if method == "get_market_news":
            assert args[0] == "2026-03-30"
            return DataResult(data=[{"title": "n1"}], source="mock")
        if method == "get_macro_calendar":
            assert args[0] == "2026-03-30"
            return DataResult(data=[], source="mock")
        if method == "get_margin_data":
            assert args[0] == "2026-03-27"
            return DataResult(
                data={
                    "trade_date": "2026-03-27",
                    "total_rzye_yi": 1.0,
                    "total_rqye_yi": 2.0,
                    "total_rzrqye_yi": 3.0,
                    "exchanges": [],
                },
                source="mock",
            )
        return DataResult(data=None, source="mock", error=f"unexpected {method}")

    registry = MagicMock()
    registry.call.side_effect = fake_call

    col = MarketCollector(registry)
    out = col.collect_pre_market(target_date="2026-03-30", prev_trade_date="2026-03-27")

    assert set(out["global_indices"].keys()) == {"dow_jones", "nasdaq", "sp500", "a50"}
    assert set(out["global_indices_apac"].keys()) == {"hsi", "hstech", "nikkei"}
    assert "KWEB" in out["us_china_assets"]
    assert out["news"] == [{"title": "n1"}]
    assert out["margin_data"]["trade_date"] == "2026-03-27"
    assert "calendar_events" in out
    assert any(c[0] == "get_margin_data" for c in calls)


def test_collect_pre_market_no_prev_date_skips_margin():
    def smarter(m, *a, **kw):
        if m == "get_global_index":
            return DataResult(data={"close": 1, "change_pct": 0, "name": a[0]}, source="mock")
        if m == "get_us_tickers_overnight":
            return DataResult(data={"KWEB": {"close": 1, "change_pct": 0, "name": "K"}}, source="mock")
        if m == "get_commodity":
            return DataResult(data={"name": a[0], "close": 1, "change_pct": 0}, source="mock")
        if m == "get_forex":
            return DataResult(data={"name": a[0], "close": 1, "change_pct": 0}, source="mock")
        if m == "get_market_news":
            return DataResult(data=[], source="mock")
        if m == "get_macro_calendar":
            return DataResult(data=[], source="mock")
        return DataResult(data=None, source="mock", error="bad")

    registry = MagicMock()
    registry.call.side_effect = smarter

    col = MarketCollector(registry)
    out = col.collect_pre_market(target_date="2026-03-30", prev_trade_date=None)
    assert out["margin_data"] == {}
    methods = [c.args[0] for c in registry.call.call_args_list]
    assert "get_margin_data" not in methods
