"""MarketCollector.collect_pre_market：mock registry，无网络。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from collectors.market import MarketCollector, filter_calendar_for_pre_market
from providers.base import DataResult


def _ok_index(name: str):
    return DataResult(data={"name": name, "close": 100.0, "change_pct": 0.5}, source="mock")


def _ok_commodity(name: str):
    return DataResult(data={"name": name, "close": 50.0, "change_pct": 0.0}, source="mock")


def test_filter_calendar_drops_low_importance():
    merged = [
        {"event": "低优先", "importance": "低"},
        {"event": "高优先", "importance": "高"},
        {"event": "中优先", "importance": "中"},
    ]
    out = filter_calendar_for_pre_market(merged)
    assert [e["event"] for e in out] == ["高优先", "中优先"]


def test_collect_pre_market_shapes_margin_and_margin_wow(tmp_path, monkeypatch):
    """亚太 nikkei+kospi、HXC、news 空、融资 T-1 + 可选 T-2 环比。"""
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
                data={"HXC": {"name": "HXC", "close": 10.0, "change_pct": 1.0}},
                source="mock",
            )
        if method == "get_commodity":
            return _ok_commodity(args[0])
        if method == "get_forex":
            return DataResult(data={"name": args[0], "close": 7.0, "change_pct": 0.0}, source="mock")
        if method == "get_macro_calendar":
            assert args[0] == "2026-03-30"
            return DataResult(data=[], source="mock")
        if method == "get_margin_data":
            d = args[0]
            if d == "2026-03-27":
                return DataResult(
                    data={
                        "trade_date": "2026-03-27",
                        "total_rzye_yi": 100.0,
                        "total_rqye_yi": 2.0,
                        "total_rzrqye_yi": 102.0,
                        "exchanges": [
                            {"exchange_id": "SSE", "rzye_yi": 50.0, "rqye_yi": 1.0, "rzrqye_yi": 51.0},
                        ],
                    },
                    source="mock",
                )
            if d == "2026-03-26":
                return DataResult(
                    data={
                        "trade_date": "2026-03-26",
                        "total_rzye_yi": 90.0,
                        "total_rqye_yi": 2.0,
                        "total_rzrqye_yi": 92.0,
                        "exchanges": [
                            {"exchange_id": "SSE", "rzye_yi": 45.0, "rqye_yi": 1.0, "rzrqye_yi": 46.0},
                        ],
                    },
                    source="mock",
                )
            raise AssertionError(f"unexpected margin date {d}")
        return DataResult(data=None, source="mock", error=f"unexpected {method}")

    registry = MagicMock()
    registry.call.side_effect = fake_call

    col = MarketCollector(registry)
    out = col.collect_pre_market(
        target_date="2026-03-30",
        prev_trade_date="2026-03-27",
        prev_prev_trade_date="2026-03-26",
    )

    assert set(out["global_indices"].keys()) == {"dow_jones", "nasdaq", "sp500", "a50"}
    assert set(out["global_indices_apac"].keys()) == {"nikkei", "kospi"}
    assert "HXC" in out["us_china_assets"]
    assert out["news"] == []
    md = out["margin_data"]
    assert md["trade_date"] == "2026-03-27"
    assert md["delta_total_rzye_yi"] == 10.0
    assert md["margin_compare_date"] == "2026-03-26"
    assert md["exchanges"][0]["delta_rzrqye_yi"] == 5.0
    assert "calendar_events" in out
    assert any(c[0] == "get_margin_data" for c in calls)


def test_collect_pre_market_no_prev_date_skips_margin():
    def smarter(m, *a, **kw):
        if m == "get_global_index":
            return DataResult(data={"close": 1, "change_pct": 0, "name": a[0]}, source="mock")
        if m == "get_us_tickers_overnight":
            return DataResult(data={"HXC": {"close": 1, "change_pct": 0, "name": "H"}}, source="mock")
        if m == "get_commodity":
            return DataResult(data={"name": a[0], "close": 1, "change_pct": 0}, source="mock")
        if m == "get_forex":
            return DataResult(data={"name": a[0], "close": 1, "change_pct": 0}, source="mock")
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
