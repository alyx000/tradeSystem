from __future__ import annotations

import json

from services.trinity_factor.review_input import normalize_review_steps
from services.trinity_factor.service import build_score_input_digest


def test_summary_step_object_and_json_text_share_canonical_output_and_digest() -> None:
    summary = {"judgement": "结构偏弱"}
    from_object = normalize_review_steps({"step1_market": summary})
    from_text = normalize_review_steps({
        "step1_market": json.dumps(summary, ensure_ascii=False),
    })

    expected = {
        "step1_market": {
            "judgement": "结构偏弱",
            "notes": "判断：结构偏弱",
        },
    }
    assert from_object == expected
    assert from_text == expected
    assert build_score_input_digest(
        trade_date="2026-07-10",
        prefill={},
        review_steps=from_object,
    ) == build_score_input_digest(
        trade_date="2026-07-10",
        prefill={},
        review_steps=from_text,
    )
