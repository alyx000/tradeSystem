from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.monthly_pattern import derived_facts, repository, service


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _alias_evidence_meta(
    *,
    denominator: int,
    raw_count: int,
    normalized_count: int,
    **extra,
) -> dict:
    return {
        "code_alias_evidence_schema_version": 2,
        "factor_coverage_denominator": denominator,
        "raw_joined_code_count": raw_count,
        "normalized_joined_code_count": normalized_count,
        "code_alias_normalizations": [],
        **extra,
    }


def _derived_run(run_id: str, *, status: str = "complete") -> dict:
    run = {
        "run_id": run_id,
        "input_by": "pytest",
        "status": status,
        "request": {"month_end": "2026-06-30"},
        "counts": {"facts": 1},
        "receipt": {"source": "fixture"},
    }
    run["receipt_hash"] = repository.compute_derived_fact_run_receipt_hash(run)
    return run


def _derived_bar(*, source_meta: dict | None = None) -> dict:
    return derived_facts.build_month_fact(
        "600000",
        "2026-06-30",
        [
            {
                "trade_date": "2026-06-29",
                "open": 10.0,
                "high": 12.0,
                "low": 9.0,
                "close": 11.0,
                "volume": 100.0,
                "amount": 1000.0,
            },
            {
                "trade_date": "2026-06-30",
                "open": 5.5,
                "high": 6.0,
                "low": 5.0,
                "close": 5.8,
                "volume": 200.0,
                "amount": 2000.0,
            },
        ],
        [
            {"trade_date": "2026-06-29", "adj_factor": 1.0},
            {"trade_date": "2026-06-30", "adj_factor": 2.0},
        ],
        raw_monthly={
            "month_end": "2026-06-30",
            "open": 10.0,
            "high": 12.0,
            "low": 5.0,
            "close": 5.8,
            "volume": 30000.0,
            "amount": 3000000.0,
        },
        stock_name="测试股份",
        source_meta=source_meta,
    )


def test_month_bars_upsert_is_idempotent_and_keeps_raw_prices() -> None:
    conn = _conn()
    row = {
        "month_end": "2026-06-30",
        "stock_code": "600000",
        "stock_name": "浦发银行",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 100.0,
        "amount": 1000.0,
        "adj_factor": 2.0,
        "source": "test",
    }

    repository.save_month_bars(conn, [row])
    repository.save_month_bars(conn, [{**row, "close": 11.5}])
    loaded = repository.load_month_bars(conn, ["2026-06-30"])

    assert len(loaded) == 1
    assert loaded[0]["close"] == 11.5
    assert loaded[0]["adj_factor"] == 2.0
    # 裸 COUNT 不是可复用缓存；只有带外部宇宙分母的 certified manifest 才能认证。
    assert repository.existing_month_ends(
        conn, ["2026-06-30"], min_rows=1
    ) == set()
    repository.save_month_bar_manifest(
        conn,
        {
            "month_end": "2026-06-30",
            "status": "certified",
            "universe_source": "test:stock_basic",
            "universe_count": 1,
            "quote_count": 1,
            "factor_count": 1,
            "joined_count": 1,
            "quote_coverage": 1.0,
            "factor_coverage": 1.0,
            "source_meta": _alias_evidence_meta(
                denominator=1,
                raw_count=1,
                normalized_count=1,
                min_universe_coverage=0.95,
            ),
        },
    )
    assert repository.existing_month_ends(
        conn, ["2026-06-30"], min_rows=1
    ) == {"2026-06-30"}
    # 历史市场可能真实少于 4000，只要外部 as-of 分母与 manifest 认证通过，
    # 固定行数地板不能让缓存失效。
    assert repository.existing_month_ends(
        conn, ["2026-06-30"], min_rows=2
    ) == {"2026-06-30"}

    repository.save_month_bar_manifest(
        conn,
        {
            "month_end": "2026-06-30",
            "status": "certified",
            "universe_source": "test:stock_basic",
            "universe_count": 2,
            "quote_count": 2,
            "factor_count": 2,
            "joined_count": 1,
            "quote_coverage": 1.0,
            "factor_coverage": 1.0,
            "source_meta": _alias_evidence_meta(
                denominator=2,
                raw_count=2,
                normalized_count=2,
                valid_universe_coverage=0.5,
                min_universe_coverage=0.5,
            ),
        },
    )
    # 旧运行在更低阈值下取得的 manifest，不能绕过当前 95% 有效覆盖门。
    assert repository.existing_month_ends(
        conn,
        ["2026-06-30"],
        min_universe_coverage=0.95,
    ) == set()

    conn.execute(
        """
        UPDATE monthly_pattern_bar_manifests
        SET universe_count = 1,
            joined_count = 1,
            quote_coverage = 1.0,
            factor_coverage = 1.0,
            source_meta_json = '{"valid_universe_coverage":"bad"}'
        WHERE month_end = '2026-06-30'
        """
    )
    assert repository.existing_month_ends(
        conn,
        ["2026-06-30"],
    ) == set()

    conn.execute(
        """
        UPDATE monthly_pattern_bar_manifests
        SET source_meta_json = '{broken'
        WHERE month_end = '2026-06-30'
        """
    )
    manifests = repository.load_month_bar_manifests(
        conn,
        ["2026-06-30"],
    )
    assert manifests[0]["status"] == "invalid"
    assert repository.existing_month_ends(
        conn,
        ["2026-06-30"],
    ) == set()

    conn.execute(
        "DELETE FROM monthly_pattern_bars WHERE month_end = ?",
        ("2026-06-30",),
    )
    assert repository.existing_month_ends(
        conn, ["2026-06-30"], min_rows=1
    ) == set()


