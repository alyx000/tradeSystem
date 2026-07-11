import json
from fractions import Fraction

import pytest

from services.recommend.formatter import REDLINE_KEYWORDS
from services.trinity_factor.constants import MAX_REASON_LENGTH
from services.trinity_factor.validation import (
    TrinityValidationError,
    parse_factor_response,
    parse_sector_response,
)


def _factor_candidate(factor_code="market_node", **overrides):
    candidate = {
        "factor_code": factor_code,
        "allowed_evidence_ids": {f"ev:{factor_code}"},
        "allowed_counter_evidence_ids": {f"counter:{factor_code}"},
        "allowed_t1_check_ids": {f"t1:{factor_code}"},
        "evidence_quality": 4,
        "caps": {},
        "critical_missing": False,
    }
    candidate.update(overrides)
    return candidate


def _factor_row(factor_code="market_node", **overrides):
    row = {
        "factor_code": factor_code,
        "dimension_scores": {
            "current_dominance": 5,
            "cross_layer_alignment": 5,
            "rhythm_clarity": 5,
            "next_stage_relevance": 5,
            "counterevidence": 0,
        },
        "evidence_refs": [f"ev:{factor_code}"],
        "counter_evidence_refs": [f"counter:{factor_code}"],
        "t1_check_ids": [f"t1:{factor_code}"],
        "reason": "[判断] 证据链完整",
    }
    row.update(overrides)
    return row


def _factor_payload(rows):
    return {
        "schema_version": "trinity_factor_score_v1",
        "factors": rows,
    }


def test_parse_factor_response_validates_and_recomputes_program_score():
    candidates = [{
        "factor_code": "market_node",
        "allowed_evidence_ids": {"ev-1"},
        "allowed_counter_evidence_ids": {"counter-1"},
        "allowed_t1_check_ids": {"t1-1"},
        "evidence_quality": 5,
        "caps": {},
        "critical_missing": False,
    }]
    raw = json.dumps({
        "schema_version": "trinity_factor_score_v1",
        "factors": [{
            "factor_code": "market_node",
            "dimension_scores": {
                "current_dominance": 5,
                "cross_layer_alignment": 5,
                "rhythm_clarity": 5,
                "next_stage_relevance": 5,
                "counterevidence": 0,
            },
            "evidence_refs": ["ev-1"],
            "counter_evidence_refs": ["counter-1"],
            "t1_check_ids": ["t1-1"],
            "reason": "[判断] 节点主导明确",
        }],
    }, ensure_ascii=False)

    result = parse_factor_response(raw, candidates)

    assert result[0]["factor_code"] == "market_node"
    assert result[0]["total_score"] == 100.0
    assert result[0]["reason"].startswith("[判断]")


@pytest.mark.parametrize(
    ("candidates", "rows"),
    [
        ([_factor_candidate(), _factor_candidate("sector_rhythm")], [_factor_row()]),
        ([_factor_candidate()], [_factor_row(), _factor_row()]),
        ([_factor_candidate()], [_factor_row(), _factor_row("style_regime")]),
    ],
    ids=["missing", "duplicate", "unexpected"],
)
def test_factor_id_set_must_exactly_match_candidates(candidates, rows):
    with pytest.raises(TrinityValidationError):
        parse_factor_response(_factor_payload(rows), candidates)


@pytest.mark.parametrize("location", ["top", "item"])
def test_unknown_fields_fail_the_whole_factor_batch(location):
    payload = _factor_payload([_factor_row()])
    target = payload if location == "top" else payload["factors"][0]
    target["unexpected"] = "x"

    with pytest.raises(TrinityValidationError):
        parse_factor_response(payload, [_factor_candidate()])


@pytest.mark.parametrize("forbidden", ["evidence_quality", "total", "total_score"])
def test_model_cannot_supply_program_owned_factor_fields(forbidden):
    row = _factor_row()
    row[forbidden] = 5

    with pytest.raises(TrinityValidationError):
        parse_factor_response(_factor_payload([row]), [_factor_candidate()])


