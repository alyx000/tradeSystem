from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from db.schema import init_schema
from services.monthly_pattern import derived_facts
from services.monthly_pattern import fact_backfill_service as subject


@dataclass
class _Bar:
    month: str
    tag: str
    is_complete: bool = True
    price_shape_valid: bool = True


def _month_ends(count: int = 20) -> list[str]:
    values = []
    year, month = 2024, 11
    for _ in range(count):
        values.append(f"{year:04d}-{month:02d}-28")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return values


def test_plan_targets_only_three_blocked_fact_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    month_ends = _month_ends()
    months = [value[:7] for value in month_ends]
    gap_month = months[-5]
    bars = {
        "000001": [],
        "000002": [
            _Bar(month, "gap")
            for month in months
            if month != gap_month
        ],
        "000003": [_Bar(month, "shape") for month in months],
        "000004": [_Bar(month, "short_circuit") for month in months],
        "000005": [_Bar(month, "insufficient") for month in months[-5:]],
        "000006": [],
        "200001": [],
    }
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "_stock_series",
        lambda _rows: (bars, {}),
    )

    def fake_detect(stock_bars):
        tag = stock_bars[0].tag
        if tag == "gap":
            return {
                "status": "blocked",
                "matched": False,
                "evidence": {
                    "reason": "non_consecutive_completed_months: gap"
                },
            }
        if tag == "shape":
            return {
                "status": "blocked",
                "matched": False,
                "evidence": {
                    "reason": f"price_shape_invalid: {months[-2]}",
                    "shape_invalid_months": [months[-2], months[-1]],
                },
            }
        if tag == "short_circuit":
            return {
                "status": "not_matched",
                "matched": False,
                "evidence": {
                    "shape_invalid_months": [months[-1]],
                },
            }
        return {
            "status": "insufficient_history",
            "matched": False,
            "evidence": {},
        }

    monkeypatch.setattr(subject.indicator_watch, "detect_monthly_seed", fake_detect)
    universe = {
        code: {"list_date": "2000-01-01"}
        for code in bars
    }
    targets, counts = subject._plan_targets(
        effective_rows=[],
        month_ends=month_ends,
        universe_rows=universe,
        no_trade_keys={("000006", month_ends[-1])},
    )

    assert set(targets) == {"000001", "000002", "000003"}
    assert len(targets["000001"]) == 20
    assert targets["000002"] == {
        month_ends[months.index(gap_month)]: {"month_gap"}
    }
    assert targets["000003"] == {
        month_ends[-2]: {"shape_unverifiable"},
        month_ends[-1]: {"shape_unverifiable"},
    }
    assert counts["skipped"]["shape_short_circuited_not_matched"] == 1
    assert counts["skipped"]["insufficient_history"] == 1
    assert counts["skipped"]["evidenced_no_trade"] == 1
    assert counts["skipped"]["excluded_b_share_universe"] == 1


def test_plan_targets_all_gaps_between_latest_twenty_existing_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    month_ends = _month_ends(24)
    months = [value[:7] for value in month_ends]
    missing = {months[3], months[14]}
    bars = {
        "000001": [
            _Bar(month, "gap")
            for month in months
            if month not in missing
        ]
    }
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "_stock_series",
        lambda _rows: (bars, {}),
    )
    monkeypatch.setattr(
        subject.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "status": "blocked",
            "matched": False,
            "evidence": {
                "reason": (
                    f"non_consecutive_completed_months: "
                    f"{months[2]}->{months[4]}"
                )
            },
        },
    )

    targets, _counts = subject._plan_targets(
        effective_rows=[],
        month_ends=month_ends,
        universe_rows={"000001": {"list_date": "2000-01-01"}},
        no_trade_keys=set(),
    )

    assert set(targets["000001"]) == {
        month_ends[3],
        month_ends[14],
    }


