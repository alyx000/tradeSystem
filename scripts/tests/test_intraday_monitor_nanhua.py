"""南华生物单日断板观察：盘中未封板不等于收盘断板。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import DEFAULT_RULES, NANHUA_BIO_BOARD_BREAK_20260923 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


def _setup(tmp_path, day="2026-09-23"):
    db = _calendar(tmp_path, dates=("2026-09-22", "2026-09-23", "2026-09-24"))
    registry, pusher = _Registry(price=13.31), _Pusher()
    registry.pre_close = 12.11
    registry.quote_overrides[RULE.code] = {"name": "南华生物"}
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    now = kwargs.pop("now", registry.now)
    return run_check(registry, rules=(RULE,), now=now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(13.31, True), (13.32, False), (13.33, False)])
def test_dynamic_limit_and_identity(price, matched):
    assert RULE.code == "000504.SZ" and RULE.instrument_name == "南华生物"
    assert RULE.threshold is None and RULE.threshold_mode == "daily_up_limit"
    assert RULE.valid_from == RULE.valid_until == date(2026, 9, 23)
    assert tuple(r for r in DEFAULT_RULES if r.code == RULE.code) == (RULE,)
    threshold = RULE.resolve_threshold({"pre_close": 12.11, "name": "南华生物"})
    assert threshold == 13.32
    assert RULE.is_active(price, threshold=threshold) is matched
    assert RULE.resolve_threshold({"pre_close": 10, "name": "南华生物"}) == 11


@pytest.mark.parametrize("day", ["2026-09-22", "2026-09-24"])
def test_outside_single_day_never_fetches_or_pushes(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "no_active_rules"
    assert registry.call_count == 0 and not pusher.messages


def test_initial_unsealed_dedupe_reseal_and_break_again(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (13.31, 13.20, 13.32, 13.31):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    message = pusher.messages[0][1]
    assert "南华生物" in message and "**13.32**元" in message
    assert "当前未封涨停" in message and "最终是否断板以收盘为准" in message
    assert "确认为当日断板" not in message


@pytest.mark.parametrize("close_price,events", [(13.31, 1), (13.32, 0)])
def test_final_confirmation_only_when_close_remains_unsealed(tmp_path, close_price, events):
    registry, pusher, db, state = _setup(tmp_path)
    assert _run(registry, pusher, db, state)["events"][0]["observation_phase"] == "intraday"
    registry.price = close_price
    registry.now = datetime(2026, 9, 23, 15, 0, tzinfo=TZ)
    closed = _run(registry, pusher, db, state, now=registry.now + timedelta(minutes=3))
    assert len(closed["events"]) == events
    if events:
        assert closed["events"][0]["observation_phase"] == "close"
        assert "确认为当日断板" in pusher.messages[-1][1]
    repeated = _run(registry, pusher, db, state, now=registry.now + timedelta(minutes=4))
    assert not repeated["events"] and len(pusher.messages) == 1 + events


def test_1459_snapshot_cannot_confirm_close(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.now = datetime(2026, 9, 23, 14, 59, tzinfo=TZ)
    result = _run(registry, pusher, db, state, now=registry.now + timedelta(minutes=1))
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages
    registry.now = datetime(2026, 9, 23, 15, 0, tzinfo=TZ)
    result = _run(registry, pusher, db, state, now=registry.now + timedelta(minutes=3))
    assert result["events"][0]["observation_phase"] == "close"


@pytest.mark.parametrize("pre_close", [None, 0, -1, "bad", float("nan")])
def test_missing_or_invalid_limit_basis_fails_closed(tmp_path, pre_close):
    registry, pusher, db, state = _setup(tmp_path)
    registry.pre_close = pre_close
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages


def test_stale_quote_and_missing_calendar_do_not_alert(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code]["quote_date"] = "2026-09-22"
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and not result["events"]
    registry.call_count = 0
    result = _run(registry, pusher, tmp_path / "absent.db", state)
    assert result["status"] == "blocked_calendar"
    assert registry.call_count == 0 and not pusher.messages


def test_dry_run_and_failed_delivery_retry(tmp_path):
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
