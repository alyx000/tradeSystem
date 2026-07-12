from fractions import Fraction
import json

import pytest

from services import trinity_factor
from services.trinity_factor.constants import FACTOR_CODES, FACTOR_WEIGHTS, SECTOR_WEIGHTS
from services.trinity_factor.scoring import score_factor, score_sector
from services.trinity_factor.selection import select_dominant_factors


FACTOR_DIMENSIONS = {
    "current_dominance": 5,
    "cross_layer_alignment": 5,
    "rhythm_clarity": 5,
    "next_stage_relevance": 5,
    "counterevidence": 0,
}

SECTOR_DIMENSIONS = {
    "primary_factor_alignment": 0,
    "stage_connection": 0,
    "market_linkage": 0,
    "leader_clarity": 0,
    "logic_aesthetic": 0,
    "expectation_gap": 0,
    "fully_priced_penalty": 0,
}


def test_package_exports_the_reusable_pure_logic_api():
    assert trinity_factor.score_factor is score_factor
    assert trinity_factor.score_sector is score_sector
    assert trinity_factor.select_dominant_factors is select_dominant_factors
    assert callable(trinity_factor.parse_factor_response)
    assert callable(trinity_factor.parse_sector_response)
    assert issubclass(trinity_factor.TrinityValidationError, ValueError)
    assert callable(trinity_factor.AntigravityStructuredRunner)
    assert trinity_factor.FACTOR_PROMPT_VERSION == "trinity_factor_score_v1"
    assert trinity_factor.SECTOR_PROMPT_VERSION == "trinity_sector_score_v1"


def test_factor_whitelist_is_exact():
    assert FACTOR_CODES == {
        "market_node",
        "sector_rhythm",
        "style_regime",
        "leader_signal",
    }


def test_score_factor_recomputes_total_and_preserves_raw_and_capped_scores():
    result = score_factor(
        factor_code="market_node",
        dimension_scores={
            "current_dominance": 5,
            "cross_layer_alignment": 4,
            "rhythm_clarity": 3,
            "next_stage_relevance": 2,
            "counterevidence": 1,
        },
        evidence_quality=4,
        caps={"cross_layer_alignment": 3},
        critical_missing=True,
    )

    assert result["model_scores"]["cross_layer_alignment"] == 4
    assert result["normalized_scores"]["cross_layer_alignment"] == 3
    assert result["evidence_quality"] == 4
    assert result["critical_missing"] is True
    assert result["total_score"] == pytest.approx(67.0)


def test_score_factor_clips_negative_program_total_to_zero():
    scores = dict(FACTOR_DIMENSIONS, current_dominance=0, cross_layer_alignment=0,
                  rhythm_clarity=0, next_stage_relevance=0, counterevidence=5)

    result = score_factor(
        factor_code="market_node",
        dimension_scores=scores,
        evidence_quality=0,
    )

    assert result["total_score"] == 0.0


@pytest.mark.parametrize("invalid_score", [True, 2.5, -1, 6])
def test_score_factor_rejects_non_integer_bool_or_out_of_range_model_score(invalid_score):
    scores = dict(FACTOR_DIMENSIONS, current_dominance=invalid_score)

    with pytest.raises(ValueError):
        score_factor(
            factor_code="market_node",
            dimension_scores=scores,
            evidence_quality=3,
        )


def test_score_factor_requires_boolean_critical_missing():
    with pytest.raises(ValueError):
        score_factor(
            factor_code="market_node",
            dimension_scores=FACTOR_DIMENSIONS,
            evidence_quality=3,
            critical_missing="no",
        )


@pytest.mark.parametrize(
    ("scorer", "kwargs"),
    [
        (
            score_factor,
            {
                "factor_code": "market_node",
                "dimension_scores": list(FACTOR_WEIGHTS),
                "evidence_quality": 3,
            },
        ),
        (
            score_sector,
            {
                "sector_key": "sw2:test",
                "dimension_scores": list(SECTOR_WEIGHTS),
            },
        ),
    ],
)
def test_public_scorers_reject_non_mapping_dimension_scores(scorer, kwargs):
    with pytest.raises(ValueError, match="mapping"):
        scorer(**kwargs)


