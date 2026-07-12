"""LLM JSON 的严格解析与校验入口。"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from numbers import Real
import unicodedata

from services.recommend.formatter import REDLINE_KEYWORDS

from .constants import (
    FACTOR_CODES,
    FACTOR_SCHEMA_VERSION,
    FACTOR_WEIGHTS,
    MAX_REASON_LENGTH,
    SECTOR_CANDIDATE_MAX,
    SECTOR_SCHEMA_VERSION,
    SECTOR_WEIGHTS,
)
from .scoring import score_factor, score_sector


class TrinityValidationError(ValueError):
    """LLM 评分响应未通过 schema 或业务白名单校验。"""


def _reject_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TrinityValidationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_json(raw: str | Mapping) -> Mapping:
    try:
        payload = (
            json.loads(raw, object_pairs_hook=_reject_duplicate_object_pairs)
            if isinstance(raw, str)
            else raw
        )
    except TrinityValidationError:
        raise
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise TrinityValidationError("response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TrinityValidationError("response must be a JSON object")
    return payload


_FACTOR_TOP_FIELDS = frozenset({"schema_version", "factors"})
_FACTOR_ITEM_FIELDS = frozenset({
    "factor_code",
    "dimension_scores",
    "evidence_refs",
    "counter_evidence_refs",
    "t1_check_ids",
    "reason",
})
_SECTOR_TOP_FIELDS = frozenset({"schema_version", "sectors"})
_SECTOR_ITEM_FIELDS = frozenset({
    "sector_key",
    "dimension_scores",
    "evidence_refs",
    "counter_evidence_refs",
    "t1_check_ids",
    "reason",
})
_REFERENCE_FIELDS = (
    ("evidence_refs", "allowed_evidence_ids"),
    ("counter_evidence_refs", "allowed_counter_evidence_ids"),
    ("t1_check_ids", "allowed_t1_check_ids"),
)


def _expect_exact_fields(value: object, expected: frozenset[str], context: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise TrinityValidationError(f"{context} must be a JSON object")
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unknown = sorted(actual - set(expected), key=str)
        raise TrinityValidationError(
            f"{context} fields mismatch; missing={missing}, unknown={unknown}"
        )
    return value


def _as_id_set(value: object, context: str) -> frozenset[str]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise TrinityValidationError(f"{context} must be a collection of IDs")
    ids = list(value)
    if any(not isinstance(item, str) or not item for item in ids):
        raise TrinityValidationError(f"{context} must contain non-empty string IDs")
    if len(ids) != len(set(ids)):
        raise TrinityValidationError(f"{context} contains duplicate IDs")
    return frozenset(ids)


def _validate_caps(caps: object, dimensions: Mapping[str, int], context: str) -> dict:
    if caps is None:
        return {}
    if not isinstance(caps, Mapping):
        raise TrinityValidationError(f"{context} must be an object")
    if not set(caps).issubset(dimensions):
        raise TrinityValidationError(f"{context} contains an unknown dimension")
    for value in caps.values():
        if isinstance(value, bool) or not isinstance(value, Real) or not 0 <= value <= 5:
            raise TrinityValidationError(f"{context} values must be numbers from 0 to 5")
    return dict(caps)


def _prepare_factor_specs(candidates: Sequence[Mapping]) -> dict[str, dict]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TrinityValidationError("factor candidates must be a sequence")
    specs: dict[str, dict] = {}
    for index, raw_spec in enumerate(candidates):
        if not isinstance(raw_spec, Mapping):
            raise TrinityValidationError(f"factor candidate {index} must be an object")
        factor_code = raw_spec.get("factor_code")
        if not isinstance(factor_code, str) or not factor_code or factor_code not in FACTOR_CODES:
            raise TrinityValidationError(f"unknown factor candidate: {factor_code}")
        if factor_code in specs:
            raise TrinityValidationError(f"duplicate factor candidate: {factor_code}")

        evidence_quality = raw_spec.get("evidence_quality")
        if (
            isinstance(evidence_quality, bool)
            or not isinstance(evidence_quality, Real)
            or not 0 <= evidence_quality <= 5
        ):
            raise TrinityValidationError("evidence_quality must be a number from 0 to 5")
        critical_missing = raw_spec.get("critical_missing", False)
        if not isinstance(critical_missing, bool):
            raise TrinityValidationError("critical_missing must be a bool")

        spec = dict(raw_spec)
        spec["caps"] = _validate_caps(
            raw_spec.get("caps", {}), FACTOR_WEIGHTS, f"caps for {factor_code}"
        )
        spec["critical_missing"] = critical_missing
        for _, allowed_field in _REFERENCE_FIELDS:
            if allowed_field not in raw_spec:
                raise TrinityValidationError(f"missing {allowed_field} for {factor_code}")
            spec[allowed_field] = _as_id_set(
                raw_spec[allowed_field], f"{allowed_field} for {factor_code}"
            )
        specs[factor_code] = spec
    return specs


def _prepare_sector_specs(candidates: Sequence[Mapping]) -> dict[str, dict]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TrinityValidationError("sector candidates must be a sequence")
    if len(candidates) > SECTOR_CANDIDATE_MAX:
        raise TrinityValidationError(
            f"sector candidates exceed the limit of {SECTOR_CANDIDATE_MAX}"
        )

    specs: dict[str, dict] = {}
    for index, raw_spec in enumerate(candidates):
        if not isinstance(raw_spec, Mapping):
            raise TrinityValidationError(f"sector candidate {index} must be an object")
        sector_key = raw_spec.get("sector_key")
        if not isinstance(sector_key, str) or not sector_key:
            raise TrinityValidationError(f"sector candidate {index} has an invalid sector_key")
        if sector_key in specs:
            raise TrinityValidationError(f"duplicate sector candidate: {sector_key}")
        if raw_spec.get("candidate_tier") != "core":
            raise TrinityValidationError(
                f"sector candidate {sector_key} must have candidate_tier=core"
            )

        spec = dict(raw_spec)
        spec["caps"] = _validate_caps(
            raw_spec.get("caps", {}), SECTOR_WEIGHTS, f"caps for {sector_key}"
        )
        for _, allowed_field in _REFERENCE_FIELDS:
            if allowed_field not in raw_spec:
                raise TrinityValidationError(f"missing {allowed_field} for {sector_key}")
            spec[allowed_field] = _as_id_set(
                raw_spec[allowed_field], f"{allowed_field} for {sector_key}"
            )
        specs[sector_key] = spec
    return specs


def _validate_response_ids(
    rows: list,
    *,
    id_field: str,
    expected_ids: set[str],
    context: str,
) -> None:
    actual_ids = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TrinityValidationError(f"{context} item {index} must be an object")
        candidate_id = row.get(id_field)
        if not isinstance(candidate_id, str) or not candidate_id:
            raise TrinityValidationError(f"{context} item {index} has an invalid {id_field}")
        actual_ids.append(candidate_id)
    if len(actual_ids) != len(set(actual_ids)):
        raise TrinityValidationError(f"{context} contains duplicate IDs")
    if set(actual_ids) != expected_ids:
        raise TrinityValidationError(
            f"{context} IDs must exactly match candidates; "
            f"expected={sorted(expected_ids)}, actual={sorted(actual_ids)}"
        )


def _validate_references(row: Mapping, spec: Mapping, context: str) -> dict[str, list[str]]:
    validated: dict[str, list[str]] = {}
    for response_field, allowed_field in _REFERENCE_FIELDS:
        refs = row[response_field]
        if not isinstance(refs, list):
            raise TrinityValidationError(f"{context}.{response_field} must be a list")
        ref_set = _as_id_set(refs, f"{context}.{response_field}")
        unknown = ref_set - spec[allowed_field]
        if unknown:
            raise TrinityValidationError(
                f"{context}.{response_field} contains unknown IDs: {sorted(unknown)}"
            )
        validated[response_field] = sorted(ref_set)
    return validated


def _validate_reference_contract(
    references: Mapping[str, list[str]],
    *,
    positive_evidence_available: bool,
    positive_score: int,
    counter_score: int,
    context: str,
) -> None:
    if positive_evidence_available and not references["evidence_refs"]:
        raise TrinityValidationError(f"{context}.evidence_refs requires at least one ID")
    if not positive_evidence_available and positive_score > 0:
        raise TrinityValidationError(
            f"{context} positive dimensions must score zero without positive evidence"
        )
    if not references["t1_check_ids"]:
        raise TrinityValidationError(f"{context}.t1_check_ids requires at least one ID")
    counter_refs = references["counter_evidence_refs"]
    if counter_score > 0 and not counter_refs:
        raise TrinityValidationError(
            f"{context}.counter_evidence_refs is required when counter score is positive"
        )
    if counter_refs and counter_score == 0:
        raise TrinityValidationError(
            f"{context} counter score must be positive when counter evidence is cited"
        )


def _validate_reason(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise TrinityValidationError(f"{context} must start with [判断]")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        char for char in normalized
        if unicodedata.category(char) not in {"Cf", "Cc"}
    )
    if not normalized.startswith("[判断]"):
        raise TrinityValidationError(f"{context} must start with [判断]")
    if len(normalized) > MAX_REASON_LENGTH:
        raise TrinityValidationError(f"{context} exceeds {MAX_REASON_LENGTH} characters")
    compact = "".join(
        char for char in normalized
        if not char.isspace()
        and unicodedata.category(char) not in {"Cf", "Cc"}
        and not unicodedata.category(char).startswith(("M", "P", "S"))
    )
    for keyword in REDLINE_KEYWORDS:
        normalized_keyword = unicodedata.normalize("NFKC", keyword)
        compact_keyword = "".join(
            char for char in normalized_keyword
            if unicodedata.category(char) not in {"Cf", "Cc"}
            and not char.isspace()
            and not unicodedata.category(char).startswith(("M", "P", "S"))
        )
        if compact_keyword in compact:
            raise TrinityValidationError(f"{context} contains redline keyword: {keyword}")
    return normalized


def parse_factor_response(raw: str | Mapping, candidates: Sequence[Mapping]) -> list[dict]:
    """解析因子层 JSON，注入程序输入并重算总分。"""
    payload = _load_json(raw)
    _expect_exact_fields(payload, _FACTOR_TOP_FIELDS, "factor response")
    if payload.get("schema_version") != FACTOR_SCHEMA_VERSION:
        raise TrinityValidationError("unexpected factor schema_version")
    rows = payload.get("factors")
    if not isinstance(rows, list):
        raise TrinityValidationError("factors must be a list")

    specs = _prepare_factor_specs(candidates)
    _validate_response_ids(
        rows,
        id_field="factor_code",
        expected_ids=set(specs),
        context="factors",
    )
    results = []
    try:
        for row in sorted(rows, key=lambda item: item["factor_code"]):
            _expect_exact_fields(row, _FACTOR_ITEM_FIELDS, "factor item")
            spec = specs[row["factor_code"]]
            scored = score_factor(
                factor_code=row["factor_code"],
                dimension_scores=row["dimension_scores"],
                evidence_quality=spec["evidence_quality"],
                caps=spec.get("caps"),
                critical_missing=spec.get("critical_missing", False),
            )
            context = f"factor {row['factor_code']}"
            references = _validate_references(row, spec, context)
            _validate_reference_contract(
                references,
                positive_evidence_available=bool(spec["allowed_evidence_ids"]),
                positive_score=sum(
                    scored["model_scores"][key]
                    for key in (
                        "current_dominance", "cross_layer_alignment",
                        "rhythm_clarity", "next_stage_relevance",
                    )
                ),
                counter_score=scored["model_scores"]["counterevidence"],
                context=context,
            )
            scored.update(references)
            scored["reason"] = _validate_reason(
                row["reason"], f"factor {row['factor_code']}.reason"
            )
            results.append(scored)
    except TrinityValidationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TrinityValidationError("invalid factor response") from exc
    return results


def parse_sector_response(raw: str | Mapping, candidates: Sequence[Mapping]) -> list[dict]:
    """解析板块层 JSON，注入程序 caps 并重算总分。"""
    payload = _load_json(raw)
    _expect_exact_fields(payload, _SECTOR_TOP_FIELDS, "sector response")
    if payload.get("schema_version") != SECTOR_SCHEMA_VERSION:
        raise TrinityValidationError("unexpected sector schema_version")
    rows = payload.get("sectors")
    if not isinstance(rows, list):
        raise TrinityValidationError("sectors must be a list")
    specs = _prepare_sector_specs(candidates)
    _validate_response_ids(
        rows,
        id_field="sector_key",
        expected_ids=set(specs),
        context="sectors",
    )
    results = []
    try:
        for row in rows:
            _expect_exact_fields(row, _SECTOR_ITEM_FIELDS, "sector item")
            spec = specs[row["sector_key"]]
            scored = score_sector(
                sector_key=row["sector_key"],
                dimension_scores=row["dimension_scores"],
                caps=spec.get("caps"),
            )
            scored["candidate_tier"] = spec["candidate_tier"]
            context = f"sector {row['sector_key']}"
            references = _validate_references(row, spec, context)
            _validate_reference_contract(
                references,
                positive_evidence_available=bool(spec["allowed_evidence_ids"]),
                positive_score=sum(
                    scored["model_scores"][key]
                    for key in (
                        "primary_factor_alignment", "stage_connection",
                        "market_linkage", "leader_clarity", "logic_aesthetic",
                        "expectation_gap",
                    )
                ),
                counter_score=scored["model_scores"]["fully_priced_penalty"],
                context=context,
            )
            scored.update(references)
            scored["reason"] = _validate_reason(
                row["reason"], f"sector {row['sector_key']}.reason"
            )
            results.append(scored)
    except TrinityValidationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TrinityValidationError("invalid sector response") from exc
    return sorted(results, key=lambda item: (-item["total_score"], item["sector_key"]))
