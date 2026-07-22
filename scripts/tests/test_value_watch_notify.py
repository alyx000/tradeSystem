"""value-watch 通知候选集判定：enter/exit 分类 + effective ledger + 日历闸门。"""
from __future__ import annotations

from services.value_watch import notify
from services.value_watch.engine import Event


def _ev(key, kind="enter", parent=None, active=True):
    return Event(key=key, kind=kind, parent_key=parent, occurred_date="2026-07-01",
                 active=active, title=key, facts={})


def test_enter_inactive_transient_skipped():
    assert notify.select_candidates([_ev("v1:a", active=False)], set()) == []


def test_enter_active_and_dedup():
    assert notify.select_candidates([_ev("v1:a")], set()) == [_ev("v1:a")]
    assert notify.select_candidates([_ev("v1:a")], {"v1:a"}) == []


def test_exit_requires_parent_in_ledger():
    ex = _ev("v1:a:exit", kind="exit", parent="v1:a")
    assert notify.select_candidates([ex], set()) == []          # parent 未发 → 不推
    assert notify.select_candidates([ex], {"v1:a"}) == [ex]     # parent 已发 → 迟到补推


def test_exit_deduped_by_ledger():
    ex = _ev("v1:a:exit", kind="exit", parent="v1:a")
    assert notify.select_candidates([ex], {"v1:a", "v1:a:exit"}) == []


def test_same_round_parent_then_exit():
    """空账本首跑:历史触档 enter(已回落但阶梯 enter 永久 active)+exit 同轮,
    effective ledger 含本轮已选键让 exit 通过;顺序 enter 在前。"""
    en = _ev("v1:a")
    ex = _ev("v1:a:exit", kind="exit", parent="v1:a")
    out = notify.select_candidates([ex, en], set())   # 传入顺序无关
    assert out == [en, ex]


def test_allow_push_gate():
    assert notify.allow_push("2026-07-21", "2026-07-21") is True
    assert notify.allow_push("2026-07-20", "2026-07-21") is False   # 历史日期不推
    assert notify.allow_push("2026-07-21", None) is False           # blocked 不推
