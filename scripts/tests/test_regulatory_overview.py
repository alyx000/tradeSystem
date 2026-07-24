"""监管异动 overview 的纯计算与持久化契约。

所有行情和事件均使用内存 fixture；禁止连接真实 Tushare 或生产数据库。
"""
from __future__ import annotations

from datetime import date, timedelta
import json

import pytest

from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.regulatory_overview import (
    RegulatoryOverviewService,
    _regulatory_stock_pct_map,
    _required_next_move,
    _reachable_at_price_limit,
    benchmark_for_code,
    calculate_cumulative_deviation,
    calculate_period_return_deviation,
    dedupe_same_direction_events,
)


@pytest.mark.parametrize(
    ("ts_code", "expected"),
    [
        ("600664.SH", "000001.SH"),
        ("601318", "000001.SH"),
        ("001258.SZ", "399107.SZ"),
        ("000815", "399107.SZ"),
        ("300534.SZ", "399102.SZ"),
        ("301520", "399102.SZ"),
        ("688432.SH", "000688.SH"),
        ("689009", "000688.SH"),
    ],
)
def test_benchmark_for_code_uses_four_official_market_benchmarks(ts_code, expected):
    assert benchmark_for_code(ts_code) == expected


@pytest.mark.parametrize("ts_code", ["", "ABC", "900001.SH", None])
def test_benchmark_for_code_rejects_unknown_or_non_a_share_code(ts_code):
    assert benchmark_for_code(ts_code) is None


def test_calculate_cumulative_deviation_sums_daily_exchange_deviations():
    dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
    stock = {
        "2026-07-20": 10.0,
        "2026-07-21": 10.0,
        "2026-07-22": 0.0,
    }
    index = {
        "2026-07-20": 5.0,
        "2026-07-21": 0.0,
        "2026-07-22": 5.0,
    }

    assert calculate_cumulative_deviation(stock, index, dates) == pytest.approx(10.0)


def test_calculate_cumulative_deviation_keeps_negative_direction():
    dates = ["2026-07-20", "2026-07-21"]
    stock = {"2026-07-20": -10.0, "2026-07-21": -10.0}
    index = {"2026-07-20": 0.0, "2026-07-21": 0.0}

    assert calculate_cumulative_deviation(stock, index, dates) == pytest.approx(-20.0)


def test_calculate_period_return_deviation_uses_sse_main_board_formula():
    dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
    stock = {
        "2026-07-20": 10.0,
        "2026-07-21": 10.0,
        "2026-07-22": 0.0,
    }
    index = {
        "2026-07-20": 5.0,
        "2026-07-21": 0.0,
        "2026-07-22": 5.0,
    }

    assert calculate_period_return_deviation(stock, index, dates) == pytest.approx(10.75)


@pytest.mark.parametrize(
    ("threshold", "assumed_index_pct"),
    [(100.0, 1.0), (-50.0, -1.0)],
)
def test_sse_period_required_move_reconstructs_threshold(
    threshold,
    assumed_index_pct,
):
    dates = ["2026-07-20", "2026-07-21"]
    stock = {"2026-07-20": 10.0, "2026-07-21": 10.0}
    index = {"2026-07-20": 5.0, "2026-07-21": 0.0}

    required = _required_next_move(
        stock,
        index,
        dates,
        threshold,
        assumed_index_pct=assumed_index_pct,
        formula="period_return_difference",
    )

    assert required is not None
    extended_dates = dates + ["2026-07-22"]
    reconstructed = calculate_period_return_deviation(
        {**stock, "2026-07-22": required},
        {**index, "2026-07-22": assumed_index_pct},
        extended_dates,
    )
    assert reconstructed == pytest.approx(threshold)


@pytest.mark.parametrize(
    ("stock", "index", "dates"),
    [
        ({"2026-07-20": 1.0}, {}, ["2026-07-20"]),
        ({}, {"2026-07-20": 1.0}, ["2026-07-20"]),
        (
            {"2026-07-20": 1.0},
            {"2026-07-20": 1.0},
            ["2026-07-20", "2026-07-21"],
        ),
    ],
)
def test_calculate_cumulative_deviation_returns_none_for_any_missing_market_day(
    stock, index, dates
):
    assert calculate_cumulative_deviation(stock, index, dates) is None


def test_calculate_cumulative_deviation_empty_window_is_missing_not_zero():
    assert calculate_cumulative_deviation({}, {}, []) is None