def test_plan_targets_gap_and_known_recent_shape_issue_in_same_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    month_ends = _month_ends()
    months = [value[:7] for value in month_ends]
    gap_month = months[-4]
    shape_month = months[-2]
    bars = {
        "000001": [
            _Bar(
                month,
                "gap",
                price_shape_valid=month != shape_month,
            )
            for month in months
            if month != gap_month
        ]
    }
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "_stock_series",
        lambda _rows: (bars, {}),
    )
    monkeypatch.setattr(
        subject.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "status": "blocked",
            "matched": False,
            "evidence": {
                "reason": f"non_consecutive_completed_months: {gap_month}"
            },
        },
    )

    targets, _counts = subject._plan_targets(
        effective_rows=[],
        month_ends=month_ends,
        universe_rows={"000001": {"list_date": "2000-01-01"}},
        no_trade_keys=set(),
    )

    assert targets["000001"] == {
        month_ends[months.index(gap_month)]: {"month_gap"},
        month_ends[months.index(shape_month)]: {"shape_unverifiable"},
    }


def test_build_receipt_merges_months_into_one_daily_and_factor_call_per_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    month_ends = _month_ends(35)
    targets = {
        "000001": {
            month_ends[-2]: {"month_gap"},
            month_ends[-1]: {"missing_latest"},
        }
    }
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "resolve_target_date",
        lambda _registry, day: (day, day),
    )
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "_certified_month_ends",
        lambda *_args, **_kwargs: month_ends,
    )
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "_certified_universe_count",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        subject,
        "_latest_universe_rows",
        lambda *_args, **_kwargs: {
            "000001": {"name": "样本", "list_date": "2000-01-01"}
        },
    )
    monkeypatch.setattr(subject, "_load_effective_rows", lambda *_args: [])
    monkeypatch.setattr(subject, "_load_no_trade_keys", lambda *_args: set())
    monkeypatch.setattr(
        subject,
        "_plan_targets",
        lambda **_kwargs: (
            targets,
            {
                "planned_stocks": 1,
                "planned_months": 2,
                "skipped": {},
                "known_names": 1,
            },
        ),
    )
    monkeypatch.setattr(subject.repository, "load_month_bars", lambda *_args: [])
    monkeypatch.setattr(
        subject.repository,
        "load_month_bar_manifests",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        subject,
        "_monthly_quote_maps",
        lambda _registry, months: (
            {month: {"000001": {"close": 10}} for month in months},
            {month: "monthly" for month in months},
            {month: "1" * 64 for month in months},
        ),
    )
    monkeypatch.setattr(
        subject,
        "_universe_maps",
        lambda _registry, months: (
            {month: {"000001"} for month in months},
            {month: "universe" for month in months},
            {month: "2" * 64 for month in months},
        ),
    )
    monkeypatch.setattr(
        subject,
        "_validate_monthly_quote_coverage",
        lambda *_args, **_kwargs: {
            month: {
                "quote_count": 5000,
                "universe_count": 5000,
                "covered_count": 5000,
                "coverage": 1.0,
            }
            for month in targets["000001"]
        },
    )
    monkeypatch.setattr(subject, "_database_state_hash", lambda *_args: "state")
    derived = []

    def fake_derive(**kwargs):
        derived.append(kwargs)
        return {
            "fact_status": "certified_bar",
            "fact_hash": (
                "a" * 64
                if kwargs["month_end"] == month_ends[-2]
                else "b" * 64
            ),
        }

    monkeypatch.setattr(subject, "_derive_fact", fake_derive)
    calls = []

    class Registry:
        def call(self, method, *args):
            calls.append((method, args))
            if method == "get_stock_daily_range":
                return SimpleNamespace(
                    success=True,
                    data=[
                        {
                            "trade_date": month_ends[-2],
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10,
                            "pre_close": 10,
                            "vol": 1,
                            "amount": 10,
                            "pct_chg": 0,
                        },
                        {
                            "trade_date": month_ends[-1],
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10,
                            "pre_close": 10,
                            "vol": 1,
                            "amount": 10,
                            "pct_chg": 0,
                        },
                    ],
                    source="daily",
                    error=None,
                )
            if method == "get_stock_adj_factor_range":
                return SimpleNamespace(
                    success=True,
                    data=[
                        {"trade_date": month_ends[-2], "adj_factor": 1},
                        {"trade_date": month_ends[-1], "adj_factor": 1},
                    ],
                    source="factor",
                    error=None,
                )
            raise AssertionError(method)

    receipt = subject._build_receipt(
        sqlite3.connect(":memory:"),
        Registry(),
        target_date="2026-07-24",
        months=35,
        max_stocks=None,
    )

    assert [method for method, _args in calls] == [
        "get_stock_daily_range",
        "get_stock_adj_factor_range",
    ]
    assert len(derived) == 2
    assert all(len(item["source_payload_hash"]) == 64 for item in derived)
    assert receipt["outcome_counts"] == {"certified_bar": 2}
    public = {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_hash", "month_ends", "_facts"}
    }
    assert public["database_state_hash"] == "state"
    assert receipt["receipt_hash"] == subject._canonical_hash(public)
    assert subject._canonical_hash(
        {**public, "database_state_hash": "changed"}
    ) != receipt["receipt_hash"]


