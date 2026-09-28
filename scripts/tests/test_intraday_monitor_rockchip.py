"""瑞芯微三交易日双向提醒：边界、去重、有效期与失败保护。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import ROCKCHIP_BELOW_203_90_20260929_1008 as BELOW
from services.intraday_monitor import ROCKCHIP_ABOVE_219_02_20260929_1008 as ABOVE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = ("2026-09-29", "2026-09-30", "2026-10-08")
CLOSED_DAYS = tuple(f"2026-10-0{day}" for day in range(1, 8))


@pytest.fixture(params=(BELOW, ABOVE), ids=("below", "above"))
def rule(request):
    return request.param


def _setup(tmp_path, rule, day="2026-09-29"):
    db = _calendar(tmp_path, dates=("2026-09-28", *OPEN_DAYS, "2026-10-09"),
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=rule.threshold + (-0.01 if rule.direction == "below" else 0.01)), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, rule, **kwargs):
    return run_check(registry, rules=(rule,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("offset,matched", [(-0.01, False), (0, False), (0.01, True)])
def test_identity_and_strict_boundary(rule, offset, matched):
    price = rule.threshold + offset * (-1 if rule is BELOW else 1)
    assert rule.code == "603893.SH" and rule.instrument_name == "瑞芯微"
    assert (rule.threshold, rule.direction) == ((203.90, "below") if rule is BELOW else (219.02, "above"))
    assert rule.provider == "sina" and rule.threshold_mode == "fixed"
    assert rule.is_active(price) is matched
    assert rule.valid_from == date(2026, 9, 29)
    assert rule.valid_until == date(2026, 10, 8)
    assert tuple(r for r in DEFAULT_RULES if r.code == rule.code) == (BELOW, ABOVE)


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_three_open_days_including_final_day(tmp_path, rule, day):
    registry, pusher, db, state = _setup(tmp_path, rule, day)
    result = _run(registry, pusher, db, state, rule)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.call_count == 1 and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ["2026-09-28", *CLOSED_DAYS, "2026-10-09"])
def test_holiday_and_expiry_never_fetch_or_push(tmp_path, rule, day):
    registry, pusher, db, state = _setup(tmp_path, rule, day)
    result = _run(registry, pusher, db, state, rule)
    assert result["status"] == ("non_trade_day" if day in CLOSED_DAYS else "no_active_rules")
    assert registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("day,included", [
    ("2026-09-28", False), ("2026-09-29", True),
    ("2026-09-30", True), ("2026-10-08", True), ("2026-10-09", False),
])
def test_default_batch_respects_window_and_preserves_retirement(tmp_path, rule, day, included):
    registry, pusher, db, state = _setup(tmp_path, rule, day)
    result = run_check(registry, rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"),
                       now=registry.now, db_path=db, state_path=state, pusher_factory=lambda: pusher)
    assert result["status"] == "complete"
    requested = {code for batch in registry.requested_codes for code in batch}
    assert (rule.code in requested) is included
    assert "300903.SZ" not in requested
    assert "000001.SH" in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path, rule):
    registry, pusher, db, state = _setup(tmp_path, rule)
    counts = []
    for offset in (0.01, 0.10, 0, 0.01):
        price = rule.threshold + offset * (-1 if rule is BELOW else 1)
        registry.price = price
        result = _run(registry, pusher, db, state, rule)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "瑞芯微" in pusher.messages[0][1] and f"**{rule.threshold:.2f}**元" in pusher.messages[0][1]
    registry.now = datetime(2026, 9, 30, 10, tzinfo=TZ)
    assert len(_run(registry, pusher, db, state, rule)["events"]) == 1


def test_stale_quote_fails_closed(tmp_path, rule):
    registry, pusher, db, state = _setup(tmp_path, rule)
    registry.quote_overrides[rule.code] = {"quote_date": "2026-09-28"}
    result = _run(registry, pusher, db, state, rule)
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages


def test_preview_and_failed_push_retry(tmp_path, rule):
    registry, pusher, db, state = _setup(tmp_path, rule)
    preview = _run(registry, pusher, db, state, rule, dry_run=True)
    assert len(preview["events"]) == 1 and not state.exists() and not pusher.messages
    pusher.succeed = False
    failed = _run(registry, pusher, db, state, rule)
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retried = _run(registry, pusher, db, state, rule)
    assert retried["status"] == "complete" and not retried["events"]
    saved = json.loads(state.read_text())
    assert not saved["pending_events"]
    assert failed["events"][0]["event_id"] in saved["sent_event_ids"]


def test_cli_exposes_rule_without_sending(rule):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    parsed = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", rule.rule_id,
                               "--input-by", "pytest", "--confirm-real-push"])
    assert parsed.rule_id == rule.rule_id


def test_both_rules_share_quote_but_keep_independent_alert_state(tmp_path):
    registry, pusher, db, state = _setup(tmp_path, BELOW)
    seen = []
    for price in (203.89, 203.90, 219.02, 219.03, 219.10, 210.0, 203.89):
        registry.price = price
        result = run_check(registry, rules=(BELOW, ABOVE), now=registry.now,
                           db_path=db, state_path=state, pusher_factory=lambda: pusher)
        assert result["status"] == "complete"
        seen.append([event["rule_id"] for event in result["events"]])
        registry.now += timedelta(minutes=3)
    assert seen == [[BELOW.rule_id], [], [], [ABOVE.rule_id], [], [], [BELOW.rule_id]]
    assert all(tuple(codes) == (BELOW.code,) for codes in registry.requested_codes)
    assert len(pusher.messages) == 3