def test_price_limit_close_uses_statutory_limit_pct_for_deviation():
    pct = _regulatory_stock_pct_map(
        [
            {
                "trade_date": "2026-07-22",
                "pre_close": 10.01,
                "close": 11.01,
                "pct_chg": 9.99,
            }
        ],
        10.0,
    )

    assert pct["2026-07-22"] == 10.0


def test_twenty_percent_down_limit_uses_statutory_limit_pct_for_deviation():
    pct = _regulatory_stock_pct_map(
        [
            {
                "trade_date": "2026-07-22",
                "pre_close": 10.01,
                "close": 8.01,
                "pct_chg": -19.98,
            }
        ],
        20.0,
    )

    assert pct["2026-07-22"] == -20.0


def test_dedupe_same_direction_events_dedupes_only_same_stock_date_and_direction():
    rows = [
        {
            "ts_code": "300534.SZ",
            "trade_date": "2026-07-22",
            "direction": "up",
            "reason": "日涨幅偏离值达到规定阈值",
        },
        {
            "code": "300534.SZ",
            "trade_date_norm": "2026-07-22",
            "direction": "up",
            "reason": "重复披露文本",
        },
        {
            "ts_code": "300534.SZ",
            "trade_date": "2026-07-22",
            "direction": "down",
            "reason": "同日反向事件必须保留",
        },
        {
            "ts_code": "300534.SZ",
            "trade_date": "2026-07-23",
            "direction": "up",
            "reason": "次日同向事件必须保留",
        },
    ]

    deduped = dedupe_same_direction_events(rows)

    assert len(deduped) == 3
    keys = {
        (
            row.get("ts_code") or row.get("code"),
            row.get("trade_date_norm") or row.get("trade_date"),
            row["direction"],
        )
        for row in deduped
    }
    assert keys == {
        ("300534.SZ", "2026-07-22", "up"),
        ("300534.SZ", "2026-07-22", "down"),
        ("300534.SZ", "2026-07-23", "up"),
    }


def test_service_exposes_frozen_policy_version():
    service = RegulatoryOverviewService(registry=None, db_path=None)
    assert service.calculation_policy_version == "regulatory-v2"


def _open_dates(count: int = 30) -> list[str]:
    start = date(2026, 6, 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


class _CalculationRegistry:
    def __init__(self, *, st_error: str = ""):
        self.st_error = st_error

    def call(self, method: str, *args, **kwargs):
        if method == "get_stock_st":
            return DataResult(
                data=None if self.st_error else [],
                source="test:st",
                error=self.st_error,
            )
        if method == "get_stock_basic_batch":
            return DataResult(
                data=[
                    {
                        "ts_code": code,
                        "name": code,
                        "list_date": "20000101",
                    }
                    for code in args[0]
                ],
                source="test:stock_basic",
            )
        if method in {"get_stock_daily_range", "get_index_daily_range"}:
            dates = _open_dates()
            pct = 5.0 if method == "get_stock_daily_range" else 0.0
            return DataResult(
                data=[
                    {"trade_date": trade_date, "pct_chg": pct, "close": 5.01}
                    for trade_date in dates
                ],
                source=f"test:{method}",
            )
        raise AssertionError(f"unexpected provider method: {method}")


def test_down_same_direction_count_keeps_down_direction():
    dates = _open_dates()
    rows = [
        {
            "ts_code": "300534.SZ",
            "trade_date": trade_date,
            "direction": "down",
        }
        for trade_date in dates[-3:]
    ]

    class _DownRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                        data=[
                            {
                                "trade_date": trade_date,
                                "pct_chg": (
                                    -5.0 if trade_date in dates[-3:-1] else 0.0
                                ),
                                "close": 10.0,
                            }
                            for trade_date in dates
                        ],
                    source="test:stock",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_DownRegistry())

    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[],
        shock_rows=rows,
        high_rows=[],
        limit_rows=[],
    )

    count_rows = [
        item
        for item in result["today"]
        if item["criterion"] == "same_direction_count"
    ]
    assert count_rows
    assert {item["direction"] for item in count_rows} == {"down"}


