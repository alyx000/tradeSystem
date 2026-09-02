from __future__ import annotations

from unittest.mock import MagicMock

from analyzers.low_price_effect import (
    calculate_low_price_effect,
    collect_low_price_effect,
)
from collectors.market import MarketCollector
from generators.report import _render_low_price_effect
from providers.base import DataResult


TRADE_DATE = "2026-09-01"


def _quote(code: str, close: float, pct_chg: float, amount: float | None = 100_000) -> dict:
    return {
        "ts_code": code,
        "trade_date": "20260901",
        "close": close,
        "pct_chg": pct_chg,
        "amount": amount,
    }


def _sections() -> tuple[dict, dict]:
    return (
        {
            "count": 1,
            "stocks": [{"code": "000001.SZ"}],
            "_source": "test:limit_up",
        },
        {
            "count": 1,
            "stocks": [{"code": "000002.SZ"}],
            "_source": "test:limit_down",
        },
    )


def test_calculate_low_price_effect_complete_and_excludes_st_and_b_shares():
    quotes = [
        _quote("000001.SZ", 4.0, 10.0),
        _quote("000002.SZ", 5.0, -2.0),
        _quote("000003.SZ", 7.0, 2.0),
        _quote("000004.SZ", 10.0, 0.0),
        _quote("000005.SZ", 11.0, -1.0),
        _quote("200001.SZ", 3.0, 9.0),
        _quote("000006.SZ", 2.0, 8.0),
    ]
    limit_up, limit_down = _sections()

    result = calculate_low_price_effect(
        quotes,
        [{"ts_code": "000006.SZ", "name": "*ST示例"}],
        TRADE_DATE,
        quote_source="test:daily",
        st_source="test:stock_st",
        limit_up_section=limit_up,
        limit_down_section=limit_down,
        min_unique_quote_count=7,
    )

    assert result["status"] == "complete"
    assert result["coverage"]["excluded_st_count"] == 1
    assert result["coverage"]["excluded_b_share_count"] == 1
    assert result["coverage"]["eligible_market_count"] == 5
    low = result["low_price"]
    assert low["sample_count"] == 4  # close==10 纳入，11 元不纳入
    assert low["pct_chg_median"] == 1.0
    assert low["pct_chg_mean"] == 2.5
    assert low["advance_rate"] == 0.5
    assert low["strong_gain_rate"] == 0.25
    assert low["limit_up_rate"] == 0.25
    assert low["limit_down_rate"] == 0.25
    assert low["median_excess_vs_market_pp"] == 1.0
    assert low["amount_share_pct"] == 80.0
    assert [band["sample_count"] for band in result["bands"]] == [2, 2]


def test_calculate_low_price_effect_fails_closed_on_duplicate_or_thin_quotes():
    limit_up, limit_down = _sections()
    duplicate = calculate_low_price_effect(
        [_quote("000001.SZ", 4, 1), _quote("000001.SZ", 4, 1)],
        [{"ts_code": "000099.SZ"}],
        TRADE_DATE,
        limit_up_section=limit_up,
        limit_down_section=limit_down,
        min_unique_quote_count=1,
    )
    assert duplicate["status"] == "source_failed"
    assert "重复代码" in duplicate["error"]

    thin = calculate_low_price_effect(
        [_quote("000001.SZ", 4, 1)],
        [{"ts_code": "000099.SZ"}],
        TRADE_DATE,
        limit_up_section=limit_up,
        limit_down_section=limit_down,
        min_unique_quote_count=2,
    )
    assert thin["status"] == "source_failed"
    assert "有效日线不足" in thin["error"]


def test_calculate_low_price_effect_marks_auxiliary_gaps_partial():
    result = calculate_low_price_effect(
        [
            _quote("000001.SZ", 4, 1, amount=100_000),
            _quote("000002.SZ", 8, -1, amount=None),
        ],
        [{"ts_code": "000099.SZ"}],
        TRADE_DATE,
        limit_up_section={"error": "up unavailable"},
        limit_down_section={"count": 0, "stocks": [], "_source": "test:down"},
        min_unique_quote_count=2,
    )

    assert result["status"] == "partial"
    assert result["low_price"]["pct_chg_median"] == 0.0
    assert result["low_price"]["amount_share_pct"] is None
    assert result["low_price"]["limit_up_rate"] is None
    assert result["low_price"]["limit_down_rate"] == 0.0
    assert any("成交额字段覆盖不足" in gap for gap in result["gaps"])
    assert any("涨停来源失败" in gap for gap in result["gaps"])


