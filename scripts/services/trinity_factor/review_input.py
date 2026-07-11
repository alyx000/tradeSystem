"""API/CLI 共用的复盘评分输入规范化。"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date as _date
from typing import Any

SCORING_STEP_KEYS = (
    "step1_market",
    "step2_sectors",
    "step3_emotion",
    "step4_style",
    "step5_leaders",
    "step6_nodes",
)


def validate_trade_date(value: Any) -> str:
    """要求严格 ISO 日历日期；不把仅形似日期的字符串放进下游。"""
    text = str(value or "").strip()
    try:
        parsed = _date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid calendar date: {text}") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"invalid calendar date: {text}")
    return text


def parse_review_step(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"notes": value}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def normalize_review_steps(
    source: Any,
    *,
    step_keys: Sequence[str] = SCORING_STEP_KEYS,
) -> dict[str, dict[str, Any]]:
    """把 DB TEXT JSON、API 对象和 steps-file 统一成相同的第 1～6 步对象。"""
    if not isinstance(source, Mapping):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key in step_keys:
        if key not in source:
            continue
        parsed = parse_review_step(source.get(key))
        if parsed:
            normalized[key] = parsed
    return normalized