def test_derived_fact_overlay_is_hashed_and_same_hash_is_idempotent() -> None:
    conn = _conn()
    repository.save_month_bars(
        conn,
        [
            {
                "month_end": "2026-06-30",
                "stock_code": "600000",
                "stock_name": "原始名称",
                "open": 10.0,
                "high": 12.0,
                "low": 5.0,
                "close": 5.8,
                "volume": 300.0,
                "amount": 3000.0,
                "adj_factor": 2.0,
                "source": "fixture:raw_monthly",
            }
        ],
    )
    fact = _derived_bar(source_meta={"batch": "first"})
    first = repository.save_derived_fact_run_and_facts(
        conn,
        run=_derived_run("derived_run_1"),
        facts=[fact],
    )
    second = repository.save_derived_fact_run_and_facts(
        conn,
        run=_derived_run("derived_run_2"),
        facts=[fact],
    )

    assert first == {"run_id": "derived_run_1", "inserted": 1, "idempotent": 0}
    assert second == {"run_id": "derived_run_2", "inserted": 0, "idempotent": 1}
    loaded = repository.load_derived_month_facts(conn, ["2026-06-30"])
    assert len(loaded) == 1
    assert loaded[0]["fact_hash"] == fact["fact_hash"]
    assert loaded[0]["first_run_id"] == "derived_run_1"
    assert loaded[0]["source_meta"]["batch"] == "first"

    effective = repository.load_effective_month_bars(conn, ["2026-06-30"])
    assert len(effective) == 1
    assert effective[0]["open"] == 5.0
    assert effective[0]["adj_factor"] == 2.0
    assert effective[0]["shape_certified"] is True
    assert effective[0]["source"] == "derived_daily_certified"
    assert effective[0]["derived_fact_hash"] == fact["fact_hash"]
    assert len(repository.load_derived_fact_runs(conn)) == 2


def test_derived_fact_different_hash_is_rejected_and_run_rolls_back() -> None:
    conn = _conn()
    original = _derived_bar(source_meta={"batch": "first"})
    repository.save_derived_fact_run_and_facts(
        conn,
        run=_derived_run("derived_run_1"),
        facts=[original],
    )
    conflicting = _derived_bar(source_meta={"batch": "changed"})

    with pytest.raises(ValueError, match="derived fact conflict"):
        repository.save_derived_fact_run_and_facts(
            conn,
            run=_derived_run("derived_run_conflict"),
            facts=[conflicting],
        )

    assert repository.load_derived_fact_runs(
        conn,
        run_id="derived_run_conflict",
    ) == []
    loaded = repository.load_derived_month_facts(conn, ["2026-06-30"])
    assert [row["fact_hash"] for row in loaded] == [original["fact_hash"]]


