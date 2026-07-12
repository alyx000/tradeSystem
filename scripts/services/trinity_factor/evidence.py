"""把复盘第 1～6 步和盘后预填整理为双层评分的结构化证据卡。"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import median
from typing import Any

from utils import is_st_stock

from .constants import FACTOR_CODES, SECTOR_CANDIDATE_MAX

RULESET_VERSION = "trinity_ruleset_v2"
SERVICE_SCHEMA_VERSION = "trinity_dual_score_run_v2"
_EVIDENCE_STRING_MAX = 200
_SECTOR_LLM_FACT_FIELDS = (
    "phase_hint",
    "rhythm_confidence",
    "duration_days",
    "pct_chg",
    "limit_up_count",
    "net_amount_yi",
    "cumulative_pct_5d",
    "cumulative_pct_10d",
)

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
        critical_missing = not any(_is_available_objective_fact(item) for item in items)
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
            if (
                _is_llm_referenceable(item)
                and item.get("polarity") != "counter"
            )
        ]
        counter_ids = [
            item["evidence_id"]
            for item in items
            if (
                _is_llm_referenceable(item)
                and item.get("polarity") == "counter"
            )
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
                str(item.get("quality_group") or item.get("source") or "")
                for item in items
                if _is_available_objective_fact(item)
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
        projection = _llm_evidence_projection(candidate)
        rows.append({
            "sector_key": candidate["sector_key"],
            "sector_name": candidate.get("sector_name"),
            "sector_type": candidate.get("sector_type"),
            "candidate_tier": "core",
            "facts": _sector_llm_facts(candidate.get("facts")),
            **projection,
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
        **_llm_evidence_projection(candidate),
        "allowed_t1_check_ids": candidate.get("allowed_t1_check_ids") or [],
        "t1_checks": candidate.get("t1_checks") or [],
    }


def _llm_evidence_projection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    evidence_items = [
        {
            str(key): value
            for key, value in item.items()
            if key != "quality_group"
        }
        for item in candidate.get("evidence_items") or []
        if isinstance(item, Mapping) and _is_llm_referenceable(item)
    ]
    visible_ids = {
        str(item.get("evidence_id"))
        for item in evidence_items
        if item.get("evidence_id")
    }
    return {
        "evidence_items": evidence_items,
        "allowed_evidence_ids": sorted({
            str(evidence_id)
            for evidence_id in candidate.get("allowed_evidence_ids") or []
            if str(evidence_id) in visible_ids
        }),
        "allowed_counter_evidence_ids": sorted({
            str(evidence_id)
            for evidence_id in candidate.get("allowed_counter_evidence_ids") or []
            if str(evidence_id) in visible_ids
        }),
    }


def _sector_llm_facts(value: Any) -> dict[str, Any]:
    """只投影无个股名称、无规则身份的板块客观字段。"""
    if not isinstance(value, Mapping):
        return {}
    return {
        field: value[field]
        for field in _SECTOR_LLM_FACT_FIELDS
        if field in value and _has_content(value[field])
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
        source_status = raw.get("data_status") or "ok"
        items = []
        for item in raw.get("evidence_items") or []:
            if not isinstance(item, Mapping) or not isinstance(item.get("evidence_id"), str):
                continue
            copied = dict(_json_safe_value(item))
            if copied.get("source") == "leader_detection":
                copied["objective"] = False
                copied["polarity"] = "context"
            elif copied.get("objective") is True:
                copied.setdefault("source_status", source_status)
            items.append(copied)
        evidence_ids = sorted({
            item["evidence_id"]
            for item in items
            if _is_llm_referenceable(item) and item.get("polarity") != "counter"
        })
        counter_ids = sorted({
            item["evidence_id"]
            for item in items
            if _is_llm_referenceable(item) and item.get("polarity") == "counter"
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
    market = market if isinstance(market, Mapping) else {}
    if isinstance(market, Mapping):
        market_fact = _pick_fields(
            market,
            (
                "date", "total_amount",
                "sh_index_change_pct", "sz_index_change_pct",
                "advance_count", "decline_count",
                "limit_up_count", "limit_down_count",
                "sh_index_change", "sz_index_change", "up_count", "down_count",
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
        style = style if isinstance(style, Mapping) else {}
        out["style_regime"].extend(_style_objective_items(trade_date, style))

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
    out["leader_signal"].extend(
        _leader_objective_items(trade_date, prefill, market, signals)
    )

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
                and item.get("source") != "leader_detection"
            ):
                leader_item = dict(copied)
                leader_item["layer"] = "stock"
                out["leader_signal"].append(leader_item)
    return out


def _style_objective_items(
    trade_date: str,
    style: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cap = style.get("cap_preference")
    if isinstance(cap, Mapping):
        relative = cap.get("relative")
        cap_numbers = [
            _strict_number(cap.get(field))
            for field in ("csi300_chg", "csi1000_chg", "spread")
        ]
        if (
            all(value is not None for value in cap_numbers)
            and relative in {"偏小盘", "偏大盘", "均衡"}
        ):
            cards.append(_lineage_fact(
                trade_date, "style_regime", "cap_relative_strength", "style",
                "index_relative_strength",
                _pick_fields(
                    cap,
                    ("csi300_chg", "csi1000_chg", "spread", "relative"),
                ),
            ))

    board = style.get("board_preference")
    if isinstance(board, Mapping):
        dominant_type = board.get("dominant_type")
        board_pcts = [
            _strict_number(board.get(field))
            for field in ("pct_10cm", "pct_20cm", "pct_30cm")
        ]
        if (
            dominant_type in {"10cm", "20cm", "30cm"}
            and all(
                value is not None and 0 <= value <= 100
                for value in board_pcts
            )
        ):
            cards.append(_lineage_fact(
                trade_date, "style_regime", "board_preference", "style",
                "limit_board_mix",
                _pick_fields(
                    board,
                    ("dominant_type", "pct_10cm", "pct_20cm", "pct_30cm"),
                ),
            ))

    snapshot = style.get("premium_snapshot")
    groups: dict[str, Any] = {}
    if isinstance(snapshot, Mapping):
        for name in (
            "first_board", "first_board_10cm", "first_board_20cm",
            "first_board_30cm", "second_board", "third_board_plus",
            "capacity_top10",
        ):
            raw = snapshot.get(name)
            if not isinstance(raw, Mapping):
                continue
            count = _strict_int(raw.get("count"))
            premium_median = _strict_number(raw.get("premium_median"))
            if count is None or count <= 0 or premium_median is None:
                continue
            compact = {
                "count": count,
                "premium_median": premium_median,
            }
            open_up_rate = _strict_number(raw.get("open_up_rate"))
            if open_up_rate is not None and 0 <= open_up_rate <= 1:
                compact["open_up_rate"] = open_up_rate
            groups[name] = compact
    if groups:
        premium_trend = style.get("premium_trend")
        if isinstance(premium_trend, Mapping) and premium_trend.get("direction") is not None:
            groups["trend_direction"] = _json_safe_value(
                premium_trend.get("direction")
            )
        groups["capacity_proxy"] = True
        cards.append(_lineage_fact(
            trade_date, "style_regime", "premium_regime", "style",
            "premium_realization", groups,
        ))
    return cards


def _leader_objective_items(
    trade_date: str,
    prefill: Mapping[str, Any],
    market: Mapping[str, Any],
    signals: Any,
) -> list[dict[str, Any]]:
    style = market.get("style_factors")
    style = style if isinstance(style, Mapping) else {}
    return [item for item in (
        _ladder_structure_item(trade_date, market, signals),
        _promotion_realization_item(trade_date, style),
        _prior_core_feedback_item(trade_date, prefill, style),
    ) if item is not None]


def _ladder_structure_item(
    trade_date: str,
    market: Mapping[str, Any],
    signals: Any,
) -> dict[str, Any] | None:
    counts, names_by_tier = _parse_ladder_counts(
        market.get("continuous_board_counts")
    )
    ladder_rows: Any = None
    if isinstance(signals, Mapping):
        emotion = signals.get("emotion")
        if isinstance(emotion, Mapping):
            ladder_rows = emotion.get("ladder_rows")

    row_names: dict[int, list[str]] = {}
    if isinstance(ladder_rows, Sequence) and not isinstance(ladder_rows, (str, bytes)):
        for row in ladder_rows:
            if not isinstance(row, Mapping):
                continue
            tier = _tier_number(row.get("nums"))
            name = _clean_stock_name(row.get("name"))
            if tier is None or tier < 2 or not name:
                continue
            row_names.setdefault(tier, []).append(name)

    for tier, names in row_names.items():
        if counts.get(tier, 0) <= 0:
            counts[tier] = len(set(names))
        names_by_tier.setdefault(tier, []).extend(names)

    corroborated_tiers = set(counts) | set(names_by_tier)
    reported_highest = _tier_number(market.get("highest_board"))
    highest_board = max(corroborated_tiers, default=0)
    if reported_highest in corroborated_tiers:
        highest_board = max(highest_board, reported_highest)
    tier_counts = [
        {"tier": tier, "count": counts[tier]}
        for tier in sorted(counts, reverse=True)[:20]
        if tier >= 2 and counts[tier] > 0
    ]
    content = {
        "tier_counts": tier_counts,
        "top_tier_names": _stable_strings(names_by_tier.get(highest_board, [])),
        "highest_board": highest_board,
        "consecutive_count": sum(row["count"] for row in tier_counts),
    }
    if tier_counts:
        return _lineage_fact(
            trade_date, "leader_signal", "ladder_structure", "stock",
            "limit_event", content,
        )
    return None


def _promotion_realization_item(
    trade_date: str,
    style: Mapping[str, Any],
) -> dict[str, Any] | None:
    promotion = style.get("promotion")
    if (
        not isinstance(promotion, Mapping)
        or str(promotion.get("trade_date") or "") != trade_date
    ):
        return None

    content: dict[str, Any] = {}
    for group_name in ("first_to_second", "second_to_third"):
        raw_group = promotion.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        base = _strict_int(raw_group.get("base"))
        promoted = _strict_int(raw_group.get("promoted"))
        rate = _strict_number(raw_group.get("rate"))
        if (
            base is None or base <= 0
            or promoted is None or not 0 <= promoted <= base
            or rate is None or not 0 <= rate <= 1
        ):
            continue
        content[group_name] = {
            "base": base,
            "promoted": promoted,
            "rate": rate,
            "promoted_names": _stable_strings(
                raw_group.get("promoted_names") or [],
                limit=10,
            ),
        }
    if content:
        return _lineage_fact(
            trade_date, "leader_signal", "promotion_realization", "stock",
            "leader_outcome", content,
        )
    return None


def _prior_core_feedback_item(
    trade_date: str,
    prefill: Mapping[str, Any],
    style: Mapping[str, Any],
) -> dict[str, Any] | None:
    popularity = style.get("popularity")
    if (
        not isinstance(popularity, Sequence)
        or isinstance(popularity, (str, bytes))
    ):
        return None
    consecutive_rows = [
        row for row in popularity
        if (
            isinstance(row, Mapping)
            and _source_includes(row.get("source"), "consecutive")
            and not is_st_stock(row.get("name"))
            and _has_feedback_outcome(row)
        )
    ]
    if not consecutive_rows:
        return None

    prev_market = prefill.get("prev_market")
    prev_market = prev_market if isinstance(prev_market, Mapping) else {}
    prev_counts, prev_names_by_tier = _parse_ladder_counts(
        prev_market.get("continuous_board_counts")
    )
    prev_tiers = set(prev_counts) | set(prev_names_by_tier)
    explicit_highest = _tier_number(prev_market.get("highest_board"))
    if explicit_highest is not None and explicit_highest >= 2:
        prev_tiers.add(explicit_highest)
    prev_highest = max(prev_tiers, default=0)
    previous_top_names = set(prev_names_by_tier.get(prev_highest, []))
    matched_rows = [
        row for row in consecutive_rows
        if (
            _clean_stock_name(row.get("name")) in previous_top_names
            or _bounded_string(row.get("code")) in previous_top_names
        )
    ]
    if previous_top_names and matched_rows:
        selected = matched_rows
        cohort_basis = "previous_highest_tier"
    else:
        selected = consecutive_rows
        cohort_basis = "all_consecutive"

    close_changes = _numeric_values(selected, "t_close_change_pct")
    content = {
        "cohort_basis": cohort_basis,
        "cohort_count": len(selected),
        "names": _stable_strings(
            [_clean_stock_name(row.get("name")) for row in selected],
            limit=10,
        ),
        "codes": _stable_strings(
            [str(row.get("code") or "").strip() for row in selected],
            limit=10,
        ),
        "median_open_premium_pct": _median_or_none(
            _numeric_values(selected, "t_open_premium_pct")
        ),
        "median_close_change_pct": _median_or_none(close_changes),
        "positive_close_count": sum(value > 0 for value in close_changes),
        "limit_up_count": sum(row.get("t_is_limit_up") is True for row in selected),
        "limit_down_count": sum(row.get("t_is_limit_down") is True for row in selected),
    }
    return _lineage_fact(
        trade_date, "leader_signal", "prior_core_feedback", "stock",
        "leader_outcome", content,
    )


def _lineage_fact(
    trade_date: str,
    factor_code: str,
    source: str,
    layer: str,
    quality_group: str,
    content: Any,
) -> dict[str, Any]:
    return _fact_item(
        f"{trade_date}:{factor_code}:{source}", source, layer, content,
        quality_group=quality_group,
    )


def _fact_item(
    evidence_id: str,
    source: str,
    layer: str,
    content: Any,
    *,
    polarity: str = "support",
    quality_group: str | None = None,
) -> dict[str, Any]:
    item = {
        "evidence_id": evidence_id,
        "source": source,
        "source_status": "ok",
        "layer": layer,
        "kind": "fact",
        "polarity": polarity,
        "content": content,
    }
    if quality_group:
        item["quality_group"] = quality_group
    return item


def _parse_ladder_counts(value: Any) -> tuple[dict[int, int], dict[int, list[str]]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, {}
    if not isinstance(value, Mapping):
        return {}, {}

    counts: dict[int, int] = {}
    names_by_tier: dict[int, list[str]] = {}
    for raw_tier, raw_value in value.items():
        tier = _tier_number(raw_tier)
        if tier is None or tier < 2:
            continue
        names: list[str] = []
        count: int | None = None
        if isinstance(raw_value, Mapping):
            names_supplied = "names" in raw_value or "stocks" in raw_value
            raw_names = (
                raw_value.get("names")
                if "names" in raw_value
                else raw_value.get("stocks")
            )
            names = _stock_names(raw_names)
            count = len(names) if names_supplied else _to_int(raw_value.get("count"))
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
            names = _stock_names(raw_value)
            count = len(names)
        else:
            count = _to_int(raw_value)
        if count is not None and count > 0:
            counts[tier] = count
        if names:
            names_by_tier[tier] = names
    return counts, names_by_tier


def _stock_names(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    names = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("name") or value.get("stock_name") or value.get("code")
        name = _clean_stock_name(value)
        if name:
            names.append(name)
    return _stable_strings(names)


def _clean_stock_name(value: Any) -> str:
    text = str(value or "").strip()
    if is_st_stock(text):
        return ""
    return text[:_EVIDENCE_STRING_MAX]


def _tier_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        tier = _to_int(value) if _strict_number(value) is not None else None
    elif isinstance(value, str):
        match = re.fullmatch(r"(\d+)(?:板)?", value.strip())
        tier = int(match.group(1)) if match else None
    else:
        tier = None
    return tier if tier is not None and 1 <= tier <= 30 else None


def _stable_strings(values: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    bounded = {_bounded_string(value) for value in values}
    bounded.discard("")
    return sorted(bounded)[:limit]


def _bounded_string(value: Any) -> str:
    return str(value or "").strip()[:_EVIDENCE_STRING_MAX]


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _to_int(value: Any) -> int | None:
    parsed = _to_number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _strict_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _to_number(value)


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return _to_int(value)


def _source_includes(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return expected in {str(item) for item in value}
    return False


def _numeric_values(rows: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    values = [_strict_number(row.get(field)) for row in rows]
    return [value for value in values if value is not None]


def _has_feedback_outcome(row: Mapping[str, Any]) -> bool:
    return any(
        _strict_number(row.get(field)) is not None
        for field in ("t_open_premium_pct", "t_close_change_pct")
    ) or any(
        isinstance(row.get(field), bool)
        for field in ("t_is_limit_up", "t_is_limit_down")
    )


def _median_or_none(values: Sequence[float]) -> float | None:
    return float(median(values)) if values else None


def _is_available_objective_fact(item: Mapping[str, Any]) -> bool:
    return item.get("kind") == "fact" and _is_valid_objective_content(item)


def _is_llm_referenceable(item: Mapping[str, Any]) -> bool:
    if item.get("kind") == "status" or item.get("source") == "leader_detection":
        return False
    if item.get("kind") == "fact" or item.get("objective") is True:
        return _is_valid_objective_content(item)
    return True


def _is_valid_objective_content(item: Mapping[str, Any]) -> bool:
    evidence_id = item.get("evidence_id")
    source = item.get("source")
    quality_group = item.get("quality_group") or source
    text = item.get("text")
    return (
        item.get("source_status") == "ok"
        and isinstance(evidence_id, str) and bool(evidence_id.strip())
        and isinstance(source, str) and bool(source.strip())
        and isinstance(quality_group, str) and bool(quality_group.strip())
        and (
            _has_meaningful_content(item.get("content"))
            or (isinstance(text, str) and bool(text.strip()))
        )
    )


def _evidence_quality(items: Sequence[Mapping[str, Any]]) -> int:
    fact_items = [item for item in items if _is_available_objective_fact(item)]
    if not fact_items:
        return 0
    sources = {
        str(item.get("quality_group") or item.get("source") or "")
        for item in fact_items
    }
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


def _has_meaningful_content(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_meaningful_content(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_meaningful_content(item) for item in value)
    if isinstance(value, float):
        return isfinite(value)
    return _has_content(value)


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
