"""伊利追加十交易日、已到期方盛恢复十交易日；保留历史与不重叠窗口。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import (
    YILI_BELOW_27_31_20261027_1109,
    FANGSHENG_REACH_11_11_20261026_1106,
    YILI_BELOW_27_31_20261013_26,
    FANGSHENG_REACH_11_11_20261013_21,
)
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


FANG_DAYS = tuple(f"2026-10-{d}" for d in (26, 27, 28, 29, 30)) + tuple(f"2026-11-{d:02d}" for d in range(2, 7))
YILI_DAYS = (*FANG_DAYS[1:], "2026-11-09")
OPEN_DAYS = ("2026-10-21", "2026-10-22", "2026-10-23", *FANG_DAYS, "2026-11-09", "2026-11-10")
CLOSED_DAYS = ("2026-10-24", "2026-10-25", "2026-10-31", "2026-11-01", "2026-11-07", "2026-11-08")
CASES = (
    (YILI_BELOW_27_31_20261027_1109, YILI_BELOW_27_31_20261013_26,
     YILI_DAYS, "伊利股份", "600887.SH", 27.31, "below", False, 27.30, 27.31),
    (FANGSHENG_REACH_11_11_20261026_1106, FANGSHENG_REACH_11_11_20261013_21,
     FANG_DAYS, "方盛制药", "603998.SH", 11.11, "above", True, 11.11, 11.10),
)


@pytest.fixture(params=CASES, ids=("yili", "fangsheng"))
def case(request):
    return request.param


def _setup(tmp_path, case, day=None):
    db = _calendar(tmp_path, dates=OPEN_DAYS, closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=case[8]), _Pusher()
    registry.now = datetime.fromisoformat((day or case[2][0]) + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(case, registry, pusher, db, state, *, rules=None, **kwargs):
    return run_check(registry, rules=rules if rules is not None else (case[0],),
                     now=registry.now, db_path=db, state_path=state,
                     pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("delta", (-0.01, 0, 0.01))
def test_identity_boundary_and_distinct_successor(case, delta):
    rule, old, days, name, code, threshold, direction, inclusive, _, _ = case
    assert (rule.instrument_name, rule.code, rule.threshold, rule.direction, rule.inclusive) == (name, code, threshold, direction, inclusive)
    assert rule.provider == "sina" and rule.threshold_mode == "fixed" and rule.emit_on_initial_match
    assert rule.value_unit == "元" and rule.value_label == "价格"
    expected = delta < 0 if direction == "below" else delta >= 0
    assert rule.is_active(round(threshold + delta, 2)) is expected
    assert rule.valid_from == date.fromisoformat(days[0]) and rule.valid_until == date.fromisoformat(days[-1])
    assert len(days) == 10 and rule.valid_from > old.valid_until
    assert rule.rule_id != old.rule_id and rule in DEFAULT_RULES and old in DEFAULT_RULES
    assert (rule.threshold, rule.inclusive, rule.direction) == (old.threshold, old.inclusive, old.direction)


@pytest.mark.parametrize("index", range(10))
def test_exact_ten_open_days_including_final_day(tmp_path, case, index):
    registry, pusher, db, state = _setup(tmp_path, case, case[2][index])
    result = _run(case, registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.requested_codes == [[case[4]]] and len(pusher.messages) == 1


@pytest.mark.parametrize("position", ("before", "after", "weekend_oct", "weekend_nov"))
def test_new_window_guards_do_not_fetch_or_push(tmp_path, case, position):
    day = {"before": date.fromisoformat(case[2][0]) - timedelta(days=1),
           "after": date.fromisoformat(case[2][-1]) + timedelta(days=1),
           "weekend_oct": date(2026, 10, 31), "weekend_nov": date(2026, 11, 1)}[position]
    registry, pusher, db, state = _setup(tmp_path, case, day.isoformat())
    result = _run(case, registry, pusher, db, state)
    expected = "non_trade_day" if position.startswith("weekend") else "no_active_rules"
    assert result["status"] == expected and registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("day,yili_index,fang_index", (
    ("2026-10-21", 0, 0), ("2026-10-22", 0, None), ("2026-10-23", 0, None),
    ("2026-10-26", 0, 1), ("2026-10-27", 1, 1), ("2026-11-06", 1, 1),
    ("2026-11-09", 1, None), ("2026-11-10", None, None),
))
def test_default_handoff_no_overlap_no_retroactive_fangsheng_gap(tmp_path, case, day, yili_index, fang_index):
    registry, pusher, db, state = _setup(tmp_path, case, day)
    index = yili_index if case[4] == "600887.SH" else fang_index
    expected = None if index is None else (case[1], case[0])[index]
    effective = [r for r in DEFAULT_RULES if r.code == case[4] and r.is_effective_on(registry.now.date())]
    assert effective == ([] if expected is None else [expected])
    result = _run(case, registry, pusher, db, state,
                  rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"))
    assert result["status"] == "complete"
    requested = [code for batch in registry.requested_codes for code in batch]
    assert requested.count(case[4]) == (0 if expected is None else 1)
    family_ids = {r.rule_id for r in DEFAULT_RULES if r.code == case[4]}
    events = [e for e in result["events"] if e["rule_id"] in family_ids]
    assert [e["rule_id"] for e in events] == ([] if expected is None else [expected.rule_id])
    assert "688216.SH" not in requested and "300903.SZ" not in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case)
    counts = []
    for price in (case[8], case[8], case[9], case[8]):
        registry.price = price
        result = _run(case, registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1] and len(pusher.messages) == 2
    assert case[3] in pusher.messages[0][1] and f"**{case[5]:.2f}**元" in pusher.messages[0][1]
    registry.now = datetime.fromisoformat(case[2][1] + "T10:00:00").replace(tzinfo=TZ)
    assert len(_run(case, registry, pusher, db, state)["events"]) == 1


def test_old_state_is_preserved_and_not_inherited_by_successor(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case, case[1].valid_until.isoformat())
    pair = (case[1], case[0])
    old = _run(case, registry, pusher, db, state, rules=(case[1],))
    assert [e["rule_id"] for e in old["events"]] == [case[1].rule_id]
    registry.now += timedelta(minutes=3)
    assert not _run(case, registry, pusher, db, state, rules=pair)["events"]
    registry.now = datetime.fromisoformat(case[2][0] + "T10:00:00").replace(tzinfo=TZ)
    new = _run(case, registry, pusher, db, state, rules=pair)
    assert [e["rule_id"] for e in new["events"]] == [case[0].rule_id]
    saved = json.loads(state.read_text())
    assert case[1].rule_id in saved["rules"] and case[0].rule_id in saved["rules"]
    assert len(pusher.messages) == 2


def test_old_failed_event_is_expired_not_replayed_on_successor(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case, case[1].valid_until.isoformat())
    pusher.succeed = False
    failed = _run(case, registry, pusher, db, state, rules=(case[1],))
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now = datetime.fromisoformat(case[2][0] + "T10:00:00").replace(tzinfo=TZ)
    new = _run(case, registry, pusher, db, state, rules=(case[1], case[0]))
    assert new["status"] == "complete" and [e["rule_id"] for e in new["events"]] == [case[0].rule_id]
    saved = json.loads(state.read_text())
    assert failed["events"][0]["event_id"] in saved["expired_event_ids"] and not saved["pending_events"]


def test_stale_quote_and_missing_calendar_fail_closed(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case)
    missing = _run(case, registry, pusher, tmp_path / "missing.db", state)
    assert missing["status"] == "blocked_calendar" and registry.call_count == 0
    registry.quote_overrides[case[4]] = {"quote_date": (date.fromisoformat(case[2][0]) - timedelta(days=1)).isoformat()}
    stale = _run(case, registry, pusher, db, state)
    assert stale["status"] == "source_failed" and not stale["events"] and not pusher.messages


def test_preview_and_failed_push_retry(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case)
    preview = _run(case, registry, pusher, db, state, dry_run=True)
    assert len(preview["events"]) == 1 and not state.exists() and not pusher.messages
    pusher.succeed = False
    failed = _run(case, registry, pusher, db, state)
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retried = _run(case, registry, pusher, db, state)
    assert retried["status"] == "complete" and not retried["events"]
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and failed["events"][0]["event_id"] in saved["sent_event_ids"]


def test_successor_expired_pending_cannot_leak_via_long_lived_rule(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case, case[2][-1])
    pusher.succeed = False
    failed = _run(case, registry, pusher, db, state)
    assert failed["status"] == "push_failed"
    pusher.messages.clear()
    pusher.succeed = True
    next_day = "2026-11-10" if case[4] == "600887.SH" else "2026-11-09"
    registry.now = datetime.fromisoformat(next_day + "T10:00:00").replace(tzinfo=TZ)
    result = _run(case, registry, pusher, db, state, rules=(case[0], SSE_COMPOSITE_RECLAIM_3955))
    assert result["status"] == "complete" and not result["events"] and not pusher.messages
    assert case[4] not in registry.requested_codes[-1]
    saved = json.loads(state.read_text())
    assert failed["events"][0]["event_id"] in saved["expired_event_ids"] and not saved["pending_events"]


def test_cli_exposes_successor_without_sending(case):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", case[0].rule_id,
                             "--input-by", "pytest", "--confirm-real-push"])
    assert args.rule_id == case[0].rule_id
