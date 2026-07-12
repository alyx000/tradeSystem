from __future__ import annotations

import json

import pytest

from db import queries as Q
from db.connection import get_connection
from db.migrate import migrate
from services.trinity_factor.evidence import build_evidence_snapshot
from services.trinity_factor.cycle import (
    _objective_fact_items,
    build_factor_metrics,
    confirm_factor_decision,
    confirm_t1_evaluation,
    suggest_t1_evaluation,
)
from services.trinity_factor.repository import (
    get_evaluation,
    insert_score_run,
)
from services.trinity_factor.scoring import score_factor


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "trinity-cycle.db")
    migrate(connection)
    Q.upsert_trade_calendar(connection, [
        {"date": "2026-07-03", "is_open": 1},
        {"date": "2026-07-09", "is_open": 1},
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-11", "is_open": 0},
        {"date": "2026-07-12", "is_open": 0},
        {"date": "2026-07-13", "is_open": 1},
        {"date": "2026-07-14", "is_open": 1},
    ])
    connection.commit()
    yield connection
    connection.close()


def _factor_score(code: str, *, primary: bool) -> dict:
    scored = score_factor(
        factor_code=code,
        dimension_scores={
            "current_dominance": 5 if primary else 3,
            "cross_layer_alignment": 5 if primary else 3,
            "rhythm_clarity": 5 if primary else 3,
            "next_stage_relevance": 5 if primary else 3,
            "counterevidence": 0 if primary else 1,
        },
        evidence_quality=5 if primary else 3,
    )
    scored["reason"] = "[判断]测试"
    return scored


def _insert_run(
    conn,
    *,
    run_id: str = "run-1",
    trade_date: str = "2026-07-10",
    primary: str = "sector_rhythm",
    status: str = "success",
    retry_of_run_id: str | None = None,
    evidence_snapshot: dict | None = None,
) -> None:
    codes = ("market_node", "sector_rhythm", "style_regime", "leader_signal")
    scores = [_factor_score(code, primary=code == primary) for code in codes]
    recommendation = {
        "primary": {"factor_code": primary, "total_score": 100},
        "supporting": [{"factor_code": "market_node"}],
        "confidence": "high",
        "undetermined_reason": None,
        "recommendation_source": "llm_program_recompute",
    }
    insert_score_run(conn, {
        "score_run_id": run_id,
        "trade_date": trade_date,
        "retry_of_run_id": retry_of_run_id,
        "cache_key": f"cache-{run_id}",
        "input_digest": f"digest-{run_id}",
        "is_cacheable": status in {"success", "sector_failed", "rule_only"},
        "provider": "antigravity",
        "requested_model": "model-a",
        "actual_model": None,
        "cli_version": "agy-1.2.3",
        "runtime_version": "python-3.9",
        "prompt_versions_json": {"factor": "factor-v1", "sector": "sector-v1"},
        "prompt_sha256_json": {"factor": "p1", "sector": "p2"},
        "schema_version": "score-v1",
        "ruleset_version": "rules-v1",
        "evidence_snapshot_json": evidence_snapshot or build_evidence_snapshot(
            trade_date,
            _evaluation_prefill(),
            {},
        ),
        "rule_gate_json": {"rule_fallback_code": primary},
        "factor_scores_json": scores,
        "sector_scores_json": [],
        "system_recommendation_json": recommendation,
        "valid_raw_json": (
            {"factor": {"schema_version": "factor-v1"}}
            if status in {"success", "sector_failed"}
            else None
        ),
        "raw_output_sha256_json": {"factor": "raw"},
        "diagnostics_json": {},
        "status": status,
        "attempt_count": 1,
        "duration_ms": 10,
    })
    conn.commit()


def _step8(conn, trade_date: str) -> dict:
    row = Q.get_daily_review(conn, trade_date)
    return json.loads(row["step8_plan"])


def _candidate() -> dict:
    return {
        "sector_key": "industry:半导体",
        "sector_name": "半导体",
        "sector_type": "industry",
        "candidate_tier": "core",
        "data_status": "ok",
        "rank_reason": "高置信节奏",
        "facts": {"lead_stock": "测试龙头"},
        "evidence_items": [
            {
                "evidence_id": "2026-07-13:industry:半导体:rhythm:1",
                "source": "sector_rhythm_industry",
                "category": "rhythm",
                "polarity": "support",
                "objective": True,
                "text": "高置信节奏",
            },
            {
                "evidence_id": "2026-07-13:industry:半导体:moneyflow:2",
                "source": "industry_moneyflow",
                "category": "moneyflow",
                "polarity": "support",
                "objective": True,
                "text": "资金净流入",
            },
        ],
    }


