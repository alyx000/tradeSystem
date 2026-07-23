"""中国货币网 USD/CNY 即期与 C-Swap 接口：mock HTTP，无外网。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from collectors.market import MarketCollector
from generators.report import ReportGenerator
from providers.akshare_provider import AkshareProvider
from providers.base import DataResult, Timeliness
from utils.fx_validation import CHINAMONEY_C_SWAP_URL, CHINAMONEY_SPOT_URL


@pytest.fixture
def provider() -> AkshareProvider:
    p = AkshareProvider({"http_timeout": 8})
    p._initialized = True
    p.ak = MagicMock()
    return p


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


@patch("providers.akshare_provider.requests.post")
def test_get_forex_usd_cny_uses_chinamoney_onshore_spot(mock_post, provider):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {"showDateCN": "2026-07-22 23:11:06"},
            "records": [
                {"ccyPair": "EUR/CNY", "bidPrc": "7.72", "askPrc": "7.73"},
                {"ccyPair": "USD/CNY", "bidPrc": "6.7725", "askPrc": "6.7733"},
            ],
        }
    )

    result = provider.get_forex("usd_cny")

    assert result.success
    assert result.source == "chinamoney:rfx-sp-quot"
    assert result.data == {
        "status": "ok",
        "name": "USD/CNY（在岸即期）",
        "pair": "USD/CNY",
        "mid": 6.7729,
        "close": 6.7729,
        "bid": 6.7725,
        "ask": 6.7733,
        "snapshot_time": "2026-07-22 23:11:06",
        "source_date": "2026-07-22",
        "source_date_kind": "source_page_update_date",
        "price_kind": "computed_bid_ask_mid",
        "close_semantics": "computed_bid_ask_mid",
        "mid_method": "bid_ask_arithmetic_mean",
    }
    mock_post.return_value.raise_for_status.assert_called_once_with()
    assert mock_post.call_args.kwargs["timeout"] == 8.0
    provider.ak.forex_spot_em.assert_not_called()


@patch("providers.akshare_provider.requests.post")
def test_get_forex_usd_cny_invalid_quote_fails_closed(mock_post, provider):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {"showDateCN": "2026-07-22 23:11:06"},
            "records": [{"ccyPair": "USD/CNY", "bidPrc": "---", "askPrc": "6.7733"}],
        }
    )

    result = provider.get_forex("usd_cny")

    assert not result.success
    assert result.data is None
    assert "买卖报价无效" in result.error
    provider.ak.forex_spot_em.assert_not_called()


@pytest.mark.parametrize(
    ("bid", "ask"),
    [("0.5", "0.6"), ("100", "101"), ("6.7", "6.81")],
)
@patch("providers.akshare_provider.requests.post")
def test_get_forex_usd_cny_rejects_implausible_range_or_spread(
    mock_post, provider, bid, ask
):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {"showDateCN": "2026-07-22 23:11:06"},
            "records": [{"ccyPair": "USD/CNY", "bidPrc": bid, "askPrc": ask}],
        }
    )

    result = provider.get_forex("usd_cny")

    assert not result.success
    assert "买卖报价无效" in result.error


@patch("providers.akshare_provider.requests.post")
def test_get_forex_usd_cny_rejects_float_overflow(mock_post, provider):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {"showDateCN": "2026-07-22 23:11:06"},
            "records": [
                {"ccyPair": "USD/CNY", "bidPrc": "1e309", "askPrc": "1e309"}
            ],
        }
    )

    result = provider.get_forex("usd_cny")

    assert not result.success
    assert "买卖报价无效" in result.error


@patch("providers.akshare_provider.requests.post")
def test_get_fx_swap_returns_one_year_c_swap_fixing(mock_post, provider):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {
                "currencyPair": "USD.CNY",
                "lastDate": "2026-07-22 16:30:00",
            },
            "records": [
                {
                    "currencyPair": "USD.CNY",
                    "tenor": "6M",
                    "swapPnt": -893.9,
                    "swapAllPrc": 6.6843,
                    "dataSource": "报价数据",
                    "curveTime": "2026-07-22 16:30:00.0",
                },
                {
                    "currencyPair": "USD.CNY",
                    "tenor": "1Y",
                    "swapPnt": -1818.25,
                    "swapAllPrc": 6.5918,
                    "dataSource": "报价数据",
                    "curveTime": "2026-07-22 16:30:00.0",
                },
            ],
        }
    )

    result = provider.get_fx_swap("usd_cny", "1 Y")

    assert result.success
    assert result.source == "chinamoney:fx-c-swap-fixing"
    assert result.timeliness is Timeliness.RECENT
    assert result.data["tenor"] == "1Y"
    assert result.data["swap_point_pips"] == -1818.25
    assert result.data["forward_rate"] == 6.5918
    assert result.data["outright_rate"] == 6.5918
    assert result.data["curve_time"] == "2026-07-22 16:30:00.0"
    assert result.data["fixing_at"] == "2026-07-22 16:30:00.0"
    assert result.data["source_date"] == "2026-07-22"
    assert result.data["quote_source"] == "报价数据"


@patch("providers.akshare_provider.requests.post")
def test_get_fx_swap_missing_tenor_is_source_failure(mock_post, provider):
    mock_post.return_value = _response(
        {"head": {"rep_code": "200"}, "data": {}, "records": []}
    )

    result = provider.get_fx_swap("USD/CNY", "1Y")

    assert not result.success
    assert "未找到期限 1Y" in result.error


@pytest.mark.parametrize(
    ("curve_time", "last_date", "source", "swap_point", "forward_rate"),
    [
        ("2026-07-22 10:00:00.0", "2026-07-22 10:00:00", "报价数据", -10, 6.7),
        ("2026-07-22 16:30:00.0", "2026-07-21 16:30:00", "报价数据", -10, 6.7),
        ("2026-07-22 16:30:00.0", "2026-07-22 16:30:00", "unexpected", -10, 6.7),
        ("2026-07-22 16:30:00.0", "2026-07-22 16:30:00", "报价数据", 99_999_999, 1000),
    ],
)
@patch("providers.akshare_provider.requests.post")
def test_get_fx_swap_rejects_non_fixing_payload(
    mock_post,
    provider,
    curve_time,
    last_date,
    source,
    swap_point,
    forward_rate,
):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": {"currencyPair": "USD.CNY", "lastDate": last_date},
            "records": [
                {
                    "currencyPair": "USD.CNY",
                    "tenor": "1Y",
                    "swapPnt": swap_point,
                    "swapAllPrc": forward_rate,
                    "dataSource": source,
                    "curveTime": curve_time,
                }
            ],
        }
    )

    result = provider.get_fx_swap("usd_cny", "1Y")

    assert not result.success


def test_get_fx_swap_rejects_unsupported_pair_without_http(provider):
    with patch("providers.akshare_provider.requests.post") as mock_post:
        result = provider.get_fx_swap("usd_cnh", "1Y")

    assert not result.success
    assert "不支持外汇掉期货币对" in result.error
    mock_post.assert_not_called()


def test_akshare_declares_fx_swap_capability(provider):
    assert "get_fx_swap" in provider.get_capabilities()


def test_chinamoney_source_can_be_disabled():
    provider = AkshareProvider({"chinamoney_enabled": False})
    with patch("providers.akshare_provider.requests.post") as mock_post:
        spot = provider.get_forex("usd_cny")
        swap = provider.get_fx_swap("usd_cny", "1Y")

    assert spot.error == "ChinaMoney 数据源已禁用"
    assert swap.error == "ChinaMoney 数据源已禁用"
    mock_post.assert_not_called()


def test_invalid_response_status_is_rejected(provider):
    with patch("providers.akshare_provider.requests.post") as mock_post:
        mock_post.return_value = _response(
            {"head": {"rep_code": "500"}, "data": {}, "records": []}
        )
        result = provider.get_forex("usd_cny")

    assert not result.success
    assert "响应状态无效" in result.error


@patch("providers.akshare_provider.requests.post")
def test_spot_rejects_non_mapping_data(mock_post, provider):
    mock_post.return_value = _response(
        {
            "head": {"rep_code": "200"},
            "data": None,
            "records": [{"ccyPair": "USD/CNY", "bidPrc": "6.77", "askPrc": "6.78"}],
        }
    )

    result = provider.get_forex("usd_cny")

    assert not result.success
    assert "缺少 data" in result.error


def test_checked_snapshot_rejects_non_mapping_payload():
    result = MarketCollector._checked_snapshot(
        None, "2026-07-22", "USD/CNY 在岸即期"
    )

    assert result["status"] == "missing_data"
    assert result["expected_date"] == "2026-07-22"
    assert "数据结构无效" in result["error"]


def test_pre_spot_snapshot_accepts_only_fresh_pre_open_page_time():
    now = datetime(2026, 7, 22, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    base = {
        "snapshot_time": "2026-07-22 06:55:00",
        "source_date": "2026-07-22",
    }

    accepted = MarketCollector._checked_pre_spot_snapshot(
        dict(base), "2026-07-22", now=now
    )
    stale = MarketCollector._checked_pre_spot_snapshot(
        {**base, "snapshot_time": "2026-07-22 06:30:00"},
        "2026-07-22",
        now=now,
    )
    future = MarketCollector._checked_pre_spot_snapshot(
        {**base, "snapshot_time": "2026-07-22 07:00:01"},
        "2026-07-22",
        now=now,
    )
    cutoff_snapshot = MarketCollector._checked_pre_spot_snapshot(
        {**base, "snapshot_time": "2026-07-22 09:30:00"},
        "2026-07-22",
        now=datetime(2026, 7, 22, 9, 29, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    after_open = MarketCollector._checked_pre_spot_snapshot(
        dict(base),
        "2026-07-22",
        now=datetime(2026, 7, 22, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert accepted["status"] == "latest_available"
    assert stale["status"] == "stale_snapshot"
    assert future["status"] == "invalid_snapshot_time"
    assert cutoff_snapshot["status"] == "lookahead_not_allowed"
    assert after_open["status"] == "lookahead_not_allowed"


def test_setup_providers_production_path_exposes_fx_swap():
    from main import setup_providers

    registry = setup_providers(
        {
            "providers": {
                "tushare": {"enabled": False},
                "akshare": {"enabled": True},
                "sina": {"enabled": False},
                "tdx": {"enabled": False},
            }
        }
    )
    provider = registry.get_provider("akshare")
    assert provider is not None
    assert provider.supports("get_forex")
    assert provider.supports("get_fx_swap")


def test_collect_post_market_wires_same_day_fx_snapshot(tmp_path, monkeypatch):
    def fake_call(method: str, *args, **kwargs):
        if method == "get_forex":
            pair = args[0]
            data = {"name": pair, "close": 1.0, "change_pct": 0.0}
            if pair == "usd_cny":
                data.update({"source_date": "2026-07-22", "bid": 6.7725, "ask": 6.7733})
            return DataResult(data=data, source="chinamoney:rfx-sp-quot")
        if method == "get_fx_swap":
            return DataResult(
                data={
                    "name": "USD/CNY 1Y C-Swap定盘",
                    "source_date": "2026-07-22",
                    "swap_point_pips": -1818.25,
                    "forward_rate": 6.5918,
                    "curve_time": "2026-07-22 16:30:00.0",
                },
                source="chinamoney:fx-c-swap-fixing",
            )
        return DataResult(data=None, source="mock", error="skip")

    registry = MagicMock()
    registry.call.side_effect = fake_call
    registry.call_specific.return_value = DataResult(data=None, source="mock", error="skip")
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)
    collector = MarketCollector(registry)
    collector._rhythm_analyzer = MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value = []
    collector._rhythm_analyzer.analyze.return_value = []

    result = collector.collect_post_market("2026-07-22")

    requested_forex_pairs = [
        call.args[1]
        for call in registry.call.call_args_list
        if call.args and call.args[0] == "get_forex"
    ]
    assert requested_forex_pairs == ["usd_cny"]
    assert result["forex"]["usd_cny"]["status"] == "ok"
    assert result["forex"]["usd_cny"]["_source"] == "chinamoney:rfx-sp-quot"
    assert result["fx_swaps"]["usd_cny_1y"]["status"] == "ok"
    assert result["fx_swaps"]["usd_cny_1y"]["swap_point_pips"] == -1818.25


def test_generate_post_market_renders_fx_source_and_fixing(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    raw_data = {
        "indices": {},
        "forex": {
            "usd_cny": {
                "status": "ok",
                "name": "USD/CNY（在岸即期）",
                "pair": "USD/CNY",
                "mid": 6.7729,
                "close": 6.7729,
                "bid": 6.7725,
                "ask": 6.7733,
                "snapshot_time": "2026-07-22 20:00:00",
                "source_date": "2026-07-22",
                "validated_for_date": "2026-07-22",
                "price_kind": "computed_bid_ask_mid",
                "close_semantics": "computed_bid_ask_mid",
                "mid_method": "bid_ask_arithmetic_mean",
                "_source": "chinamoney:rfx-sp-quot",
                "_source_url": CHINAMONEY_SPOT_URL,
            }
        },
        "fx_swaps": {
            "usd_cny_1y": {
                "status": "ok",
                "name": "USD/CNY 1Y C-Swap定盘",
                "pair": "USD/CNY",
                "tenor": "1Y",
                "swap_point_pips": -1818.25,
                "forward_rate": 6.5918,
                "curve_time": "2026-07-22 16:30:00.0",
                "source_date": "2026-07-22",
                "validated_for_date": "2026-07-22",
                "quote_source": "报价数据",
                "fixing_source": "报价数据",
                "price_kind": "c_swap_fixing",
                "_source": "chinamoney:fx-c-swap-fixing",
                "_source_url": CHINAMONEY_C_SWAP_URL,
            }
        },
        "_fx_context": {
            "phase": "post",
            "spot_expected_date": "2026-07-22",
            "swap_expected_date": "2026-07-22",
        },
    }

    markdown, _ = generator.generate_post_market("2026-07-22", raw_data)

    assert "汇率与外汇掉期" in markdown
    assert "系统按买 6.7725 / 卖 6.7733 计算中值" in markdown
    assert "数据页更新于 2026-07-22 20:00:00" in markdown
    assert "USD/CNY 1Y C-Swap定盘: -1818.25 Pips" in markdown
    assert "来源 中国货币网" in markdown


def test_generate_post_market_rejects_future_unvalidated_fx(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    raw_data = {
        "indices": {},
        "forex": {
            "usd_cny": {
                "status": "ok",
                "name": "USD/CNY（在岸即期）",
                "pair": "USD/CNY",
                "mid": 6.8,
                "close": 6.8,
                "bid": 6.7,
                "ask": 6.9,
                "snapshot_time": "2026-07-23 07:00:00",
                "source_date": "2026-07-23",
                "validated_for_date": "2026-07-23",
                "price_kind": "computed_bid_ask_mid",
                "close_semantics": "computed_bid_ask_mid",
                "mid_method": "bid_ask_arithmetic_mean",
                "_source": "chinamoney:rfx-sp-quot",
                "_source_url": CHINAMONEY_SPOT_URL,
            }
        },
        "fx_swaps": {},
        "_fx_context": {
            "phase": "post",
            "spot_expected_date": "2026-07-23",
            "swap_expected_date": "2026-07-22",
        },
    }

    markdown, _ = generator.generate_post_market("2026-07-22", raw_data)

    assert "usd_cny: 数据可信度校验失败: 即期预期日期与报告日期不一致" in markdown
    assert "[事实] USD/CNY（在岸即期）" not in markdown


def test_generate_post_market_rejects_wrong_swap_endpoint_and_time(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    raw_data = {
        "indices": {},
        "forex": {},
        "fx_swaps": {
            "usd_cny_1y": {
                "status": "ok",
                "name": "USD/CNY 1Y C-Swap定盘",
                "pair": "USD/CNY",
                "tenor": "1Y",
                "swap_point_pips": -10,
                "forward_rate": 6.7,
                "curve_time": "2026-07-22 10:00:00",
                "source_date": "2026-07-22",
                "validated_for_date": "2026-07-22",
                "quote_source": "unexpected",
                "fixing_source": "unexpected",
                "price_kind": "c_swap_fixing",
                "_source": "chinamoney:rfx-sp-quot",
                "_source_url": CHINAMONEY_SPOT_URL,
            }
        },
        "_fx_context": {
            "phase": "post",
            "spot_expected_date": "2026-07-22",
            "swap_expected_date": "2026-07-22",
        },
    }

    markdown, _ = generator.generate_post_market("2026-07-22", raw_data)

    assert "数据可信度校验失败: 中国货币网来源端点不匹配" in markdown
    assert "[事实] USD/CNY 1Y C-Swap定盘" not in markdown


def test_generate_post_market_rejects_bad_url_and_corrupt_fx_values(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    context = {
        "phase": "post",
        "spot_expected_date": "2026-07-22",
        "swap_expected_date": "2026-07-22",
    }
    spot = {
        "status": "ok",
        "name": "USD/CNY（在岸即期）",
        "pair": "USD/CNY",
        "mid": -999,
        "close": -999,
        "bid": 999,
        "ask": 1,
        "snapshot_time": "2026-07-22 20:00:00",
        "source_date": "2026-07-22",
        "validated_for_date": "2026-07-22",
        "price_kind": "computed_bid_ask_mid",
        "close_semantics": "computed_bid_ask_mid",
        "mid_method": "bid_ask_arithmetic_mean",
        "_source": "chinamoney:rfx-sp-quot",
        "_source_url": "https://evil.invalid",
    }
    raw_data = {
        "indices": {},
        "forex": {"usd_cny": spot},
        "fx_swaps": {},
        "_fx_context": context,
    }

    bad_url_markdown, _ = generator.generate_post_market("2026-07-22", raw_data)
    spot["_source_url"] = CHINAMONEY_SPOT_URL
    bad_value_markdown, _ = generator.generate_post_market("2026-07-22", raw_data)

    assert "数据可信度校验失败: 中国货币网来源 URL 不匹配" in bad_url_markdown
    assert "数据可信度校验失败: 即期报价数值未通过校验" in bad_value_markdown
    assert "[事实] USD/CNY（在岸即期）" not in bad_value_markdown


def test_generate_post_market_rejects_non_numeric_swap(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    raw_data = {
        "indices": {},
        "forex": {},
        "fx_swaps": {
            "usd_cny_1y": {
                "status": "ok",
                "name": "USD/CNY 1Y C-Swap定盘",
                "pair": "USD/CNY",
                "tenor": "1Y",
                "swap_point_pips": "not-a-number",
                "forward_rate": 999,
                "curve_time": "2026-07-22 16:30:00",
                "source_date": "2026-07-22",
                "validated_for_date": "2026-07-22",
                "quote_source": "报价数据",
                "fixing_source": "报价数据",
                "price_kind": "c_swap_fixing",
                "_source": "chinamoney:fx-c-swap-fixing",
                "_source_url": CHINAMONEY_C_SWAP_URL,
            }
        },
        "_fx_context": {
            "phase": "post",
            "spot_expected_date": "2026-07-22",
            "swap_expected_date": "2026-07-22",
        },
    }

    markdown, _ = generator.generate_post_market("2026-07-22", raw_data)

    assert "数据可信度校验失败: C-Swap 定盘数值未通过校验" in markdown
    assert "[事实] USD/CNY 1Y C-Swap定盘" not in markdown