def test_today_candidate_uses_prior_days_and_target_date_index_move():
    dates = _open_dates()

    class _TodayRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": (
                                10.5
                                if trade_date == dates[-10]
                                else 10.0
                                if trade_date in dates[-9:-1]
                                else 0.0
                            ),
                            "close": 10.0,
                            "pre_close": 9.5 if trade_date == dates[-1] else 10.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            if method == "get_index_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": 0.5 if trade_date == dates[-1] else 0.0,
                            "close": 100.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:index",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_TodayRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "001258.SZ", "name": "立新能源"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    candidate = next(
        item
        for item in result["today"]
        if item["criterion"] == "deviation_10d" and item["direction"] == "up"
    )
    assert candidate["required_pct"] == pytest.approx(10.0)
    assert candidate["target_price"] == 10.45
    assert candidate["price_base_date"] == dates[-1]
    assert candidate["price_base"] == 9.5
    assert candidate["price_base_status"] == "target_pre_close"
    assert candidate["index_assumption"] == "target_date_actual"
    assert candidate["reachable_at_limit"] is True


def test_next_day_candidate_uses_nine_days_including_target_and_flat_index():
    dates = _open_dates()

    class _NextDayRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": 10.0 if trade_date in dates[-9:] else 0.0,
                            "close": 10.0,
                            "pre_close": 10.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_NextDayRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "001258.SZ", "name": "立新能源"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    candidate = next(
        item
        for item in result["next_day"]
        if item["criterion"] == "deviation_10d" and item["direction"] == "up"
    )
    assert candidate["required_pct"] == pytest.approx(10.0)
    assert candidate["target_price"] == 11.0
    assert candidate["price_base_date"] == dates[-1]
    assert candidate["index_assumption"] == "flat"


def test_sse_main_board_candidates_use_period_return_difference():
    dates = _open_dates()
    stock_moves = {
        dates[-3]: 10.0,
        dates[-2]: 10.0,
        dates[-1]: 0.0,
    }
    index_moves = {
        dates[-3]: 5.0,
        dates[-2]: 0.0,
        dates[-1]: 5.0,
    }

    class _SseRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": stock_moves.get(trade_date, 0.0),
                            "close": 10.0,
                            "pre_close": 10.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            if method == "get_index_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": index_moves.get(trade_date, 0.0),
                            "close": 100.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:index",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_SseRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH", "name": "哈药股份"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["hot"][0]["deviation_pct"] == pytest.approx(10.75)
    assert result["hot"][0]["deviation_formula"] == "period_return_difference"


def test_ipo_first_five_open_days_are_excluded_from_anomaly_windows():
    dates = _open_dates()

    class _IpoRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_basic_batch":
                return DataResult(
                    data=[
                        {
                            "ts_code": "001258.SZ",
                            "name": "立新能源",
                            "list_date": dates[-6],
                        }
                    ],
                    source="test:stock_basic",
                )
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": (
                                100.0
                                if trade_date == dates[-6]
                                else 10.0
                                if trade_date >= dates[-5]
                                else 0.0
                            ),
                            "close": 10.0,
                            "pre_close": 10.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_IpoRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "001258.SZ", "name": "立新能源"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["hot"] == []
    assert result["today"] == []
    assert result["next_day"] == []
    assert result["legacy"] == []
    assert result["excluded_no_limit_dates"][0]["dates"] == dates[-6:-1]


def test_ipo_buffer_excludes_first_five_days_when_listing_precedes_30_day_window():
    dates = _open_dates(40)

    class _BufferedIpoRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_basic_batch":
                return DataResult(
                    data=[
                        {
                            "ts_code": "001258.SZ",
                            "name": "立新能源",
                            "list_date": dates[8],
                        }
                    ],
                    source="test:stock_basic",
                )
            if method in {"get_stock_daily_range", "get_index_daily_range"}:
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": 0.0,
                            "close": 10.0,
                            "pre_close": 10.0,
                        }
                        for trade_date in dates
                    ],
                    source=f"test:{method}",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_BufferedIpoRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "001258.SZ", "name": "立新能源"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["excluded_no_limit_dates"][0]["dates"] == dates[8:13]
    assert result["missing_market_dates"] == []


def test_confirmed_high_shock_resets_deviation_and_event_windows():
    dates = _open_dates()
    service = RegulatoryOverviewService(registry=_CalculationRegistry())

    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH", "name": "哈药股份"}],
        shock_rows=[
            {
                "ts_code": "600664.SH",
                "trade_date": trade_date,
                "direction": "up",
            }
            for trade_date in dates[-9:-6]
        ],
        high_rows=[
            {
                "ts_code": "600664.SH",
                "trade_date": dates[-5],
                "reason": "交易所确认严重异常波动",
            }
        ],
        limit_rows=[],
    )

    assert result["today"] == []
    assert result["next_day"] == []
    assert result["legacy"] == []
    assert result["reset_boundaries"] == [
        {
            "code": "600664.SH",
            "confirmed_high_shock_date": dates[-5],
            "recalculation_start_date": dates[-4],
            "rule": "next_open_after_confirmed_high_shock",
        }
    ]