def _evaluation_prefill(*, candidates=None, market=True) -> dict:
    return {
        "market": {"date": "2026-07-13", "total_amount": 15000} if market else None,
        "review_signals": {
            "market": {"market_structure_rows": [{"name": "沪市A股"}]},
            "sectors": {
                "projection_candidates": [_candidate()] if candidates is None else candidates,
            },
            "emotion": {"ladder_rows": [{"name": "3板", "nums": 3}]},
        },
    }


def _directional_prefill(
    *,
    date: str,
    sh_change: float,
    sz_change: float,
    up_count: int,
    down_count: int,
    limit_up_count: int,
    limit_down_count: int,
) -> dict:
    return {
        "market": {
            "date": date,
            "total_amount": 15000,
            "sh_index_change": sh_change,
            "sz_index_change": sz_change,
            "up_count": up_count,
            "down_count": down_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
        },
        "review_signals": {
            "market": {"market_structure_rows": [{"name": "沪市A股"}]},
            "sectors": {"projection_candidates": []},
            "emotion": {"ladder_rows": []},
        },
    }


def _production_directional_prefill(
    *,
    date: str,
    sh_change: float,
    sz_change: float,
    advance_count: int,
    decline_count: int,
    limit_up_count: int,
    limit_down_count: int,
) -> dict:
    return {
        "market": {
            "date": date,
            "total_amount": 15000,
            "sh_index_change_pct": sh_change,
            "sz_index_change_pct": sz_change,
            "advance_count": advance_count,
            "decline_count": decline_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
        },
        "review_signals": {
            "market": {"market_structure_rows": [{"name": "沪市A股"}]},
            "sectors": {"projection_candidates": []},
            "emotion": {"ladder_rows": []},
        },
    }


def _style_prefill(
    *,
    date: str,
    cap: str | None,
    board: str | None,
    premium: str | None,
) -> dict:
    style_factors: dict = {}
    if cap is not None:
        style_factors["cap_preference"] = {
            "csi300_chg": -0.4,
            "csi1000_chg": 1.1,
            "spread": 1.5,
            "relative": cap,
        }
    if board is not None:
        style_factors["board_preference"] = {
            "dominant_type": board,
            "pct_10cm": 20.0,
            "pct_20cm": 75.0,
            "pct_30cm": 5.0,
        }
    if premium is not None:
        style_factors["premium_snapshot"] = {
            "first_board": {
                "count": 20,
                "premium_median": 1.2,
                "open_up_rate": 0.6,
            },
        }
        style_factors["premium_trend"] = {"direction": premium}
    return {
        "market": {
            "date": date,
            "style_factors": style_factors,
        },
        "review_signals": {
            "market": {},
            "sectors": {"projection_candidates": []},
            "emotion": {"ladder_rows": []},
        },
    }


def _leader_prefill(
    *,
    date: str,
    highest_board: int | None,
    top_names: list[str],
    feedback_close_change: float | None = None,
    feedback_source_date: str | None = None,
    feedback_limit_up: bool = False,
    feedback_name: str = "龙头甲",
    with_promotion: bool = False,
) -> dict:
    market: dict = {"date": date, "total_amount": 15000}
    if highest_board is not None:
        market.update({
            "highest_board": highest_board,
            "continuous_board_counts": {str(highest_board): top_names},
        })

    style_factors: dict = {}
    if feedback_close_change is not None:
        style_factors["popularity"] = [{
            "code": "000001.SZ",
            "name": feedback_name,
            "source": ["consecutive"],
            "prev_close": 10.0,
            "t_open_premium_pct": 1.0,
            "t_close_change_pct": feedback_close_change,
            "t_is_limit_up": feedback_limit_up,
            "t_is_limit_down": False,
        }]
        market["style_factors"] = style_factors
    if with_promotion:
        style_factors["promotion"] = {
            "trade_date": date,
            "first_to_second": {
                "base": 2,
                "promoted": 1,
                "rate": 0.5,
                "promoted_names": ["晋级甲"],
            },
        }
        market["style_factors"] = style_factors

    prefill = {
        "market": market,
        "review_signals": {
            "market": {},
            "sectors": {"projection_candidates": []},
            "emotion": {"ladder_rows": []},
        },
    }
    if feedback_close_change is not None:
        prefill["prev_market"] = {
            "highest_board": 4,
            "continuous_board_counts": {"4": [feedback_name]},
        }
        if feedback_source_date is not None:
            prefill["prev_market"]["date"] = feedback_source_date
    return prefill