def test_monthly_quote_coverage_rejects_empty_or_truncated_sources() -> None:
    universe = {f"{index:06d}" for index in range(5000)}
    with pytest.raises(subject.FactBackfillError, match="覆盖不足"):
        subject._validate_monthly_quote_coverage(
            {"2026-06-30": {}},
            {"2026-06-30": universe},
            monthly_payload_hashes={"2026-06-30": "1" * 64},
            universe_payload_hashes={"2026-06-30": "2" * 64},
        )
    with pytest.raises(subject.FactBackfillError, match="覆盖不足"):
        subject._validate_monthly_quote_coverage(
            {"2026-06-30": {"000001": {}}},
            {"2026-06-30": universe},
            monthly_payload_hashes={"2026-06-30": "1" * 64},
            universe_payload_hashes={"2026-06-30": "2" * 64},
        )
    small_universe = {f"{index:06d}" for index in range(3000)}
    receipt = subject._validate_monthly_quote_coverage(
        {
            "2000-06-30": {
                code: {}
                for code in small_universe
            }
        },
        {"2000-06-30": small_universe},
        monthly_payload_hashes={"2000-06-30": "1" * 64},
        universe_payload_hashes={"2000-06-30": "2" * 64},
    )
    assert receipt["2000-06-30"]["coverage"] == 1.0
    assert receipt["2000-06-30"]["monthly_payload_sha256"] == "1" * 64
    assert receipt["2000-06-30"]["universe_payload_sha256"] == "2" * 64


def test_provider_payload_hashes_bind_full_monthly_and_universe_rows() -> None:
    month_end = "2026-06-30"

    class Registry:
        def __init__(self, monthly_codes, universe_codes):
            self.monthly_codes = monthly_codes
            self.universe_codes = universe_codes

        def call(self, method, _month_end):
            if method == "get_market_monthly_quotes":
                return SimpleNamespace(
                    success=True,
                    data=[
                        {
                            "ts_code": f"{code}.SZ",
                            "trade_date": "20260630",
                            "close": index + 1,
                        }
                        for index, code in enumerate(self.monthly_codes)
                    ],
                    source="tushare:monthly",
                    error=None,
                )
            if method == "get_stock_universe_as_of":
                return SimpleNamespace(
                    success=True,
                    data=[
                        {
                            "ts_code": f"{code}.SZ",
                            "list_status": "L",
                            "list_date": "20000101",
                        }
                        for code in self.universe_codes
                    ],
                    source="tushare:stock_basic",
                    error=None,
                )
            raise AssertionError(method)

    first = Registry(["000001", "000002"], ["000001", "000002"])
    second = Registry(["000001", "000003"], ["000001", "000003"])
    first_monthly = subject._monthly_quote_maps(first, [month_end])[2][month_end]
    second_monthly = subject._monthly_quote_maps(second, [month_end])[2][month_end]
    first_universe = subject._universe_maps(first, [month_end])[2][month_end]
    second_universe = subject._universe_maps(second, [month_end])[2][month_end]

    assert first_monthly != second_monthly
    assert first_universe != second_universe
    assert all(
        len(value) == 64
        for value in (
            first_monthly,
            second_monthly,
            first_universe,
            second_universe,
        )
    )


