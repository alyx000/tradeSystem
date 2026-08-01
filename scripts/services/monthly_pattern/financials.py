"""月线模式的财务硬门与上下文证据。

这里只做可重复的数值判断。合同负债、研发、商誉余额、应收和存货有明显行业差异，
只作为证据展示，不抬高或否决核心财务门槛。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


ROE_MIN = 15.0
DEBT_TO_ASSETS_MAX = 50.0
PROFIT_GROWTH_MIN = 15.0
CONTRACT_LIABILITY_GROWTH_GOOD = 20.0
# 上一报告期余额不为正，或本期相对其增长达到 10 倍时，提示环比基数敏感。
CONTRACT_LIABILITY_QOQ_LOW_BASE_PCT = 1000.0
INDUSTRY_SHADOW_VERSION = "monthly_financial_industry_shadow_v1"

_FINANCIAL_INDUSTRY_TOKENS = ("银行", "证券", "保险", "多元金融")
_CONTRACT_SPECIAL_CONTEXT_TOKENS = (
    "房地产",
    "房屋建设",
    "基础建设",
    "专业工程",
    "装修装饰",
    "工程咨询",
)
_CONTRACT_CORE_TOKENS = (
    "软件开发",
    "IT服务",
    "计算机设备",
    "专用设备",
    "通用设备",
    "自动化设备",
    "旅游及景区",
    "酒店餐饮",
    "教育",
)
_RD_CORE_TOKENS = (
    "半导体",
    "软件开发",
    "IT服务",
    "计算机设备",
    "通信设备",
    "光学光电子",
    "消费电子",
    "元件",
    "电子化学品",
    "医疗器械",
    "化学制药",
    "生物制品",
    "医疗服务",
    "专用设备",
    "通用设备",
    "自动化设备",
    "军工电子",
    "航空装备",
    "航天装备",
    "汽车零部件",
    "电池",
    "光伏设备",
    "风电设备",
    "电网设备",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _growth_pct(current: Any, previous: Any) -> float | None:
    current_number = _number(current)
    previous_number = _number(previous)
    if current_number is None or previous_number is None or previous_number <= 0:
        return None
    return round((current_number - previous_number) / previous_number * 100.0, 4)


def _ratio_pct(numerator: Any, denominator: Any) -> float | None:
    numerator_number = _number(numerator)
    denominator_number = _number(denominator)
    if numerator_number is None or denominator_number is None or denominator_number <= 0:
        return None
    return round(numerator_number / denominator_number * 100.0, 4)


def _difference(current: Any, previous: Any) -> float | None:
    current_number = _number(current)
    previous_number = _number(previous)
    if current_number is None or previous_number is None:
        return None
    return round(current_number - previous_number, 4)


def _normalize_industry(value: Any) -> str:
    text = "".join(str(value or "").split())
    if not text or text.lower() in {
        "未提供",
        "未分类",
        "行业冲突",
        "unknown",
        "none",
        "null",
    }:
        return ""
    return text


def _contains_any(industry: str, tokens: tuple[str, ...]) -> bool:
    return any(token in industry for token in tokens)


def _profile(applicability: str, reason: str) -> dict[str, str]:
    return {"applicability": applicability, "reason": reason}


def classify_industry_factors(industry: Any) -> dict[str, Any]:
    """给合同负债与研发费用分配行业解释模板，不生成分数或硬门。"""
    normalized = _normalize_industry(industry)
    if not normalized:
        unknown = _profile("unknown", "行业证据缺失，不能选择行业解释模板")
        return {
            "industry": "未分类",
            "version": INDUSTRY_SHADOW_VERSION,
            "contract_liability": dict(unknown),
            "rd_exp": dict(unknown),
        }

    if _contains_any(normalized, _FINANCIAL_INDUSTRY_TOKENS):
        contract = _profile(
            "not_applicable",
            "金融行业不以合同负债作为订单或预收款代理",
        )
        rd_exp = _profile(
            "not_applicable",
            "金融行业研发费用口径缺乏横向可比性",
        )
    else:
        if _contains_any(normalized, _CONTRACT_SPECIAL_CONTEXT_TOKENS):
            contract = _profile(
                "special_context",
                "需结合交付、现金和债务审视，增长不能机械视作利好",
            )
        elif _contains_any(normalized, _CONTRACT_CORE_TOKENS):
            contract = _profile(
                "core",
                "预收或订单模式较常见，需继续核对收入和现金流兑现",
            )
        else:
            contract = _profile(
                "secondary",
                "仅作订单或需求辅助证据，不因余额或增速单独加分",
            )

        if _contains_any(normalized, _RD_CORE_TOKENS):
            rd_exp = _profile(
                "core",
                "技术与产品迭代相关，需结合研发强度、连续性和成果转化",
            )
        else:
            rd_exp = _profile(
                "secondary",
                "研发费用不是该行业统一核心门槛，不因投入偏低直接扣分",
            )

    return {
        "industry": normalized,
        "version": INDUSTRY_SHADOW_VERSION,
        "contract_liability": contract,
        "rd_exp": rd_exp,
    }


def _assessment_point(assessment: Any, factor: str) -> dict[str, Any] | None:
    if not isinstance(assessment, Mapping):
        return None
    context = assessment.get("context")
    if not isinstance(context, Mapping):
        return None
    if factor == "contract_liability":
        value_key = "contract_liability"
        growth_key = "contract_liability_growth_pct"
        ratio_key = "contract_liability_to_revenue_pct"
    elif factor == "rd_exp":
        value_key = "rd_exp"
        growth_key = "rd_exp_growth_pct"
        ratio_key = "rd_exp_to_revenue_pct"
    else:  # pragma: no cover - internal programming guard
        raise ValueError(f"unknown industry shadow factor: {factor}")
    point = {
        "report_period": str(assessment.get("report_period") or ""),
        "value": _number(context.get(value_key)),
        "growth_pct": _number(context.get(growth_key)),
        "to_revenue_pct": _number(context.get(ratio_key)),
    }
    if factor == "contract_liability":
        low_base = context.get("contract_liability_qoq_low_base")
        point.update(
            {
                "qoq_pct": _number(
                    context.get("contract_liability_qoq_pct")
                ),
                "qoq_delta": _number(
                    context.get("contract_liability_qoq_delta")
                ),
                "qoq_prior_value": _number(
                    context.get("contract_liability_qoq_prior_value")
                ),
                "qoq_prior_period": str(
                    context.get("contract_liability_qoq_prior_period") or ""
                ),
                "qoq_low_base": low_base if isinstance(low_base, bool) else None,
            }
        )
    return point


def _factor_shadow(
    profile: Mapping[str, str],
    *,
    latest: Any,
    annual: Any,
    factor: str,
) -> dict[str, Any]:
    latest_point = _assessment_point(latest, factor)
    annual_point = _assessment_point(annual, factor)
    applicability = str(profile.get("applicability") or "unknown")
    if applicability == "not_applicable":
        status = "not_applicable"
    else:
        available = any(
            point
            and any(
                point.get(key) is not None
                for key in ("value", "growth_pct", "to_revenue_pct")
            )
            for point in (latest_point, annual_point)
        )
        status = "available" if available else "missing"
    return {
        **dict(profile),
        "status": status,
        "latest": latest_point,
        "annual": annual_point,
    }


def build_industry_shadow(financial_view: Any, industry: Any) -> dict[str, Any]:
    """生成只展示、不计分的行业增强证据卡；不修改传入财务视图。"""
    view = financial_view if isinstance(financial_view, Mapping) else {}
    latest = view.get("latest")
    annual = view.get("annual")
    if latest is None and isinstance(view.get("context"), Mapping):
        latest = view
    profiles = classify_industry_factors(industry)
    return {
        "version": profiles["version"],
        "industry": profiles["industry"],
        "scoring_effect": "display_only",
        "has_assessment": isinstance(latest, Mapping) or isinstance(annual, Mapping),
        "contract_liability": _factor_shadow(
            profiles["contract_liability"],
            latest=latest,
            annual=annual,
            factor="contract_liability",
        ),
        "rd_exp": _factor_shadow(
            profiles["rd_exp"],
            latest=latest,
            annual=annual,
            factor="rd_exp",
        ),
    }


def _period_kind(report_period: str) -> str:
    compact = str(report_period or "").replace("-", "")
    if compact.endswith("1231"):
        return "annual"
    if compact.endswith("0930"):
        return "q3"
    if compact.endswith("0630"):
        return "half_year"
    if compact.endswith("0331"):
        return "q1"
    return "unknown"


def evaluate_financial_snapshot(
    snapshot: dict,
    *,
    prior_same_period: dict | None = None,
    prior_period: dict | None = None,
) -> dict:
    """评价一个公告日可见的合并财务快照。

    年报通过核心门槛记为 ``verified``；中报只可作为 ``pre_screen``，避免把年化
    指标伪装成年报事实。核心字段缺失记 ``insufficient``，明确不达标才记
    ``failed``。
    """
    indicator = snapshot.get("fina_indicator") or {}
    balance = snapshot.get("balancesheet") or {}
    income = snapshot.get("income") or {}
    prior = prior_same_period or {}
    prior_indicator = prior.get("fina_indicator") or {}
    prior_balance = prior.get("balancesheet") or {}
    prior_income = prior.get("income") or {}
    sequential_prior = prior_period or {}
    sequential_prior_balance = sequential_prior.get("balancesheet") or {}

    report_period = str(snapshot.get("report_period") or snapshot.get("end_date") or "")
    period_kind = _period_kind(report_period)
    if period_kind == "annual":
        roe = _number(indicator.get("roe_waa"))
        roe_basis = "annual_roe_waa"
    else:
        roe = _number(indicator.get("roe_yearly"))
        roe_basis = "interim_roe_yearly"

    debt = _number(indicator.get("debt_to_assets"))
    netprofit_yoy = _number(indicator.get("netprofit_yoy"))
    dt_netprofit_yoy = _number(indicator.get("dt_netprofit_yoy"))

    hard_gates = {
        "roe": None if roe is None else roe > ROE_MIN,
        "debt_to_assets": None if debt is None else debt < DEBT_TO_ASSETS_MAX,
        "netprofit_yoy": (
            None if netprofit_yoy is None else netprofit_yoy > PROFIT_GROWTH_MIN
        ),
        "dt_netprofit_yoy": (
            None
            if dt_netprofit_yoy is None
            else dt_netprofit_yoy > PROFIT_GROWTH_MIN
        ),
    }
    missing_fields = [name for name, passed in hard_gates.items() if passed is None]
    core_passed = not missing_fields and all(hard_gates.values())
    if missing_fields:
        status = "insufficient"
    elif not core_passed:
        status = "failed"
    elif period_kind == "annual":
        status = "verified"
    else:
        status = "pre_screen"

    contract_liability = balance.get("contract_liab")
    contract_growth = _growth_pct(
        contract_liability,
        prior_balance.get("contract_liab"),
    )
    sequential_prior_period = str(
        sequential_prior.get("report_period")
        or sequential_prior.get("end_date")
        or ""
    )
    if sequential_prior_period == report_period:
        sequential_prior_period = ""
        sequential_contract_liability = None
    else:
        sequential_contract_liability = sequential_prior_balance.get(
            "contract_liab"
        )
    contract_qoq = _growth_pct(
        contract_liability,
        sequential_contract_liability,
    )
    contract_qoq_delta = _difference(
        contract_liability,
        sequential_contract_liability,
    )
    current_contract_number = _number(contract_liability)
    prior_contract_number = _number(sequential_contract_liability)
    if current_contract_number is None or prior_contract_number is None:
        contract_qoq_low_base = None
    elif prior_contract_number <= 0:
        contract_qoq_low_base = current_contract_number > 0
    else:
        contract_qoq_low_base = bool(
            contract_qoq is not None
            and contract_qoq >= CONTRACT_LIABILITY_QOQ_LOW_BASE_PCT
        )
    rd_exp = (
        indicator.get("rd_exp")
        if _number(indicator.get("rd_exp")) is not None
        else income.get("rd_exp")
    )
    prior_rd_exp = (
        prior_indicator.get("rd_exp")
        if _number(prior_indicator.get("rd_exp")) is not None
        else prior_income.get("rd_exp")
    )
    rd_growth = _growth_pct(rd_exp, prior_rd_exp)
    revenue = (
        income.get("revenue")
        if _number(income.get("revenue")) is not None
        else income.get("total_revenue")
    )
    context = {
        "contract_liability": _number(contract_liability),
        "contract_liability_growth_pct": contract_growth,
        "contract_liability_growth_ge_20": (
            None
            if contract_growth is None
            else contract_growth >= CONTRACT_LIABILITY_GROWTH_GOOD
        ),
        "contract_liability_to_revenue_pct": _ratio_pct(
            contract_liability,
            revenue,
        ),
        "contract_liability_qoq_pct": contract_qoq,
        "contract_liability_qoq_delta": contract_qoq_delta,
        "contract_liability_qoq_prior_value": prior_contract_number,
        "contract_liability_qoq_prior_period": sequential_prior_period,
        "contract_liability_qoq_low_base": contract_qoq_low_base,
        "revenue": _number(revenue),
        "rd_exp": _number(rd_exp),
        "rd_exp_growth_pct": rd_growth,
        "rd_exp_increasing": None if rd_growth is None else rd_growth > 0,
        "rd_exp_to_revenue_pct": _ratio_pct(rd_exp, revenue),
        # balance-sheet goodwill is a book balance, not proof that impairment is zero.
        "goodwill_balance": _number(balance.get("goodwill")),
        "asset_impairment_loss": _number(income.get("assets_impair_loss")),
        "accounts_receivable": _number(balance.get("accounts_receiv")),
        "inventories": _number(balance.get("inventories")),
    }
    return {
        "status": status,
        "core_passed": core_passed,
        "period_kind": period_kind,
        "report_period": report_period,
        "financial_ann_date": snapshot.get("financial_ann_date"),
        "roe_basis": roe_basis,
        "values": {
            "roe": roe,
            "debt_to_assets": debt,
            "netprofit_yoy": netprofit_yoy,
            "dt_netprofit_yoy": dt_netprofit_yoy,
        },
        "hard_gates": hard_gates,
        "missing_fields": missing_fields,
        "context": context,
    }