def _legacy_factor_snapshot(
    factor_code: str,
    *,
    source: str,
    content: object,
    source_status: str | None = None,
) -> dict:
    fact = {
        "evidence_id": f"2026-07-10:{factor_code}:{source}",
        "source": source,
        "kind": "fact",
        "polarity": "support",
        "content": content,
    }
    if source_status is not None:
        fact["source_status"] = source_status
    return {
        "factor_candidates": [{
            "factor_code": factor_code,
            "evidence_items": [fact],
        }],
        "sector_candidates": [],
        "rule_gate": {},
    }


def _suggest_for_prefills(
    conn,
    *,
    primary: str,
    source_prefill: dict | None,
    actual_prefill: dict,
    source_snapshot: dict | None = None,
) -> dict:
    _insert_run(
        conn,
        primary=primary,
        evidence_snapshot=(
            source_snapshot
            if source_snapshot is not None
            else build_evidence_snapshot("2026-07-10", source_prefill, {})
        ),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()
    return suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )


def test_t1_objective_facts_require_valid_identity_group_and_content() -> None:
    def fact(identifier: str, **overrides) -> dict:
        item = {
            "evidence_id": identifier,
            "source": "test_source",
            "source_status": "ok",
            "kind": "fact",
            "polarity": "support",
            "content": {"value": 1},
        }
        item.update(overrides)
        return item

    missing_id = fact("missing-id")
    missing_id.pop("evidence_id")
    missing_source = fact("missing-source")
    missing_source.pop("source")
    legacy_valid = fact(
        "legacy-valid", source="legacy_source", content={"value": 0}
    )
    legacy_valid.pop("source_status")
    factor = {"evidence_items": [
        missing_id,
        fact("blank-id", evidence_id=" "),
        missing_source,
        fact("blank-source", source=" ", quality_group="valid_group"),
        fact("blank-group", quality_group=" "),
        fact("invalid-group", quality_group=42),
        fact("empty-body", content={"nested": []}, text=" "),
        fact("nonfinite-body", content=float("nan")),
        fact("non-ok", source_status="source_failed"),
        legacy_valid,
        fact("empty-group-fallback", quality_group="", content={"value": False}),
        fact("text-valid", quality_group="text_group", content={}, text="客观文本"),
    ]}

    assert [
        item.get("evidence_id") for item in _objective_fact_items(factor)
    ] == ["legacy-valid", "empty-group-fallback", "text-valid"]


def test_accept_decision_syncs_legacy_fields_and_preserves_step8(conn) -> None:
    _insert_run(conn)
    Q.upsert_daily_review(conn, "2026-07-10", {
        "step8_plan": {"summary": {"one_sentence": "原结论"}, "key_factor": "旧值"}
    })
    conn.commit()

    decision = confirm_factor_decision(
        conn,
        trade_date="2026-07-10",
        score_run_id="run-1",
        decision={"status": "accepted"},
        input_by="alyx",
        current_input_digest="digest-run-1",
    )

    step8 = _step8(conn, "2026-07-10")
    assert decision["primary_factor"] == "sector_rhythm"
    assert decision["supporting_factors"] == ["market_node"]
    assert step8["factor_decision"] == decision
    assert step8["key_factor"] == "sector_rhythm"
    assert step8["secondary_factors"] == ["market_node"]
    assert step8["summary"] == {"one_sentence": "原结论"}


def test_override_requires_reason_and_valid_at_most_two_factors(conn) -> None:
    _insert_run(conn)

    with pytest.raises(ValueError, match="override_reason"):
        confirm_factor_decision(
            conn,
            trade_date="2026-07-10",
            score_run_id="run-1",
            decision={"status": "overridden", "primary_factor": "style_regime"},
            input_by="alyx",
            current_input_digest="digest-run-1",
        )
    with pytest.raises(ValueError, match="supporting_factors"):
        confirm_factor_decision(
            conn,
            trade_date="2026-07-10",
            score_run_id="run-1",
            decision={
                "status": "overridden",
                "primary_factor": "style_regime",
                "supporting_factors": ["market_node", "leader_signal", "sector_rhythm"],
                "override_reason": "人工看到不同连接",
            },
            input_by="alyx",
            current_input_digest="digest-run-1",
        )

    decision = confirm_factor_decision(
        conn,
        trade_date="2026-07-10",
        score_run_id="run-1",
        decision={
            "status": "overridden",
            "primary_factor": "style_regime",
            "supporting_factors": ["market_node"],
            "override_reason": "人工看到不同连接",
        },
        input_by="alyx",
        current_input_digest="digest-run-1",
    )
    assert decision["primary_factor"] == "style_regime"
    assert _step8(conn, "2026-07-10")["key_factor"] == "style_regime"


