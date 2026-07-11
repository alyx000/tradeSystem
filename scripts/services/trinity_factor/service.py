"""三位一体双层评分编排：规则门、LLM、程序重算、缓存与审计落库。"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import uuid
from collections.abc import Mapping
from typing import Any

from .constants import FACTOR_SCHEMA_VERSION, SECTOR_SCHEMA_VERSION
from .evidence import (
    RULESET_VERSION,
    SERVICE_SCHEMA_VERSION,
    build_evidence_snapshot,
    build_factor_llm_input,
    build_sector_llm_input,
)
from .repository import (
    find_cached_score_run,
    get_score_run,
    insert_score_run,
)
from .runner import (
    FACTOR_PROMPT,
    FACTOR_PROMPT_VERSION,
    SECTOR_PROMPT,
    SECTOR_PROMPT_VERSION,
    AntigravityStructuredRunner,
    StructuredRunResult,
    prompt_template_sha256,
)
from .selection import select_dominant_factors
from .validation import parse_factor_response, parse_sector_response


class TrinityFactorService:
    """API/CLI 共用的评分服务；LLM 调用期间不持有 SQLite 写事务。"""

    def __init__(self, *, runner: AntigravityStructuredRunner | None = None) -> None:
        self.runner = runner or AntigravityStructuredRunner()

    def score(
        self,
        conn: sqlite3.Connection,
        *,
        trade_date: str,
        prefill: Mapping[str, Any] | None,
        review_steps: Mapping[str, Any] | None,
        no_llm: bool = False,
        retry_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        if conn.in_transaction:
            raise RuntimeError(
                "trinity scoring cannot start inside an active write transaction"
            )
        snapshot = build_evidence_snapshot(trade_date, prefill, review_steps)
        input_digest = _json_digest(snapshot)
        requested_model = "disabled" if no_llm else str(os.getenv("LLM_MODEL") or "").strip()
        prompt_versions = {
            "factor": FACTOR_PROMPT_VERSION,
            "sector": SECTOR_PROMPT_VERSION,
        }
        prompt_hashes = {
            "factor": prompt_template_sha256(FACTOR_PROMPT_VERSION, FACTOR_PROMPT),
            "sector": prompt_template_sha256(SECTOR_PROMPT_VERSION, SECTOR_PROMPT),
        }
        cache_key = _json_digest({
            "trade_date": trade_date,
            "input_digest": input_digest,
            "provider": "rules" if no_llm else "antigravity",
            "requested_model": requested_model,
            "prompt_versions": prompt_versions,
            "prompt_hashes": prompt_hashes,
            "schema_version": SERVICE_SCHEMA_VERSION,
            "ruleset_version": RULESET_VERSION,
            "no_llm": no_llm,
        })

        if retry_of_run_id is None:
            cached = find_cached_score_run(conn, cache_key)
            if cached:
                return _public_run(cached, cache_hit=True)
        else:
            parent = get_score_run(conn, retry_of_run_id)
            if not parent:
                raise ValueError("retry_of_run_id does not exist")
            if parent["trade_date"] != trade_date:
                raise ValueError("retry_of_run_id belongs to a different trade_date")
            if parent["cache_key"] != cache_key:
                raise ValueError("retry_of_run_id must reference the same cache input")

        factor_result: StructuredRunResult | None = None
        sector_result: StructuredRunResult | None = None
        factor_scores: list[dict[str, Any]] | None = None
        sector_scores: list[dict[str, Any]] | None = None
        valid_raw: dict[str, Any] = {}
        raw_hashes: dict[str, str] = {}
        diagnostics: dict[str, Any] = {}

        if no_llm:
            status = "rule_only"
            recommendation = _rule_fallback_recommendation(
                snapshot, failure_reason="llm_disabled"
            )
        else:
            factor_specs = snapshot["factor_candidates"]
            factor_result = self.runner.run(
                prompt_version=FACTOR_PROMPT_VERSION,
                prompt=FACTOR_PROMPT,
                input_payload=build_factor_llm_input(snapshot),
                validator=lambda raw: parse_factor_response(raw, factor_specs),
                schema_version=FACTOR_SCHEMA_VERSION,
                ruleset_version=RULESET_VERSION,
            )
            diagnostics["factor"] = _layer_diagnostics(factor_result)
            if factor_result.raw_output_sha256:
                raw_hashes["factor"] = factor_result.raw_output_sha256

            if factor_result.status != "success":
                status = "factor_failed"
                recommendation = _rule_fallback_recommendation(
                    snapshot, failure_reason=factor_result.status
                )
            else:
                factor_scores = list(factor_result.parsed_output or [])
                if factor_result.valid_raw_json is not None:
                    valid_raw["factor"] = dict(factor_result.valid_raw_json)
                selection = select_dominant_factors(
                    factor_scores,
                    primary_category_lock=(
                        snapshot["rule_gate"].get("primary_category_lock")
                    ),
                )
                recommendation = {
                    **selection,
                    "recommendation_source": "llm_program_recompute",
                    "failure_reason": None,
                    "sector_fallback": [],
                    "notice": "LLM 相对重要度评分，非胜率",
                }
                primary = selection.get("primary")
                sector_specs = snapshot["sector_candidates"]
                if primary and sector_specs:
                    sector_result = self.runner.run(
                        prompt_version=SECTOR_PROMPT_VERSION,
                        prompt=SECTOR_PROMPT,
                        input_payload=build_sector_llm_input(
                            snapshot,
                            primary_factor_code=primary["factor_code"],
                        ),
                        validator=lambda raw: parse_sector_response(raw, sector_specs),
                        schema_version=SECTOR_SCHEMA_VERSION,
                        ruleset_version=RULESET_VERSION,
                    )
                    diagnostics["sector"] = _layer_diagnostics(sector_result)
                    if sector_result.raw_output_sha256:
                        raw_hashes["sector"] = sector_result.raw_output_sha256
                    if sector_result.status == "success":
                        status = "success"
                        sector_scores = list(sector_result.parsed_output or [])
                        recommendation["sector_scores"] = sector_scores
                        if sector_result.valid_raw_json is not None:
                            valid_raw["sector"] = dict(sector_result.valid_raw_json)
                    else:
                        status = "sector_failed"
                        recommendation["failure_reason"] = sector_result.status
                        recommendation["sector_fallback"] = list(
                            snapshot["rule_gate"]["deterministic_sector_order"]
                        )
                else:
                    status = "success"
                    sector_scores = [] if primary else None
                    recommendation["sector_scores"] = sector_scores

        run_results = [
            result for result in (factor_result, sector_result) if result is not None
        ]
        provider = (
            run_results[0].provider if run_results else "rules"
        )
        stored_requested_model = (
            run_results[0].requested_model if run_results else requested_model
        )
        record = {
            "score_run_id": f"factor-run-{uuid.uuid4().hex}",
            "trade_date": trade_date,
            "retry_of_run_id": retry_of_run_id,
            "cache_key": cache_key,
            "input_digest": input_digest,
            "is_cacheable": status in {"success", "sector_failed", "rule_only"},
            "provider": provider,
            "requested_model": stored_requested_model,
            "actual_model": next(
                (result.actual_model for result in run_results if result.actual_model),
                None,
            ),
            "cli_version": next(
                (result.cli_version for result in run_results if result.cli_version),
                None,
            ),
            "runtime_version": (
                run_results[0].runtime_version
                if run_results
                else f"python-{platform.python_version()}"
            ),
            "prompt_versions_json": prompt_versions,
            "prompt_sha256_json": prompt_hashes,
            "schema_version": SERVICE_SCHEMA_VERSION,
            "ruleset_version": RULESET_VERSION,
            "evidence_snapshot_json": snapshot,
            "rule_gate_json": snapshot["rule_gate"],
            "factor_scores_json": factor_scores,
            "sector_scores_json": sector_scores,
            "system_recommendation_json": recommendation,
            "valid_raw_json": valid_raw or None,
            "raw_output_sha256_json": raw_hashes or None,
            "diagnostics_json": diagnostics,
            "status": status,
            "attempt_count": sum(result.attempt_count for result in run_results),
            "duration_ms": sum(result.duration_ms for result in run_results),
        }
        insert_score_run(conn, record)
        conn.commit()
        return _public_run(record, cache_hit=False)


def _rule_fallback_recommendation(
    snapshot: Mapping[str, Any],
    *,
    failure_reason: str,
) -> dict[str, Any]:
    fallback_code = (snapshot.get("rule_gate") or {}).get("rule_fallback_code")
    missing_all = all(
        bool(row.get("critical_missing"))
        for row in snapshot.get("factor_candidates") or []
        if isinstance(row, Mapping)
    )
    return {
        "primary": (
            {"factor_code": fallback_code, "judgement_label": "[判断]"}
            if fallback_code
            else None
        ),
        "supporting": [],
        "confidence": "rule_only" if fallback_code else None,
        "undetermined_reason": (
            None
            if fallback_code
            else "undetermined_missing_data" if missing_all else "undetermined_weak"
        ),
        "recommendation_source": "rule_fallback",
        "failure_reason": failure_reason,
        "sector_scores": None,
        "sector_fallback": list(
            (snapshot.get("rule_gate") or {}).get("deterministic_sector_order") or []
        ),
        "notice": "规则降级建议，不展示数字 LLM 分；LLM 相对重要度评分，非胜率",
        "judgement_label": "[判断]",
    }


def _layer_diagnostics(result: StructuredRunResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "attempt_count": result.attempt_count,
        "duration_ms": result.duration_ms,
        "diagnostics": dict(result.diagnostics or {}),
    }


def _public_run(record: Mapping[str, Any], *, cache_hit: bool) -> dict[str, Any]:
    return {
        "score_run_id": record["score_run_id"],
        "trade_date": record["trade_date"],
        "retry_of_run_id": record.get("retry_of_run_id"),
        "status": record["status"],
        "cache_hit": cache_hit,
        "is_cacheable": bool(record["is_cacheable"]),
        "factor_scores": record.get("factor_scores_json"),
        "sector_scores": record.get("sector_scores_json"),
        "system_recommendation": record.get("system_recommendation_json"),
        "rule_gate": record.get("rule_gate_json"),
        "diagnostics": record.get("diagnostics_json"),
        "provider": record.get("provider"),
        "requested_model": record.get("requested_model"),
        "prompt_versions": record.get("prompt_versions_json"),
        "schema_version": record.get("schema_version"),
        "ruleset_version": record.get("ruleset_version"),
        "created_at": record.get("created_at"),
    }


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    import hashlib
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
