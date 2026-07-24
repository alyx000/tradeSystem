from __future__ import annotations

import pytest

from services.monthly_pattern.market import (
    SourceCoverageError,
    apply_month_end_qfq,
    join_month_quotes_and_factors,
    select_completed_month_ends,
    validate_month_manifest_sequence,
)


def test_completed_month_ends_excludes_an_unfinished_current_month() -> None:
    rows = [
        {"cal_date": "20260529", "is_open": 1},
        {"cal_date": "20260629", "is_open": 1},
        {"cal_date": "20260630", "is_open": 1},
        {"cal_date": "20260701", "is_open": 1},
        {"cal_date": "20260731", "is_open": 1},
    ]

    assert select_completed_month_ends(rows, "2026-07-24", months=2) == [
        "2026-05-29",
        "2026-06-30",
    ]


def test_completed_month_ends_does_not_trust_a_calendar_truncated_mid_month() -> None:
    rows = [
        {"cal_date": "20260529", "is_open": 1},
        {"cal_date": "20260630", "is_open": 1},
        # 来源异常时只返回到 as-of；7 月 24 日不能被误认成 7 月完成月。
        {"cal_date": "20260701", "is_open": 1},
        {"cal_date": "20260724", "is_open": 1},
    ]

    assert select_completed_month_ends(rows, "2026-07-24", months=2) == [
        "2026-05-29",
        "2026-06-30",
    ]


def test_completed_month_ends_includes_current_month_after_last_open_day() -> None:
    rows = [
        {"cal_date": "20260630", "is_open": 1},
        {"cal_date": "20260730", "is_open": 1},
        {"cal_date": "20260731", "is_open": 1},
    ]

    assert select_completed_month_ends(rows, "2026-07-31", months=2) == [
        "2026-06-30",
        "2026-07-31",
    ]


def test_join_normalizes_codes_and_rejects_low_factor_coverage() -> None:
    quotes = [
        {"ts_code": "600000.SH", "open": 10, "high": 12, "low": 9, "close": 11},
        {"ts_code": "000001.SZ", "open": 20, "high": 21, "low": 19, "close": 20},
    ]
    factors = [{"ts_code": "600000.SH", "adj_factor": 2.0}]

    with pytest.raises(SourceCoverageError, match="复权因子覆盖率"):
        join_month_quotes_and_factors(
            quotes,
            factors,
            month_end="2026-06-30",
            min_rows=1,
            min_factor_coverage=0.95,
        )


def test_join_rejects_a_common_quote_and_factor_truncation_against_external_universe() -> None:
    universe = [
        {"ts_code": f"{code:06d}.SZ", "list_date": "20000101"}
        for code in range(1, 55)
    ]
    quotes = [
        {
            "ts_code": f"{code:06d}.SZ",
            "trade_date": "20260630",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
        }
        for code in range(1, 41)
    ]
    factors = [
        {
            "ts_code": f"{code:06d}.SZ",
            "trade_date": "20260630",
            "adj_factor": 1.0,
        }
        for code in range(1, 41)
    ]

    with pytest.raises(SourceCoverageError, match="外部股票宇宙覆盖率"):
        join_month_quotes_and_factors(
            quotes,
            factors,
            month_end="2026-06-30",
            min_rows=1,
            universe_rows=universe,
            min_universe_coverage=0.95,
        )


def test_join_rejects_codes_outside_the_certified_external_universe() -> None:
    universe = [{"ts_code": "600000.SH", "list_date": "20000101"}]
    quotes = [
        {
            "ts_code": code,
            "trade_date": "20260630",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
        }
        for code in ("600000.SH", "999999.SZ")
    ]
    factors = [
        {
            "ts_code": code,
            "trade_date": "20260630",
            "adj_factor": 1.0,
        }
        for code in ("600000.SH", "999999.SZ")
    ]

    with pytest.raises(SourceCoverageError, match="股票宇宙之外代码"):
        join_month_quotes_and_factors(
            quotes,
            factors,
            month_end="2026-06-30",
            min_rows=1,
            universe_rows=universe,
            min_universe_coverage=0.95,
        )


