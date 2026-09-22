"""科翔股份七个开放日的严格跌破、去重、日期与失败保护。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor.rules import DEFAULT_RULES, KEXIANG_BELOW_108_30_20260922_1008 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = ("2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08")
CLOSED_DAYS = ("2026-09-25", "2026-09-26", "2026-09-27", *(f"2026-10-0{d}" for d in range(1, 8)))


def _setup(tmp_path, day="2026-09-22"):
    db = _calendar(tmp_path, dates=OPEN_DAYS,
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=108.29), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(108.31, False), (108.30, False), (108.29, True)])
def test_strict_threshold_and_identity(price, matched):
    assert RULE.code == "300903.SZ" and RULE.instrument_name == "科翔股份"
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 9, 22)
    assert RULE.valid_until == date(2026, 10, 8)
    assert tuple(r for r in DEFAULT_RULES if r.code == RULE.code and r.threshold_mode == "fixed") == (RULE,)


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_seven_open_days_including_last_day(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.call_count == 1 and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ["2026-09-21", *CLOSED_DAYS, "2026-10-09"])
def test_closed_or_outside_validity_never_fetches(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    expected = "non_trade_day" if day in CLOSED_DAYS else "no_active_rules"
    assert result["status"] == expected
    assert registry.call_count == 0 and not pusher.messages


def test_initial_match_dedupe_reentry_and_message(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (108.29, 108.20, 108.30, 108.29):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "科翔股份" in pusher.messages[0][1] and "**108.30**元" in pusher.messages[0][1]


def test_stale_quote_fails_closed(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-21"}
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
