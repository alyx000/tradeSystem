from __future__ import annotations

import json
import sqlite3

import pytest

from db.connection import get_connection
from db.migrate import migrate
from services.trinity_factor import repository as R


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "factor_repository.db")
    migrate(connection)
    yield connection
    connection.close()


def _run(score_run_id: str = "run-1", **overrides) -> dict:
    record = {
        "score_run_id": score_run_id,
        "trade_date": "2026-07-10",
        "retry_of_run_id": None,
        "cache_key": "cache:2026-07-10:v1",
        "input_digest": "input-digest",
        "is_cacheable": True,
        "provider": "antigravity",
        "requested_model": "model-requested",
        "actual_model": None,
        "cli_version": "agy-1.2.3",
        "runtime_version": "python-3.9.6",
        "prompt_versions_json": {"factor": "factor-v1", "sector": "sector-v1"},
        "prompt_sha256_json": {"factor": "sha-factor", "sector": "sha-sector"},
        "schema_version": "trinity-score-v1",
        "ruleset_version": "rules-v1",
        "evidence_snapshot_json": {"facts": [{"id": "ev-1", "value": 1}]},
        "rule_gate_json": {"caps": {"market_node": 4}},
        "factor_scores_json": [{"factor_code": "market_node", "total_score": 82}],
        "sector_scores_json": [{"sector_key": "sw2:半导体", "total_score": 76}],
        "system_recommendation_json": {"primary": "market_node"},
        "valid_raw_json": {"factor": {"schema_version": "factor-v1"}, "sector": {}},
        "raw_output_sha256_json": {"factor": "raw-factor", "sector": "raw-sector"},
        "diagnostics_json": {},
        "status": "success",
        "attempt_count": 1,
        "duration_ms": 321,
    }
    record.update(overrides)
    return record


def _request(request_id: str = "request-1", **overrides) -> dict:
    record = {
        "request_id": request_id,
        "trade_date": "2026-07-10",
        "input_by": "alice",
        "cache_hit": False,
        "resolved_run_id": "run-1",
        "cache_key": "cache:2026-07-10:v1",
    }
    record.update(overrides)
    return record


def test_insert_and_get_score_run_round_trips_json(conn):
    record = _run()

    R.insert_score_run(conn, record)
    loaded = R.get_score_run(conn, "run-1")

    assert loaded is not None
    for key, value in record.items():
        assert loaded[key] == value
    assert loaded["created_at"]


def test_insert_and_list_score_requests_round_trip_request_audit(conn):
    R.insert_score_run(conn, _run())
    R.insert_score_request(conn, _request())

    rows = R.list_score_requests(
        conn,
        trade_date="2026-07-10",
        resolved_run_id="run-1",
    )

    assert len(rows) == 1
    assert rows[0] == {
        **_request(),
        "created_at": rows[0]["created_at"],
    }
    assert rows[0]["created_at"]
    assert rows[0]["cache_hit"] is False


def test_score_requests_are_append_only_and_enforce_audit_constraints(conn):
    R.insert_score_run(conn, _run())
    R.insert_score_request(conn, _request())

    with pytest.raises(sqlite3.IntegrityError):
        R.insert_score_request(conn, _request(input_by="bob"))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE daily_review_factor_score_requests "
            "SET input_by = ? WHERE request_id = ?",
            ("bob", "request-1"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "DELETE FROM daily_review_factor_score_requests WHERE request_id = ?",
            ("request-1",),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        R.insert_score_request(conn, _request("request-blank", input_by="  "))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        R.insert_score_request(
            conn,
            _request("request-missing", resolved_run_id="missing"),
        )

    assert [row["request_id"] for row in R.list_score_requests(conn)] == [
        "request-1"
    ]


def _evaluation(evaluation_id: str = "eval-1", **overrides) -> dict:
    record = {
        "evaluation_id": evaluation_id,
        "score_run_id": "run-1",
        "source_review_date": "2026-07-10",
        "evaluation_trade_date": "2026-07-11",
        "rule_top_code": "market_node",
        "llm_top_code": "market_node",
        "system_top_code": "market_node",
        "human_top_code": None,
        "system_outcome": "pending",
        "confirmed_outcome": None,
        "actual_evidence_json": {"market": {"close": 3500}, "checks": ["t1-1"]},
        "evaluation_note": None,
        "input_by": "codex",
    }
    record.update(overrides)
    return record


def test_score_run_json_is_stored_canonically(conn):
    R.insert_score_run(conn, _run())

    row = conn.execute(
        "SELECT prompt_versions_json, evidence_snapshot_json "
        "FROM daily_review_factor_score_runs WHERE score_run_id = ?",
        ("run-1",),
    ).fetchone()

    assert row["prompt_versions_json"] == '{"factor":"factor-v1","sector":"sector-v1"}'
    assert row["evidence_snapshot_json"] == (
        '{"facts":[{"id":"ev-1","value":1}]}'
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.pop("provider"),
        lambda record: record.__setitem__("diagnostics_json", {"bad": float("nan")}),
        lambda record: record.__setitem__("valid_raw_json", object()),
    ],
)
def test_insert_score_run_fails_fast_on_missing_or_invalid_json(conn, mutate):
    record = _run()
    mutate(record)

    with pytest.raises(ValueError):
        R.insert_score_run(conn, record)

    assert conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_runs"
    ).fetchone()[0] == 0


def test_score_runs_are_append_only_and_duplicate_insert_never_overwrites(conn):
    R.insert_score_run(conn, _run())

    with pytest.raises(sqlite3.IntegrityError):
        R.insert_score_run(conn, _run(provider="other"))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE daily_review_factor_score_runs SET provider = ? WHERE score_run_id = ?",
            ("other", "run-1"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "DELETE FROM daily_review_factor_score_runs WHERE score_run_id = ?",
            ("run-1",),
        )

    assert R.get_score_run(conn, "run-1")["provider"] == "antigravity"


