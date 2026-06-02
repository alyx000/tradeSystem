from __future__ import annotations
import pytest
from services.cognition_digest.windows import WINDOWS, resolve_window, window_bounds


def test_windows_three_keys():
    assert set(WINDOWS) == {"recent3d", "weekly", "monthly"}
    assert WINDOWS["recent3d"].lookback_days == 3
    assert WINDOWS["weekly"].lookback_days == 7
    assert WINDOWS["monthly"].lookback_days == 30


def test_resolve_window_unknown_raises():
    with pytest.raises(ValueError):
        resolve_window("yearly")


def test_window_bounds_closed_interval():
    # recent3d, anchor=06-02 → [05-31, 06-02]（含 anchor，跨度=lookback）
    spec = resolve_window("recent3d")
    assert window_bounds(spec, "2026-06-02") == ("2026-05-31", "2026-06-02")


from services.cognition_digest.collector import CognitionActivity
from services.cognition_digest import scorer


def _act(cid, *, instances, confidence=0.5, created_at="2026-01-01 00:00:00", category="signal"):
    return CognitionActivity(
        cognition_id=cid, title=f"T{cid}", category=category, sub_category=None,
        pattern="p", confidence=confidence, status="candidate",
        created_at=created_at, instances=instances,
    )


def _inst(date, tid):
    return {"observed_date": date, "teacher_id": tid, "teacher_name": None}


def test_score_heat_and_consensus():
    a = _act("c1", instances=[_inst("2026-06-01", 1), _inst("2026-06-02", 2)])
    out = scorer.score_activities([a], anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert out[0].heat == 2
    assert out[0].consensus == 2  # 两个不同 teacher_id


def test_score_is_new_bonus_ranks_higher():
    old = _act("old", instances=[_inst("2026-06-01", 1)], created_at="2026-01-01 00:00:00")
    new = _act("new", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 09:00:00")
    out = scorer.score_activities([old, new], anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert out[0].cognition_id == "new"  # is_new 加分使其靠前
    assert out[0].is_new is True


def test_score_top_n_truncates():
    acts = [_act(f"c{i}", instances=[_inst("2026-06-01", 1)]) for i in range(8)]
    out = scorer.score_activities(acts, anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert len(out) == 5


def test_score_consensus_by_name_fallback():
    # teacher_id 为 None 时用 teacher_name 去重（轻微项回归）
    insts = [
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "沈纯"},
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "李四"},
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "沈纯"},
    ]
    out = scorer.score_activities([_act("c1", instances=insts)], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    assert out[0].consensus == 2  # 沈纯 / 李四 去重


def test_score_is_new_boundary_equal_start_and_anchor():
    # created_at 日期恰好 == start 或 == anchor 都算 is_new（闭区间端点）
    at_start = _act("s", instances=[_inst("2026-06-01", 1)], created_at="2026-05-31 00:00:00")
    at_anchor = _act("a", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 23:59:00")
    out = scorer.score_activities([at_start, at_anchor], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    assert all(s.is_new for s in out)


def test_score_tiebreak_by_created_at():
    # 同 score/heat/consensus 时，created_at 更新者稳定靠前（codex 中项回归）
    older = _act("old", instances=[_inst("2026-06-01", 1)], created_at="2026-06-01 09:00:00")
    newer = _act("new", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 09:00:00")
    out = scorer.score_activities([older, newer], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    # 两者 heat/consensus/is_new/recency 全同 → 仅 created_at 决定顺序
    assert [s.cognition_id for s in out] == ["new", "old"]
