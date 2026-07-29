"""盘后主链跨资产快照：mock registry，无网络。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from collectors.market import MarketCollector
from generators.report import ReportGenerator
from providers.base import DataResult


def _success(data: dict, source: str = "mock") -> DataResult:
    return DataResult(
        data=data,
        source=source,
        fetched_at="2026-07-27T20:00:00",
    )


def test_collect_post_market_persists_cross_asset_snapshot(tmp_path, monkeypatch):
    def fake_call(method: str, *args, **kwargs):
        if method == "get_forex":
            return _success(
                {
                    "name": "USD/CNY",
                    "source_date": "2026-07-27",
                    "bid": 6.77,
                    "ask": 6.78,
                    "close": 6.775,
                },
                "chinamoney:rfx-sp-quot",
            )
        if method == "get_fx_swap":
            return _success(
                {
                    "name": "USD/CNY 1Y C-Swap定盘",
                    "source_date": "2026-07-27",
                    "swap_point_pips": -1800.0,
                    "forward_rate": 6.595,
                },
                "chinamoney:fx-c-swap-fixing",
            )
        if method == "get_global_index":
            name = args[0]
            data = {"name": name, "close": 100.0, "change_pct": 0.5}
            data["as_of"] = "2026-07-27"
            if name == "a50":
                assert args == ("a50", "2026-07-27")
            return _success(data, f"mock:{name}")
        if method == "get_us_tickers_overnight":
            return _success(
                {
                    "HXC": {
                        "name": "金龙代理",
                        "close": 23.71,
                        "change_pct": -0.63,
                        "as_of": "2026-07-24",
                    }
                },
                "mock:PGJ",
            )
        if method == "get_commodity":
            assert args == (args[0], "2026-07-27")
            return _success(
                {
                    "name": args[0],
                    "close": 50.0,
                    "change_pct": 0.1,
                    "as_of": "2026-07-27",
                },
                f"mock:{args[0]}",
            )
        return DataResult(data=None, source="mock", error="skip")

    registry = MagicMock()
    registry.call.side_effect = fake_call
    registry.call_specific.return_value = DataResult(
        data=None,
        source="mock",
        error="skip",
    )
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    collector = MarketCollector(registry)
    collector._rhythm_analyzer = MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value = []
    collector._rhythm_analyzer.analyze.return_value = []

    raw_data = collector.collect_post_market("2026-07-27")

    assert set(raw_data["global_indices"]) == {
        "dow_jones",
        "nasdaq",
        "sp500",
        "a50",
    }
    assert set(raw_data["global_indices_apac"]) == {"nikkei", "kospi"}
    assert raw_data["us_china_assets"]["HXC"]["as_of"] == "2026-07-24"
    assert set(raw_data["commodities"]) == {"gold", "crude_oil", "copper"}
    assert set(raw_data["risk_indicators"]) == {
        "vix",
        "us10y",
        "cn10y",
        "cn30y",
    }
    assert raw_data["global_indices"]["dow_jones"]["_source"] == "mock:dow_jones"
    assert raw_data["global_indices"]["dow_jones"]["_fetched_at"] == "2026-07-27T20:00:00"
    assert raw_data["global_indices"]["a50"]["as_of"] == "2026-07-27"
    assert raw_data["commodities"]["gold"]["as_of"] == "2026-07-27"
    assert raw_data["_cross_asset_context"]["phase"] == "post"
    assert raw_data["_cross_asset_context"]["expected_date"] == "2026-07-27"
    assert (
        raw_data["_cross_asset_context"]["source_date_policy"]
        == "provider_as_of_else_fetch_only"
    )

    generator = ReportGenerator()
    generator.daily_dir = tmp_path / "daily"
    _, yaml_path = generator.generate_post_market("2026-07-27", raw_data)
    envelope = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    persisted = envelope["raw_data"]
    assert persisted["global_indices"]["sp500"]["as_of"] == "2026-07-27"
    assert persisted["global_indices"]["a50"]["as_of"] == "2026-07-27"
    assert persisted["commodities"]["gold"]["as_of"] == "2026-07-27"
    assert persisted["commodities"]["gold"]["_fetched_at"] == "2026-07-27T20:00:00"
    assert persisted["_cross_asset_context"]["phase"] == "post"


def test_cross_asset_historical_backfill_rejects_latest_only_snapshots():
    registry = MagicMock()

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success(
                {
                    "HXC": {
                        "name": "金龙代理",
                        "close": 23.71,
                        "change_pct": -0.63,
                        "as_of": "2026-07-27",
                    }
                }
            )
        if method == "get_global_index":
            return _success(
                {
                    "name": args[0],
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": "2026-07-27",
                }
            )
        if method == "get_commodity":
            return _success(
                {"name": args[0], "close": 50.0, "change_pct": 0.1}
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-24",
    )

    for block in (
        snapshot["global_indices"],
        snapshot["global_indices_apac"],
        snapshot["commodities"],
        snapshot["risk_indicators"],
    ):
        assert all(
            item["status"] == "historical_not_supported"
            and "close" not in item
            and "change_pct" not in item
            for item in block.values()
        )
    assert (
        snapshot["us_china_assets"]["HXC"]["status"]
        == "historical_not_supported"
    )
    assert "close" not in snapshot["us_china_assets"]["HXC"]


def test_cross_asset_passes_target_date_to_all_global_index_calls():
    registry = MagicMock()
    global_calls: list[tuple[str, str]] = []

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success(
                {
                    "HXC": {
                        "name": "金龙代理",
                        "close": 23.71,
                        "change_pct": -0.63,
                        "as_of": "2026-07-27",
                    }
                }
            )
        if method == "get_global_index":
            name, target_date = args
            global_calls.append((name, target_date))
            return _success(
                {
                    "name": name,
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": target_date,
                }
            )
        if method == "get_commodity":
            return _success(
                {
                    "name": args[0],
                    "close": 50.0,
                    "change_pct": 0.1,
                    "as_of": args[1],
                }
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-28",
    )

    assert snapshot["global_indices"]["dow_jones"]["as_of"] == "2026-07-28"
    assert snapshot["global_indices_apac"]["nikkei"]["as_of"] == "2026-07-28"
    assert global_calls == [
        ("dow_jones", "2026-07-28"),
        ("nasdaq", "2026-07-28"),
        ("sp500", "2026-07-28"),
        ("a50", "2026-07-28"),
        ("nikkei", "2026-07-28"),
        ("kospi", "2026-07-28"),
        ("vix", "2026-07-28"),
        ("us10y", "2026-07-28"),
        ("cn10y", "2026-07-28"),
        ("cn30y", "2026-07-28"),
    ]


@pytest.mark.parametrize(
    "bad_expected_date",
    ["2026-7-27", "07/27/2026", "2026-02-30", ""],
)
def test_cross_asset_rejects_noncanonical_expected_date(bad_expected_date):
    registry = MagicMock()

    with pytest.raises(ValueError, match="跨资产目标日期无效"):
        MarketCollector(registry)._collect_cross_asset_snapshot(
            phase="post",
            expected_date=bad_expected_date,
        )

    registry.call.assert_not_called()


def test_cross_asset_replaces_fetch_only_a50_with_explicit_dated_proxy():
    registry = MagicMock()

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success(
                {
                    "HXC": {
                        "name": "金龙代理",
                        "close": 23.71,
                        "change_pct": -0.63,
                        "as_of": "2026-07-27",
                    }
                }
            )
        if method == "get_global_index":
            name = args[0]
            if name == "a50":
                return DataResult(
                    data={
                        "name": "A50期指当月连续",
                        "close": 14826.0,
                        "change_pct": 0.05,
                    },
                    source="akshare:futures_global_spot_em",
                    fetched_at="2026-07-28T20:00:00",
                )
            if name == "a50_proxy":
                assert args == ("a50_proxy", "2026-07-28")
                return _success(
                    {
                        "name": "富时中国A50指数代理（XIN9）",
                        "close": 14852.88,
                        "change_pct": -2.4,
                        "as_of": "2026-07-28",
                        "instrument_kind": "index_proxy",
                        "proxy_for": "A50期指当月连续",
                    },
                    "tushare:index_global",
                )
            return _success(
                {
                    "name": name,
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": "2026-07-27",
                }
            )
        if method == "get_commodity":
            return _success(
                {
                    "name": args[0],
                    "close": 50.0,
                    "change_pct": 0.1,
                    "as_of": "2026-07-28",
                }
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-28",
    )

    a50 = snapshot["global_indices"]["a50"]
    assert a50["name"] == "富时中国A50指数代理（XIN9）"
    assert a50["as_of"] == "2026-07-28"
    assert a50["instrument_kind"] == "index_proxy"
    assert a50["proxy_for"] == "A50期指当月连续"
    assert a50["_source"] == "tushare:index_global"


def test_cross_asset_rejects_unmarked_a50_proxy_and_keeps_honest_fetch_only():
    registry = MagicMock()

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success({"HXC": {"error": "unavailable"}})
        if method == "get_global_index":
            name = args[0]
            if name == "a50":
                return DataResult(
                    data={
                        "name": "A50期指当月连续",
                        "close": 14826.0,
                        "change_pct": 0.05,
                    },
                    source="akshare:futures_global_spot_em",
                    fetched_at="2026-07-28T20:00:00",
                )
            if name == "a50_proxy":
                return _success(
                    {
                        "name": "未标记代理",
                        "close": 14852.88,
                        "change_pct": -2.4,
                        "as_of": "2026-07-28",
                    },
                    "mock:bad-proxy",
                )
            return _success(
                {
                    "name": name,
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": "2026-07-27",
                }
            )
        if method == "get_commodity":
            return _success(
                {
                    "name": args[0],
                    "close": 50.0,
                    "change_pct": 0.1,
                    "as_of": "2026-07-28",
                }
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-28",
    )

    a50 = snapshot["global_indices"]["a50"]
    assert a50["name"] == "A50期指当月连续"
    assert "as_of" not in a50
    assert a50["_source"] == "akshare:futures_global_spot_em"


@pytest.mark.parametrize("bad_source_date", ["07/27/2026", "2026-7-27"])
def test_cross_asset_rejects_invalid_source_date_and_malformed_ticker_key(
    bad_source_date,
):
    registry = MagicMock()

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success({1: {"close": 23.71}})
        if method == "get_global_index":
            return _success(
                {
                    "name": args[0],
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": bad_source_date,
                }
            )
        if method == "get_commodity":
            return _success(
                {
                    "name": args[0],
                    "close": 50.0,
                    "change_pct": 0.1,
                    "as_of": bad_source_date,
                }
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-27",
    )

    assert snapshot["global_indices"]["dow_jones"] == {
        "status": "historical_not_supported",
        "error": "dow_jones 当前接口只提供最新快照，不能用于补跑 2026-07-27",
        "source_date": bad_source_date,
        "expected_date": "2026-07-27",
        "_source": "mock",
        "_source_url": "",
        "_fetched_at": "2026-07-27T20:00:00",
        "_timeliness": "[实时]",
    }
    assert snapshot["us_china_assets"]["HXC"]["status"] == "missing_data"
    assert snapshot["us_china_assets"]["_error"].startswith("HXC 数据结构无效")


def test_cross_asset_normalizes_nested_hxc_failure():
    registry = MagicMock()

    def fake_call(method: str, *args, **kwargs):
        if method == "get_us_tickers_overnight":
            return _success({"HXC": {"error": "无隔夜对比数据"}})
        if method == "get_global_index":
            return _success(
                {
                    "name": args[0],
                    "close": 100.0,
                    "change_pct": 0.5,
                    "as_of": "2026-07-27",
                }
            )
        if method == "get_commodity":
            return _success(
                {"name": args[0], "close": 50.0, "change_pct": 0.1}
            )
        raise AssertionError(method)

    registry.call.side_effect = fake_call
    snapshot = MarketCollector(registry)._collect_cross_asset_snapshot(
        phase="post",
        expected_date="2026-07-27",
    )

    hxc = snapshot["us_china_assets"]["HXC"]
    assert hxc["status"] == "source_failed"
    assert hxc["error"] == "无隔夜对比数据"
    assert hxc["_source"] == "mock"
    assert hxc["_fetched_at"] == "2026-07-27T20:00:00"
