from __future__ import annotations

from services.monthly_pattern import focus


def _financial(*, verified: bool = True) -> dict:
    status = "verified" if verified else "failed"
    return {
        "status": status,
        "verified": verified,
        "report_period": "2026-03-31",
        "financial_ann_date": "2026-04-30",
        "latest": {
            "status": "pre_screen" if verified else "failed",
            "report_period": "2026-03-31",
            "values": {
                "roe": 30.0,
                "debt_to_assets": 25.0,
                "netprofit_yoy": 60.0,
                "dt_netprofit_yoy": 60.0,
            },
            "context": {
                "contract_liability": 300_000_000.0,
                "contract_liability_growth_pct": 30.0,
                "contract_liability_qoq_pct": 20.0,
                "contract_liability_to_revenue_pct": 10.0,
                "rd_exp": 100_000_000.0,
                "rd_exp_growth_pct": 25.0,
                "rd_exp_to_revenue_pct": 8.0,
            },
        },
        "annual": {
            "status": status,
            "report_period": "2025-12-31",
            "values": {"roe": 30.0},
            "context": {},
        },
    }


def _candidate(
    code: str,
    *,
    strategy: str = "fundamental_monthly_trend",
    industry: str = "通信设备",
    status: str = "fundamental_verified",
    verified: bool = True,
    percentile: float | None = 10.0,
) -> dict:
    valuation = (
        {
            "status": "success",
            "as_of_date": "2026-07-31",
            "metric": "pe_ttm",
            "value": 20.0,
            "industry_percentile": percentile,
            "industry_sample_size": 50,
        }
        if percentile is not None
        else {"status": "missing"}
    )
    return {
        "stock_code": code,
        "stock_name": f"样本{code}",
        "strategy_type": strategy,
        "pool_status": status,
        "signal_month": "2026-06",
        "industry": industry,
        "financial_evidence": _financial(verified=verified),
        "source_meta": {
            "industry": industry,
            "industry_map": "success",
            "mainline": {
                "status": "ok",
                "required_hits": 2,
                "hit_counts": {industry: 3},
            },
            "valuation": valuation,
        },
    }


def test_score_stock_uses_six_explainable_dimensions() -> None:
    records = [
        _candidate("300001", strategy="fundamental_monthly_trend"),
        _candidate("300001", strategy="theme_monthly_attack"),
        _candidate("300001", strategy="monthly_reacceleration"),
    ]

    scored = focus.score_stock(records)

    assert scored is not None
    assert scored["score"] == 100.0
    assert scored["breakdown"] == {
        "technical": 30.0,
        "mainline": 25.0,
        "fundamental": 20.0,
        "valuation": 10.0,
        "industry_factors": 10.0,
        "data_quality": 5.0,
    }
    assert scored["mainline_match"] is True
    assert scored["priority_eligible"] is True


def test_unverified_stock_is_backend_candidate_not_focus_candidate() -> None:
    assert focus.score_stock([_candidate("300001", verified=False)]) is None


def test_missing_or_not_applicable_industry_factors_are_not_failures() -> None:
    bank = _candidate("600000", industry="股份制银行Ⅱ", percentile=30.0)
    scored = focus.score_stock([bank])

    assert scored is not None
    assert scored["breakdown"]["industry_factors"] == 0.0
    assert scored["industry_shadow"]["contract_liability"]["status"] == "not_applicable"
    assert scored["industry_shadow"]["rd_exp"]["status"] == "not_applicable"


def test_missing_valuation_is_zero_not_cross_industry_guess() -> None:
    scored = focus.score_stock([_candidate("300001", percentile=None)])

    assert scored is not None
    assert scored["breakdown"]["valuation"] == 0.0
    assert scored["breakdown"]["data_quality"] == 4.0


def test_focus_funnel_dedupes_limits_and_excludes_risk_from_priority() -> None:
    candidates = []
    for index in range(1, 13):
        code = f"300{index:03d}"
        candidates.append(_candidate(code, percentile=float(index * 5)))
        if index == 1:
            candidates.append(
                _candidate(
                    code,
                    strategy="monthly_reacceleration",
                    status="risk",
                    percentile=5.0,
                )
            )
    candidates.append(_candidate("600999", verified=False))

    funnel = focus.build_focus_funnel(candidates)

    assert funnel["input_stocks"] == 13
    assert funnel["verified_stocks"] == 12
    assert len(funnel["focus"]) == 10
    assert len(funnel["priority"]) == 3
    assert funnel["omitted_verified"] == 2
    assert funnel["focus"][0]["stock_code"] == "300001"
    assert "300001" not in {item["stock_code"] for item in funnel["priority"]}


def test_focus_order_is_stable_for_reversed_input() -> None:
    candidates = [
        _candidate("300002", percentile=20.0),
        _candidate("300001", percentile=20.0),
    ]

    forward = focus.build_focus_funnel(candidates)
    reversed_result = focus.build_focus_funnel(list(reversed(candidates)))

    assert [item["stock_code"] for item in forward["focus"]] == [
        "300001",
        "300002",
    ]
    assert forward == reversed_result


def test_priority_searches_beyond_top10_when_higher_scores_have_risk() -> None:
    candidates = []
    for index in range(1, 11):
        code = f"300{index:03d}"
        candidates.extend(
            [
                _candidate(code, percentile=5.0),
                _candidate(
                    code,
                    strategy="monthly_reacceleration",
                    status="risk",
                    percentile=5.0,
                ),
            ]
        )
    for index in range(11, 14):
        candidates.append(_candidate(f"300{index:03d}", percentile=95.0))

    funnel = focus.build_focus_funnel(candidates)

    assert all(item["has_risk"] for item in funnel["focus"])
    assert [item["stock_code"] for item in funnel["priority"]] == [
        "300011",
        "300012",
        "300013",
    ]
    assert funnel["priority_eligible_stocks"] == 3
