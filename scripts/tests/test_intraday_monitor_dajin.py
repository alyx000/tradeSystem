"""大金重工七个开放日的严格突破、去重、日期与失败保护。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor.rules import DEFAULT_RULES, DAJIN_HEAVY_BREAKOUT_41_96_20260917_28 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = (17, 18, 21, 22, 23, 24, 28)
CLOSED_DAYS = (19, 20, 25, 26, 27)


def _setup(tmp_path, day=17):
    db = _calendar(tmp_path, dates=tuple(f"2026-09-{d}" for d in OPEN_DAYS),
                   closed_dates=tuple(f"2026-09-{d}" for d in CLOSED_DAYS))
    registry, pusher = _Registry(price=41.97), _Pusher()
    registry.now = datetime(2026, 9, day, 10, tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(41.95, False), (41.96, False), (41.97, True)])
def test_strict_threshold_and_identity(price, matched):
    assert RULE.code == "002487.SZ" and RULE.instrument_name == "大金重工"
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 9, 17)
    assert RULE.valid_until == date(2026, 9, 28)
    assert tuple(r for r in DEFAULT_RULES if r.code == RULE.code) == (RULE,)


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_seven_open_days_including_last_day(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.call_count == 1 and len(pusher.messages) == 1


@pytest.mark.parametrize("day", [16, *CLOSED_DAYS, 29, 30])
def test_closed_or_outside_validity_never_fetches(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    expected = "non_trade_day" if day in CLOSED_DAYS else "no_active_rules"
    assert result["status"] == expected
    assert registry.call_count == 0 and not pusher.messages


def test_initial_match_dedupe_reentry_and_message(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (41.97, 42.01, 41.96, 41.97):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "大金重工" in pusher.messages[0][1] and "**41.96**元" in pusher.messages[0][1]


def test_stale_quote_fails_closed(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-16"}
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages


def test_preview_and_failed_push_retry(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    preview = _run(registry, pusher, db, state, dry_run=True)
    assert len(preview["events"]) == 1 and not state.exists() and not pusher.messages
    pusher.succeed = False
    failed = _run(registry, pusher, db, state)
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retried = _run(registry, pusher, db, state)
    assert retried["status"] == "complete" and not retried["events"]
    saved = json.loads(state.read_text())
    assert not saved["pending_events"]
    assert failed["events"][0]["event_id"] in saved["sent_event_ids"]


def test_old_active_rule_does_not_suppress_new_threshold(tmp_path):
    from services.intraday_monitor.rules import DAJIN_HEAVY_BREAKOUT_35_95_20260912_18 as old_rule

    registry, pusher, db, state = _setup(tmp_path)
    registry.price = 39.16
    old = run_check(registry, rules=(old_rule,), now=registry.now, db_path=db,
                    state_path=state, pusher_factory=lambda: pusher)
    assert len(old["events"]) == 1
    registry.now += timedelta(minutes=3)
    registry.price = 41.97
    current = _run(registry, pusher, db, state)
    assert [event["rule_id"] for event in current["events"]] == [RULE.rule_id]
    assert len(pusher.messages) == 2
    assert old_rule.rule_id in json.loads(state.read_text())["rules"]
    assert old_rule not in DEFAULT_RULES