def test_score_sector_recomputes_total_applies_cap_and_assigns_tier():
    result = score_sector(
        sector_key="sw2:半导体",
        dimension_scores={
            "primary_factor_alignment": 5,
            "stage_connection": 4,
            "market_linkage": 3,
            "leader_clarity": 2,
            "logic_aesthetic": 1,
            "expectation_gap": 5,
            "fully_priced_penalty": 4,
        },
        caps={"fully_priced_penalty": 2},
    )

    assert result["model_scores"]["fully_priced_penalty"] == 4
    assert result["normalized_scores"]["fully_priced_penalty"] == 2
    assert result["total_score"] == pytest.approx(63.0)
    assert result["tier"] == "watch"


@pytest.mark.parametrize(
    ("updates", "expected_total", "expected_tier"),
    [
        ({"primary_factor_alignment": 5, "stage_connection": 5,
          "market_linkage": 5, "logic_aesthetic": 5}, 75.0, "priority"),
        ({"primary_factor_alignment": 5, "stage_connection": 5,
          "leader_clarity": 5}, 60.0, "watch"),
        ({"primary_factor_alignment": 5, "stage_connection": 5,
          "leader_clarity": 4, "logic_aesthetic": 1}, 59.0, "deprioritized"),
    ],
)
def test_score_sector_tier_boundaries(updates, expected_total, expected_tier):
    result = score_sector(
        sector_key="sw2:test",
        dimension_scores=dict(SECTOR_DIMENSIONS, **updates),
    )

    assert result["total_score"] == expected_total
    assert result["tier"] == expected_tier


def test_score_sector_clips_negative_total_and_rejects_bool_score():
    negative = score_sector(
        sector_key="sw2:test",
        dimension_scores=dict(SECTOR_DIMENSIONS, fully_priced_penalty=5),
    )
    assert negative["total_score"] == 0.0

    with pytest.raises(ValueError):
        score_sector(
            sector_key="sw2:test",
            dimension_scores=dict(SECTOR_DIMENSIONS, leader_clarity=True),
        )


def _scored_factor(
    factor_code,
    total_score,
    *,
    current_dominance=5,
    rhythm_clarity=5,
    counterevidence=1,
    evidence_quality=4,
    critical_missing=False,
):
    fixed_score = (
        6 * current_dominance
        + 4 * rhythm_clarity
        - 4 * counterevidence
        + 2 * evidence_quality
    )
    flexible_score = total_score - fixed_score
    assert 0 <= flexible_score <= 40
    cross_layer_alignment = min(5, flexible_score / 5)
    next_stage_relevance = (flexible_score - 5 * cross_layer_alignment) / 3
    return {
        "factor_code": factor_code,
        "total_score": total_score,
        "normalized_scores": {
            "current_dominance": current_dominance,
            "cross_layer_alignment": cross_layer_alignment,
            "rhythm_clarity": rhythm_clarity,
            "next_stage_relevance": next_stage_relevance,
            "counterevidence": counterevidence,
        },
        "evidence_quality": evidence_quality,
        "critical_missing": critical_missing,
    }


def test_select_dominant_factor_high_with_at_most_two_supporting_factors():
    result = select_dominant_factors([
        _scored_factor("style_regime", 63),
        _scored_factor("leader_signal", 60),
        _scored_factor("market_node", 88),
        _scored_factor("sector_rhythm", 70),
    ])

    assert result["primary"]["factor_code"] == "market_node"
    assert result["confidence"] == "high"
    assert [item["factor_code"] for item in result["supporting"]] == [
        "sector_rhythm",
        "style_regime",
    ]
    assert result["undetermined_reason"] is None
    assert result["judgement_label"] == "[判断]"


def test_single_eligible_factor_requires_total_at_least_75():
    below = select_dominant_factors([
        _scored_factor("market_node", 74),
        _scored_factor("sector_rhythm", 60),
    ])
    at_threshold = select_dominant_factors([
        _scored_factor("market_node", 75),
        _scored_factor("sector_rhythm", 60),
    ])

    assert below["primary"] is None
    assert below["undetermined_reason"] == "undetermined_weak"
    assert at_threshold["primary"]["factor_code"] == "market_node"
    assert at_threshold["confidence"] == "medium"

    only_scored = select_dominant_factors([
        _scored_factor("market_node", 75),
    ])
    assert only_scored["primary"]["factor_code"] == "market_node"


