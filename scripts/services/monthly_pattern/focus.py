"""月线观察池的可解释重点漏斗与 100 分观察评分。"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from services.monthly_pattern import financials


FOCUS_SCORE_VERSION = "monthly_focus_score_v1"
FOCUS_LIMIT = 10
PRIORITY_LIMIT = 3

_WEIGHTS = {
    "technical": 30.0,
    "mainline": 25.0,
    "fundamental": 20.0,
    "valuation": 10.0,
    "industry_factors": 10.0,
    "data_quality": 5.0,
}
_VALID_STRATEGIES = {
    "fundamental_monthly_trend",
    "theme_monthly_attack",
    "monthly_reacceleration",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _code(record: Mapping[str, Any]) -> str:
    return str(record.get("stock_code") or record.get("code") or "").strip().upper().split(".", 1)[0]


def _status(record: Mapping[str, Any]) -> str:
    value = record.get("pool_status", record.get("status"))
    return str(value or "").strip().lower()


def _source_meta(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("source_meta")
    return value if isinstance(value, Mapping) else {}


def _financial_view(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("financial_evidence")
    return value if isinstance(value, Mapping) else {}


def _verified(view: Mapping[str, Any]) -> bool:
    return view.get("verified") is True or str(view.get("status") or "").lower() == "verified"


def _industry(records: Sequence[Mapping[str, Any]]) -> str:
    values: list[str] = []
    for record in records:
        value = record.get("industry") or _source_meta(record).get("industry")
        normalized = "".join(str(value or "").split())
        if normalized and normalized not in {"未分类", "未提供", "unknown"}:
            values.append(normalized)
    unique = sorted(set(values))
    if len(unique) > 1:
        return "行业冲突"
    return unique[0] if unique else "未分类"


def _name(records: Sequence[Mapping[str, Any]], code: str) -> str:
    names = [
        str(record.get("stock_name") or record.get("name") or "").strip()
        for record in records
    ]
    usable = [name for name in names if name and name.upper().split(".", 1)[0] != code]
    return sorted(set(usable))[0] if usable else code


def _month_rank(value: Any) -> int:
    digits = str(value or "").replace("-", "")[:6]
    return int(digits) if len(digits) == 6 and digits.isdigit() else -1


def _best_financial(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    verified = [
        _financial_view(record)
        for record in records
        if _status(record) in {"active", "fundamental_verified"}
        if _verified(_financial_view(record))
    ]
    if not verified:
        return {}
    return max(
        verified,
        key=lambda view: (
            str(view.get("report_period") or ""),
            str(view.get("financial_ann_date") or ""),
        ),
    )


def _mainline(records: Sequence[Mapping[str, Any]], industry: str) -> tuple[bool | None, str]:
    explicit = [
        record.get("mainline_match", _source_meta(record).get("mainline_match"))
        for record in records
    ]
    if any(value is True for value in explicit):
        return True, "候选显式命中稳定主线"
    evidence_seen = any(value is False for value in explicit)
    for record in records:
        meta = _source_meta(record)
        mainline = meta.get("mainline")
        if not isinstance(mainline, Mapping):
            continue
        hit_counts = mainline.get("hit_counts")
        if not isinstance(hit_counts, Mapping):
            continue
        evidence_seen = True
        required = int(_number(mainline.get("required_hits")) or 1)
        hits = int(_number(hit_counts.get(industry)) or 0)
        if industry != "未分类" and hits >= required:
            return True, f"申万二级近端稳定主线命中 {hits}/{required}"
    if evidence_seen:
        return False, "主线证据存在但该行业未达到稳定门槛"
    return None, "主线证据缺失"


def _threshold_score(value: Any, bands: Sequence[tuple[float, float]], default: float = 0.0) -> float:
    number = _number(value)
    if number is None:
        return default
    for threshold, score in bands:
        if number >= threshold:
            return score
    return default


def _fundamental_score(view: Mapping[str, Any]) -> tuple[float, list[str]]:
    if not _verified(view):
        return 0.0, ["四项基本面硬门未核验"]
    latest = view.get("latest") if isinstance(view.get("latest"), Mapping) else {}
    annual = view.get("annual") if isinstance(view.get("annual"), Mapping) else {}
    latest_values = latest.get("values") if isinstance(latest.get("values"), Mapping) else {}
    annual_values = annual.get("values") if isinstance(annual.get("values"), Mapping) else {}
    roe = _number(annual_values.get("roe"))
    if roe is None:
        roe = _number(latest_values.get("roe"))
    debt = _number(latest_values.get("debt_to_assets"))
    netprofit = _number(latest_values.get("netprofit_yoy"))
    deducted = _number(latest_values.get("dt_netprofit_yoy"))
    roe_score = _threshold_score(roe, ((25, 5), (20, 4), (15, 3)))
    debt_score = 5 if debt is not None and debt < 30 else 4 if debt is not None and debt < 40 else 3 if debt is not None and debt < 50 else 0
    net_score = _threshold_score(netprofit, ((50, 5), (30, 4), (15, 3)))
    deducted_score = _threshold_score(deducted, ((50, 5), (30, 4), (15, 3)))
    return roe_score + debt_score + net_score + deducted_score, [
        f"ROE={roe if roe is not None else '缺失'}",
        f"资产负债率={debt if debt is not None else '缺失'}",
        f"净利润同比={netprofit if netprofit is not None else '缺失'}",
        f"扣非净利润同比={deducted if deducted is not None else '缺失'}",
    ]


def _valuation(records: Sequence[Mapping[str, Any]]) -> tuple[float, Mapping[str, Any]]:
    views = [
        meta.get("valuation")
        for meta in (_source_meta(record) for record in records)
        if isinstance(meta.get("valuation"), Mapping)
    ]
    successful = [view for view in views if str(view.get("status") or "") == "success"]
    if not successful:
        return 0.0, {}
    view = max(successful, key=lambda item: str(item.get("as_of_date") or ""))
    percentile = _number(view.get("industry_percentile"))
    if percentile is None:
        return 0.0, view
    score = 10 if percentile <= 20 else 8 if percentile <= 40 else 6 if percentile <= 60 else 3 if percentile <= 80 else 1
    return float(score), view


def _industry_factor_score(view: Mapping[str, Any], industry: str) -> tuple[float, dict[str, Any]]:
    shadow = financials.build_industry_shadow(view, industry)
    latest_contract = (shadow.get("contract_liability") or {}).get("latest") or {}
    latest_rd = (shadow.get("rd_exp") or {}).get("latest") or {}
    contract_profile = shadow.get("contract_liability") or {}
    rd_profile = shadow.get("rd_exp") or {}
    contract = 0.0
    if contract_profile.get("applicability") in {"core", "secondary"}:
        contract += 2.0 if (_number(latest_contract.get("growth_pct")) or 0) > 0 else 0.0
        contract += 2.0 if (_number(latest_contract.get("qoq_pct")) or 0) > 0 else 0.0
        contract += 1.0 if _number(latest_contract.get("to_revenue_pct")) is not None else 0.0
    rd_exp = 0.0
    if rd_profile.get("applicability") == "core":
        rd_exp += 3.0 if (_number(latest_rd.get("growth_pct")) or 0) > 0 else 0.0
        rd_exp += 2.0 if _number(latest_rd.get("to_revenue_pct")) is not None else 0.0
    elif rd_profile.get("applicability") == "secondary":
        rd_exp += 1.5 if (_number(latest_rd.get("growth_pct")) or 0) > 0 else 0.0
        rd_exp += 1.5 if _number(latest_rd.get("to_revenue_pct")) is not None else 0.0
    return min(10.0, contract + rd_exp), shadow


def score_stock(records: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    records = sorted(
        (record for record in records if isinstance(record, Mapping)),
        key=lambda record: (
            str(record.get("strategy_type") or ""),
            _status(record),
            str(record.get("signal_month") or ""),
            _code(record),
        ),
    )
    if not records:
        return None
    financial_view = _best_financial(records)
    if not _verified(financial_view):
        return None
    code = _code(records[0])
    industry = _industry(records)
    strategies = sorted(
        {
            str(record.get("strategy_type") or "")
            for record in records
            if str(record.get("strategy_type") or "") in _VALID_STRATEGIES
        }
    )
    technical_score = min(_WEIGHTS["technical"], len(strategies) * 10.0)
    mainline_match, mainline_reason = _mainline(records, industry)
    mainline_score = _WEIGHTS["mainline"] if mainline_match is True else 0.0
    fundamental_score, fundamental_reasons = _fundamental_score(financial_view)
    valuation_score, valuation_view = _valuation(records)
    industry_score, shadow = _industry_factor_score(financial_view, industry)
    classified = industry not in {"未分类", "行业冲突"}
    mainline_available = mainline_match is not None
    data_score = 2.0 + (1.0 if classified else 0.0) + (1.0 if mainline_available else 0.0) + (1.0 if valuation_view else 0.0)
    breakdown = {
        "technical": technical_score,
        "mainline": mainline_score,
        "fundamental": fundamental_score,
        "valuation": valuation_score,
        "industry_factors": industry_score,
        "data_quality": data_score,
    }
    total = round(sum(breakdown.values()), 2)
    latest_month = max((str(record.get("signal_month") or "") for record in records), default="")
    has_risk = any(_status(record) == "risk" for record in records)
    return {
        "stock_code": code,
        "stock_name": _name(records, code),
        "industry": industry,
        "score": total,
        "score_version": FOCUS_SCORE_VERSION,
        "breakdown": breakdown,
        "strategies": strategies,
        "strategy_count": len(strategies),
        "latest_signal_month": latest_month,
        "mainline_match": mainline_match,
        "mainline_reason": mainline_reason,
        "fundamental_reasons": fundamental_reasons,
        "valuation": valuation_view,
        "industry_shadow": shadow,
        "has_risk": has_risk,
        "priority_eligible": not has_risk and classified and data_score >= 4.0,
        "records": list(records),
    }


def build_focus_funnel(
    candidates: Sequence[Mapping[str, Any]],
    *,
    focus_limit: int = FOCUS_LIMIT,
    priority_limit: int = PRIORITY_LIMIT,
) -> dict[str, Any]:
    if focus_limit <= 0 or priority_limit <= 0:
        raise ValueError("focus_limit 和 priority_limit 必须为正整数")
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        code = _code(candidate)
        if code:
            buckets[code].append(candidate)
    scored = [item for records in buckets.values() if (item := score_stock(records)) is not None]
    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            -int(item["strategy_count"]),
            -_month_rank(item["latest_signal_month"]),
            str(item["stock_code"]),
        )
    )
    focus_items = scored[:focus_limit]
    priority_eligible = [item for item in scored if item["priority_eligible"]]
    priority_items = priority_eligible[:priority_limit]
    return {
        "version": FOCUS_SCORE_VERSION,
        "weights": dict(_WEIGHTS),
        "input_stocks": len(buckets),
        "verified_stocks": len(scored),
        "focus": focus_items,
        "priority": priority_items,
        "priority_eligible_stocks": len(priority_eligible),
        "omitted_verified": max(0, len(scored) - len(focus_items)),
    }


__all__ = [
    "FOCUS_LIMIT",
    "FOCUS_SCORE_VERSION",
    "PRIORITY_LIMIT",
    "build_focus_funnel",
    "score_stock",
]
