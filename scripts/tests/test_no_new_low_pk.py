"""七天横盘不创新低两两 PK 排序契约。"""
from __future__ import annotations

import json

from services.no_new_low import pk


def _cards():
    return [
        {"code": "A", "name": "甲", "industry": "电力", "base_range_pct": 8, "low_floor_margin_pct": 2,
         "max_close_deviation_pct": 4, "abs_window_return_pct": 1,
         "volume_ratio": 1.0, "today_amount_yi": 30, "close_vs_anchor_pct": 5},
        {"code": "B", "name": "乙", "base_range_pct": 9, "low_floor_margin_pct": 1,
         "max_close_deviation_pct": 5, "abs_window_return_pct": 2,
         "volume_ratio": 1.1, "today_amount_yi": 20, "close_vs_anchor_pct": 4},
        {"code": "C", "name": "丙", "base_range_pct": 10, "low_floor_margin_pct": 1,
         "max_close_deviation_pct": 6, "abs_window_return_pct": 3,
         "volume_ratio": 0.8, "today_amount_yi": 10, "close_vs_anchor_pct": 3},
        {"code": "D", "name": "丁", "base_range_pct": 11, "low_floor_margin_pct": 1,
         "max_close_deviation_pct": 7, "abs_window_return_pct": 4,
         "volume_ratio": 1.3, "today_amount_yi": 5, "close_vs_anchor_pct": 2},
    ]


def test_pk_win_tie_uses_direct_head_to_head_before_fact_tiebreak():
    # A: 胜B、负C/D =1胜；B: 负A、胜C、负D =1胜。胜场相同，A凭直接交手排前。
    winners = {
        frozenset(("A", "B")): "A", frozenset(("A", "C")): "C",
        frozenset(("A", "D")): "D", frozenset(("B", "C")): "B",
        frozenset(("B", "D")): "D", frozenset(("C", "D")): "C",
    }

    def runner(_prompt, payload):
        matches = []
        for pair in payload["pairs"]:
            a, b = pair["a"], pair["b"]
            matches.append({"a": a, "b": b, "winner": winners[frozenset((a, b))],
                            "reason": "事实比较"})
        return json.dumps({"matches": matches})

    result = pk.run_pk(_cards(), runner)
    assert result["status"] == "ok"
    assert result["wins"]["A"] == result["wins"]["B"] == 1
    assert result["ranks"]["A"] < result["ranks"]["B"]


def test_exact_business_tie_shares_rank_instead_of_code_rank():
    cards = [
        {"code": "600002", "base_range_pct": 8, "low_floor_margin_pct": 2,
         "max_close_deviation_pct": 4, "abs_window_return_pct": 1,
         "volume_ratio": 1, "today_amount_yi": 30, "close_vs_anchor_pct": 5},
        {"code": "600001", "base_range_pct": 8, "low_floor_margin_pct": 2,
         "max_close_deviation_pct": 4, "abs_window_return_pct": 1,
         "volume_ratio": 1, "today_amount_yi": 30, "close_vs_anchor_pct": 5},
    ]
    # 无有效直接交手时熔断，直接验证排序器的并列契约。
    order, ranks = pk._build_order(
        [item["code"] for item in cards], {item["code"]: item for item in cards},
        {"600002": 1, "600001": 1}, [],
    )
    assert order == ["600002", "600001"]
    assert ranks["600002"] == ranks["600001"] == 1


def test_runner_startup_failure_melts_after_first_match():
    class Runner:
        last_diagnostics = None

        def __call__(self, _prompt, _payload):
            self.last_diagnostics = {"reason": "startup_failed"}
            return None

    result = pk.run_pk(_cards(), Runner())
    assert result["status"] == "melted"
    assert result["attempted"] == 0
    assert result["invalid"] == result["total"] == 6
    assert result["batch_attempts"] == 1
    assert result["failure_reasons"] == {"startup_failed": 1}


