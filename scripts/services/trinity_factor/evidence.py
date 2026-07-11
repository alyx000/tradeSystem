"""把复盘第 1～6 步和盘后预填整理为双层评分的结构化证据卡。"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import FACTOR_CODES, SECTOR_CANDIDATE_MAX

RULESET_VERSION = "trinity_ruleset_v1"
SERVICE_SCHEMA_VERSION = "trinity_dual_score_run_v1"

_FACTOR_STEP_MAP = {
    "market_node": ("step1_market", "step6_nodes"),
    "sector_rhythm": ("step2_sectors",),
    "style_regime": ("step4_style",),
    "leader_signal": ("step3_emotion", "step5_leaders"),
}
_FACTOR_T1_LABELS = {
    "market_node": "下一交易日大盘节点是否继续成为结构约束",
    "sector_rhythm": "核心板块在下一交易日是否保持客观连接",
    "style_regime": "风格事实在下一交易日是否延续或明确切换",
    "leader_signal": "龙头与梯队事实在下一交易日是否继续印证",
}


def build_evidence_snapshot(
    trade_date: str,
    prefill: Mapping[str, Any] | None,
    review_steps: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """构造可审计证据快照；规则元数据保留在快照，不直接展示给 LLM。"""
    prefill = prefill if isinstance(prefill, Mapping) else {}
    review_steps = review_steps if isinstance(review_steps, Mapping) else {}
    sector_candidates = _prepare_sector_candidates(trade_date, prefill)
    factor_items = _factor_objective_items(trade_date, prefill, sector_candidates)

    factor_candidates: list[dict[str, Any]] = []
    for factor_code in sorted(FACTOR_CODES):
        items = list(factor_items.get(factor_code, []))
        for step_key in _FACTOR_STEP_MAP[factor_code]:
            step_value = review_steps.get(step_key)
            if _has_content(step_value):
                items.append({
                    "evidence_id": f"{trade_date}:{factor_code}:{step_key}",
                    "source": step_key,
                    "source_status": "ok",
                    "layer": "review",
                    "kind": "judgement",
                    "polarity": "context",
                    "content": _json_safe_value(_strip_rule_metadata(step_value)),
                })

        quality = _evidence_quality(items)
        critical_missing = not any(item.get("kind") == "fact" for item in items)
        caps: dict[str, int] = {}
        if critical_missing:
            caps = {
                "current_dominance": 2,
                "cross_layer_alignment": 2,
                "rhythm_clarity": 2,
                "next_stage_relevance": 2,
            }
        elif quality <= 2:
            caps = {
                "current_dominance": 3,
                "cross_layer_alignment": 3,
                "rhythm_clarity": 3,
            }

        evidence_ids = [
            item["evidence_id"]
            for item in items
            if item.get("polarity") != "counter"
        ]
        counter_ids = [
            item["evidence_id"]
            for item in items
            if item.get("polarity") == "counter"
        ]
        t1_check_id = f"{trade_date}:{factor_code}:t1"
        factor_candidates.append({
            "factor_code": factor_code,
            "evidence_quality": quality,
            "critical_missing": critical_missing,
            "caps": caps,
            "evidence_items": items,
            "allowed_evidence_ids": sorted(set(evidence_ids)),
            "allowed_counter_evidence_ids": sorted(set(counter_ids)),
            "allowed_t1_check_ids": [t1_check_id],
            "t1_checks": [{
                "t1_check_id": t1_check_id,
                "description": _FACTOR_T1_LABELS[factor_code],
            }],
            "objective_source_count": len({
                str(item.get("source") or "")
                for item in items
                if item.get("kind") == "fact"
            }),
        })

    primary_lock = _primary_category_lock(review_steps)
    fallback_code = _rule_fallback_code(factor_candidates, primary_lock)
    deterministic_sector_order = [row["sector_key"] for row in sector_candidates]
    return {
        "trade_date": trade_date,
        "ruleset_version": RULESET_VERSION,
        "schema_version": SERVICE_SCHEMA_VERSION,
        "factor_candidates": factor_candidates,
        "sector_candidates": sector_candidates,
        "rule_gate": {
            "primary_category_lock": primary_lock,
            "rule_fallback_code": fallback_code,
            "deterministic_sector_order": deterministic_sector_order,
            "core_sector_count": len(sector_candidates),
            "sector_source_status": _sector_source_status(prefill),
        },
    }


def build_factor_llm_input(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """生成不含规则分、封顶值或规则排序的因子层输入。"""
    rows = []
    for candidate in snapshot.get("factor_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        rows.append(_factor_llm_card(candidate))
    return {
        "trade_date": snapshot.get("trade_date"),
        "factors": sorted(rows, key=lambda row: row["factor_code"]),
    }


def build_sector_llm_input(
    snapshot: Mapping[str, Any],
    *,
    primary_factor_code: str,
) -> dict[str, Any]:
    """生成条件化板块层输入，候选按稳定 sector_key 排序。"""
    primary_candidates = [
        candidate
        for candidate in snapshot.get("factor_candidates") or []
        if (
            isinstance(candidate, Mapping)
            and candidate.get("factor_code") == primary_factor_code
        )
    ]
    if len(primary_candidates) != 1:
        raise ValueError("primary_factor_code must match exactly one factor candidate")

    rows = []
    for candidate in snapshot.get("sector_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        rows.append({
            "sector_key": candidate["sector_key"],
            "sector_name": candidate.get("sector_name"),
            "sector_type": candidate.get("sector_type"),
            "candidate_tier": "core",
            "facts": candidate.get("facts") or {},
            "evidence_items": candidate.get("evidence_items") or [],
            "allowed_evidence_ids": candidate.get("allowed_evidence_ids") or [],
            "allowed_counter_evidence_ids": (
                candidate.get("allowed_counter_evidence_ids") or []
            ),
            "allowed_t1_check_ids": candidate.get("allowed_t1_check_ids") or [],
            "t1_checks": candidate.get("t1_checks") or [],
        })
    return {
        "trade_date": snapshot.get("trade_date"),
        "primary_factor_code": primary_factor_code,
        "primary_factor": _factor_llm_card(primary_candidates[0]),
        "sectors": sorted(rows, key=lambda row: row["sector_key"]),
    }


def _factor_llm_card(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """只暴露 LLM 可引用的因子证据，隐藏程序规则与评分元数据。"""
    return {
        "factor_code": candidate["factor_code"],
        "evidence_items": candidate.get("evidence_items") or [],
        "allowed_evidence_ids": candidate.get("allowed_evidence_ids") or [],
        "allowed_counter_evidence_ids": (
            candidate.get("allowed_counter_evidence_ids") or []
        ),
        "allowed_t1_check_ids": candidate.get("allowed_t1_check_ids") or [],
        "t1_checks": candidate.get("t1_checks") or [],
    }


def _prepare_sector_candidates(
    trade_date: str,
    prefill: Mapping[str, Any],
) -> list[dict[str, Any]]:
    signals = prefill.get("review_signals")
    sectors = signals.get("sectors") if isinstance(signals, Mapping) else {}
    candidates = sectors.get("projection_candidates") if isinstance(sectors, Mapping) else []
    core_rows = [
        row for row in candidates or []
        if isinstance(row, Mapping) and row.get("candidate_tier") == "core"
    ][:SECTOR_CANDIDATE_MAX]

    prepared: list[dict[str, Any]] = []
    for raw in core_rows:
        sector_key = raw.get("sector_key")
        if not isinstance(sector_key, str) or not sector_key:
            continue
        items = [
            _json_safe_value(item)
            for item in raw.get("evidence_items") or []
            if isinstance(item, Mapping) and isinstance(item.get("evidence_id"), str)
        ]
        evidence_ids = sorted({
            item["evidence_id"]
            for item in items
            if item.get("polarity") != "counter"
        })
        counter_ids = sorted({
            item["evidence_id"]
            for item in items
            if item.get("polarity") == "counter"
        })
        t1_check_id = f"{trade_date}:{sector_key}:t1-continuity"
        prepared.append({
            "sector_key": sector_key,
            "sector_name": raw.get("sector_name"),
            "sector_type": raw.get("sector_type"),
            "candidate_tier": "core",
            "data_status": raw.get("data_status"),
            "facts": _json_safe_value(raw.get("facts") or {}),
            "key_stocks": _json_safe_value(raw.get("key_stocks") or []),
            "evidence_items": items,
            "caps": {},
            "allowed_evidence_ids": evidence_ids,
            "allowed_counter_evidence_ids": counter_ids,
            "allowed_t1_check_ids": [t1_check_id],
            "t1_checks": [{
                "t1_check_id": t1_check_id,
                "description": "下一交易日仍有客观板块连接或延续证据",
            }],
        })
    return prepared


def _sector_source_status(prefill: Mapping[str, Any]) -> str:
    signals = prefill.get("review_signals")
    sectors = signals.get("sectors") if isinstance(signals, Mapping) else None
    if isinstance(sectors, Mapping):
        explicit = str(
            sectors.get("data_status") or sectors.get("source_status") or ""
        ).strip()
        if explicit:
            return explicit
        candidates = sectors.get("projection_candidates")
        if isinstance(candidates, list):
            market = prefill.get("market")
            if isinstance(market, Mapping):
                return "ok" if candidates else "source_ok_empty"
    return "missing"


def _factor_objective_items(
    trade_date: str,
    prefill: Mapping[str, Any],
    sector_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    out = {factor_code: [] for factor_code in FACTOR_CODES}
    market = prefill.get("market")
    if isinstance(market, Mapping):
        market_fact = _pick_fields(
            market,
            (
                "date", "total_amount", "sh_index_change", "sz_index_change",
                "up_count", "down_count", "limit_up_count", "limit_down_count",
            ),
        )
        if market_fact:
            out["market_node"].append(_fact_item(
                f"{trade_date}:market_node:daily_market",
                "daily_market",
                "market",
                market_fact,
            ))
        style = market.get("style_factors")
        if isinstance(style, Mapping) and style:
            out["style_regime"].append(_fact_item(
                f"{trade_date}:style_regime:style_factors",
                "style_factors",
                "style",
                _json_safe_value(style),
            ))

    signals = prefill.get("review_signals")
    if isinstance(signals, Mapping):
        market_signals = signals.get("market")
        if isinstance(market_signals, Mapping):
            structure_rows = market_signals.get("market_structure_rows")
            if _has_content(structure_rows):
                out["market_node"].append(_fact_item(
                    f"{trade_date}:market_node:market_structure",
                    "market_structure",
                    "market",
                    _json_safe_value(structure_rows),
                ))
        emotion_signals = signals.get("emotion")
        if isinstance(emotion_signals, Mapping):
            ladder_rows = emotion_signals.get("ladder_rows")
            if _has_content(ladder_rows):
                out["leader_signal"].append(_fact_item(
                    f"{trade_date}:leader_signal:ladder",
                    "limit_ladder",
                    "stock",
                    _json_safe_value(ladder_rows),
                ))

    for candidate in sector_candidates:
        for item in candidate.get("evidence_items") or []:
            if not isinstance(item, Mapping) or item.get("objective") is not True:
                continue
            copied = dict(_json_safe_value(item))
            copied["kind"] = "fact"
            copied["layer"] = "sector"
            copied.setdefault("source_status", candidate.get("data_status") or "ok")
            out["sector_rhythm"].append(copied)
            if (
                item.get("category") == "leader"
                or item.get("source") == "leader_detection"
            ):
                leader_item = dict(copied)
                leader_item["layer"] = "stock"
                out["leader_signal"].append(leader_item)
    return out


def _fact_item(
    evidence_id: str,
    source: str,
    layer: str,
    content: Any,
    *,
    polarity: str = "support",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source": source,
        "source_status": "ok",
        "layer": layer,
        "kind": "fact",
        "polarity": polarity,
        "content": content,
    }


def _evidence_quality(items: Sequence[Mapping[str, Any]]) -> int:
    fact_items = [item for item in items if item.get("kind") == "fact"]
    judgement_items = [item for item in items if item.get("kind") == "judgement"]
    if not fact_items:
        return 0
    sources = {str(item.get("source") or "") for item in fact_items}
    layers = {str(item.get("layer") or "") for item in fact_items}
    if len(sources) == 1:
        return 2
    if len(sources) == 2:
        return 3
    if len(sources) >= 4 and len(layers) >= 2:
        return 5
    return 4


def _primary_category_lock(review_steps: Mapping[str, Any]) -> str | None:
    for step_key in ("step1_market", "step6_nodes"):
        value = review_steps.get(step_key)
        if not isinstance(value, Mapping):
            continue
        if value.get("systemic_risk") is True or value.get("primary_category_lock") == "market_node":
            return "market_node"
    return None


def _strip_rule_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_rule_metadata(item)
            for key, item in value.items()
            if str(key) not in {"primary_category_lock", "systemic_risk"}
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_strip_rule_metadata(item) for item in value]
    return value


def _rule_fallback_code(
    candidates: Sequence[Mapping[str, Any]],
    primary_lock: str | None,
) -> str | None:
    if primary_lock:
        locked = next(
            (row for row in candidates if row.get("factor_code") == primary_lock),
            None,
        )
        return primary_lock if locked and locked.get("evidence_quality", 0) >= 3 else None
    ranked = sorted(
        candidates,
        key=lambda row: (
            -int(row.get("evidence_quality") or 0),
            -int(row.get("objective_source_count") or 0),
            str(row.get("factor_code") or ""),
        ),
    )
    if not ranked or ranked[0].get("evidence_quality", 0) < 4:
        return None
    top_strength = int(ranked[0].get("objective_source_count") or 0)
    second_strength = int(ranked[1].get("objective_source_count") or 0) if len(ranked) > 1 else -1
    return str(ranked[0]["factor_code"]) if top_strength - second_strength >= 2 else None


def _pick_fields(value: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {
        field: _json_safe_value(value[field])
        for field in fields
        if field in value and value[field] is not None
    }


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes)):
        return bool(value)
    return True


def _json_safe_value(value: Any) -> Any:
    """生成稳定 JSON 值并拒绝 NaN/自定义对象进入证据快照。"""
    try:
        parsed = json.loads(json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            default=str,
        ))
        return _bound_json_value(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence value is not JSON serializable") from exc


def _bound_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list):
        return [_bound_json_value(item) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key): _bound_json_value(item)
            for key, item in list(value.items())[:100]
        }
    return value