def test_single_eligible_factor_still_requires_eight_point_lead():
    result = select_dominant_factors([
        _scored_factor("market_node", 76),
        _scored_factor("sector_rhythm", 72, evidence_quality=2),
    ])

    assert result["primary"] is None
    assert result["undetermined_reason"] == "undetermined_competing"


@pytest.mark.parametrize(
    ("factors", "expected_reason"),
    [
        ([_scored_factor("market_node", 70, critical_missing=True)],
         "undetermined_missing_data"),
        ([_scored_factor("market_node", 80, evidence_quality=0)],
         "undetermined_missing_data"),
        ([_scored_factor("market_node", 80), _scored_factor("sector_rhythm", 75)],
         "undetermined_competing"),
        ([_scored_factor("market_node", 85, counterevidence=3)],
         "undetermined_conflicted"),
        ([_scored_factor("market_node", 69)], "undetermined_weak"),
    ],
)
def test_undetermined_reason_classification(factors, expected_reason):
    result = select_dominant_factors(factors)

    assert result["primary"] is None
    assert result["undetermined_reason"] == expected_reason


def test_market_node_lock_never_falls_through_to_another_factor():
    result = select_dominant_factors(
        [
            _scored_factor("sector_rhythm", 90),
            _scored_factor("market_node", 69),
        ],
        primary_category_lock="market_node",
    )

    assert result["primary"] is None
    assert result["undetermined_reason"] == "undetermined_weak"


def test_market_node_lock_without_market_node_is_missing_data():
    result = select_dominant_factors(
        [_scored_factor("sector_rhythm", 90)],
        primary_category_lock="market_node",
    )

    assert result["primary"] is None
    assert result["undetermined_reason"] == "undetermined_missing_data"


def test_critical_missing_downgrades_established_primary_from_high_to_medium():
    result = select_dominant_factors([
        _scored_factor("market_node", 88, critical_missing=True),
        _scored_factor("sector_rhythm", 70),
    ])

    assert result["primary"]["factor_code"] == "market_node"
    assert result["confidence"] == "medium"


def test_selection_rejects_duplicate_factor_codes():
    duplicate = _scored_factor("market_node", 80)

    with pytest.raises(ValueError, match="duplicate"):
        select_dominant_factors([duplicate, dict(duplicate)])


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [{"factor_code": "market_node"}],
        [{
            **_scored_factor("market_node", 80),
            "normalized_scores": [],
        }],
        [{
            **_scored_factor("market_node", 80),
            "factor_code": [],
        }],
    ],
)
def test_selection_rejects_malformed_inputs_with_value_error(malformed):
    with pytest.raises(ValueError):
        select_dominant_factors(malformed)


@pytest.mark.parametrize("field", ["total_score", "evidence_quality"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, 101])
def test_selection_rejects_nonfinite_or_out_of_range_program_values(field, invalid):
    item = _scored_factor("market_node", 80)
    item[field] = invalid if field == "total_score" else (6 if invalid == 101 else invalid)

    with pytest.raises(ValueError):
        select_dominant_factors([item])


@pytest.mark.parametrize("dimension", tuple(FACTOR_WEIGHTS))
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, 6])
def test_selection_rejects_invalid_normalized_score_in_any_dimension(dimension, invalid):
    item = _scored_factor("market_node", 80)
    item["normalized_scores"][dimension] = invalid

    with pytest.raises(ValueError):
        select_dominant_factors([item])


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_selection_requires_exact_normalized_score_dimensions(mutation):
    item = _scored_factor("market_node", 80)
    if mutation == "missing":
        item["normalized_scores"].pop("next_stage_relevance")
    else:
        item["normalized_scores"]["unexpected"] = 3

    with pytest.raises(ValueError):
        select_dominant_factors([item])


def test_selection_rejects_total_that_does_not_match_canonical_formula():
    tampered = _scored_factor("market_node", 72)
    tampered["total_score"] = 100

    with pytest.raises(ValueError, match="canonical"):
        select_dominant_factors([tampered])


def test_score_factor_output_flows_into_selection_without_total_drift():
    scored = score_factor(
        factor_code="market_node",
        dimension_scores={
            "current_dominance": 5,
            "cross_layer_alignment": 5,
            "rhythm_clarity": 5,
            "next_stage_relevance": 5,
            "counterevidence": 0,
        },
        evidence_quality=5,
    )

    result = select_dominant_factors([scored])

    assert result["primary"]["factor_code"] == "market_node"
    assert result["primary"]["total_score"] == 100.0


