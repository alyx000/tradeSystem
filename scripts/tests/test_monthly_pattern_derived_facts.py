from __future__ import annotations

import copy
import json

import pytest

from services.monthly_pattern import derived_facts


MONTH_END = "2026-06-30"
CODE = "600001"


def _daily() -> list[dict]:
    return [
        {
            "trade_date": "2026-06-02",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "vol": 100.0,
            "amount": 1000.0,
        },
        {
            "trade_date": "2026-06-20",
            "open": 11.0,
            "high": 14.0,
            "low": 10.0,
            "close": 13.0,
            "vol": 200.0,
            "amount": 2200.0,
        },
    ]


def _factors(first: float = 2.0, last: float = 2.0) -> list[dict]:
    return [
        {"trade_date": "2026-06-02", "adj_factor": first},
        {"trade_date": "2026-06-20", "adj_factor": last},
    ]


def _raw() -> dict:
    return {
        "trade_date": MONTH_END,
        "open": 10.0,
        "high": 14.0,
        "low": 9.0,
        "close": 13.0,
        # Tushare monthly 原生单位：股、元；daily 则是手、千元。
        "vol": 30000.0,
        "amount": 3200000.0,
        "adj_factor": 2.0,
    }


def test_constant_factor_builds_full_ohlcv_certified_bar() -> None:
    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(),
        raw_monthly=_raw(),
        stock_name="测试股份",
    )

    assert fact["fact_status"] == "certified_bar"
    assert fact["formula_version"] == derived_facts.FORMULA_VERSION
    assert fact["raw_crosscheck_status"] == "full_ohlcv"
    assert fact["open"] == 10.0
    assert fact["high"] == 14.0
    assert fact["low"] == 9.0
    assert fact["close"] == 13.0
    assert fact["volume"] == 30000.0
    assert fact["amount"] == 3200000.0
    assert fact["anchor_adj_factor"] == 2.0
    assert fact["trading_days"] == 2
    assert fact["first_trade_date"] == "2026-06-02"
    assert fact["last_trade_date"] == "2026-06-20"
    assert len(fact["fact_hash"]) == 64


def test_factor_change_uses_last_trade_anchor_and_partial_raw_crosscheck() -> None:
    raw = _raw()
    # 首日按 1/2 前复权：open=5、high=6、low=4.5、close=5.5。
    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(1.0, 2.0),
        raw_monthly=raw,
    )

    assert fact["raw_crosscheck_status"] == "close_volume_amount"
    assert fact["open"] == 5.0
    assert fact["high"] == 14.0
    assert fact["low"] == 4.5
    assert fact["close"] == 13.0
    meta = json.loads(fact["source_meta_json"])
    assert meta["derivation"]["factor_changed_within_month"] is True
    assert meta["crosscheck"]["fields"] == [
        "close",
        "volume",
        "amount",
        "adj_factor",
    ]


def test_tushare_compact_monthly_date_is_accepted() -> None:
    raw = _raw()
    raw["trade_date"] = "20260630"

    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(),
        raw_monthly=raw,
    )

    assert fact["fact_status"] == "certified_bar"


def test_raw_anchor_adj_factor_mismatch_is_rejected() -> None:
    raw = _raw()
    raw["adj_factor"] = 3.0

    with pytest.raises(ValueError, match="adj_factor"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            _daily(),
            _factors(),
            raw_monthly=raw,
        )


def test_suspension_allows_last_trade_before_canonical_month_end() -> None:
    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(),
        raw_monthly=None,
    )

    assert fact["last_trade_date"] == "2026-06-20"
    assert fact["anchor_adj_factor"] == 2.0
    assert fact["raw_crosscheck_status"] == "source_only_no_raw"


def test_certified_no_trade_requires_all_three_external_proofs() -> None:
    fact = derived_facts.build_certified_no_trade_fact(
        CODE,
        MONTH_END,
        universe_proven=True,
        raw_monthly_empty=True,
        daily_empty=True,
    )

    assert fact["fact_status"] == "certified_no_trade"
    assert fact["trading_days"] == 0
    assert fact["open"] is None
    assert fact["anchor_adj_factor"] is None
    assert fact["raw_crosscheck_status"] == "certified_no_trade"
    assert json.loads(fact["source_meta_json"])["no_trade_evidence"] == {
        "universe_proven": True,
        "raw_monthly_empty": True,
        "daily_empty": True,
    }

    for field in ("universe_proven", "raw_monthly_empty", "daily_empty"):
        kwargs = {
            "universe_proven": True,
            "raw_monthly_empty": True,
            "daily_empty": True,
        }
        kwargs[field] = False
        with pytest.raises(ValueError, match="requires"):
            derived_facts.build_certified_no_trade_fact(
                CODE,
                MONTH_END,
                **kwargs,
            )


