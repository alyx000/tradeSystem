"""中证2000七交易日严格跌破提醒；外网、推送及状态均隔离。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import CSI2000_BELOW_2585_35_20261030_1109 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = ("2026-10-30",) + tuple(f"2026-11-{day:02d}" for day in (2, 3, 4, 5, 6, 9))
CLOSED_DAYS = ("2026-10-31", "2026-11-01", "2026-11-07", "2026-11-08")


def _setup(tmp_path, day=OPEN_DAYS[0]):
    db = _calendar(tmp_path, dates=("2026-10-29", *OPEN_DAYS, "2026-11-10"),
                   closed_dates=CLOSED_DAYS)
    registry, pusher = _Registry(price=2585.34), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, *, rules=(RULE,), **kwargs):
    return run_check(registry, rules=rules, now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", ((2585.34, True), (2585.35, False), (2585.36, False)))
def test_identity_and_strict_boundary(price, matched):
    assert (RULE.instrument_name, RULE.code, RULE.threshold) == ("中证2000", "932000.CSI", 2585.35)
    assert RULE.provider == "eastmoney" and RULE.threshold_mode == "fixed"
    assert RULE.direction == "below" and not RULE.inclusive and RULE.emit_on_initial_match
    assert (RULE.value_label, RULE.value_unit, RULE.action_text) == ("点位", "", "跌破")
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 10, 30) and RULE.valid_until == date(2026, 11, 9)
    assert [r for r in DEFAULT_RULES if r.code == RULE.code and r.is_effective_on(RULE.valid_from)] == [RULE]


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_exact_seven_open_days_include_final_day(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert registry.requested_codes == [[RULE.code]] and len(pusher.messages) == 1


@pytest.mark.parametrize("day", ("2026-10-29", *CLOSED_DAYS, "2026-11-10"))
def test_outside_window_and_closed_days_do_not_fetch_or_push(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    # 周末在规则自然日窗口内，由只读日历门禁拦下。
    assert result["status"] == ("non_trade_day" if day in CLOSED_DAYS else "no_active_rules")
    assert not registry.call_count and not pusher.messages


def test_calendar_closed_inside_window_does_not_fetch_or_push(tmp_path):
    registry, pusher = _Registry(price=2585.34), _Pusher()
    registry.now = datetime(2026, 11, 2, 10, tzinfo=TZ)
    db = _calendar(tmp_path, dates=("2026-11-02",), is_open=0)
    result = _run(registry, pusher, db, tmp_path / "state.json")
    assert result["status"] == "non_trade_day" and not registry.call_count and not pusher.messages


@pytest.mark.parametrize("day,included", (("2026-10-29", False), ("2026-10-30", True),
                                         ("2026-11-09", True), ("2026-11-10", False)))
def test_default_batch_window_preserves_other_rules_and_retirements(tmp_path, day, included):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state,
                  rules=tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed"))
    assert result["status"] == "complete"
    requested = [code for batch in registry.requested_codes for code in batch]
    assert requested.count(RULE.code) == int(included)
    assert "000001.SH" in requested
    assert "688216.SH" not in requested and "300903.SZ" not in requested


def test_initial_match_dedupe_reentry_message_and_next_day(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (2585.34, 2585.33, 2585.35, 2585.34):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1] and len(pusher.messages) == 2
    assert "中证2000" in pusher.messages[0][1] and "**2585.35**" in pusher.messages[0][1]
    registry.now = datetime(2026, 11, 2, 10, tzinfo=TZ)
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
    registry.now = datetime(2026, 11, 10, 10, tzinfo=TZ)
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


@pytest.mark.parametrize("change,expected", [
    ({}, "complete"),
    ({"f57": "000001"}, "source_failed"),
    ({"f58": "上证指数"}, "source_failed"),
    ({"f43": "-"}, "source_failed"),
    ({"f86": None}, "source_failed"),
    ({"f86": "stale"}, "source_failed"),
    ({"f86": "future"}, "source_failed"),
])
def test_real_provider_contract_to_monitor_with_mock_delivery(tmp_path, monkeypatch, change, expected):
    from providers.eastmoney_provider import EastmoneyProvider
    from providers.registry import ProviderRegistry
    from tests.test_eastmoney_provider import payload
    now = datetime(2026, 10, 30, 10, tzinfo=TZ)
    values = dict(change)
    if values.get("f86") == "stale":
        values["f86"] = int((now - timedelta(minutes=11)).timestamp())
    elif values.get("f86") == "future":
        values["f86"] = int((now + timedelta(minutes=60)).timestamp())
    provider, registry = EastmoneyProvider(), ProviderRegistry()
    monkeypatch.setattr(provider, "_fetch_one", lambda session, secid: payload(**values))
    registry.register(provider)
    db = _calendar(tmp_path, dates=OPEN_DAYS, closed_dates=CLOSED_DAYS)
    pusher = _Pusher()
    result = run_check(registry, rules=(RULE,), now=now, db_path=db,
                       state_path=tmp_path / "state.json", pusher_factory=lambda: pusher)
    assert result["status"] == expected
    if expected == "complete":
        assert result["events"][0]["source"] == "eastmoney:quote"
        assert result["events"][0]["price"] == 2585.34 and len(pusher.messages) == 1
    else:
        assert not result["events"] and not pusher.messages