@pytest.mark.parametrize(
    ("field", "unknown"),
    [
        ("evidence_refs", "ev:unknown"),
        ("counter_evidence_refs", "counter:unknown"),
        ("t1_check_ids", "t1:unknown"),
    ],
)
def test_unknown_factor_references_fail_the_whole_batch(field, unknown):
    row = _factor_row()
    row[field] = [unknown]

    with pytest.raises(TrinityValidationError):
        parse_factor_response(_factor_payload([row]), [_factor_candidate()])


@pytest.mark.parametrize("invalid_score", [True, 2.5, -1, 6])
def test_factor_model_scores_must_be_zero_to_five_integers(invalid_score):
    row = _factor_row()
    row["dimension_scores"]["current_dominance"] = invalid_score

    with pytest.raises(TrinityValidationError):
        parse_factor_response(_factor_payload([row]), [_factor_candidate()])


def test_factor_parser_wraps_non_mapping_dimension_scores_as_validation_error():
    row = _factor_row(dimension_scores=list(_factor_row()["dimension_scores"]))

    with pytest.raises(TrinityValidationError):
        parse_factor_response(_factor_payload([row]), [_factor_candidate()])


@pytest.mark.parametrize(
    "reason",
    [
        "没有判断前缀",
        "[判断]" + "x" * (MAX_REASON_LENGTH - len("[判断]") + 1),
        f"[判断] 这里建议{REDLINE_KEYWORDS[0]}",
        123,
    ],
)
def test_factor_reason_requires_prefix_length_limit_and_redline_safety(reason):
    with pytest.raises(TrinityValidationError):
        parse_factor_response(
            _factor_payload([_factor_row(reason=reason)]),
            [_factor_candidate()],
        )


def test_factor_reason_at_length_limit_is_accepted():
    reason = "[判断]" + "x" * (MAX_REASON_LENGTH - len("[判断]"))

    result = parse_factor_response(
        _factor_payload([_factor_row(reason=reason)]),
        [_factor_candidate()],
    )

    assert result[0]["reason"] == reason


@pytest.mark.parametrize(
    "reason",
    [
        "[判断] 建\u200b仓风险上升",
        "[判断] 满\u200b仓风险上升",
        "[判断] 建　仓风险上升",
        "[判断] 满 仓风险上升",
        "[判断] 买，入风险上升",
    ],
)
def test_factor_reason_redline_scan_blocks_obfuscated_variants(reason):
    with pytest.raises(TrinityValidationError):
        parse_factor_response(
            _factor_payload([_factor_row(reason=reason)]),
            [_factor_candidate()],
        )


def test_safe_reason_is_nfkc_normalized_and_control_characters_are_removed():
    result = parse_factor_response(
        _factor_payload([_factor_row(reason="［判断］ 节奏\u200b清晰")]),
        [_factor_candidate()],
    )

    assert result[0]["reason"] == "[判断] 节奏清晰"


def test_factor_output_is_stable_by_factor_id_not_input_order():
    candidates = [_factor_candidate("sector_rhythm"), _factor_candidate("market_node")]
    rows = [_factor_row("sector_rhythm"), _factor_row("market_node")]

    forward = parse_factor_response(_factor_payload(rows), candidates)
    reverse = parse_factor_response(_factor_payload(list(reversed(rows))), list(reversed(candidates)))

    assert [item["factor_code"] for item in forward] == ["market_node", "sector_rhythm"]
    assert forward == reverse


def test_duplicate_candidate_specs_fail_closed():
    with pytest.raises(TrinityValidationError):
        parse_factor_response(
            _factor_payload([_factor_row()]),
            [_factor_candidate(), _factor_candidate()],
        )


def test_wrapped_or_trailing_text_is_not_accepted_as_strict_json():
    wrapped = "model output: " + json.dumps(_factor_payload([_factor_row()]), ensure_ascii=False)

    with pytest.raises(TrinityValidationError):
        parse_factor_response(wrapped, [_factor_candidate()])