def test_standalone_derived_fact_preflight_avoids_partial_batch_insert() -> None:
    conn = _conn()
    original = _derived_bar(source_meta={"batch": "first"})
    repository.save_derived_fact_run_and_facts(
        conn,
        run=_derived_run("derived_run_1"),
        facts=[original],
    )
    new_fact = derived_facts.build_certified_no_trade_fact(
        "600001",
        "2026-06-30",
        universe_proven=True,
        raw_monthly_empty=True,
        daily_empty=True,
    )
    conflicting = _derived_bar(source_meta={"batch": "changed"})

    with pytest.raises(ValueError, match="derived fact conflict"):
        repository.save_derived_month_facts(
            conn,
            [new_fact, conflicting],
            first_run_id="derived_run_1",
        )

    assert [
        row["stock_code"]
        for row in repository.load_derived_month_facts(
            conn,
            ["2026-06-30"],
        )
    ] == ["600000"]


def test_effective_no_trade_yields_to_later_raw_month_fact() -> None:
    conn = _conn()
    fact = derived_facts.build_certified_no_trade_fact(
        "600001",
        "2026-06-30",
        universe_proven=True,
        raw_monthly_empty=True,
        daily_empty=True,
        source_meta={"proof": "fixture"},
    )
    repository.save_derived_fact_run_and_facts(
        conn,
        run=_derived_run("derived_run_no_trade"),
        facts=[fact],
    )
    assert [
        row["stock_code"]
        for row in repository.load_effective_no_trade_facts(
            conn,
            ["2026-06-30"],
        )
    ] == ["600001"]

    repository.save_month_bars(
        conn,
        [
            {
                "month_end": "2026-06-30",
                "stock_code": "600001",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "amount": 1000.0,
                "adj_factor": 1.0,
                "source": "fixture:late_raw",
            }
        ],
    )
    assert repository.load_effective_no_trade_facts(
        conn,
        ["2026-06-30"],
    ) == []
    assert [
        row["stock_code"]
        for row in repository.load_effective_month_bars(
            conn,
            ["2026-06-30"],
        )
    ] == ["600001"]


def test_effective_month_bars_falls_back_to_raw_when_derived_table_is_absent() -> None:
    conn = _conn()
    repository.save_month_bars(
        conn,
        [
            {
                "month_end": "2026-06-30",
                "stock_code": "600000",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "amount": 1000.0,
                "adj_factor": 1.0,
                "source": "fixture:raw",
            }
        ],
    )
    conn.execute("DROP TABLE monthly_pattern_derived_month_facts")
    conn.execute("DROP TABLE monthly_pattern_derived_fact_runs")

    effective = repository.load_effective_month_bars(conn, ["2026-06-30"])

    assert len(effective) == 1
    assert effective[0]["source"] == "fixture:raw"
    assert "shape_certified" not in effective[0]
    assert repository.load_effective_no_trade_facts(
        conn,
        ["2026-06-30"],
    ) == []


def test_financial_snapshot_keeps_distinct_announcement_revisions() -> None:
    conn = _conn()
    base = {
        "ts_code": "600000.SH",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "fina_indicator": {"roe_waa": 18.0},
        "balancesheet": {"contract_liab": 100.0},
        "income": {"n_income_attr_p": 10.0},
        "source_meta": {"status": "complete"},
    }

    repository.save_financial_snapshots(conn, [base, {**base, "financial_ann_date": "2026-04-02"}])
    loaded = repository.load_financial_snapshots(conn, as_of_date="2026-04-30")

    assert [row["financial_ann_date"] for row in loaded] == [
        "2026-03-28",
        "2026-04-02",
    ]
    assert loaded[-1]["fina_indicator"]["roe_waa"] == 18.0