def test_high_shock_on_target_date_keeps_current_fact_but_resets_next_day():
    dates = _open_dates()

    class _LimitUpRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {"trade_date": trade_date, "pct_chg": 10.0, "close": 10.0}
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_LimitUpRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[],
        shock_rows=[],
        high_rows=[
            {
                "ts_code": "600664.SH",
                "trade_date": dates[-1],
                "reason": "交易所确认严重异常波动",
            }
        ],
        limit_rows=[],
    )

    assert result["legacy"]
    assert result["next_day"] == []
    assert result["reset_boundaries"][0]["confirmed_high_shock_date"] == dates[-1]
    assert result["reset_boundaries"][0]["recalculation_start_date"] is None


def test_missing_post_reset_market_dates_stays_partial_without_guessing_resumption():
    dates = _open_dates()

    class _ResumptionRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            result = super().call(method, *args, **kwargs)
            if method == "get_stock_daily_range":
                result.data = [
                    row
                    for row in result.data
                    if row["trade_date"] not in {dates[-7], dates[-6]}
                ]
            return result

    service = RegulatoryOverviewService(registry=_ResumptionRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH", "name": "哈药股份"}],
        shock_rows=[],
        high_rows=[
            {
                "ts_code": "600664.SH",
                "trade_date": dates[-8],
                "reason": "交易所确认严重异常波动",
            }
        ],
        limit_rows=[],
    )

    assert result["status"] == "partial"
    assert result["reset_boundaries"][0]["recalculation_start_date"] == dates[-7]
    assert result["missing_market_dates"] == [
        {
            "code": "600664.SH",
            "benchmark_code": "000001.SH",
            "dates": [dates[-7], dates[-6]],
        }
    ]


def test_st_source_failure_blocks_all_candidate_calculations():
    dates = _open_dates()
    service = RegulatoryOverviewService(
        registry=_CalculationRegistry(st_error="st source unavailable")
    )

    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["status"] == "missing_st_status"
    assert result["hot"] == []
    assert result["today"] == []
    assert result["next_day"] == []


def test_price_limit_reachability_compares_rounded_prices():
    reachable, limit_price = _reachable_at_price_limit(
        5.01,
        5.52,
        10.0,
        "up",
    )

    assert limit_price == 5.51
    assert reachable is False


def test_small_positive_deviation_is_not_written_to_legacy_type2():
    dates = _open_dates()

    class _SmallMoveRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            if method == "get_stock_daily_range":
                return DataResult(
                    data=[
                        {
                            "trade_date": trade_date,
                            "pct_chg": 0.1,
                            "close": 10.0,
                        }
                        for trade_date in dates
                    ],
                    source="test:stock",
                )
            return super().call(method, *args, **kwargs)

    service = RegulatoryOverviewService(registry=_SmallMoveRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH", "name": "哈药股份"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["hot"]
    assert result["legacy"] == []


def test_degraded_rerun_preserves_previous_complete_calculation_sections():
    service = RegulatoryOverviewService(registry=None)
    previous = {
        "status": "partial",
        "calculation_meta": {"status": "complete"},
        "hot_deviations": [{"ts_code": "600664.SH"}],
        "trigger_candidates": {"today": [{"ts_code": "600664.SH"}], "next_day": []},
    }
    current = {
        "status": "partial",
        "hot_deviations": [],
        "trigger_candidates": {"today": [], "next_day": []},
        "calculation_meta": {"status": "missing_st_status"},
    }

    merged = service._preserve_previous_complete_sections(
        current,
        previous,
        calculation_status="missing_st_status",
        alert_usable=True,
        high_shock_usable=True,
    )

    assert merged["hot_deviations"] == previous["hot_deviations"]
    assert merged["trigger_candidates"] == previous["trigger_candidates"]
    assert merged["status"] == "partial"


def test_missing_middle_market_date_marks_calculation_partial():
    dates = _open_dates()

    class _MissingDateRegistry(_CalculationRegistry):
        def call(self, method: str, *args, **kwargs):
            result = super().call(method, *args, **kwargs)
            if method == "get_stock_daily_range":
                result.data = [
                    row
                    for row in result.data
                    if row["trade_date"] != dates[-15]
                ]
            return result

    service = RegulatoryOverviewService(registry=_MissingDateRegistry())
    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[{"ts_code": "600664.SH", "name": "哈药股份"}],
        shock_rows=[],
        high_rows=[],
        limit_rows=[],
    )

    assert result["status"] == "partial"
    assert result["missing_codes"] == ["600664.SH"]
    assert result["missing_market_dates"] == [
        {
            "code": "600664.SH",
            "benchmark_code": "000001.SH",
            "dates": [dates[-15]],
        }
    ]