@pytest.mark.parametrize("invalid_caps", [False, [], (), "", 0, [("current_dominance", 3)]])
@pytest.mark.parametrize("scorer", [score_factor, score_sector])
def test_public_scorers_reject_non_mapping_caps(scorer, invalid_caps):
    kwargs = (
        {
            "factor_code": "market_node",
            "dimension_scores": FACTOR_DIMENSIONS,
            "evidence_quality": 3,
        }
        if scorer is score_factor
        else {
            "sector_key": "sw2:test",
            "dimension_scores": SECTOR_DIMENSIONS,
        }
    )

    with pytest.raises(ValueError, match="caps"):
        scorer(**kwargs, caps=invalid_caps)


def test_scorers_normalize_fraction_program_values_for_json_serialization():
    factor = score_factor(
        factor_code="market_node",
        dimension_scores=FACTOR_DIMENSIONS,
        evidence_quality=Fraction(7, 2),
        caps={"cross_layer_alignment": Fraction(5, 2)},
    )
    sector = score_sector(
        sector_key="sw2:test",
        dimension_scores=SECTOR_DIMENSIONS,
        caps={"market_linkage": Fraction(3, 2)},
    )

    json.dumps({"factor": factor, "sector": sector})
    assert isinstance(factor["evidence_quality"], (int, float))
    assert isinstance(factor["normalized_scores"]["cross_layer_alignment"], (int, float))
    assert isinstance(sector["normalized_scores"]["market_linkage"], (int, float))


@pytest.mark.parametrize("factor_code", [[], {}, None, ""])
def test_score_factor_rejects_non_string_or_empty_factor_code(factor_code):
    with pytest.raises(ValueError):
        score_factor(
            factor_code=factor_code,
            dimension_scores=FACTOR_DIMENSIONS,
            evidence_quality=3,
        )


def test_selection_returns_canonical_70_for_tolerated_supporting_total():
    primary = _scored_factor("market_node", 82)
    supporting = _scored_factor("sector_rhythm", 70)
    supporting["total_score"] = 69.99995

    result = select_dominant_factors([primary, supporting])

    assert result["supporting"][0]["total_score"] == 70.0


def test_selection_uses_canonical_75_at_single_candidate_threshold():
    item = _scored_factor("market_node", 75)
    item["total_score"] = 74.99995

    result = select_dominant_factors([item])

    assert result["primary"]["total_score"] == 75.0


def test_selection_uses_canonical_82_at_high_confidence_threshold():
    item = _scored_factor("market_node", 82)
    item["total_score"] = 81.99995

    result = select_dominant_factors([item])

    assert result["primary"]["total_score"] == 82.0
    assert result["confidence"] == "high"


def test_selection_uses_canonical_totals_at_eight_point_lead_threshold():
    primary = _scored_factor("market_node", 80)
    competitor = _scored_factor("sector_rhythm", 72)
    primary["total_score"] = 79.99995
    competitor["total_score"] = 72.00005

    result = select_dominant_factors([primary, competitor])

    assert result["primary"]["factor_code"] == "market_node"


def test_selection_uses_canonical_totals_at_fifteen_point_high_threshold():
    primary = _scored_factor("market_node", 90)
    competitor = _scored_factor("sector_rhythm", 75)
    primary["total_score"] = 89.99995
    competitor["total_score"] = 75.00005

    result = select_dominant_factors([primary, competitor])

    assert result["confidence"] == "high"


def test_selection_normalizes_fraction_inputs_for_json_serialization():
    item = _scored_factor("market_node", 82)
    item["total_score"] = Fraction(82, 1)
    item["evidence_quality"] = Fraction(item["evidence_quality"], 1)
    item["normalized_scores"] = {
        name: Fraction(str(value))
        for name, value in item["normalized_scores"].items()
    }

    result = select_dominant_factors([item])

    json.dumps(result)
    assert isinstance(result["primary"]["total_score"], (int, float))
    assert isinstance(result["primary"]["evidence_quality"], (int, float))
    assert all(
        isinstance(value, (int, float))
        for value in result["primary"]["normalized_scores"].values()
    )
