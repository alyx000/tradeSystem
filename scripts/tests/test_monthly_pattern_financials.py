from __future__ import annotations

from services.monthly_pattern.financials import (
    build_industry_shadow,
    classify_industry_factors,
    evaluate_financial_snapshot,
)


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
    revenue: float | None = 100.0,
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
            "revenue": revenue,
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


def test_context_adds_revenue_normalized_shadow_ratios_without_changing_gates() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(contract_liab=20.0, rd_exp=12.0, revenue=100.0)
    )

    assert result["status"] == "verified"
    assert result["core_passed"] is True
    assert result["context"]["revenue"] == 100.0
    assert result["context"]["contract_liability_to_revenue_pct"] == 20.0
    assert result["context"]["rd_exp_to_revenue_pct"] == 12.0


def test_contract_liability_qoq_uses_previous_report_period_and_stays_shadow_only() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(
            period="2026-03-31",
            roe_waa=None,
            roe_yearly=18.0,
            contract_liab=120.0,
        ),
        prior_same_period=_snapshot(
            period="2025-03-31",
            contract_liab=100.0,
        ),
        prior_period=_snapshot(
            period="2025-12-31",
            contract_liab=40.0,
        ),
    )

    assert result["status"] == "pre_screen"
    assert result["core_passed"] is True
    assert result["context"]["contract_liability_growth_pct"] == 20.0
    assert result["context"]["contract_liability_qoq_pct"] == 200.0
    assert result["context"]["contract_liability_qoq_delta"] == 80.0
    assert result["context"]["contract_liability_qoq_prior_value"] == 40.0
    assert (
        result["context"]["contract_liability_qoq_prior_period"]
        == "2025-12-31"
    )
    assert result["context"]["contract_liability_qoq_low_base"] is False


def test_contract_liability_qoq_zero_base_has_delta_but_no_infinite_rate() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(period="2026-03-31", contract_liab=20.0),
        prior_period=_snapshot(period="2025-12-31", contract_liab=0.0),
    )

    assert result["context"]["contract_liability_qoq_pct"] is None
    assert result["context"]["contract_liability_qoq_delta"] == 20.0
    assert result["context"]["contract_liability_qoq_low_base"] is True


def test_contract_liability_qoq_rejects_same_period_comparison() -> None:
    result = evaluate_financial_snapshot(
        _snapshot(period="2026-03-31", contract_liab=120.0),
        prior_period=_snapshot(period="2026-03-31", contract_liab=40.0),
    )

    assert result["context"]["contract_liability_qoq_pct"] is None
    assert result["context"]["contract_liability_qoq_delta"] is None
    assert result["context"]["contract_liability_qoq_prior_period"] == ""
    assert result["context"]["contract_liability_qoq_low_base"] is None


def test_industry_factor_templates_are_conservative_and_explainable() -> None:
    semiconductor = classify_industry_factors("半导体")
    communication = classify_industry_factors("通信设备")
    software = classify_industry_factors("软件开发")
    property_developer = classify_industry_factors("房地产开发")
    bank = classify_industry_factors("股份制银行Ⅱ")
    unknown = classify_industry_factors("未分类")

    assert semiconductor["contract_liability"]["applicability"] == "secondary"
    assert semiconductor["rd_exp"]["applicability"] == "core"
    assert communication["contract_liability"]["applicability"] == "secondary"
    assert communication["rd_exp"]["applicability"] == "core"
    assert software["contract_liability"]["applicability"] == "core"
    assert software["rd_exp"]["applicability"] == "core"
    assert (
        property_developer["contract_liability"]["applicability"]
        == "special_context"
    )
    assert bank["contract_liability"]["applicability"] == "not_applicable"
    assert bank["rd_exp"]["applicability"] == "not_applicable"
    assert unknown["contract_liability"]["applicability"] == "unknown"
    assert unknown["rd_exp"]["applicability"] == "unknown"


def test_industry_shadow_keeps_missing_and_not_applicable_out_of_failure_semantics() -> None:
    current = evaluate_financial_snapshot(
        _snapshot(contract_liab=None, rd_exp=None, revenue=100.0)
    )
    semiconductor = build_industry_shadow(
        {"latest": current, "annual": current},
        "半导体",
    )
    bank = build_industry_shadow(
        {"latest": current, "annual": current},
        "股份制银行Ⅱ",
    )

    assert semiconductor["scoring_effect"] == "display_only"
    assert semiconductor["contract_liability"]["status"] == "missing"
    assert semiconductor["rd_exp"]["status"] == "missing"
    assert bank["contract_liability"]["status"] == "not_applicable"
    assert bank["rd_exp"]["status"] == "not_applicable"
    assert "failed" not in {
        semiconductor["contract_liability"]["status"],
        semiconductor["rd_exp"]["status"],
        bank["contract_liability"]["status"],
        bank["rd_exp"]["status"],
    }


def test_industry_shadow_uses_latest_and_annual_same_period_evidence() -> None:
    latest = evaluate_financial_snapshot(
        _snapshot(
            period="2026-03-31",
            roe_waa=None,
            roe_yearly=18.0,
            contract_liab=120.0,
            rd_exp=18.0,
            revenue=200.0,
        ),
        prior_same_period=_snapshot(
            period="2025-03-31",
            contract_liab=100.0,
            rd_exp=15.0,
            revenue=160.0,
        ),
    )
    annual = evaluate_financial_snapshot(
        _snapshot(contract_liab=300.0, rd_exp=40.0, revenue=500.0),
        prior_same_period=_snapshot(
            period="2024-12-31",
            contract_liab=250.0,
            rd_exp=32.0,
            revenue=420.0,
        ),
    )

    shadow = build_industry_shadow(
        {"latest": latest, "annual": annual},
        "通信设备",
    )

    assert shadow["contract_liability"]["status"] == "available"
    assert shadow["contract_liability"]["latest"] == {
        "report_period": "2026-03-31",
        "value": 120.0,
        "growth_pct": 20.0,
        "to_revenue_pct": 60.0,
        "qoq_pct": None,
        "qoq_delta": None,
        "qoq_prior_value": None,
        "qoq_prior_period": "",
        "qoq_low_base": None,
    }
    assert shadow["rd_exp"]["annual"] == {
        "report_period": "2025-12-31",
        "value": 40.0,
        "growth_pct": 25.0,
        "to_revenue_pct": 8.0,
    }
