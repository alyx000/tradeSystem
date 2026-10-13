"""旋极信息突破、有研新材跌破：三个交易日的独立价格提醒。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import (
    XUANJI_BREAKOUT_3_76_20261014_16,
    GRINM_MATERIALS_BELOW_42_40_20261014_16,
)
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


CASES = (
    (XUANJI_BREAKOUT_3_76_20261014_16, "旋极信息", "300324.SZ", 3.76, "above", 3.77),
    (GRINM_MATERIALS_BELOW_42_40_20261014_16, "有研新材", "600206.SH", 42.40, "below", 42.39),
)
OPEN_DAYS = ("2026-10-14", "2026-10-15", "2026-10-16")
CLOSED_DAYS = ("2026-10-17", "2026-10-18")


@pytest.fixture(params=CASES, ids=("xuanji", "grinm"))
def case(request):
    return request.param


def _setup(tmp_path, case, day="2026-10-14"):
    db = _calendar(tmp_path, dates=("2026-10-13", *OPEN_DAYS, "2026-10-19"),
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=case[-1]), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(case, registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(case[0],), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("delta", (-0.01, 0, 0.01))
def test_identity_and_strict_boundary(case, delta):
    rule, name, code, threshold, direction, _ = case
    assert (rule.instrument_name, rule.code, rule.threshold, rule.direction) == (name, code, threshold, direction)
    assert rule.provider == "sina" and rule.threshold_mode == "fixed"
    assert not rule.inclusive and rule.emit_on_initial_match
    assert rule.value_label == "价格" and rule.value_unit == "元"
    assert rule.is_active(round(threshold + delta, 2)) is (delta > 0 if direction == "above" else delta < 0)
    assert rule.valid_from == date(2026, 10, 14) and rule.valid_until == date(2026, 10, 16)
    assert tuple(r for r in DEFAULT_RULES if r.code == code) == (rule,)


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_three_open_days_including_final_day(tmp_path, case, day):
    registry, pusher, db, state = _setup(tmp_path, case, day)
    result = _run(case, registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.call_count == 1 and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ("2026-10-13", *CLOSED_DAYS, "2026-10-19"))
def test_outside_window_and_weekends_never_fetch_or_push(tmp_path, case, day):
    registry, pusher, db, state = _setup(tmp_path, case, day)
    result = _run(case, registry, pusher, db, state)
    # 周末也已在有效期外：规则过滤先于日历门，统一为无有效规则。
    assert result["status"] == "no_active_rules"
    assert registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("day,included", (
    ("2026-10-13", False), ("2026-10-14", True),
    ("2026-10-15", True), ("2026-10-16", True), ("2026-10-19", False),
))
def test_default_batch_and_retirement(tmp_path, case, day, included):
    registry, pusher, db, state = _setup(tmp_path, case, day)
    result = run_check(registry, rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"),
                       now=registry.now, db_path=db, state_path=state, pusher_factory=lambda: pusher)
    assert result["status"] == "complete"
    requested = {code for batch in registry.requested_codes for code in batch}
    assert (case[2] in requested) is included
    assert "300903.SZ" not in requested and "000001.SH" in requested
    assert "688216.SH" not in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case)
    counts = []
    for price in (case[-1], case[-1], case[3], case[-1]):
        registry.price = price
        result = _run(case, registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1] and len(pusher.messages) == 2
    assert case[1] in pusher.messages[0][1]
    assert f"**{case[3]:.2f}**元" in pusher.messages[0][1]
    registry.now = datetime(2026, 10, 15, 10, tzinfo=TZ)
    assert len(_run(case, registry, pusher, db, state)["events"]) == 1


def test_stale_quote_fails_closed(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case)
    registry.quote_overrides[case[2]] = {"quote_date": "2026-10-13"}
    result = _run(case, registry, pusher, db, state)
    assert result["status"] == "source_failed" and not result["events"] and not pusher.messages


def test_missing_calendar_never_fetches_or_pushes(tmp_path, case):
    registry, pusher, _, state = _setup(tmp_path, case)
    result = _run(case, registry, pusher, tmp_path / "missing.db", state)
    assert result["status"] == "blocked_calendar"
    assert registry.call_count == 0 and not pusher.messages and not state.exists()


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
    assert not saved["pending_events"]
    assert failed["events"][0]["event_id"] in saved["sent_event_ids"]


def test_expired_pending_cannot_leak_via_long_lived_rule(tmp_path, case):
    registry, pusher, db, state = _setup(tmp_path, case, OPEN_DAYS[-1])
    pusher.succeed = False
    failed = _run(case, registry, pusher, db, state)
    event_id = failed["events"][0]["event_id"]
    pusher.messages.clear()
    pusher.succeed = True
    registry.now = datetime(2026, 10, 19, 10, tzinfo=TZ)
    result = run_check(registry, rules=(case[0], SSE_COMPOSITE_RECLAIM_3955), now=registry.now,
                       db_path=db, state_path=state, pusher_factory=lambda: pusher)
    assert result["status"] == "complete" and not result["events"] and not pusher.messages
    assert case[2] not in registry.requested_codes[-1]
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and event_id not in saved["sent_event_ids"]


def test_cli_exposes_rule_without_sending(case):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    parsed = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", case[0].rule_id,
                               "--input-by", "pytest", "--confirm-real-push"])
    assert parsed.rule_id == case[0].rule_id


def test_both_rules_share_batch_but_have_independent_state(tmp_path):
    registry, pusher, db, state = _setup(tmp_path, CASES[0])
    rules = tuple(c[0] for c in CASES)
    for case in CASES:
        registry.quote_overrides[case[2]] = {"price": case[-1]}
    def run():
        return run_check(registry, rules=rules, now=registry.now, db_path=db,
                         state_path=state, pusher_factory=lambda: pusher)
    assert {e["rule_id"] for e in run()["events"]} == {r.rule_id for r in rules}
    assert registry.call_count == 1 and len(pusher.messages) == 1
    registry.now += timedelta(minutes=3)
    assert not run()["events"]
    for case in CASES:
        registry.quote_overrides[case[2]]["price"] = case[3]
    registry.now += timedelta(minutes=3)
    assert not run()["events"]
    registry.quote_overrides[CASES[0][2]]["price"] = CASES[0][-1]
    registry.now += timedelta(minutes=3)
    assert [e["rule_id"] for e in run()["events"]] == [rules[0].rule_id]
