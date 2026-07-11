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
REVIEW_STEP_KEYS = (*SCORING_STEP_KEYS, "step7_positions", "step8_plan")


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


def _textify_review_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(
            item for item in (_textify_review_value(v) for v in value) if item
        )
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _append_note_lines(step: dict[str, Any], lines: list[str]) -> None:
    compact = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not compact or step.get("notes"):
        return
    step["notes"] = "\n".join(compact)


def normalize_review_step_for_display(step_key: str, value: Any) -> Any:
    """把摘要式复盘字段投影到 Web 已有结构，同时保留原字段。"""
    if not isinstance(value, dict):
        return value
    step = dict(value)
    note_lines: list[str] = []
    for key, label in (
        ("facts", "事实"),
        ("judgement", "判断"),
        ("judgment", "判断"),
        ("plan", "计划"),
    ):
        text = _textify_review_value(step.get(key))
        if text:
            note_lines.append(f"{label}：{text}")

    if step_key == "step6_nodes" and step.get("judgement") and not step.get("overall"):
        step["overall"] = _textify_review_value(step.get("judgement"))
    if step_key == "step6_nodes" and step.get("node_type") and not step.get("market_node"):
        step["market_node"] = _textify_review_value(step.get("node_type"))

    if step_key == "step7_positions" and isinstance(step.get("holdings"), list) and not step.get("positions"):
        positions: list[dict[str, Any]] = []
        for item in step["holdings"]:
            if not isinstance(item, dict):
                continue
            code = _textify_review_value(item.get("code"))
            name = _textify_review_value(item.get("name"))
            stock = f"{name}({code})" if name and code else name or code
            positions.append({
                "stock": stock,
                "cost": item.get("cost"),
                "current_price": item.get("current_price"),
                "prefill_pnl_pct": item.get("prefill_pnl_pct"),
                "position_pct": item.get("position_pct"),
                "in_hot_sector": bool(item.get("in_hot_sector", False)),
                "price_trend": item.get("price_trend") or "",
                "volume_vs_avg": item.get("volume_vs_avg") or "",
                "amplitude_ok": bool(item.get("amplitude_ok", False)),
                "action_plan": _textify_review_value(item.get("task") or item.get("action_plan")),
            })
        if positions:
            step["positions"] = positions

    if step_key == "step8_plan":
        plan_text = _textify_review_value(step.get("plan"))
        if plan_text:
            summary = step.get("summary") if isinstance(step.get("summary"), dict) else {}
            summary = dict(summary)
            summary.setdefault("one_sentence", plan_text)
            summary.setdefault("trinity", plan_text)
            step["summary"] = summary
        watch_signals = step.get("watch_signals")
        if isinstance(watch_signals, list) and not step.get("secondary_factors"):
            step["secondary_factors"] = [
                text for text in (_textify_review_value(v) for v in watch_signals) if text
            ]
        avoids = step.get("avoid")
        if isinstance(avoids, list) and not step.get("risks"):
            step["risks"] = [
                {"description": _textify_review_value(v), "impact": "中"}
                for v in avoids
                if _textify_review_value(v)
            ]

    _append_note_lines(step, note_lines)
    return step


def normalize_review_payload_for_display(body: Mapping[str, Any]) -> dict[str, Any]:
    """API 保存、API 评分与 CLI 评分共用的复盘输入标准化边界。"""
    normalized = dict(body)
    for step_key in REVIEW_STEP_KEYS:
        if step_key in normalized:
            normalized[step_key] = normalize_review_step_for_display(
                step_key, normalized[step_key]
            )
    return normalized
