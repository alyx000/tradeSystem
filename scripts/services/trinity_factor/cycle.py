"""人工因子确认、严格 T+1 回验与 20 日影子指标。"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from db import queries as Q

from .constants import FACTOR_CODES
from .evidence import build_evidence_snapshot
from .repository import (
    get_score_run,
    list_evaluations,
    list_score_runs,
    upsert_evaluation,
)

_DECISION_STATUSES = frozenset({"accepted", "overridden", "undetermined"})
_OUTCOMES = frozenset({"hit", "partial", "miss", "missing_data", "not_applicable"})
_CANONICAL_SUCCESS_STATUSES = frozenset({"success", "sector_failed", "rule_only"})


def confirm_factor_decision(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    score_run_id: str,
    decision: Mapping[str, Any],
    input_by: str,
    current_input_digest: str,
) -> dict[str, Any]:
    """人工接受、改选或标记看不懂，并镜像旧 key_factor 字段。"""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        confirmed = _prepare_factor_decision(
            conn,
            trade_date=trade_date,
            score_run_id=score_run_id,
            decision=decision,
            input_by=input_by,
            current_input_digest=current_input_digest,
        )
        review = Q.get_daily_review(conn, trade_date) or {}
        step8 = _parse_json_object(review.get("step8_plan"))
        step8 = _apply_confirmed_decision(step8, confirmed)
        Q.upsert_daily_review(conn, trade_date, {"step8_plan": step8})
        if owns_transaction:
            conn.commit()
        return confirmed
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


def normalize_step8_factor_decision(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    step8: Mapping[str, Any],
    current_input_digest: str,
) -> dict[str, Any]:
    """供 PUT /review 共用：校验 factor_decision 并同步 legacy 镜像。"""
    normalized = dict(step8)
    decision = normalized.get("factor_decision")
    if not isinstance(decision, Mapping):
        return normalized
    confirmed = _prepare_factor_decision(
        conn,
        trade_date=trade_date,
        score_run_id=str(decision.get("score_run_id") or ""),
        decision=decision,
        input_by=str(decision.get("input_by") or ""),
        current_input_digest=current_input_digest,
    )
    return _apply_confirmed_decision(normalized, confirmed)


def _prepare_factor_decision(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    score_run_id: str,
    decision: Mapping[str, Any],
    input_by: str,
    current_input_digest: str,
) -> dict[str, Any]:
    if not str(input_by or "").strip():
        raise ValueError("input_by is required")
    _require_open_trade_date(conn, trade_date, "trade_date")
    run = get_score_run(conn, score_run_id)
    if not run:
        raise ValueError("score_run_id does not exist")
    if run["trade_date"] != trade_date:
        raise ValueError("score_run_id belongs to a different trade_date")
    if run.get("input_digest") != current_input_digest:
        raise ValueError("score input has changed; rerun scoring before confirmation")
    if not isinstance(decision, Mapping):
        raise ValueError("decision must be an object")
    status = str(decision.get("status") or "").strip()
    if status not in _DECISION_STATUSES:
        raise ValueError("decision status must be accepted, overridden or undetermined")

    recommendation = run.get("system_recommendation_json") or {}
    system_primary = _factor_code((recommendation.get("primary") or {}).get("factor_code"))
    system_supporting = _supporting_codes(recommendation.get("supporting") or [])
    override_reason = str(decision.get("override_reason") or "").strip()

    if status == "accepted":
        if not system_primary:
            raise ValueError("accepted decision requires a system primary factor")
        primary = system_primary
        supporting = system_supporting
    elif status == "overridden":
        primary = _factor_code(decision.get("primary_factor"), required=True)
        supporting = _validate_supporting(
            decision.get("supporting_factors") or [], primary=primary
        )
        if not override_reason:
            raise ValueError("override_reason is required for overridden decision")
    else:
        primary = None
        supporting = []

    return {
        "score_run_id": score_run_id,
        "status": status,
        "primary_factor": primary,
        "supporting_factors": supporting,
        "override_reason": override_reason or None,
        "confirmed_at": (
            str(decision.get("confirmed_at") or "").strip()
            or datetime.now(timezone.utc).isoformat()
        ),
        "input_by": str(input_by).strip(),
    }


def _apply_confirmed_decision(
    step8: Mapping[str, Any],
    confirmed: Mapping[str, Any],
) -> dict[str, Any]:
    out = dict(step8)
    primary = confirmed.get("primary_factor")
    supporting = list(confirmed.get("supporting_factors") or [])
    out["factor_decision"] = dict(confirmed)
    out["key_factor"] = primary or ""
    out["secondary_factors"] = supporting
    return out


def invalidate_factor_decision(step8: Any) -> tuple[dict[str, Any], bool]:
    """清除旧人工因子决定及其兼容镜像，保留第 8 步其他内容。"""
    out = _parse_json_object(step8)
    if not isinstance(out.get("factor_decision"), Mapping):
        return out, False
    out["factor_decision"] = None
    out["key_factor"] = ""
    out["secondary_factors"] = []
    return out, True


def invalidate_stale_factor_decision(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    step8: Any,
    current_input_digest: str,
) -> tuple[dict[str, Any], bool]:
    """当前证据与已确认 run 不一致时，令人工决定失效。"""
    parsed = _parse_json_object(step8)
    decision = parsed.get("factor_decision")
    if not isinstance(decision, Mapping):
        return parsed, False
    run_id = str(decision.get("score_run_id") or "")
    run = get_score_run(conn, run_id) if run_id else None
    if (
        run
        and run.get("trade_date") == trade_date
        and run.get("input_digest") == current_input_digest
    ):
        return parsed, False
    return invalidate_factor_decision(parsed)


def _require_open_trade_date(
    conn: sqlite3.Connection,
    trade_date: str,
    field: str,
) -> None:
    if Q.is_trade_day_from_db(conn, trade_date) is not True:
        raise ValueError(f"{field} must be an open trade date")


def _snapshot_factor(snapshot: Any, factor_code: str) -> dict[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    matches = [
        dict(row)
        for row in snapshot.get("factor_candidates") or []
        if isinstance(row, Mapping) and row.get("factor_code") == factor_code
    ]
    return matches[0] if len(matches) == 1 else None


def _objective_fact_items(factor: Any) -> list[dict[str, Any]]:
    if not isinstance(factor, Mapping):
        return []
    return [
        dict(item)
        for item in factor.get("evidence_items") or []
        if (
            isinstance(item, Mapping)
            and item.get("kind") == "fact"
            and (
                "source_status" not in item
                or item.get("source_status") == "ok"
            )
        )
    ]


def _fact_content(factor: Any, source: str) -> Any:
    for item in _objective_fact_items(factor):
        if item.get("source") == source:
            return item.get("content")
    return None


def _fact_source_groups(items: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        group
        for item in items
        if (
            group := str(
                item.get("quality_group") or item.get("source") or ""
            ).strip()
        )
    }


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _first_numeric(content: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            return float(content[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _market_direction(factor: Any) -> tuple[int | None, list[int]]:
    content = _fact_content(factor, "daily_market")
    if not isinstance(content, Mapping):
        return None, []
    votes: list[int] = []
    for keys in (
        ("sh_index_change_pct", "sh_index_change"),
        ("sz_index_change_pct", "sz_index_change"),
    ):
        value = _first_numeric(content, *keys)
        if value is not None:
            votes.append(_sign(value))
    for positive_keys, negative_keys in (
        (("advance_count", "up_count"), ("decline_count", "down_count")),
        (("limit_up_count",), ("limit_down_count",)),
    ):
        positive = _first_numeric(content, *positive_keys)
        negative = _first_numeric(content, *negative_keys)
        if positive is None or negative is None:
            continue
        votes.append(_sign(positive - negative))
    if not votes:
        return None, []
    return _sign(sum(votes)), votes


def _style_signature(factor: Any) -> dict[str, str]:
    new_cards = (
        (_fact_content(factor, "cap_relative_strength"), "relative", "cap_preference"),
        (_fact_content(factor, "board_preference"), "dominant_type", "board_preference"),
        (_fact_content(factor, "premium_regime"), "trend_direction", "premium_trend"),
    )
    if any(isinstance(content, Mapping) for content, _, _ in new_cards):
        return {
            dimension: value
            for content, key, dimension in new_cards
            if (value := _mapping_string(content, key)) is not None
        }

    legacy = _fact_content(factor, "style_factors")
    if not isinstance(legacy, Mapping):
        return {}
    legacy_cards = (
        (legacy.get("cap_preference"), "relative", "cap_preference"),
        (legacy.get("board_preference"), "dominant_type", "board_preference"),
        (legacy.get("premium_trend"), "direction", "premium_trend"),
    )
    return {
        dimension: value
        for content, key, dimension in legacy_cards
        if (value := _mapping_string(content, key)) is not None
    }


def _nonempty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _mapping_string(value: Any, key: str) -> str | None:
    return _nonempty_string(value.get(key)) if isinstance(value, Mapping) else None


def _finite_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _tier_number(value: Any, *, allow_text: bool = False) -> int | None:
    tier = _finite_integer(value)
    if tier is None and allow_text and isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("板"):
            normalized = normalized[:-1].strip()
        if normalized and all("0" <= char <= "9" for char in normalized):
            tier = int(normalized)
    return tier if tier is not None and 1 <= tier <= 30 else None


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = value
    else:
        return set()
    return {
        normalized
        for item in values
        if (normalized := _nonempty_string(item)) is not None
    }


def _leader_structure_signature(factor: Any) -> tuple[int, set[str]] | None:
    current = _fact_content(factor, "ladder_structure")
    if isinstance(current, Mapping):
        highest_board = _tier_number(current.get("highest_board"))
        if highest_board is not None:
            return highest_board, _string_set(current.get("top_tier_names"))

    legacy = _fact_content(factor, "limit_ladder")
    if isinstance(legacy, Mapping):
        highest_board = _tier_number(legacy.get("highest_board"))
        if highest_board is not None:
            return highest_board, _string_set(legacy.get("top_tier_names"))
        nested_rows = next(
            (
                legacy.get(key)
                for key in ("ladder_rows", "rows")
                if isinstance(legacy.get(key), Sequence)
                and not isinstance(legacy.get(key), (str, bytes))
            ),
            None,
        )
        if nested_rows is not None:
            rows: Sequence[Any] = nested_rows
        elif "nums" in legacy or "tier" in legacy:
            rows = (legacy,)
        else:
            rows = tuple(
                {"nums": tier, "names": names} for tier, names in legacy.items()
            )
    elif isinstance(legacy, Sequence) and not isinstance(legacy, (str, bytes)):
        rows = legacy
    else:
        return None

    names_by_tier: dict[int, set[str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        tier = _tier_number(row.get("nums"), allow_text=True) or _tier_number(
            row.get("tier"), allow_text=True
        )
        if tier is None:
            continue
        names = (
            _string_set(row.get("name"))
            | _string_set(row.get("names"))
            | _string_set(row.get("top_tier_names"))
        )
        names_by_tier.setdefault(tier, set()).update(names)
    if not names_by_tier:
        return None
    highest_board = max(names_by_tier)
    return highest_board, names_by_tier[highest_board]


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _leader_feedback_result(factor: Any) -> bool | None:
    content = _fact_content(factor, "prior_core_feedback")
    if not isinstance(content, Mapping):
        return None
    limit_up_count = _finite_integer(content.get("limit_up_count"))
    if limit_up_count is not None and limit_up_count < 0:
        limit_up_count = None
    median_close_change = _finite_number(content.get("median_close_change_pct"))
    if limit_up_count is None and median_close_change is None:
        return None
    return bool(
        (limit_up_count is not None and limit_up_count > 0)
        or (
            median_close_change is not None
            and median_close_change >= 0
        )
    )


def _sector_keys(snapshot: Any) -> set[str]:
    if not isinstance(snapshot, Mapping):
        return set()
    return {
        str(row["sector_key"])
        for row in snapshot.get("sector_candidates") or []
        if isinstance(row, Mapping) and row.get("sector_key")
    }


def _sector_source_status(snapshot: Any) -> str:
    if not isinstance(snapshot, Mapping):
        return "missing"
    rule_gate = snapshot.get("rule_gate")
    status = (
        rule_gate.get("sector_source_status")
        if isinstance(rule_gate, Mapping)
        else None
    )
    if status:
        return str(status)
    return "ok" if _sector_keys(snapshot) else "missing"


def _compare_sets(
    source_values: set[str],
    actual_values: set[str],
) -> tuple[str, int]:
    overlap = len(source_values & actual_values)
    if overlap == len(source_values) and source_values:
        return "hit", overlap
    if overlap:
        return "partial", overlap
    return "miss", 0


def _compare_t1_factor(
    factor_code: str,
    *,
    source_snapshot: Any,
    source_factor: Any,
    actual_snapshot: Any,
    actual_factor: Any,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(source_factor, Mapping):
        return "missing_data", {"reason": "source_factor_missing"}

    if factor_code == "market_node":
        source_direction, source_votes = _market_direction(source_factor)
        actual_direction, actual_votes = _market_direction(actual_factor)
        comparison = {
            "comparator": "market_direction",
            "source_direction": source_direction,
            "actual_direction": actual_direction,
            "source_votes": source_votes,
            "actual_votes": actual_votes,
        }
        if source_direction is None or actual_direction is None:
            return "missing_data", comparison
        if source_direction == actual_direction:
            return "hit", comparison
        if source_direction == 0 or actual_direction == 0:
            return "partial", comparison
        return "miss", comparison

    if factor_code == "style_regime":
        source_signature = _style_signature(source_factor)
        actual_signature = _style_signature(actual_factor)
        common = sorted(set(source_signature) & set(actual_signature))
        matched = sum(
            source_signature[key] == actual_signature[key] for key in common
        )
        comparison = {
            "comparator": "style_dimensions",
            "source_signature": source_signature,
            "actual_signature": actual_signature,
            "comparable_dimensions": len(common),
            "matched_dimensions": matched,
        }
        if not common:
            return "missing_data", comparison
        if matched == len(common):
            return "hit", comparison
        if matched:
            return "partial", comparison
        return "miss", comparison

    if factor_code == "sector_rhythm":
        source_keys = _sector_keys(source_snapshot)
        actual_keys = _sector_keys(actual_snapshot)
        source_source_status = _sector_source_status(source_snapshot)
        actual_source_status = _sector_source_status(actual_snapshot)
        comparison = {
            "comparator": "core_sector_continuity",
            "source_sector_keys": sorted(source_keys),
            "actual_sector_keys": sorted(actual_keys),
            "source_source_status": source_source_status,
            "actual_source_status": actual_source_status,
            "observed_empty": actual_source_status in {
                "ok",
                "source_ok_empty",
                "rule_filtered_empty",
            } and not actual_keys,
        }
        if (
            source_source_status in {"missing", "source_failed"}
            or actual_source_status in {"missing", "source_failed"}
        ):
            return "missing_data", comparison
        if not source_keys:
            return "missing_data", comparison
        if not actual_keys and not comparison["observed_empty"]:
            return "missing_data", comparison
        outcome, overlap = _compare_sets(source_keys, actual_keys)
        comparison["overlap_count"] = overlap
        return outcome, comparison

    if factor_code == "leader_signal":
        source_structure = _leader_structure_signature(source_factor)
        actual_structure = _leader_structure_signature(actual_factor)
        source_highest = source_structure[0] if source_structure is not None else None
        actual_highest = actual_structure[0] if actual_structure is not None else None
        source_names = source_structure[1] if source_structure is not None else set()
        actual_names = actual_structure[1] if actual_structure is not None else set()
        identity_overlap = source_names & actual_names
        dimension_results: dict[str, bool | None] = {
            "height": (
                actual_highest >= source_highest
                if source_highest is not None and actual_highest is not None
                else None
            ),
            "identity": (
                bool(identity_overlap)
                if source_names and actual_structure is not None
                else None
            ),
            "feedback": _leader_feedback_result(actual_factor),
        }
        comparable_dimensions = sum(
            result is not None for result in dimension_results.values()
        )
        positive_dimensions = sum(
            result is True for result in dimension_results.values()
        )
        comparison = {
            "comparator": "leader_structure",
            "source_highest_board": source_highest,
            "actual_highest_board": actual_highest,
            "source_top_names": sorted(source_names),
            "actual_top_names": sorted(actual_names),
            "identity_overlap": sorted(identity_overlap),
            "dimension_results": dimension_results,
            "comparable_dimensions": comparable_dimensions,
            "positive_dimensions": positive_dimensions,
        }
        if source_structure is None or actual_structure is None:
            return "missing_data", comparison
        if not comparable_dimensions:
            return "missing_data", comparison
        if positive_dimensions >= 2:
            return "hit", comparison
        if positive_dimensions == 1:
            return "partial", comparison
        return "miss", comparison

    return "missing_data", {"reason": "unknown_factor"}


def suggest_t1_evaluation(
    conn: sqlite3.Connection,
    *,
    evaluation_trade_date: str,
    prefill: Mapping[str, Any] | None,
    source_review_date: str | None = None,
    score_run_id: str | None = None,
) -> dict[str, Any]:
    """按严格上一/下一交易日生成程序事实建议；不调用 LLM、不写库。"""
    source_date = source_review_date or Q.get_prev_trade_date_from_db(
        conn, evaluation_trade_date
    )
    if not source_date:
        raise ValueError("strict previous trade date is unavailable")
    _require_open_trade_date(conn, source_date, "source_review_date")
    _require_open_trade_date(conn, evaluation_trade_date, "evaluation_trade_date")
    strict_next = Q.get_next_trade_date(conn, source_date)
    if strict_next != evaluation_trade_date:
        raise ValueError("evaluation_trade_date is not the strict next trade date")

    run = _resolve_source_run(conn, source_date, score_run_id)
    recommendation = run.get("system_recommendation_json") or {}
    primary_code = _factor_code((recommendation.get("primary") or {}).get("factor_code"))
    target_review = Q.get_daily_review(conn, evaluation_trade_date)
    source_review = Q.get_daily_review(conn, source_date)
    human_decision = _factor_decision_from_review(source_review)

    actual_evidence: dict[str, Any] = {
        "primary_factor": primary_code,
        "objective_source_count": 0,
        "counter_source_count": 0,
        "support_evidence_ids": [],
        "counter_evidence_ids": [],
    }
    if not target_review:
        system_outcome = "not_applicable"
    elif not primary_code:
        system_outcome = "missing_data"
    else:
        source_snapshot = run.get("evidence_snapshot_json") or {}
        source_factor = _snapshot_factor(source_snapshot, primary_code)
        actual_snapshot = build_evidence_snapshot(
            evaluation_trade_date,
            prefill,
            {},  # T+1 系统判卷只看客观预填，不消费当日人工判断。
        )
        actual_factor = next(
            (
                row
                for row in actual_snapshot["factor_candidates"]
                if row["factor_code"] == primary_code
            ),
            None,
        )
        fact_items = _objective_fact_items(actual_factor)
        support_items = [
            item for item in fact_items if item.get("polarity") != "counter"
        ]
        counter_items = [
            item for item in fact_items if item.get("polarity") == "counter"
        ]
        support_sources = _fact_source_groups(support_items)
        counter_sources = _fact_source_groups(counter_items)
        system_outcome, comparison = _compare_t1_factor(
            primary_code,
            source_snapshot=source_snapshot,
            source_factor=source_factor,
            actual_snapshot=actual_snapshot,
            actual_factor=actual_factor,
        )
        actual_evidence = {
            "primary_factor": primary_code,
            "evidence_quality": (
                actual_factor.get("evidence_quality") if actual_factor else None
            ),
            "objective_source_count": len(support_sources),
            "counter_source_count": len(counter_sources),
            "support_evidence_ids": [item["evidence_id"] for item in support_items],
            "counter_evidence_ids": [item["evidence_id"] for item in counter_items],
            "core_sector_keys": [
                row["sector_key"] for row in actual_snapshot["sector_candidates"]
            ],
            "comparison": comparison,
        }

    evaluation_id = _evaluation_id(source_date, evaluation_trade_date, run["score_run_id"])
    return {
        "evaluation_id": evaluation_id,
        "score_run_id": run["score_run_id"],
        "source_review_date": source_date,
        "evaluation_trade_date": evaluation_trade_date,
        "rule_top_code": (run.get("rule_gate_json") or {}).get("rule_fallback_code"),
        "llm_top_code": _llm_top_code(run.get("factor_scores_json")),
        "system_top_code": primary_code,
        "human_top_code": _factor_code(
            human_decision.get("primary_factor") if human_decision else None
        ),
        "system_outcome": system_outcome,
        "confirmed_outcome": None,
        "actual_evidence_json": actual_evidence,
        "evaluation_note": None,
    }


def confirm_t1_evaluation(
    conn: sqlite3.Connection,
    *,
    suggestion: Mapping[str, Any],
    confirmed_outcome: str,
    input_by: str,
    evaluation_note: str | None = None,
) -> dict[str, Any]:
    if confirmed_outcome not in _OUTCOMES:
        raise ValueError("confirmed_outcome is invalid")
    if not str(input_by or "").strip():
        raise ValueError("input_by is required")
    required = (
        "evaluation_id", "score_run_id", "source_review_date",
        "evaluation_trade_date", "actual_evidence_json", "system_outcome",
    )
    if not isinstance(suggestion, Mapping) or any(key not in suggestion for key in required):
        raise ValueError("evaluation suggestion is incomplete")
    source_date = str(suggestion["source_review_date"])
    evaluation_date = str(suggestion["evaluation_trade_date"])
    _require_open_trade_date(conn, source_date, "source_review_date")
    _require_open_trade_date(conn, evaluation_date, "evaluation_trade_date")
    strict_next = Q.get_next_trade_date(conn, source_date)
    if strict_next != suggestion["evaluation_trade_date"]:
        raise ValueError("evaluation is not for the strict next trade date")

    record = {
        "evaluation_id": suggestion["evaluation_id"],
        "score_run_id": suggestion["score_run_id"],
        "source_review_date": suggestion["source_review_date"],
        "evaluation_trade_date": suggestion["evaluation_trade_date"],
        "rule_top_code": suggestion.get("rule_top_code"),
        "llm_top_code": suggestion.get("llm_top_code"),
        "system_top_code": suggestion.get("system_top_code"),
        "human_top_code": suggestion.get("human_top_code"),
        "system_outcome": suggestion["system_outcome"],
        "confirmed_outcome": confirmed_outcome,
        "actual_evidence_json": suggestion["actual_evidence_json"],
        "evaluation_note": str(evaluation_note or "").strip() or None,
        "input_by": str(input_by).strip(),
    }
    evaluation_id = upsert_evaluation(conn, record)
    conn.commit()
    return {**record, "evaluation_id": evaluation_id}


def build_factor_metrics(conn: sqlite3.Connection, *, days: int = 20) -> dict[str, Any]:
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError("days must be a positive integer")
    all_runs = list_score_runs(conn, limit=max(100, days * 20))
    runs_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    date_order: list[str] = []
    for run in all_runs:
        trade_date = str(run["trade_date"])
        if Q.is_trade_day_from_db(conn, trade_date) is not True:
            continue
        if trade_date not in runs_by_date:
            if len(date_order) >= days:
                break
            date_order.append(trade_date)
        runs_by_date[trade_date].append(run)

    selected: list[dict[str, Any]] = []
    for trade_date in date_order[:days]:
        rows = runs_by_date[trade_date]
        review = Q.get_daily_review(conn, trade_date)
        decision = _factor_decision_from_review(review)
        decision_run = None
        if decision and decision.get("score_run_id"):
            decision_run_id = str(decision["score_run_id"])
            decision_run = next(
                (row for row in rows if row["score_run_id"] == decision_run_id),
                None,
            ) or get_score_run(conn, decision_run_id)
            if decision_run and decision_run.get("trade_date") != trade_date:
                decision_run = None
        selected_run = _canonical_score_run(rows, decision_run=decision_run)
        if selected_run is not None:
            selected.append(selected_run)

    evaluations = list_evaluations(conn, limit=max(100, days * 10))
    evaluation_by_run = {row["score_run_id"]: row for row in evaluations}
    outcomes = Counter({"hit": 0, "partial": 0, "miss": 0})
    data_quality = Counter({"missing_data": 0, "not_applicable": 0})
    accept_count = 0
    override_count = 0
    undetermined_decision_count = 0
    grouped: dict[str, dict[str, dict[str, int]]] = {
        "model": defaultdict(lambda: _group_bucket()),
        "prompt": defaultdict(lambda: _group_bucket()),
        "factor": defaultdict(lambda: _group_bucket()),
        "score_bucket": defaultdict(lambda: _group_bucket()),
    }

    success_count = 0
    invalid_output_count = 0
    fallback_count = 0
    coverage_count = 0
    undetermined_count = 0
    for run in selected:
        if run["status"] == "success":
            success_count += 1
        diagnostics_text = json.dumps(run.get("diagnostics_json") or {}, ensure_ascii=False)
        if "schema_invalid" in diagnostics_text:
            invalid_output_count += 1
        recommendation = run.get("system_recommendation_json") or {}
        primary = (recommendation.get("primary") or {}).get("factor_code")
        if primary:
            coverage_count += 1
        else:
            undetermined_count += 1
        if recommendation.get("recommendation_source") == "rule_fallback" or run["status"] == "rule_only":
            fallback_count += 1

        review = Q.get_daily_review(conn, run["trade_date"])
        decision = _factor_decision_from_review(review)
        if decision and decision.get("score_run_id") == run["score_run_id"]:
            if decision.get("status") == "accepted":
                accept_count += 1
            elif decision.get("status") == "overridden":
                override_count += 1
            elif decision.get("status") == "undetermined":
                undetermined_decision_count += 1

        evaluation = evaluation_by_run.get(run["score_run_id"])
        outcome = None
        if evaluation:
            outcome = evaluation.get("confirmed_outcome") or evaluation.get("system_outcome")
            if outcome in outcomes:
                outcomes[outcome] += 1
            elif outcome in data_quality:
                data_quality[outcome] += 1

        score = _primary_score(run.get("factor_scores_json"), primary)
        group_values = {
            "model": str(run.get("requested_model") or "unknown"),
            "prompt": json.dumps(
                run.get("prompt_versions_json") or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "factor": str(primary or "undetermined"),
            "score_bucket": _score_bucket(score),
        }
        for group_name, group_value in group_values.items():
            bucket = grouped[group_name][group_value]
            bucket["runs"] += 1
            if outcome in outcomes:
                bucket[outcome] += 1

    performance_samples = sum(outcomes.values())
    runs = len(selected)
    decisions = accept_count + override_count + undetermined_decision_count
    return {
        "days": days,
        "trade_dates": [run["trade_date"] for run in selected],
        "runs": runs,
        "success_rate": _rate(success_count, runs),
        "invalid_output_rate": _rate(invalid_output_count, runs),
        "rule_fallback_rate": _rate(fallback_count, runs),
        "recommendation_coverage_rate": _rate(coverage_count, runs),
        "undetermined_rate": _rate(undetermined_count, runs),
        "accept_count": accept_count,
        "override_count": override_count,
        "undetermined_decision_count": undetermined_decision_count,
        "accept_rate": _rate(accept_count, decisions),
        "override_rate": _rate(override_count, decisions),
        "performance_samples": performance_samples,
        "outcomes": dict(outcomes),
        "data_quality": dict(data_quality),
        "groups": {
            name: {key: dict(value) for key, value in values.items()}
            for name, values in grouped.items()
        },
    }


def _resolve_source_run(
    conn: sqlite3.Connection,
    source_date: str,
    score_run_id: str | None,
) -> dict[str, Any]:
    if score_run_id:
        run = get_score_run(conn, score_run_id)
        if not run or run["trade_date"] != source_date:
            raise ValueError("score_run_id does not belong to source_review_date")
        return run
    review = Q.get_daily_review(conn, source_date)
    decision = _factor_decision_from_review(review)
    decision_run = None
    if decision and decision.get("score_run_id"):
        candidate = get_score_run(conn, str(decision["score_run_id"]))
        if candidate and candidate["trade_date"] == source_date:
            decision_run = candidate
    runs = list_score_runs(conn, trade_date=source_date, limit=50)
    run = _canonical_score_run(runs, decision_run=decision_run)
    if run is not None:
        return run
    raise ValueError("no score run exists for strict source review date")


def _canonical_score_run(
    runs: list[dict[str, Any]],
    *,
    decision_run: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """从最新优先的 runs 中选人工确认、成功类或失败降级的 canonical run。"""
    if decision_run is not None:
        return dict(decision_run)
    successful = next(
        (
            run
            for run in runs
            if run.get("status") in _CANONICAL_SUCCESS_STATUSES
            and run.get("is_cacheable")
        ),
        None,
    )
    if successful is not None:
        return successful
    return next(
        (run for run in runs if run.get("system_recommendation_json")),
        None,
    )


def _factor_code(value: Any, *, required: bool = False) -> str | None:
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or value not in FACTOR_CODES:
        raise ValueError(f"unknown factor code: {value}")
    return value


def _supporting_codes(items: Any) -> list[str]:
    values = []
    for item in items if isinstance(items, list) else []:
        value = item.get("factor_code") if isinstance(item, Mapping) else item
        code = _factor_code(value)
        if code and code not in values:
            values.append(code)
    return values[:2]


def _validate_supporting(items: Any, *, primary: str) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("supporting_factors must be a list")
    if len(items) > 2:
        raise ValueError("supporting_factors can contain at most two factors")
    codes = [_factor_code(item, required=True) for item in items]
    if len(codes) != len(set(codes)) or primary in codes:
        raise ValueError("supporting_factors must be unique and exclude primary")
    return [str(code) for code in codes]


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"notes": value}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _factor_decision_from_review(review: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not review:
        return None
    step8 = _parse_json_object(review.get("step8_plan"))
    decision = step8.get("factor_decision")
    return dict(decision) if isinstance(decision, Mapping) else None


def _llm_top_code(scores: Any) -> str | None:
    if not isinstance(scores, list) or not scores:
        return None
    rows = [row for row in scores if isinstance(row, Mapping)]
    if not rows:
        return None
    top = sorted(
        rows,
        key=lambda row: (-float(row.get("total_score") or 0), str(row.get("factor_code") or "")),
    )[0]
    return _factor_code(top.get("factor_code"))


def _evaluation_id(source_date: str, evaluation_date: str, run_id: str) -> str:
    digest = hashlib.sha256(
        f"{source_date}|{evaluation_date}|{run_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"factor-eval-{digest}"


def _group_bucket() -> dict[str, int]:
    return {"runs": 0, "hit": 0, "partial": 0, "miss": 0}


def _primary_score(scores: Any, primary_code: str | None) -> float | None:
    if not primary_code or not isinstance(scores, list):
        return None
    row = next(
        (
            item for item in scores
            if isinstance(item, Mapping) and item.get("factor_code") == primary_code
        ),
        None,
    )
    try:
        return float(row.get("total_score")) if row else None
    except (TypeError, ValueError):
        return None


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "undetermined"
    if score >= 82:
        return "82-100"
    if score >= 70:
        return "70-81"
    if score >= 60:
        return "60-69"
    return "0-59"


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
