"""月线模式的财务硬门与上下文证据。

这里只做可重复的数值判断。合同负债、研发、商誉余额、应收和存货有明显行业差异，
只作为证据展示，不抬高或否决核心财务门槛。
"""
from __future__ import annotations

import math
from typing import Any


ROE_MIN = 15.0
DEBT_TO_ASSETS_MAX = 50.0
PROFIT_GROWTH_MIN = 15.0
CONTRACT_LIABILITY_GROWTH_GOOD = 20.0


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

    contract_growth = _growth_pct(
        balance.get("contract_liab"),
        prior_balance.get("contract_liab"),
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
    context = {
        "contract_liability": _number(balance.get("contract_liab")),
        "contract_liability_growth_pct": contract_growth,
        "contract_liability_growth_ge_20": (
            None
            if contract_growth is None
            else contract_growth >= CONTRACT_LIABILITY_GROWTH_GOOD
        ),
        "rd_exp": _number(rd_exp),
        "rd_exp_growth_pct": rd_growth,
        "rd_exp_increasing": None if rd_growth is None else rd_growth > 0,
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
