"""主导因子与辅助因子选择纯函数。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from numbers import Real

from .constants import FACTOR_CODES, FACTOR_WEIGHTS


def _is_bounded_number(value: object, upper: float) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and isfinite(value)
        and 0 <= value <= upper
    )


def _validate_scored_factors(scored_factors: object) -> list[Mapping]:
    if isinstance(scored_factors, (str, bytes)) or not isinstance(scored_factors, Sequence):
        raise ValueError("scored_factors must be a sequence")

    validated: list[Mapping] = []
    seen_codes: set[str] = set()
    for index, item in enumerate(scored_factors):
        if not isinstance(item, Mapping):
            raise ValueError(f"scored_factors[{index}] must be a mapping")
        factor_code = item.get("factor_code")
        if not isinstance(factor_code, str) or factor_code not in FACTOR_CODES:
            raise ValueError(f"scored_factors[{index}] has an unknown factor_code")
        if factor_code in seen_codes:
            raise ValueError(f"duplicate factor_code: {factor_code}")
        seen_codes.add(factor_code)

        if not _is_bounded_number(item.get("total_score"), 100):
            raise ValueError(f"scored_factors[{index}].total_score must be from 0 to 100")
        if not _is_bounded_number(item.get("evidence_quality"), 5):
            raise ValueError(f"scored_factors[{index}].evidence_quality must be from 0 to 5")
        if not isinstance(item.get("critical_missing", False), bool):
            raise ValueError(f"scored_factors[{index}].critical_missing must be a bool")

        scores = item.get("normalized_scores")
        if not isinstance(scores, Mapping):
            raise ValueError(f"scored_factors[{index}].normalized_scores must be a mapping")
        if set(scores) != set(FACTOR_WEIGHTS):
            raise ValueError(
                f"scored_factors[{index}].normalized_scores must contain exact dimensions"
            )
        for field in FACTOR_WEIGHTS:
            if not _is_bounded_number(scores.get(field), 5):
                raise ValueError(
                    f"scored_factors[{index}].normalized_scores.{field} must be from 0 to 5"
                )
        validated.append(item)
    return validated


def _base_eligible(item: Mapping) -> bool:
    scores = item["normalized_scores"]
    return (
        item["total_score"] >= 70
        and scores["current_dominance"] >= 4
        and scores["rhythm_clarity"] >= 3
        and item["evidence_quality"] >= 3
        and scores["counterevidence"] <= 2
    )


def _empty(reason: str) -> dict:
    return {
        "primary": None,
        "supporting": [],
        "confidence": None,
        "undetermined_reason": reason,
        "judgement_label": "[判断]",
    }


def _undetermined_reason(
    ranked: Sequence[Mapping],
    primary: Mapping | None,
    *,
    lead: float,
) -> str:
    relevant = primary or ranked[0]
    if relevant.get("critical_missing", False) or relevant["evidence_quality"] <= 0:
        return "undetermined_missing_data"
    if primary is not None and _base_eligible(primary) and lead < 8:
        return "undetermined_competing"
    if relevant["normalized_scores"]["counterevidence"] > 2:
        return "undetermined_conflicted"
    return "undetermined_weak"


def select_dominant_factors(
    scored_factors: Sequence[Mapping],
    *,
    primary_category_lock: str | None = None,
) -> dict:
    """按程序门槛选择至多一个主导因子和两个辅助因子。"""
    if primary_category_lock not in (None, "market_node"):
        raise ValueError("primary_category_lock must be market_node or None")

    ranked = sorted(
        _validate_scored_factors(scored_factors),
        key=lambda item: (-item["total_score"], item["factor_code"]),
    )
    if not ranked:
        return _empty("undetermined_missing_data")
    if primary_category_lock:
        primary = next(
            (item for item in ranked if item["factor_code"] == primary_category_lock),
            None,
        )
    else:
        primary = ranked[0]
    eligible_count = sum(_base_eligible(item) for item in ranked)
    competitors = [item for item in ranked if item is not primary]
    lead = (
        float("-inf") if primary is None
        else float("inf") if not competitors
        else primary["total_score"] - competitors[0]["total_score"]
    )
    established = (
        primary is not None
        and _base_eligible(primary)
        and lead >= 8
        and (eligible_count > 1 or primary["total_score"] >= 75)
    )
    if not established:
        return _empty(_undetermined_reason(
            ranked,
            primary,
            lead=lead,
        ))

    counterevidence = primary["normalized_scores"]["counterevidence"]
    confidence = (
        "high"
        if primary["total_score"] >= 82
        and lead >= 15
        and not primary.get("critical_missing", False)
        and counterevidence <= 1
        else "medium"
    )
    supporting = [
        item for item in ranked
        if item is not primary
        and item["total_score"] >= 55
        and primary["total_score"] - item["total_score"] <= 25
    ][:2]

    return {
        "primary": primary,
        "supporting": supporting,
        "confidence": confidence,
        "undetermined_reason": None,
        "judgement_label": "[判断]",
    }