def test_batch_requires_exact_pair_coverage_and_retries_once():
    calls = []

    def runner(_prompt, payload):
        calls.append(1)
        # 少一场，且两次都不修复：不得用部分对局伪造排名。
        matches = [
            {"a": pair["a"], "b": pair["b"], "winner": pair["a"], "reason": "事实比较"}
            for pair in payload["pairs"][:-1]
        ]
        return json.dumps({"matches": matches})

    result = pk.run_pk(_cards(), runner)
    assert result["status"] == "melted"
    assert result["attempted"] == 0 and result["invalid"] == result["total"] == 6
    assert result["batch_attempts"] == 2 and calls == [1, 1]


def test_fact_payload_excludes_non_fact_and_redline_reason_is_filtered():
    payload = pk._fact_payload({**_cards()[0], "secret": "x", "score": 99})
    assert "secret" not in payload and "score" not in payload
    assert payload["max_close_deviation_pct"] == 4
    assert set(payload) == {
        "code", "name", "anchor_date", "latest_date", "base_range_pct",
        "max_close_deviation_pct", "abs_window_return_pct", "volume_ratio", "bar_count",
    }
    for forbidden in (
        "today_amount_yi", "window_return_pct", "low_floor_margin_pct",
        "latest_low_lift_pct", "close_vs_anchor_pct", "industry",
    ):
        assert forbidden not in payload
    assert "过滤" in pk._filter_reason("建议买入")


def test_prompt_explicitly_does_not_reward_upward_strength():
    prompts = []

    def runner(prompt, payload):
        prompts.append(prompt)
        matches = [
            {"a": pair["a"], "b": pair["b"], "winner": pair["a"], "reason": "横盘更窄"}
            for pair in payload["pairs"]
        ]
        return json.dumps({"matches": matches})

    assert pk.run_pk(_cards(), runner)["status"] == "ok"
    assert "不得把区间上涨" in prompts[0]
    assert "相对强度当作优势" in prompts[0]


def test_fact_tiebreak_prefers_tighter_shape_before_turnover():
    cards = [
        {"code": "TIGHT", "base_range_pct": 6, "max_close_deviation_pct": 3,
         "abs_window_return_pct": 1, "volume_ratio": 1, "today_amount_yi": 2},
        {"code": "LIQUID", "base_range_pct": 10, "max_close_deviation_pct": 6,
         "abs_window_return_pct": 4, "volume_ratio": 1, "today_amount_yi": 100},
    ]
    order, _ = pk._build_order(
        [item["code"] for item in cards], {item["code"]: item for item in cards},
        {"TIGHT": 1, "LIQUID": 1}, [],
    )
    assert order == ["TIGHT", "LIQUID"]


def test_missing_volume_ratio_is_not_treated_as_perfectly_stable():
    complete = {
        "base_range_pct": 8, "max_close_deviation_pct": 4,
        "abs_window_return_pct": 2, "volume_ratio": 1, "today_amount_yi": 1,
    }
    missing = {**complete, "volume_ratio": None, "today_amount_yi": 100}
    assert pk._evidence_key(complete) > pk._evidence_key(missing)


def test_llm_cannot_see_turnover_or_upward_lift_fields():
    seen_cards = []

    def runner(_prompt, payload):
        seen_cards.extend(payload["cards"])
        matches = [
            {"a": pair["a"], "b": pair["b"], "winner": pair["a"], "reason": "横盘事实"}
            for pair in payload["pairs"]
        ]
        return json.dumps({"matches": matches})

    result = pk.run_pk(_cards()[:2], runner)
    assert result["status"] == "ok"
    assert seen_cards
    assert all("today_amount_yi" not in card for card in seen_cards)
    assert all("window_return_pct" not in card for card in seen_cards)
    assert all("low_floor_margin_pct" not in card for card in seen_cards)


def test_unhashable_winner_is_invalid_output_not_exception():
    for winner in ([], {}, None, 1):
        text = json.dumps({"matches": [{"a": "600000", "b": "600001",
                                        "winner": winner, "reason": "横盘"}]})
        assert pk._parse_tournament(text, [("600000", "600001")]) is None