def test_undetermined_clears_legacy_factor_fields(conn) -> None:
    _insert_run(conn)
    Q.upsert_daily_review(conn, "2026-07-10", {
        "step8_plan": {"key_factor": "旧值", "secondary_factors": ["旧辅助"]}
    })

    decision = confirm_factor_decision(
        conn,
        trade_date="2026-07-10",
        score_run_id="run-1",
        decision={"status": "undetermined", "override_reason": "看不懂"},
        input_by="alyx",
        current_input_digest="digest-run-1",
    )

    step8 = _step8(conn, "2026-07-10")
    assert decision["primary_factor"] is None
    assert step8["key_factor"] == ""
    assert step8["secondary_factors"] == []


def test_factor_confirmation_rejects_closed_trade_date(conn) -> None:
    _insert_run(conn, trade_date="2026-07-11")

    with pytest.raises(ValueError, match="trade_date must be an open trade date"):
        confirm_factor_decision(
            conn,
            trade_date="2026-07-11",
            score_run_id="run-1",
            decision={"status": "accepted"},
            input_by="alyx",
            current_input_digest="digest-run-1",
        )


def test_t1_evaluation_uses_strict_next_trade_date_and_missing_review_semantics(conn) -> None:
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-11", "is_open": 0},
        {"date": "2026-07-12", "is_open": 0},
        {"date": "2026-07-13", "is_open": 1},
        {"date": "2026-07-14", "is_open": 1},
    ])
    _insert_run(conn)

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=_evaluation_prefill(),
    )
    assert suggestion["system_outcome"] == "not_applicable"
    assert suggestion["actual_evidence_json"]["counter_source_count"] == 0

    with pytest.raises(ValueError, match="strict next trade date"):
        suggest_t1_evaluation(
            conn,
            evaluation_trade_date="2026-07-14",
            source_review_date="2026-07-10",
            score_run_id="run-1",
            prefill=_evaluation_prefill(),
        )


def test_t1_default_prefers_cacheable_success_parent_over_later_failed_retry(conn) -> None:
    _insert_run(conn, run_id="parent-run", status="success")
    _insert_run(
        conn,
        run_id="failed-retry",
        status="factor_failed",
        retry_of_run_id="parent-run",
    )

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id=None,
        prefill=_evaluation_prefill(),
    )

    assert suggestion["score_run_id"] == "parent-run"


def test_t1_default_falls_back_to_latest_recommended_failed_run(conn) -> None:
    _insert_run(conn, run_id="older-failure", status="factor_failed")
    _insert_run(conn, run_id="latest-failure", status="factor_failed")

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id=None,
        prefill=_evaluation_prefill(),
    )

    assert suggestion["score_run_id"] == "latest-failure"


def test_t1_explicit_failed_run_id_is_respected(conn) -> None:
    _insert_run(conn, run_id="successful-run", status="success")
    _insert_run(conn, run_id="explicit-failure", status="factor_failed")

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="explicit-failure",
        prefill=_evaluation_prefill(),
    )

    assert suggestion["score_run_id"] == "explicit-failure"


def test_t1_objective_suggestion_and_manual_confirmation(conn) -> None:
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-13", "is_open": 1},
    ])
    _insert_run(conn)
    Q.upsert_daily_review(conn, "2026-07-10", {"step8_plan": {}})
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=_evaluation_prefill(),
    )

    assert suggestion["system_outcome"] == "hit"
    assert suggestion["actual_evidence_json"]["objective_source_count"] >= 2
    stored = confirm_t1_evaluation(
        conn,
        suggestion=suggestion,
        confirmed_outcome="partial",
        evaluation_note="延续但强度一般",
        input_by="alyx",
    )
    row = get_evaluation(conn, stored["evaluation_id"])
    assert row["system_outcome"] == "hit"
    assert row["confirmed_outcome"] == "partial"
    assert row["input_by"] == "alyx"