def test_parse_sector_response_validates_recomputes_and_assigns_tier():
    candidates = [{
        "sector_key": "sw2:半导体",
        "candidate_tier": "core",
        "allowed_evidence_ids": {"ev:sector"},
        "allowed_counter_evidence_ids": {"counter:sector"},
        "allowed_t1_check_ids": {"t1:sector"},
        "caps": {},
    }]
    raw = {
        "schema_version": "trinity_sector_score_v1",
        "sectors": [{
            "sector_key": "sw2:半导体",
            "dimension_scores": {
                "primary_factor_alignment": 5,
                "stage_connection": 5,
                "market_linkage": 5,
                "leader_clarity": 5,
                "logic_aesthetic": 5,
                "expectation_gap": 5,
                "fully_priced_penalty": 0,
            },
            "evidence_refs": ["ev:sector"],
            "counter_evidence_refs": ["counter:sector"],
            "t1_check_ids": ["t1:sector"],
            "reason": "[判断] 板块与主导因子同向",
        }],
    }

    result = parse_sector_response(raw, candidates)

    assert result[0]["sector_key"] == "sw2:半导体"
    assert result[0]["total_score"] == 100.0
    assert result[0]["tier"] == "priority"
    assert result[0]["candidate_tier"] == "core"


def _sector_candidate(sector_key="sector:a", **overrides):
    candidate = {
        "sector_key": sector_key,
        "candidate_tier": "core",
        "allowed_evidence_ids": {f"ev:{sector_key}"},
        "allowed_counter_evidence_ids": {f"counter:{sector_key}"},
        "allowed_t1_check_ids": {f"t1:{sector_key}"},
        "caps": {},
    }
    candidate.update(overrides)
    return candidate


def _sector_row(sector_key="sector:a", **overrides):
    row = {
        "sector_key": sector_key,
        "dimension_scores": {
            "primary_factor_alignment": 5,
            "stage_connection": 5,
            "market_linkage": 5,
            "leader_clarity": 5,
            "logic_aesthetic": 5,
            "expectation_gap": 5,
            "fully_priced_penalty": 0,
        },
        "evidence_refs": [f"ev:{sector_key}"],
        "counter_evidence_refs": [f"counter:{sector_key}"],
        "t1_check_ids": [f"t1:{sector_key}"],
        "reason": "[判断] 板块证据链完整",
    }
    row.update(overrides)
    return row


def _sector_payload(rows):
    return {
        "schema_version": "trinity_sector_score_v1",
        "sectors": rows,
    }


@pytest.mark.parametrize("candidate_tier", ["watch", "context"])
def test_sector_candidates_must_all_be_core(candidate_tier):
    with pytest.raises(TrinityValidationError):
        parse_sector_response(
            _sector_payload([_sector_row()]),
            [_sector_candidate(candidate_tier=candidate_tier)],
        )


def test_role_is_not_accepted_as_a_candidate_tier_alias():
    candidate = _sector_candidate()
    candidate.pop("candidate_tier")
    candidate["role"] = "core"

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload([_sector_row()]), [candidate])


def test_sector_candidates_are_limited_to_six():
    candidates = [_sector_candidate(f"sector:{index}") for index in range(7)]
    rows = [_sector_row(f"sector:{index}") for index in range(7)]

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload(rows), candidates)


@pytest.mark.parametrize(
    ("candidates", "rows"),
    [
        ([_sector_candidate(), _sector_candidate("sector:b")], [_sector_row()]),
        ([_sector_candidate()], [_sector_row(), _sector_row()]),
        ([_sector_candidate()], [_sector_row(), _sector_row("sector:extra")]),
    ],
    ids=["missing", "duplicate", "unexpected"],
)
def test_sector_id_set_must_exactly_match_candidates(candidates, rows):
    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload(rows), candidates)