def test_derive_adapter_certifies_no_trade_without_factors_and_rejects_source_conflict() -> None:
    no_trade = subject._derive_fact(
        stock_code="000001",
        stock_name="样本",
        month_end="2026-06-30",
        daily_rows=[],
        factor_rows=[],
        raw_monthly=None,
        universe_member=True,
        replacement_reason="missing_latest",
        source_meta={"daily_source": "tushare:daily"},
    )
    assert no_trade["fact_status"] == "certified_no_trade"
    assert no_trade["trading_days"] == 0

    with pytest.raises(subject.FactBackfillError, match="日线非空.*月线为空"):
        subject._derive_fact(
            stock_code="000001",
            stock_name="样本",
            month_end="2026-06-30",
            daily_rows=[{"trade_date": "2026-06-30"}],
            factor_rows=[],
            raw_monthly=None,
            universe_member=True,
            replacement_reason="missing_latest",
            source_meta={},
        )


def _receipt(*, unresolved: int = 0) -> dict:
    return {
        "target_date": "2026-07-24",
        "months": 35,
        "formula_version": subject.FORMULA_VERSION,
        "plan_counts": {
            "planned_stocks": 1,
            "planned_months": 1,
            "truncated_stocks": 0,
        },
        "outcome_counts": (
            {"unresolved": unresolved}
            if unresolved
            else {"certified_bar": 1}
        ),
        "items": [
            {
                "stock_code": "000001",
                "month_end": "2026-06-30",
                "outcome": (
                    "unresolved" if unresolved else "certified_bar"
                ),
            }
        ],
        "receipt_hash": "a" * 64,
        "database_state_hash": "state",
        "month_ends": ["2026-06-30"],
        "_facts": [{"fact_hash": "b" * 64}],
    }


def test_actual_receipt_mismatch_and_unresolved_are_zero_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    persist_calls = []
    migrate_calls = []
    monkeypatch.setattr(subject, "_database_state_hash", lambda *_args: "state")
    monkeypatch.setattr(
        subject,
        "_persist_receipt",
        lambda *_args, **_kwargs: persist_calls.append(1),
    )

    monkeypatch.setattr(subject, "_build_receipt", lambda *_args, **_kwargs: _receipt())
    mismatch = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=False,
        expected_receipt_hash="c" * 64,
        ensure_schema_before_persist=lambda: migrate_calls.append(1),
    )
    assert mismatch["status"] == "receipt_mismatch"
    assert persist_calls == []
    assert migrate_calls == []

    monkeypatch.setattr(
        subject,
        "_build_receipt",
        lambda *_args, **_kwargs: _receipt(unresolved=1),
    )
    partial = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=False,
        expected_receipt_hash="a" * 64,
        ensure_schema_before_persist=lambda: migrate_calls.append(1),
    )
    assert partial["status"] == "partial"
    assert persist_calls == []
    assert migrate_calls == []


