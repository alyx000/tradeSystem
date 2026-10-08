"""圣邦股份十交易日突破提醒：边界、去重、有效期与失败保护。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import SGMICRO_BREAKOUT_103_41_20261009_22 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = tuple(f"2026-10-{day:02d}" for day in (9, 12, 13, 14, 15, 16, 19, 20, 21, 22))
CLOSED_DAYS = tuple(f"2026-10-{day:02d}" for day in (10, 11, 17, 18))


def _setup(tmp_path, day="2026-10-09"):
    db = _calendar(tmp_path, dates=("2026-10-08", *OPEN_DAYS, "2026-10-23"),
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=103.42), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(103.40, False), (103.41, False), (103.42, True)])
def test_identity_and_strict_boundary(price, matched):
    assert RULE.code == "300661.SZ" and RULE.instrument_name == "圣邦股份"
    assert RULE.threshold == 103.41 and RULE.direction == "above"
    assert RULE.provider == "sina" and RULE.threshold_mode == "fixed"
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 10, 9)
    assert RULE.valid_until == date(2026, 10, 22)
    assert tuple(r for r in DEFAULT_RULES if r.code == RULE.code) == (RULE,)


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_ten_open_days_including_final_day(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.call_count == 1 and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ["2026-10-08", *CLOSED_DAYS, "2026-10-23"])
def test_holiday_and_expiry_never_fetch_or_push(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == ("non_trade_day" if day in CLOSED_DAYS else "no_active_rules")
    assert registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("day,included", [
    ("2026-10-08", False), ("2026-10-09", True),
    ("2026-10-12", True), ("2026-10-22", True), ("2026-10-23", False),
])
def test_default_batch_respects_window_and_preserves_retirement(tmp_path, day, included):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = run_check(registry, rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"),
                       now=registry.now, db_path=db, state_path=state, pusher_factory=lambda: pusher)
    assert result["status"] == "complete"
    requested = {code for batch in registry.requested_codes for code in batch}
    assert (RULE.code in requested) is included
    assert "300903.SZ" not in requested
    assert "000001.SH" in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (103.42, 103.50, 103.41, 103.42):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "圣邦股份" in pusher.messages[0][1] and "**103.41**元" in pusher.messages[0][1]
    registry.now = datetime(2026, 10, 12, 10, tzinfo=TZ)
    assert len(_run(registry, pusher, db, state)["events"]) == 1


def test_stale_quote_fails_closed(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-10-08"}
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


def test_cli_exposes_rule_without_sending():
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    parsed = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", RULE.rule_id,
                               "--input-by", "pytest", "--confirm-real-push"])
    assert parsed.rule_id == RULE.rule_id
