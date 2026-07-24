from __future__ import annotations

from services.monthly_pattern.financials import evaluate_financial_snapshot


def _snapshot(
    *,
    period: str = "2025-12-31",
    roe_waa: float | None = 18.0,
    roe_yearly: float | None = None,
    debt: float | None = 42.0,
    netprofit_yoy: float | None = 26.0,
    dt_netprofit_yoy: float | None = 22.0,
    contract_liab: float | None = 120.0,
    rd_exp: float | None = 12.0,
) -> dict:
    return {
        "ts_code": "600000.SH",
        "report_period": period,
        "financial_ann_date": "2026-03-28",
        "fina_indicator": {
            "ann_date": "2026-03-28",
            "roe_waa": roe_waa,
            "roe_yearly": roe_yearly,
            "debt_to_assets": debt,
            "netprofit_yoy": netprofit_yoy,
            "dt_netprofit_yoy": dt_netprofit_yoy,
            "rd_exp": rd_exp,
        },
        "balancesheet": {
            "ann_date": "2026-03-28",
            "contract_liab": contract_liab,
            "goodwill": 3.0,
            "accounts_receiv": 20.0,
            "inventories": 8.0,
        },
        "income": {
            "ann_date": "2026-03-28",
            "assets_impair_loss": 0.0,
        },
    }


def test_annual_core_gates_pass_as_verified() -> None:
    result = evaluate_financial_snapshot(_snapshot())

    assert result["status"] == "verified"
    assert result["core_passed"] is True
    assert result["hard_gates"] == {
        "roe": True,
        "debt_to_assets": True,
        "netprofit_yoy": True,
        "dt_netprofit_yoy": True,
    }
    assert result["roe_basis"] == "annual_roe_waa"


def test_thresholds_are_strict_not_greater_or_equal() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(roe_waa=15.0, debt=50.0, netprofit_yoy=15.0, dt_netprofit_yoy=15.0)
    )

    assert result["status"] == "failed"
    assert result["hard_gates"] == {
        "roe": False,
        "debt_to_assets": False,
        "netprofit_yoy": False,
        "dt_netprofit_yoy": False,
    }


def test_missing_core_value_is_insufficient_not_failed() -> None:
    result = evaluate_financial_snapshot(_snapshot(dt_netprofit_yoy=None))

    assert result["status"] == "insufficient"
    assert result["core_passed"] is False
    assert result["hard_gates"]["dt_netprofit_yoy"] is None
    assert result["missing_fields"] == ["dt_netprofit_yoy"]


def test_q3_uses_annualized_roe_but_remains_pre_screen() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(period="2025-09-30", roe_waa=12.0, roe_yearly=17.0)
    )

    assert result["status"] == "pre_screen"
    assert result["core_passed"] is True
    assert result["roe_basis"] == "interim_roe_yearly"


def test_context_growth_is_evidence_not_a_hard_gate() -> None:
    current = _snapshot(contract_liab=90.0, rd_exp=9.0)
    prior = _snapshot(
        period="2024-12-31",
        contract_liab=100.0,
        rd_exp=10.0,
    )

    result = evaluate_financial_snapshot(current, prior_same_period=prior)

    assert result["status"] == "verified"
    assert result["core_passed"] is True
    assert result["context"]["contract_liability_growth_pct"] == -10.0
    assert result["context"]["contract_liability_growth_ge_20"] is False
    assert result["context"]["rd_exp_increasing"] is False
    assert "goodwill_balance" in result["context"]
    assert "goodwill_impairment_zero" not in result["context"]


def test_rd_exp_can_fall_back_to_income_statement() -> None:
    current = _snapshot(rd_exp=None)
    current["income"]["rd_exp"] = 12.0
    prior = _snapshot(period="2024-12-31", rd_exp=None)
    prior["income"]["rd_exp"] = 10.0

    result = evaluate_financial_snapshot(current, prior_same_period=prior)

    assert result["context"]["rd_exp"] == 12.0
    assert result["context"]["rd_exp_growth_pct"] == 20.0
    assert result["context"]["rd_exp_increasing"] is True