@pytest.mark.parametrize("location", ["top", "item"])
def test_unknown_fields_fail_the_whole_sector_batch(location):
    payload = _sector_payload([_sector_row()])
    target = payload if location == "top" else payload["sectors"][0]
    target["unexpected"] = "x"

    with pytest.raises(TrinityValidationError):
        parse_sector_response(payload, [_sector_candidate()])


@pytest.mark.parametrize("forbidden", ["evidence_quality", "total", "total_score"])
def test_model_cannot_supply_program_owned_sector_fields(forbidden):
    row = _sector_row()
    row[forbidden] = 5

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload([row]), [_sector_candidate()])


@pytest.mark.parametrize(
    ("field", "unknown"),
    [
        ("evidence_refs", "ev:unknown"),
        ("counter_evidence_refs", "counter:unknown"),
        ("t1_check_ids", "t1:unknown"),
    ],
)
def test_unknown_sector_references_fail_the_whole_batch(field, unknown):
    row = _sector_row()
    row[field] = [unknown]

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload([row]), [_sector_candidate()])


@pytest.mark.parametrize("invalid_score", [True, 2.5, -1, 6])
def test_sector_model_scores_must_be_zero_to_five_integers(invalid_score):
    row = _sector_row()
    row["dimension_scores"]["leader_clarity"] = invalid_score

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload([row]), [_sector_candidate()])


def test_sector_parser_wraps_non_mapping_dimension_scores_as_validation_error():
    row = _sector_row(dimension_scores=list(_sector_row()["dimension_scores"]))

    with pytest.raises(TrinityValidationError):
        parse_sector_response(_sector_payload([row]), [_sector_candidate()])


@pytest.mark.parametrize(
    "reason",
    [
        "没有判断前缀",
        "[判断]" + "x" * (MAX_REASON_LENGTH - len("[判断]") + 1),
        f"[判断] 这里建议{REDLINE_KEYWORDS[0]}",
    ],
)
def test_sector_reason_uses_shared_strict_judgement_validation(reason):
    with pytest.raises(TrinityValidationError):
        parse_sector_response(
            _sector_payload([_sector_row(reason=reason)]),
            [_sector_candidate()],
        )


def test_sector_output_is_stable_by_total_desc_then_id_asc_and_keeps_caps():
    candidates = [
        _sector_candidate("sector:c"),
        _sector_candidate("sector:b"),
        _sector_candidate("sector:a"),
    ]
    rows = [
        _sector_row("sector:c", dimension_scores={
            **_sector_row("sector:c")["dimension_scores"],
            "fully_priced_penalty": 5,
        }),
        _sector_row("sector:b"),
        _sector_row("sector:a"),
    ]

    result = parse_sector_response(_sector_payload(rows), candidates)

    assert [item["sector_key"] for item in result] == ["sector:a", "sector:b", "sector:c"]
    assert all(item["candidate_tier"] == "core" for item in result)
    assert result[-1]["total_score"] == 80.0

    capped = parse_sector_response(
        _sector_payload([_sector_row(dimension_scores={
            **_sector_row()["dimension_scores"],
            "fully_priced_penalty": 5,
        })]),
        [_sector_candidate(caps={"fully_priced_penalty": 2})],
    )[0]
    assert capped["model_scores"]["fully_priced_penalty"] == 5
    assert capped["normalized_scores"]["fully_priced_penalty"] == 2
    assert capped["total_score"] == 92.0


def test_empty_sector_candidate_set_is_valid():
    assert parse_sector_response(_sector_payload([]), []) == []


def test_parsers_normalize_fraction_program_values_for_json_serialization():
    factor = parse_factor_response(
        _factor_payload([_factor_row()]),
        [_factor_candidate(
            evidence_quality=Fraction(7, 2),
            caps={"cross_layer_alignment": Fraction(5, 2)},
        )],
    )
    sector = parse_sector_response(
        _sector_payload([_sector_row()]),
        [_sector_candidate(caps={"market_linkage": Fraction(3, 2)})],
    )

    json.dumps({"factor": factor, "sector": sector})