def test_exact_duplicate_dates_are_suppressed_but_conflicts_rejected() -> None:
    daily = _daily()
    factors = _factors()
    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        [daily[1], daily[0], copy.deepcopy(daily[0])],
        [factors[1], factors[0], copy.deepcopy(factors[0])],
        raw_monthly=_raw(),
    )
    assert fact["trading_days"] == 2

    conflicting_daily = [*daily, {**daily[0], "close": 10.5}]
    with pytest.raises(ValueError, match="daily duplicate conflict"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            conflicting_daily,
            factors,
        )

    conflicting_factors = [*factors, {**factors[0], "adj_factor": 1.5}]
    with pytest.raises(ValueError, match="factor duplicate conflict"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            daily,
            conflicting_factors,
        )


@pytest.mark.parametrize("bad", [0, float("nan"), float("inf"), -1])
def test_invalid_factor_values_fail_closed(bad: float) -> None:
    factors = _factors()
    factors[0]["adj_factor"] = bad
    with pytest.raises(ValueError, match="adj_factor"):
        derived_facts.build_month_fact(CODE, MONTH_END, _daily(), factors)


def test_missing_factor_fails_closed() -> None:
    with pytest.raises(ValueError, match="missing adj_factor"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            _daily(),
            _factors()[1:],
        )


@pytest.mark.parametrize("field", ["close", "volume", "amount"])
def test_raw_crosscheck_mismatch_is_rejected(field: str) -> None:
    raw = _raw()
    increments = {"close": 0.01, "volume": 1000.0, "amount": 1000.0}
    raw["vol" if field == "volume" else field] += increments[field]
    with pytest.raises(ValueError, match=field):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            _daily(),
            _factors(1.0, 2.0),
            raw_monthly=raw,
        )


def test_flow_crosscheck_accepts_only_bounded_provider_rounding_noise() -> None:
    raw = _raw()
    raw["vol"] += 2.0
    raw["amount"] += 3.0

    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(1.0, 2.0),
        raw_monthly=raw,
    )

    assert fact["volume"] == 30000.0
    assert fact["amount"] == 3200000.0
    meta = json.loads(fact["source_meta_json"])
    assert meta["derivation"]["output_units"] == {
        "volume": "share",
        "amount": "yuan",
    }


def test_constant_factor_full_raw_crosscheck_rejects_open_mismatch() -> None:
    raw = _raw()
    raw["open"] += 0.01
    with pytest.raises(ValueError, match="open"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            _daily(),
            _factors(),
            raw_monthly=raw,
        )


def test_hash_round_trip_ignores_audit_fields_and_detects_tampering() -> None:
    fact = derived_facts.build_month_fact(
        CODE,
        MONTH_END,
        _daily(),
        _factors(),
        raw_monthly=_raw(),
        source_meta={"provider": "fixture"},
    )
    with_audit = {
        **fact,
        "input_by": "codex",
        "run_id": "run-1",
        "fetched_at": "2026-07-25T12:00:00+08:00",
    }
    validated = derived_facts.validate_fact_row(with_audit)
    assert validated["fact_hash"] == fact["fact_hash"]
    assert validated["input_by"] == "codex"

    tampered = {**fact, "close": fact["close"] + 0.01}
    with pytest.raises(ValueError, match="fact_hash mismatch"):
        derived_facts.validate_fact_row(tampered)

    tampered_meta = {
        **fact,
        "source_meta_json": json.dumps({"provider": "other"}),
    }
    with pytest.raises(ValueError, match="fact_hash mismatch"):
        derived_facts.validate_fact_row(tampered_meta)


@pytest.mark.parametrize(
    ("stock_code", "month_end"),
    [
        ("600001.SH", MONTH_END),
        ("60001", MONTH_END),
        (CODE, "20260630"),
        (CODE, "2026-06-31"),
    ],
)
def test_strict_stock_code_and_date_validation(
    stock_code: str,
    month_end: str,
) -> None:
    with pytest.raises(ValueError):
        derived_facts.build_month_fact(
            stock_code,
            month_end,
            _daily(),
            _factors(),
        )


def test_malformed_source_date_is_rejected_even_if_outside_target_month() -> None:
    daily = [*_daily(), {**_daily()[0], "trade_date": "20260531"}]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        derived_facts.build_month_fact(
            CODE,
            MONTH_END,
            daily,
            _factors(),
        )