def test_same_announcement_revision_is_append_only_and_not_visible_before_observed() -> None:
    conn = _conn()
    original = {
        "ts_code": "600000.SH",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "fina_indicator": {"roe_waa": 18.0, "update_flag": "0"},
        "balancesheet": {"report_type": "1"},
        "income": {"report_type": "1"},
        "source_meta": {"status": "complete"},
    }
    revision = {
        **original,
        "fina_indicator": {"roe_waa": 9.0, "update_flag": "1"},
    }

    repository.save_financial_snapshots(
        conn,
        [original],
        observed_date="2026-03-28",
    )
    repository.save_financial_snapshots(
        conn,
        [revision],
        observed_date="2026-05-10",
    )

    historical = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-04-30",
    )
    current = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-05-10",
    )

    assert len(historical) == 1
    assert historical[0]["fina_indicator"]["roe_waa"] == 18.0
    assert historical[0]["version_visible_date"] == "2026-03-28"
    assert len(current) == 2
    assert [row["fina_indicator"]["roe_waa"] for row in current] == [18.0, 9.0]
    assert current[-1]["version_visible_date"] == "2026-05-10"
    assert len({row["snapshot_hash"] for row in current}) == 2


def test_repeated_identical_snapshot_is_idempotent_without_overwrite() -> None:
    conn = _conn()
    snapshot = {
        "ts_code": "600000.SH",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "fina_indicator": {"roe_waa": 18.0, "update_flag": "0"},
        "balancesheet": {},
        "income": {},
        "source_meta": {"batch": "first"},
    }

    repository.save_financial_snapshots(
        conn,
        [snapshot],
        observed_date="2026-03-28",
    )
    repository.save_financial_snapshots(
        conn,
        [{**snapshot, "source_meta": {"batch": "retry"}}],
        observed_date="2026-04-02",
    )

    rows = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-04-30",
    )
    assert len(rows) == 1
    assert rows[0]["source_meta"] == {"batch": "first"}


def test_same_day_revisions_choose_the_later_observation_time() -> None:
    conn = _conn()
    base = {
        "ts_code": "600000.SH",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "balancesheet": {"report_type": "1"},
        "income": {"report_type": "1"},
    }
    repository.save_financial_snapshots(
        conn,
        [
            {
                **base,
                "fina_indicator": {"roe_waa": 18.0, "update_flag": "1"},
            }
        ],
        observed_date="2026-05-10",
        observed_at="2026-05-10T09:00:00+08:00",
    )
    repository.save_financial_snapshots(
        conn,
        [
            {
                **base,
                "fina_indicator": {"roe_waa": 8.0, "update_flag": "1"},
            }
        ],
        observed_date="2026-05-10",
        observed_at="2026-05-10T10:00:00+08:00",
    )

    rows = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-05-10",
    )
    latest = service._latest_financial_views(rows)["600000"]

    assert latest["latest"]["values"]["roe"] == 8.0


def test_unflagged_content_change_is_not_backdated_to_original_announcement() -> None:
    conn = _conn()
    base = {
        "ts_code": "600000.SH",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "balancesheet": {"report_type": "1"},
        "income": {"report_type": "1"},
    }
    repository.save_financial_snapshots(
        conn,
        [{**base, "fina_indicator": {"roe_waa": 18.0, "update_flag": "0"}}],
        observed_date="2026-03-28",
    )
    repository.save_financial_snapshots(
        conn,
        [{**base, "fina_indicator": {"roe_waa": 9.0, "update_flag": "0"}}],
        observed_date="2026-05-10",
    )

    historical = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-04-30",
    )
    current = repository.load_financial_snapshots(
        conn,
        as_of_date="2026-05-10",
    )

    assert [row["fina_indicator"]["roe_waa"] for row in historical] == [18.0]
    assert [row["fina_indicator"]["roe_waa"] for row in current] == [18.0, 9.0]


def test_run_status_preserves_failed_vs_complete_empty() -> None:
    conn = _conn()
    repository.save_run(
        conn,
        scan_date="2026-06-30",
        signal_month="2026-06",
        status="failed",
        input_by="codex",
        source_status={"monthly": "source_failed"},
        counts={},
        error="monthly timeout",
    )
    repository.save_run(
        conn,
        scan_date="2026-06-30",
        signal_month="2026-06",
        status="complete",
        input_by="launchd",
        source_status={"monthly": "success"},
        counts={"technical_candidates": 0},
        error=None,
    )

    run = repository.get_run(conn, "2026-06-30")
    assert run["status"] == "complete"
    assert run["input_by"] == "launchd"
    assert run["counts"] == {"technical_candidates": 0}
    assert run["error"] is None
