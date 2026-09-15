"""福龙马严格突破：首触、去重、有效期、失败重试与不推送预览。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor.rules import FULONGMA_BREAKOUT_14_15_20260916_24 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = (16, 17, 18, 21, 22, 23, 24)


def _setup(tmp_path, day=16):
    db = _calendar(tmp_path, dates=tuple(f"2026-09-{d}" for d in OPEN_DAYS),
                   closed_dates=("2026-09-19", "2026-09-20", "2026-09-25"))
    registry, pusher = _Registry(price=14.16), _Pusher()
    registry.now = datetime(2026, 9, day, 10, tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,expected", [(14.14, False), (14.15, False), (14.16, True)])
def test_strict_breakout(price, expected):
    assert RULE.code == "603686.SH" and RULE.instrument_name == "福龙马"
    assert RULE.is_active(price) is expected


def test_initial_dedupe_reentry_and_message(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (14.16, 14.20, 14.15, 14.16):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "福龙马" in pusher.messages[0][1] and "**14.15**元" in pusher.messages[0][1]


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_each_of_seven_open_days_is_enabled(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    assert RULE.is_effective_on(date(2026, 9, day))
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1


@pytest.mark.parametrize("day", [15, 19, 20, 25, 28])
def test_inactive_or_closed_day_does_not_fetch(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] in {"non_trade_day", "no_active_rules"}
    assert registry.call_count == 0 and pusher.messages == []


def test_stale_quote_never_pushes(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-15"}
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and result["events"] == []
    assert pusher.messages == []


def test_dry_run_and_failed_push_retry(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    preview = _run(registry, pusher, db, state, dry_run=True)
    assert len(preview["events"]) == 1 and not state.exists() and not pusher.messages
    pusher.succeed = False
    failed = _run(registry, pusher, db, state)
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retried = _run(registry, pusher, db, state)
    assert retried["status"] == "complete" and retried["events"] == []
    saved = json.loads(state.read_text())
    assert not saved["pending_events"]
    assert failed["events"][0]["event_id"] in saved["sent_event_ids"]
