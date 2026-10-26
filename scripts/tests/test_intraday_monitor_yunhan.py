"""云汉芯城五交易日严格跌破提醒；外网、推送及状态均隔离。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import YUNHAN_BELOW_105_49_20261026_30 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = tuple(f"2026-10-{day}" for day in range(26, 31))
CLOSED_DAYS = ("2026-10-24", "2026-10-25", "2026-10-31", "2026-11-01")


def _setup(tmp_path, day=OPEN_DAYS[0]):
    db = _calendar(tmp_path, dates=("2026-10-23", *OPEN_DAYS, "2026-11-02"),
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=105.48), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, *, rules=(RULE,), **kwargs):
    return run_check(registry, rules=rules, now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", ((105.48, True), (105.49, False), (105.50, False)))
def test_identity_and_strict_boundary(price, matched):
    assert (RULE.instrument_name, RULE.code, RULE.threshold) == ("云汉芯城", "301563.SZ", 105.49)
    assert RULE.provider == "sina" and RULE.threshold_mode == "fixed"
    assert RULE.direction == "below" and not RULE.inclusive and RULE.emit_on_initial_match
    assert (RULE.value_label, RULE.value_unit, RULE.action_text) == ("价格", "元", "跌破")
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 10, 26) and RULE.valid_until == date(2026, 10, 30)
    assert [r for r in DEFAULT_RULES if r.code == RULE.code] == [RULE]


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_exact_five_open_days_include_final_day(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.requested_codes == [[RULE.code]] and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ("2026-10-23", *CLOSED_DAYS, "2026-11-02"))
def test_outside_window_and_closed_days_do_not_fetch_or_push(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    # 所有这些日期均在规则窗口外，先被有效期门禁拦下。
    assert result["status"] == "no_active_rules"
    assert not registry.call_count and not pusher.messages


def test_calendar_closed_inside_window_does_not_fetch_or_push(tmp_path):
    registry, pusher = _Registry(price=105.48), _Pusher()
    registry.now = datetime(2026, 10, 28, 10, tzinfo=TZ)
    db = _calendar(tmp_path, dates=("2026-10-28",), is_open=0)
    result = _run(registry, pusher, db, tmp_path / "state.json")
    assert result["status"] == "non_trade_day" and not registry.call_count and not pusher.messages


@pytest.mark.parametrize("day,included", (("2026-10-23", False), ("2026-10-26", True),
                                         ("2026-10-30", True), ("2026-11-02", False)))
def test_default_batch_window_preserves_other_rules_and_retirements(tmp_path, day, included):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state,
                  rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"))
    assert result["status"] == "complete"
    requested = [code for batch in registry.requested_codes for code in batch]
    assert requested.count(RULE.code) == int(included)
    assert "000001.SH" in requested and "600887.SH" in requested
    assert "688216.SH" not in requested and "300903.SZ" not in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (105.48, 105.47, 105.49, 105.48):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1] and len(pusher.messages) == 2
    assert "云汉芯城" in pusher.messages[0][1] and "**105.49**元" in pusher.messages[0][1]
    registry.now = datetime(2026, 10, 27, 10, tzinfo=TZ)
    assert len(_run(registry, pusher, db, state)["events"]) == 1


def test_stale_quote_and_missing_calendar_fail_closed(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    missing = _run(registry, pusher, tmp_path / "missing.db", state)
    assert missing["status"] == "blocked_calendar" and not registry.call_count
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-10-23"}
    stale = _run(registry, pusher, db, state)
    assert stale["status"] == "source_failed" and not stale["events"] and not pusher.messages


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
    assert not saved["pending_events"] and failed["events"][0]["event_id"] in saved["sent_event_ids"]


def test_expired_pending_cannot_leak_via_long_lived_rule(tmp_path):
    registry, pusher, db, state = _setup(tmp_path, OPEN_DAYS[-1])
    pusher.succeed = False
    failed = _run(registry, pusher, db, state)
    assert failed["status"] == "push_failed"
    pusher.messages.clear()
    pusher.succeed = True
    registry.now = datetime(2026, 11, 2, 10, tzinfo=TZ)
    result = _run(registry, pusher, db, state, rules=(RULE, SSE_COMPOSITE_RECLAIM_3955))
    assert result["status"] == "complete" and not result["events"] and not pusher.messages
    assert RULE.code not in registry.requested_codes[-1]
    saved = json.loads(state.read_text())
    assert failed["events"][0]["event_id"] in saved["expired_event_ids"] and not saved["pending_events"]


def test_cli_exposes_rule_without_sending():
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", RULE.rule_id,
                             "--input-by", "pytest", "--confirm-real-push"])
    assert args.rule_id == RULE.rule_id
