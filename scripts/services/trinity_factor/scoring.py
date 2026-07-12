"""因子与板块评分纯函数。"""
from __future__ import annotations

from collections.abc import Mapping
from numbers import Real

from .constants import FACTOR_CODES, FACTOR_WEIGHTS, SECTOR_WEIGHTS


def _is_score(value: object, *, integer_only: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    if integer_only and not isinstance(value, int):
        return False
    return 0 <= value <= 5


def _as_builtin_number(value: Real) -> int | float:
    return int(value) if isinstance(value, int) else float(value)


def _normalize_caps(
    caps: Mapping[str, Real] | None,
    dimensions: Mapping[str, int],
) -> dict[str, int | float]:
    if caps is None:
        return {}
    if not isinstance(caps, Mapping):
        raise ValueError("caps must be a mapping or None")
    if not set(caps).issubset(dimensions):
        raise ValueError("caps contain an unknown dimension")
    if any(not _is_score(value, integer_only=False) for value in caps.values()):
        raise ValueError("caps must be numbers from 0 to 5")
    return {name: _as_builtin_number(value) for name, value in caps.items()}


def calculate_factor_total(
    normalized_scores: Mapping[str, Real],
    evidence_quality: Real,
) -> float:
    """使用唯一权重公式计算、裁剪并按四位小数规范化因子总分。"""
    total = sum(
        FACTOR_WEIGHTS[name] * normalized_scores[name] / 5
        for name in FACTOR_WEIGHTS
    )
    total += 10 * evidence_quality / 5
    return round(max(0.0, min(100.0, float(total))), 4)


def score_factor(
    *,
    factor_code: str,
    dimension_scores: Mapping[str, int],
    evidence_quality: Real,
    caps: Mapping[str, Real] | None = None,
    critical_missing: bool = False,
) -> dict:
    """按固定权重重算单个因子总分，并同时保留模型原分与规则封顶分。"""
    if not isinstance(factor_code, str) or not factor_code or factor_code not in FACTOR_CODES:
        raise ValueError(f"unknown factor_code: {factor_code}")
    if not isinstance(dimension_scores, Mapping):
        raise ValueError("dimension_scores must be a mapping")
    if set(dimension_scores) != set(FACTOR_WEIGHTS):
        raise ValueError("dimension_scores must contain exactly the factor dimensions")
    if any(not _is_score(value, integer_only=True) for value in dimension_scores.values()):
        raise ValueError("factor dimension scores must be integers from 0 to 5")
    if not _is_score(evidence_quality, integer_only=False):
        raise ValueError("evidence_quality must be a number from 0 to 5")
    if not isinstance(critical_missing, bool):
        raise ValueError("critical_missing must be a bool")

    caps = _normalize_caps(caps, FACTOR_WEIGHTS)
    normalized_evidence_quality = _as_builtin_number(evidence_quality)

    raw_scores = dict(dimension_scores)
    normalized_scores = {
        name: min(value, caps.get(name, value))
        for name, value in raw_scores.items()
    }
    total_score = calculate_factor_total(normalized_scores, normalized_evidence_quality)

    return {
        "factor_code": factor_code,
        "model_scores": raw_scores,
        "normalized_scores": normalized_scores,
        "evidence_quality": normalized_evidence_quality,
        "critical_missing": critical_missing,
        "total_score": total_score,
    }


def score_sector(
    *,
    sector_key: str,
    dimension_scores: Mapping[str, int],
    caps: Mapping[str, Real] | None = None,
) -> dict:
    """按固定权重重算单个板块总分并给出程序分档。"""
    if not isinstance(sector_key, str) or not sector_key:
        raise ValueError("sector_key must be a non-empty string")
    if not isinstance(dimension_scores, Mapping):
        raise ValueError("dimension_scores must be a mapping")
    if set(dimension_scores) != set(SECTOR_WEIGHTS):
        raise ValueError("dimension_scores must contain exactly the sector dimensions")
    if any(not _is_score(value, integer_only=True) for value in dimension_scores.values()):
        raise ValueError("sector dimension scores must be integers from 0 to 5")

    caps = _normalize_caps(caps, SECTOR_WEIGHTS)

    raw_scores = dict(dimension_scores)
    normalized_scores = {
        name: min(value, caps.get(name, value))
        for name, value in raw_scores.items()
    }
    total = sum(
        SECTOR_WEIGHTS[name] * normalized_scores[name] / 5
        for name in SECTOR_WEIGHTS
    )
    total_score = round(max(0.0, min(100.0, float(total))), 4)
    tier = "priority" if total_score >= 75 else "watch" if total_score >= 60 else "deprioritized"

    return {
        "sector_key": sector_key,
        "model_scores": raw_scores,
        "normalized_scores": normalized_scores,
        "total_score": total_score,
        "tier": tier,
    }