def test_t1_market_reversal_is_miss_instead_of_counting_available_sources(conn) -> None:
    source_prefill = _directional_prefill(
        date="2026-07-10", sh_change=2.0, sz_change=2.5,
        up_count=4200, down_count=700, limit_up_count=120, limit_down_count=3,
    )
    actual_prefill = _directional_prefill(
        date="2026-07-13", sh_change=-8.8, sz_change=-9.2,
        up_count=100, down_count=5000, limit_up_count=2, limit_down_count=500,
    )
    _insert_run(
        conn,
        primary="market_node",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "miss"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_direction"] == 1
    assert comparison["actual_direction"] == -1


def test_t1_market_direction_reads_production_daily_market_fields(conn) -> None:
    source_prefill = _production_directional_prefill(
        date="2026-07-10",
        sh_change=2.0,
        sz_change=2.5,
        advance_count=4200,
        decline_count=700,
        limit_up_count=10,
        limit_down_count=10,
    )
    actual_prefill = _production_directional_prefill(
        date="2026-07-13",
        sh_change=-8.8,
        sz_change=-9.2,
        advance_count=100,
        decline_count=5000,
        limit_up_count=10,
        limit_down_count=10,
    )
    _insert_run(
        conn,
        primary="market_node",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "miss"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_direction"] == 1
    assert comparison["actual_direction"] == -1
    assert comparison["source_votes"] == [1, 1, 1, 0]
    assert comparison["actual_votes"] == [-1, -1, -1, 0]


@pytest.mark.parametrize(
    ("source_values", "actual_values", "expected_outcome", "expected_matches"),
    [
        (("偏小盘", "20cm", "走强"), ("偏大盘", "10cm", "走弱"), "miss", 0),
        (("偏小盘", "20cm", "走强"), ("偏小盘", "20cm", "走强"), "hit", 3),
        (("偏小盘", "20cm", "走强"), ("偏小盘", "10cm", "走弱"), "partial", 1),
        (("偏小盘", "20cm", "走强"), ("偏小盘", "20cm", "走弱"), "partial", 2),
        (("偏小盘", None, None), (None, "20cm", None), "missing_data", 0),
    ],
    ids=("switch", "same", "one-match", "two-matches", "no-common"),
)
def test_t1_style_dimensions_are_compared_structurally(
    conn,
    source_values: tuple[str | None, str | None, str | None],
    actual_values: tuple[str | None, str | None, str | None],
    expected_outcome: str,
    expected_matches: int,
) -> None:
    source_prefill = _style_prefill(
        date="2026-07-10",
        cap=source_values[0],
        board=source_values[1],
        premium=source_values[2],
    )
    actual_prefill = _style_prefill(
        date="2026-07-13",
        cap=actual_values[0],
        board=actual_values[1],
        premium=actual_values[2],
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="style_regime",
        source_prefill=source_prefill,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == expected_outcome
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["matched_dimensions"] == expected_matches
    assert comparison["comparable_dimensions"] == (0 if expected_outcome == "missing_data" else 3)


@pytest.mark.parametrize(
    ("new_content", "expected_outcome", "expected_matches", "expected_comparable"),
    [
        ({"spread": 1.5}, "hit", 3, 3),
        ({"relative": "偏大盘"}, "miss", 0, 1),
    ],
    ids=("empty-new-signature-falls-back", "valid-new-signature-precedes"),
)
def test_t1_style_new_signature_fallback_and_precedence(
    conn,
    new_content: dict,
    expected_outcome: str,
    expected_matches: int,
    expected_comparable: int,
) -> None:
    source_snapshot = _legacy_factor_snapshot(
        "style_regime",
        source="style_factors",
        content={
            "cap_preference": {"relative": "偏小盘"},
            "board_preference": {"dominant_type": "20cm"},
            "premium_trend": {"direction": "走强"},
        },
    )
    source_snapshot["factor_candidates"][0]["evidence_items"].insert(0, {
        "evidence_id": "2026-07-10:style_regime:cap_relative_strength",
        "source": "cap_relative_strength",
        "source_status": "ok",
        "kind": "fact",
        "polarity": "support",
        "content": new_content,
    })
    actual_prefill = _style_prefill(
        date="2026-07-13", cap="偏小盘", board="20cm", premium="走强"
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="style_regime",
        source_prefill=None,
        source_snapshot=source_snapshot,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == expected_outcome
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["matched_dimensions"] == expected_matches
    assert comparison["comparable_dimensions"] == expected_comparable


@pytest.mark.parametrize(
    (
        "actual_height", "actual_names", "feedback_close", "feedback_limit_up",
        "expected_outcome", "expected_results", "expected_positive",
    ),
    [
        (5, ["龙头丙", "龙头甲"], -5.0, True, "hit", (True, True, True), 3),
        (3, ["龙头丙"], 0.5, False, "partial", (False, False, True), 1),
        (3, ["龙头丙"], -0.5, False, "miss", (False, False, False), 0),
    ],
    ids=("hit", "partial", "miss"),
)
def test_t1_leader_dimensions_determine_structural_outcome(
    conn,
    actual_height: int,
    actual_names: list[str],
    feedback_close: float,
    feedback_limit_up: bool,
    expected_outcome: str,
    expected_results: tuple[bool, bool, bool],
    expected_positive: int,
) -> None:
    source_prefill = _leader_prefill(
        date="2026-07-10", highest_board=4, top_names=["龙头乙", "龙头甲"]
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13",
        highest_board=actual_height,
        top_names=actual_names,
        feedback_close_change=feedback_close,
        feedback_source_date="2026-07-10",
        feedback_limit_up=feedback_limit_up,
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=source_prefill,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == expected_outcome
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["comparator"] == "leader_structure"
    assert comparison["source_highest_board"] == 4
    assert comparison["actual_highest_board"] == actual_height
    assert comparison["source_top_names"] == ["龙头乙", "龙头甲"]
    assert comparison["actual_top_names"] == sorted(actual_names)
    assert comparison["identity_overlap"] == (
        ["龙头甲"] if "龙头甲" in actual_names else []
    )
    assert comparison["dimension_results"] == {
        "height": expected_results[0],
        "identity": expected_results[1],
        "feedback": expected_results[2],
    }
    assert comparison["comparable_dimensions"] == 3
    assert comparison["positive_dimensions"] == expected_positive


@pytest.mark.parametrize(
    (
        "feedback_source_date", "expected_actual_source_date",
        "expected_feedback", "expected_outcome", "expected_comparable",
    ),
    [
        ("2026-07-09", "2026-07-09", None, "partial", 2),
        ("2026-07-10", "2026-07-10", True, "hit", 3),
        (None, None, None, "partial", 2),
        ("2026-02-30", None, None, "partial", 2),
    ],
    ids=("stale", "matching", "missing", "invalid"),
)
def test_t1_leader_feedback_requires_matching_source_date(
    conn,
    feedback_source_date: str | None,
    expected_actual_source_date: str | None,
    expected_feedback: bool | None,
    expected_outcome: str,
    expected_comparable: int,
) -> None:
    source_prefill = _leader_prefill(
        date="2026-07-10", highest_board=4, top_names=["龙头甲"]
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13",
        highest_board=4,
        top_names=["龙头乙"],
        feedback_close_change=2.0,
        feedback_source_date=feedback_source_date,
        feedback_name="龙头甲",
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=source_prefill,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == expected_outcome
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["expected_feedback_source_date"] == "2026-07-10"
    assert comparison["actual_feedback_source_date"] == expected_actual_source_date
    assert comparison["dimension_results"]["feedback"] is expected_feedback
    assert comparison["comparable_dimensions"] == expected_comparable


@pytest.mark.parametrize(
    ("source", "content"),
    [
        (
            "ladder_structure",
            {"highest_board": 1, "top_tier_names": ["首板甲"]},
        ),
        ("limit_ladder", [{"name": "首板甲", "nums": "1板"}]),
    ],
    ids=("new-highest-one", "legacy-one-board"),
)
def test_t1_leader_rejects_tier_below_two(
    conn,
    source: str,
    content: object,
) -> None:
    source_snapshot = _legacy_factor_snapshot(
        "leader_signal",
        source=source,
        content=content,
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13", highest_board=2, top_names=["首板甲"]
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=None,
        source_snapshot=source_snapshot,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_highest_board"] is None


def test_t1_leader_missing_actual_ladder_structure_is_missing_data(conn) -> None:
    source_prefill = _leader_prefill(
        date="2026-07-10", highest_board=4, top_names=["龙头甲"]
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13",
        highest_board=None,
        top_names=[],
        feedback_close_change=3.0,
        feedback_source_date="2026-07-10",
        feedback_name="龙头甲",
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=source_prefill,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_highest_board"] == 4
    assert comparison["actual_highest_board"] is None


def test_t1_legacy_limit_ladder_matches_new_ladder_structure(conn) -> None:
    source_snapshot = _legacy_factor_snapshot(
        "leader_signal",
        source="limit_ladder",
        content=[
            {"name": "龙头甲", "nums": 4},
            {"name": "龙头乙", "nums": 3},
        ],
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13", highest_board=4, top_names=["龙头甲"]
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=None,
        source_snapshot=source_snapshot,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "hit"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_highest_board"] == 4
    assert comparison["source_top_names"] == ["龙头甲"]
    assert comparison["positive_dimensions"] == 2


def test_t1_explicit_non_ok_source_fact_is_ignored(conn) -> None:
    source_snapshot = _legacy_factor_snapshot(
        "leader_signal",
        source="ladder_structure",
        source_status="source_failed",
        content={"highest_board": 4, "top_tier_names": ["龙头甲"]},
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13", highest_board=4, top_names=["龙头甲"]
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=None,
        source_snapshot=source_snapshot,
        actual_prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"


def test_t1_actual_evidence_counts_distinct_quality_groups(conn) -> None:
    source_prefill = _leader_prefill(
        date="2026-07-10", highest_board=4, top_names=["当日龙头"]
    )
    actual_prefill = _leader_prefill(
        date="2026-07-13",
        highest_board=4,
        top_names=["当日龙头"],
        feedback_close_change=2.0,
        feedback_source_date="2026-07-10",
        feedback_name="昨日龙头",
        with_promotion=True,
    )

    suggestion = _suggest_for_prefills(
        conn,
        primary="leader_signal",
        source_prefill=source_prefill,
        actual_prefill=actual_prefill,
    )

    evidence = suggestion["actual_evidence_json"]
    assert evidence["objective_source_count"] == 2
    assert evidence["counter_source_count"] == 0
    assert len(evidence["support_evidence_ids"]) == 3


def test_t1_sector_loses_all_core_continuity_is_miss(conn) -> None:
    source_prefill = _evaluation_prefill()
    actual_prefill = _evaluation_prefill(candidates=[])
    _insert_run(
        conn,
        primary="sector_rhythm",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "miss"
    assert suggestion["actual_evidence_json"]["comparison"]["actual_sector_keys"] == []


@pytest.mark.parametrize("actual_status", ["missing", "source_failed"])
def test_t1_sector_missing_or_failed_actual_source_is_missing_data(conn, actual_status) -> None:
    source_prefill = _evaluation_prefill()
    actual_prefill = _evaluation_prefill(candidates=[])
    actual_prefill["review_signals"]["sectors"]["data_status"] = actual_status
    _insert_run(
        conn,
        primary="sector_rhythm",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"
    assert suggestion["actual_evidence_json"]["comparison"]["actual_source_status"] == actual_status


@pytest.mark.parametrize("actual_status", ["missing", "source_failed"])
def test_t1_sector_degraded_actual_source_with_candidates_is_missing_data(conn, actual_status) -> None:
    source_prefill = _evaluation_prefill()
    actual_prefill = _evaluation_prefill()
    actual_prefill["review_signals"]["sectors"]["data_status"] = actual_status
    _insert_run(
        conn,
        primary="sector_rhythm",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_sector_keys"] == ["industry:半导体"]
    assert comparison["actual_sector_keys"] == ["industry:半导体"]
    assert comparison["actual_source_status"] == actual_status


@pytest.mark.parametrize("source_status", ["missing", "source_failed"])
def test_t1_sector_missing_or_failed_source_day_is_missing_data(conn, source_status) -> None:
    source_prefill = _evaluation_prefill()
    source_prefill["review_signals"]["sectors"]["data_status"] = source_status
    actual_prefill = _evaluation_prefill()
    _insert_run(
        conn,
        primary="sector_rhythm",
        evidence_snapshot=build_evidence_snapshot("2026-07-10", source_prefill, {}),
    )
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )

    assert suggestion["system_outcome"] == "missing_data"
    comparison = suggestion["actual_evidence_json"]["comparison"]
    assert comparison["source_source_status"] == source_status
    assert comparison["actual_source_status"] == "ok"


def test_t1_rejects_closed_source_trade_date(conn) -> None:
    _insert_run(conn, trade_date="2026-07-11")
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    with pytest.raises(ValueError, match="source_review_date must be an open trade date"):
        suggest_t1_evaluation(
            conn,
            evaluation_trade_date="2026-07-13",
            source_review_date="2026-07-11",
            score_run_id="run-1",
            prefill=_evaluation_prefill(),
        )


def test_missing_objective_facts_is_missing_data(conn) -> None:
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-13", "is_open": 1},
    ])
    _insert_run(conn, primary="style_regime")
    Q.upsert_daily_review(conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}})
    conn.commit()

    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=_evaluation_prefill(candidates=[], market=False),
    )

    assert suggestion["system_outcome"] == "missing_data"


def test_metrics_excludes_missing_data_from_performance_samples(conn) -> None:
    _insert_run(conn, run_id="run-1", trade_date="2026-07-09")
    _insert_run(conn, run_id="run-2", trade_date="2026-07-10", primary="market_node")
    Q.upsert_daily_review(conn, "2026-07-09", {
        "step8_plan": {"factor_decision": {"status": "accepted", "score_run_id": "run-1"}}
    })
    Q.upsert_daily_review(conn, "2026-07-10", {
        "step8_plan": {"factor_decision": {"status": "overridden", "score_run_id": "run-2"}}
    })
    from services.trinity_factor.repository import upsert_evaluation
    upsert_evaluation(conn, {
        "evaluation_id": "eval-1",
        "score_run_id": "run-1",
        "source_review_date": "2026-07-09",
        "evaluation_trade_date": "2026-07-10",
        "rule_top_code": "sector_rhythm",
        "llm_top_code": "sector_rhythm",
        "system_top_code": "sector_rhythm",
        "human_top_code": "sector_rhythm",
        "system_outcome": "hit",
        "confirmed_outcome": "hit",
        "actual_evidence_json": {"ok": True},
        "evaluation_note": None,
        "input_by": "alyx",
    })
    upsert_evaluation(conn, {
        "evaluation_id": "eval-2",
        "score_run_id": "run-2",
        "source_review_date": "2026-07-10",
        "evaluation_trade_date": "2026-07-13",
        "rule_top_code": "market_node",
        "llm_top_code": "market_node",
        "system_top_code": "market_node",
        "human_top_code": "style_regime",
        "system_outcome": "missing_data",
        "confirmed_outcome": "missing_data",
        "actual_evidence_json": {},
        "evaluation_note": None,
        "input_by": "alyx",
    })
    conn.commit()

    metrics = build_factor_metrics(conn, days=20)

    assert metrics["runs"] == 2
    assert metrics["accept_count"] == 1
    assert metrics["override_count"] == 1
    assert metrics["performance_samples"] == 1
    assert metrics["outcomes"] == {"hit": 1, "partial": 0, "miss": 0}
    assert metrics["data_quality"]["missing_data"] == 1


def test_metrics_prefers_confirmed_parent_over_later_failed_retry(conn) -> None:
    _insert_run(conn, run_id="parent-run", trade_date="2026-07-10")
    Q.upsert_daily_review(conn, "2026-07-10", {
        "step8_plan": {
            "factor_decision": {
                "status": "accepted",
                "score_run_id": "parent-run",
            },
        },
    })
    from services.trinity_factor.repository import upsert_evaluation
    upsert_evaluation(conn, {
        "evaluation_id": "eval-parent",
        "score_run_id": "parent-run",
        "source_review_date": "2026-07-10",
        "evaluation_trade_date": "2026-07-13",
        "rule_top_code": "sector_rhythm",
        "llm_top_code": "sector_rhythm",
        "system_top_code": "sector_rhythm",
        "human_top_code": "sector_rhythm",
        "system_outcome": "hit",
        "confirmed_outcome": "hit",
        "actual_evidence_json": {"ok": True},
        "evaluation_note": None,
        "input_by": "alyx",
    })
    conn.commit()
    _insert_run(
        conn,
        run_id="failed-retry",
        trade_date="2026-07-10",
        status="factor_failed",
        retry_of_run_id="parent-run",
    )

    metrics = build_factor_metrics(conn, days=1)

    assert metrics["runs"] == 1
    assert metrics["success_rate"] == 1.0
    assert metrics["accept_count"] == 1
    assert metrics["performance_samples"] == 1
    assert metrics["outcomes"]["hit"] == 1


def test_metrics_excludes_closed_or_missing_trade_dates(conn) -> None:
    _insert_run(conn, run_id="open-run", trade_date="2026-07-10")
    _insert_run(conn, run_id="closed-run", trade_date="2026-07-11")
    _insert_run(conn, run_id="missing-calendar-run", trade_date="2026-07-15")

    metrics = build_factor_metrics(conn, days=20)

    assert metrics["runs"] == 1
    assert metrics["trade_dates"] == ["2026-07-10"]
