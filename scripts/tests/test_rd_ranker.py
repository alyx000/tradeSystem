"""research_digest.ranker：A/US 各≥1 软约束 + 空集不冒充（M1/M2）。"""
from __future__ import annotations

from services.research_digest import ranker


def _cn(code, score):
    return {"market": "A", "stock_code": code, "score": score, "org_count": int(score)}


def _us(t, action):
    return {"market": "US", "ticker": t, "action": action}


def test_both_present_each_at_least_one():
    cn = [_cn("600519", 5), _cn("000001", 3)]
    us = [_us("NVDA", "up")]
    top = ranker.pick_top3(cn, us)
    assert any(x["market"] == "A" for x in top)
    assert any(x["market"] == "US" for x in top)


def test_us_empty_all_a_no_fabrication():
    cn = [_cn("600519", 5), _cn("000001", 3), _cn("000002", 1)]
    top = ranker.pick_top3(cn, [])
    assert len(top) == 3 and all(x["market"] == "A" for x in top)


def test_a_empty_all_us():
    us = [_us("NVDA", "up"), _us("AMD", "down")]
    top = ranker.pick_top3([], us)
    assert all(x["market"] == "US" for x in top)


def test_both_empty_returns_empty():
    assert ranker.pick_top3([], []) == []


def test_both_one_each_k3_returns_two_no_fabrication():
    """k=3 但两侧各仅 1 条 → 返 2 条，不冒充第 3 条（M2 边界）。"""
    top = ranker.pick_top3([_cn("600519", 5)], [_us("NVDA", "up")], k=3)
    assert len(top) == 2


def test_caps_at_k():
    cn = [_cn(str(i), i) for i in range(5)]
    us = [_us("A", "up"), _us("B", "down")]
    assert len(ranker.pick_top3(cn, us)) == 3


def test_us_priority_up_before_down():
    us = [_us("D", "down"), _us("U", "up")]
    assert ranker.pick_top3([], us)[0]["ticker"] == "U"


def test_us_init_first_coverage_ranks_before_upgrade():
    """鞠磊 #1：首次覆盖(init) 优先于评级上调(up)。"""
    us = [_us("U", "up"), _us("I", "init")]
    assert ranker.pick_top3([], us)[0]["ticker"] == "I"