def test_join_uses_historical_as_of_universe_instead_of_fixed_4000_floor() -> None:
    universe = [
        {"ts_code": "000001.SZ", "list_date": "19910403"},
        {
            "ts_code": "000002.SZ",
            "list_date": "19910129",
            "delist_date": "20260615",
        },
        # 该股票在目标月之后上市，不能进入历史分母。
        {"ts_code": "000003.SZ", "list_date": "20260701"},
    ]
    quotes = [
        {
            "ts_code": code,
            "trade_date": "20260630",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "vol": 100,
            "amount": 1000,
        }
        for code in ("000001.SZ", "000002.SZ")
    ]
    factors = [
        {
            "ts_code": code,
            "trade_date": "20260630",
            "adj_factor": 1.0,
        }
        for code in ("000001.SZ", "000002.SZ")
    ]

    joined, manifest = join_month_quotes_and_factors(
        quotes,
        factors,
        month_end="2026-06-30",
        min_rows=4000,
        universe_rows=universe,
        min_universe_coverage=0.95,
        return_manifest=True,
    )

    assert len(joined) == 2
    assert manifest["universe_count"] == 2
    assert manifest["quote_coverage"] == 1.0
    assert manifest["factor_coverage"] == 1.0


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"open": 10, "high": 9, "low": 8, "close": 9},
        {"open": 10, "high": 11, "low": 10.5, "close": 10},
        {
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "vol": None,
        },
        {
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "amount": -1,
        },
    ],
)
def test_join_rejects_invalid_ohlcv_before_certifying_month(
    bad_fields: dict,
) -> None:
    quote = {
        "ts_code": "600000.SH",
        "trade_date": "20260630",
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10,
        "vol": 100,
        "amount": 1000,
        **bad_fields,
    }

    with pytest.raises(SourceCoverageError, match="清洗后"):
        join_month_quotes_and_factors(
            [quote],
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260630",
                    "adj_factor": 1.0,
                }
            ],
            month_end="2026-06-30",
            min_rows=1,
            universe_rows=[
                {"ts_code": "600000.SH", "list_date": "20000101"}
            ],
        )


def test_adjacent_manifest_guard_uses_universe_normalized_coverage() -> None:
    manifests = [
        {
            "month_end": "2026-05-29",
            "universe_count": 100,
            "joined_count": 99,
        },
        {
            "month_end": "2026-06-30",
            "universe_count": 100,
            "joined_count": 96,
        },
    ]

    with pytest.raises(SourceCoverageError, match="相邻月有效覆盖异常"):
        validate_month_manifest_sequence(
            manifests,
            min_adjacent_coverage_ratio=0.98,
            max_adjacent_coverage_ratio=1.02,
        )


@pytest.mark.parametrize(
    ("quotes", "factors", "mismatched_date"),
    [
        (
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260529",
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                }
            ],
            [{"ts_code": "000001.SZ", "trade_date": "20260630", "adj_factor": 1.0}],
            "2026-05-29",
        ),
        (
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260630",
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                }
            ],
            [{"ts_code": "000001.SZ", "trade_date": "20260529", "adj_factor": 1.0}],
            "2026-05-29",
        ),
    ],
)
def test_join_rejects_quote_or_factor_rows_from_another_month_end(
    quotes: list[dict],
    factors: list[dict],
    mismatched_date: str,
) -> None:
    with pytest.raises(SourceCoverageError, match=mismatched_date):
        join_month_quotes_and_factors(
            quotes,
            factors,
            month_end="2026-06-30",
            min_rows=1,
        )


def test_duplicate_market_code_is_source_failure() -> None:
    quotes = [
        {"ts_code": "600000.SH", "close": 11},
        {"ts_code": "600000.SH", "close": 11},
    ]

    with pytest.raises(SourceCoverageError, match="重复股票代码"):
        join_month_quotes_and_factors(
            quotes,
            [{"ts_code": "600000.SH", "adj_factor": 2.0}],
            month_end="2026-06-30",
            min_rows=1,
        )


def test_qfq_adjusts_all_price_fields_but_not_volume_or_amount() -> None:
    rows = [
        {
            "month_end": "2026-05-29",
            "stock_code": "600000",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 100.0,
            "amount": 1000.0,
            "adj_factor": 1.0,
        },
        {
            "month_end": "2026-06-30",
            "stock_code": "600000",
            "open": 6.0,
            "high": 7.0,
            "low": 5.0,
            "close": 6.5,
            "volume": 120.0,
            "amount": 900.0,
            "adj_factor": 2.0,
        },
    ]

    adjusted = apply_month_end_qfq(rows)

    assert adjusted[0]["open"] == 5.0
    assert adjusted[0]["high"] == 6.0
    assert adjusted[0]["low"] == 4.5
    assert adjusted[0]["close"] == 5.5
    assert adjusted[0]["volume"] == 100.0
    assert adjusted[0]["amount"] == 1000.0
    assert adjusted[1]["close"] == 6.5


def test_qfq_marks_the_month_with_an_adj_factor_change_as_shape_invalid() -> None:
    rows = [
        {
            "month_end": "2026-04-30",
            "stock_code": "600000",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "adj_factor": 1.0,
        },
        {
            "month_end": "2026-05-29",
            "stock_code": "600000",
            "open": 10.5,
            "high": 12.0,
            "low": 10.0,
            "close": 11.5,
            "adj_factor": 1.0,
        },
        {
            # 月内发生 2:1 除权。月末因子可以统一 close/MA 口径，却无法把
            # raw monthly 的月初 open 与月内 high/low 还原为可信前复权形态。
            "month_end": "2026-06-30",
            "stock_code": "600000",
            "open": 11.5,
            "high": 12.0,
            "low": 5.5,
            "close": 6.0,
            "adj_factor": 2.0,
        },
    ]

    adjusted = apply_month_end_qfq(rows)

    assert [row["price_shape_valid"] for row in adjusted] == [True, True, False]
    assert adjusted[-1]["close"] == 6.0
    assert adjusted[-1]["open"] == 11.5