def test_read_source_uses_insertion_order_for_same_timestamp_runs(tmp_path):
    db_path = str(tmp_path / "regulatory.db")
    conn = get_connection(db_path)
    try:
        migrate(conn)
        conn.execute(
            """
            INSERT INTO raw_interface_payloads
            (interface_name, provider, stage, biz_date, target_date, raw_table,
             dedupe_key, payload_json, payload_hash, row_count, status, params_json)
            VALUES ('stk_alert', 'test:stk_alert', 'post_extended', '2026-07-23',
                    '2026-07-23', 'raw_stk_alert', 'stk-alert-20260723', ?, 'hash',
                    1, 'success', '{}')
            """,
            (
                json.dumps(
                    {
                        "rows": [
                            {
                                "ts_code": "600664.SH",
                                "start_date": "20260723",
                                "end_date": "20260806",
                            }
                        ]
                    }
                ),
            ),
        )
        for run_id, status in (("z_success", "success"), ("a_failed", "failed")):
            conn.execute(
                """
                INSERT INTO ingest_runs
                (run_id, interface_name, provider, stage, biz_date, target_date,
                 params_json, status, row_count, started_at, triggered_by, input_by, notes)
                VALUES (?, 'stk_alert', 'test:stk_alert', 'post_extended',
                        '2026-07-23', '2026-07-23', '{}', ?, 0,
                        '2026-07-23T20:00:00', 'system', 'test', ?)
                """,
                (run_id, status, "latest failed" if status == "failed" else ""),
            )
        conn.commit()

        state = RegulatoryOverviewService()._read_source(
            conn,
            "stk_alert",
            "2026-07-23",
        )
    finally:
        conn.close()

    assert state["status"] == "stale"
    assert state["error"] == "latest failed"


def test_partial_overview_does_not_delete_existing_legacy_type2(tmp_path):
    db_path = str(tmp_path / "regulatory.db")
    conn = get_connection(db_path)
    try:
        migrate(conn)
        conn.execute(
            """
            INSERT INTO stock_regulatory_monitor
            (ts_code, name, regulatory_type, risk_level, reason, publish_date,
             source, risk_score, detail_json)
            VALUES ('600664.SH', '哈药股份', 2, 2, 'existing', '2026-07-23',
                    'calculated:regulatory-v2', 0.75, '{}')
            """
        )
        conn.commit()
    finally:
        conn.close()

    service = RegulatoryOverviewService(registry=None, db_path=db_path)
    service._persist_overview(
        {
            "snapshot_date": "2026-07-23",
            "status": "partial",
            "calculation_meta": {"status": "complete"},
        },
        alert_state={"status": "failed", "snapshot_date": None},
        suspend_state={"status": "failed", "snapshot_date": None},
        alert_rows=[],
        type1_rows=[],
        type2_rows=[],
    )

    conn = get_connection(db_path)
    try:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM stock_regulatory_monitor
            WHERE publish_date = '2026-07-23' AND regulatory_type = 2
            """
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_optional_cap_keeps_current_limit_candidates_before_older_shocks():
    dates = _open_dates()
    service = RegulatoryOverviewService(
        registry=_CalculationRegistry(st_error="stop before market requests")
    )
    shocks = [
        {
            "ts_code": f"{600000 + index}.SH",
            "trade_date": dates[-2],
            "direction": "up",
        }
        for index in range(301)
    ]

    result = service._calculate_candidates(
        target_date=dates[-1],
        open_dates=dates,
        alert_rows=[],
        shock_rows=shocks,
        high_rows=[],
        limit_rows=[
            {
                "ts_code": "605999.SH",
                "trade_date": dates[-1],
            }
        ],
    )

    assert result["optional_truncated"] is True
    assert result["candidate_count"] == 300
    assert "605999.SH" in result["missing_codes"]
    assert "600300.SH" not in result["missing_codes"]