def test_incomplete_limit_code_coverage_is_partial_and_rate_is_not_computed():
    result = calculate_low_price_effect(
        [_quote("000001.SZ", 4, 1), _quote("000002.SZ", 8, -1)],
        [{"ts_code": "000099.SZ"}],
        TRADE_DATE,
        limit_up_section={
            "count": 2,
            "stocks": [{"code": "000001.SZ"}, {"name": "缺代码"}],
        },
        limit_down_section={"count": 0, "stocks": []},
        min_unique_quote_count=2,
    )

    assert result["status"] == "partial"
    assert result["low_price"]["limit_up_count"] is None
    assert result["low_price"]["limit_up_rate"] is None
    assert any("涨停事实代码覆盖不足" in gap for gap in result["gaps"])


def test_collect_low_price_effect_preserves_quote_source_failure():
    registry = MagicMock()
    registry.call.return_value = DataResult(
        data=None,
        source="test:daily",
        error="network down",
    )

    result = collect_low_price_effect(
        registry,
        TRADE_DATE,
        stock_st_result=DataResult(data=[{"ts_code": "000099.SZ"}], source="test:st"),
        limit_up_section={"count": 0, "stocks": []},
        limit_down_section={"count": 0, "stocks": []},
    )

    assert result["status"] == "source_failed"
    assert result["error"] == "network down"


def test_market_collector_wires_low_price_effect_into_post_market(monkeypatch):
    registry = MagicMock()

    def _call(name, *_args, **_kwargs):
        if name == "get_stock_st":
            return DataResult(data=[{"ts_code": "000099.SZ"}], source="test:st")
        return DataResult(data=None, source="test", error="skip")

    registry.call.side_effect = _call
    expected = {
        "status": "complete",
        "trade_date": TRADE_DATE,
        "low_price": {"sample_count": 123},
    }
    low_price_mock = MagicMock(return_value=expected)
    monkeypatch.setattr(
        "analyzers.low_price_effect.collect_low_price_effect",
        low_price_mock,
    )
    monkeypatch.setattr("analyzers.StyleAnalyzer.analyze", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("analyzers.NodeSignalAnalyzer.analyze", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "analyzers.board_break_feedback.collect_board_break_feedback",
        lambda *_args, **_kwargs: {"status": "complete", "sample_count": 0},
    )
    collector = MarketCollector(registry)
    collector._compute_index_ma = MagicMock()
    collector._collect_research_coverage = MagicMock(return_value=[])
    collector._rhythm_analyzer = MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value = []
    collector._rhythm_analyzer.analyze.return_value = []

    result = collector.collect_post_market(TRADE_DATE)

    assert result["low_price_effect"] == expected
    assert low_price_mock.call_args.kwargs["stock_st_result"].source == "test:st"


def test_render_low_price_effect_complete_and_source_failed():
    complete = {
        "status": "complete",
        "definition": {
            "low_price_max_yuan": 10.0,
            "very_low_price_max_yuan": 5.0,
        },
        "coverage": {
            "unique_quote_count": 5300,
            "eligible_market_count": 5100,
            "amount_coverage_ratio": 1.0,
        },
        "low_price": {
            "sample_count": 800,
            "pct_chg_median": 1.2,
            "pct_chg_mean": 1.5,
            "advance_rate": 0.6,
            "strong_gain_rate": 0.1,
            "limit_up_rate": 0.03,
            "limit_down_rate": 0.01,
            "median_excess_vs_market_pp": 0.8,
            "amount_share_pct": 12.34,
        },
        "bands": [],
        "market_benchmark": {
            "sample_count": 5100,
            "pct_chg_median": 0.4,
            "pct_chg_mean": 0.3,
            "advance_rate": 0.52,
            "strong_gain_rate": 0.05,
            "limit_up_rate": 0.01,
            "limit_down_rate": 0.005,
        },
    }
    lines: list[str] = []
    assert _render_low_price_effect(lines, {"low_price_effect": complete}, 8) == 9
    text = "\n".join(lines)
    assert "低价股赚钱效应" in text
    assert "低价股（≤10元）" in text
    assert "+0.80%" in text
    assert "12.34%" in text

    failed_lines: list[str] = []
    failed = {"status": "source_failed", "error": "行情不可得"}
    assert _render_low_price_effect(
        failed_lines, {"low_price_effect": failed}, 3
    ) == 4
    failed_text = "\n".join(failed_lines)
    assert "来源失败" in failed_text
    assert "不代表低价股样本为 0" in failed_text