def test_actual_complete_receipt_ensures_schema_then_persists_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE marker (value TEXT)")
    events = []
    monkeypatch.setattr(subject, "_build_receipt", lambda *_args, **_kwargs: _receipt())
    state_checks = []

    def locked_state_hash(got_conn, *_args):
        state_checks.append(got_conn.in_transaction)
        return "state"

    monkeypatch.setattr(subject, "_database_state_hash", locked_state_hash)

    def persist(got_conn, *, run, facts):
        assert got_conn.in_transaction
        assert run["input_by"] == "pytest"
        assert facts == [{"fact_hash": "b" * 64}]
        got_conn.execute("INSERT INTO marker VALUES ('persisted')")
        events.append("persist")

    monkeypatch.setattr(subject, "_persist_receipt", persist)
    def ensure_schema():
        assert conn.in_transaction
        events.append("ensure_schema")

    summary = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=False,
        expected_receipt_hash="a" * 64,
        ensure_schema_before_persist=ensure_schema,
    )

    assert events == ["ensure_schema", "persist"]
    assert state_checks == [True]
    assert summary["status"] == "complete"
    assert summary["write_boundary"]["database"] is True
    assert conn.execute("SELECT value FROM marker").fetchone()[0] == "persisted"


def test_actual_state_drift_is_checked_under_write_lock_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    persist_calls = []
    state_checks = []
    monkeypatch.setattr(
        subject,
        "_build_receipt",
        lambda *_args, **_kwargs: _receipt(),
    )

    def drifted(got_conn, *_args):
        state_checks.append(got_conn.in_transaction)
        return "changed"

    monkeypatch.setattr(subject, "_database_state_hash", drifted)
    monkeypatch.setattr(
        subject,
        "_persist_receipt",
        lambda *_args, **_kwargs: persist_calls.append(1),
    )

    ensure_calls = []
    summary = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=False,
        expected_receipt_hash="a" * 64,
        ensure_schema_before_persist=lambda: ensure_calls.append(1),
    )

    assert summary["status"] == "state_drift"
    assert state_checks == [True]
    assert persist_calls == []
    assert ensure_calls == []
    assert conn.in_transaction is False


def test_partial_dry_run_persists_only_in_memory_and_builds_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    persisted = []
    monkeypatch.setattr(
        subject,
        "_build_receipt",
        lambda *_args, **_kwargs: _receipt(unresolved=1),
    )
    monkeypatch.setattr(subject, "_database_state_hash", lambda *_args: "state")
    monkeypatch.setattr(
        subject,
        "_persist_receipt",
        lambda _conn, *, run, facts: persisted.append((run, facts)),
    )
    monkeypatch.setattr(
        subject.indicator_watch_service,
        "run_monitor",
        lambda *_args, **_kwargs: {"status": "partial"},
    )

    summary = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=True,
    )

    assert summary["status"] == "partial"
    assert summary["monitor_preview"] == {"status": "partial"}
    assert persisted[0][0]["status"] == "partial"
    assert summary["write_boundary"]["database"] is False


def test_actual_complete_receipt_integrates_with_repository_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    fact = derived_facts.build_certified_no_trade_fact(
        "000001",
        "2026-06-30",
        universe_proven=True,
        raw_monthly_empty=True,
        daily_empty=True,
        replacement_reason="missing_latest",
        source_meta={"daily_source": "fixture"},
    )
    receipt = _receipt()
    receipt["_facts"] = [fact]
    receipt["items"] = [
        {
            "stock_code": "000001",
            "month_end": "2026-06-30",
            "reasons": ["missing_latest"],
            "outcome": "certified_no_trade",
            "fact_hash": fact["fact_hash"],
        }
    ]
    receipt["outcome_counts"] = {"certified_no_trade": 1}
    monkeypatch.setattr(
        subject,
        "_build_receipt",
        lambda *_args, **_kwargs: receipt,
    )
    monkeypatch.setattr(subject, "_database_state_hash", lambda *_args: "state")

    summary = subject.run_backfill(
        conn,
        object(),
        target_date="2026-07-24",
        months=35,
        input_by="pytest",
        dry_run=False,
        expected_receipt_hash="a" * 64,
    )

    assert summary["status"] == "complete"
    assert conn.execute(
        "SELECT COUNT(*) FROM monthly_pattern_derived_fact_runs"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT fact_status FROM monthly_pattern_derived_month_facts"
    ).fetchone()[0] == "certified_no_trade"