def test_retry_child_is_new_row_and_foreign_key_is_enforced(conn):
    R.insert_score_run(conn, _run())
    R.insert_score_run(conn, _run(
        "run-2",
        retry_of_run_id="run-1",
        attempt_count=2,
        is_cacheable=False,
    ))

    child = R.get_score_run(conn, "run-2")
    assert child["retry_of_run_id"] == "run-1"
    assert conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_runs"
    ).fetchone()[0] == 2

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        R.insert_score_run(conn, _run("run-bad", retry_of_run_id="missing"))


def test_find_cached_score_run_returns_latest_cacheable_success_only(conn):
    R.insert_score_run(conn, _run("run-old"))
    R.insert_score_run(conn, _run(
        "run-failed",
        status="runtime_failed",
        valid_raw_json=None,
    ))
    R.insert_score_run(conn, _run("run-noncache", is_cacheable=False))
    R.insert_score_run(conn, _run("run-latest"))

    cached = R.find_cached_score_run(conn, "cache:2026-07-10:v1")

    assert cached["score_run_id"] == "run-latest"
    assert R.find_cached_score_run(conn, "missing-cache") is None


def test_list_score_runs_supports_metrics_filters_and_limit(conn):
    R.insert_score_run(conn, _run("run-1", trade_date="2026-07-09"))
    R.insert_score_run(conn, _run("run-2", trade_date="2026-07-10"))
    R.insert_score_run(conn, _run(
        "run-3",
        trade_date="2026-07-10",
        status="runtime_failed",
        valid_raw_json=None,
    ))

    rows = R.list_score_runs(
        conn,
        trade_date="2026-07-10",
        status="success",
        limit=10,
    )

    assert [row["score_run_id"] for row in rows] == ["run-2"]
    assert R.list_score_runs(conn, trade_date="' OR 1=1 --", limit=10) == []
    assert len(R.list_score_runs(conn, limit=2)) == 2


def test_valid_raw_json_is_only_accepted_for_success(conn):
    with pytest.raises(ValueError):
        R.insert_score_run(conn, _run(status="runtime_failed"))
    with pytest.raises(ValueError):
        R.insert_score_run(conn, _run(valid_raw_json=None))


def test_partial_valid_layer_and_rule_only_results_can_be_cached(conn):
    R.insert_score_run(conn, _run(
        "run-sector-failed",
        status="sector_failed",
        valid_raw_json={"factor": {"schema_version": "factor-v1"}},
    ))
    R.insert_score_run(conn, _run(
        "run-rule-only",
        cache_key="cache:rule-only",
        status="rule_only",
        valid_raw_json=None,
        factor_scores_json=None,
        sector_scores_json=None,
    ))

    assert R.find_cached_score_run(
        conn, "cache:2026-07-10:v1"
    )["score_run_id"] == "run-sector-failed"
    assert R.find_cached_score_run(
        conn, "cache:rule-only"
    )["score_run_id"] == "run-rule-only"


@pytest.mark.parametrize("trade_date", ["20260710", "not-a-date"])
def test_score_run_date_check_rejects_invalid_format(conn, trade_date):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        R.insert_score_run(conn, _run(trade_date=trade_date))


def test_evaluation_upsert_round_trips_json_and_preserves_natural_unique_row(conn):
    R.insert_score_run(conn, _run())
    assert R.upsert_evaluation(conn, _evaluation()) == "eval-1"

    first = R.get_evaluation(conn, "eval-1")
    assert first["actual_evidence_json"] == {
        "market": {"close": 3500},
        "checks": ["t1-1"],
    }

    stored_id = R.upsert_evaluation(conn, _evaluation(
        "eval-replacement",
        human_top_code="sector_rhythm",
        confirmed_outcome="partial",
        actual_evidence_json={"checks": ["t1-2"]},
        evaluation_note="T+1 回验",
        input_by="alyx",
    ))

    assert stored_id == "eval-1"
    rows = R.list_evaluations(conn, score_run_id="run-1")
    assert len(rows) == 1
    assert rows[0]["human_top_code"] == "sector_rhythm"
    assert rows[0]["confirmed_outcome"] == "partial"
    assert rows[0]["actual_evidence_json"] == {"checks": ["t1-2"]}
    assert rows[0]["input_by"] == "alyx"


def test_evaluation_foreign_key_date_json_and_required_fields_fail_fast(conn):
    R.insert_score_run(conn, _run())

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        R.upsert_evaluation(conn, _evaluation("eval-fk", score_run_id="missing"))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        R.upsert_evaluation(conn, _evaluation("eval-date", evaluation_trade_date="20260711"))
    with pytest.raises(ValueError):
        R.upsert_evaluation(conn, _evaluation("eval-json", actual_evidence_json={"x": object()}))
    missing = _evaluation("eval-missing")
    missing.pop("input_by")
    with pytest.raises(ValueError):
        R.upsert_evaluation(conn, missing)


def test_list_evaluations_filters_by_dates_and_uses_parameters(conn):
    R.insert_score_run(conn, _run())
    R.upsert_evaluation(conn, _evaluation("eval-1"))
    R.upsert_evaluation(conn, _evaluation(
        "eval-2",
        source_review_date="2026-07-09",
        evaluation_trade_date="2026-07-10",
    ))

    rows = R.list_evaluations(
        conn,
        source_review_date="2026-07-10",
        evaluation_trade_date="2026-07-11",
        limit=10,
    )

    assert [row["evaluation_id"] for row in rows] == ["eval-1"]
    assert R.list_evaluations(conn, source_review_date="' OR 1=1 --") == []
