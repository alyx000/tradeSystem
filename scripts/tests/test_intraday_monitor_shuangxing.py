"""双星新材一个自然月严格突破，复用引擎并隔离真实行情和推送。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    FULONGMA_BREAKOUT_14_36_20260916_24,
    SHUANGXING_MATERIALS_BREAKOUT_12_98_20260917_1016 as RULE,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


START, END = date(2026, 9, 17), date(2026, 10, 16)
DAYS = tuple(START + timedelta(days=i) for i in range(30))
# 与已核对的 SSE 日历一致：中秋与国庆假期不检查，周末调休不补开市。
OPEN_DATES = tuple(
    date.fromisoformat(day) for day in (
        "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22",
        "2026-09-23", "2026-09-24", "2026-09-28", "2026-09-29",
        "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12",
        "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16",
    )
)


def _setup(tmp_path, day=START, price=12.99):
    db = _calendar(tmp_path, dates=tuple(map(str, OPEN_DATES)),
                   closed_dates=tuple(str(d) for d in DAYS if d not in OPEN_DATES))
    registry, pusher = _Registry(price=price), _Pusher()
    registry.now = datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=10)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(12.97, False), (12.98, False), (12.99, True)])
def test_strict_price_boundary(price, matched):
    assert RULE.code == "002585.SZ" and RULE.instrument_name == "双星新材"
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == START and RULE.valid_until == END
    assert tuple(r for r in DEFAULT_RULES if r.code == RULE.code) == (RULE,)
    assert FULONGMA_BREAKOUT_14_36_20260916_24.threshold == 14.36
    assert all(r.code != "688432.SH" for r in DEFAULT_RULES)


@pytest.mark.parametrize("day", DAYS)
def test_whole_month_calendar_and_expiry(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    if day in OPEN_DATES:
        assert result["status"] == "complete" and len(result["events"]) == 1
        assert registry.call_count == 1 and len(pusher.messages) == 1
    else:
        assert result["status"] == "non_trade_day"
        assert registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("day", [date(2026, 9, 16), date(2026, 10, 17), date(2026, 10, 19)])
def test_outside_validity_never_fetches(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state)
    assert result["status"] == "no_active_rules"
    assert registry.call_count == 0 and not pusher.messages


def test_initial_match_dedupe_reentry_and_message(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (12.99, 13.01, 12.98, 12.99):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    assert len(pusher.messages) == 2
    assert "双星新材" in pusher.messages[0][1] and "**12.98**元" in pusher.messages[0][1]


def test_stale_quote_is_not_an_alert(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-16"}
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages


def test_preview_and_retry_do_not_duplicate_event(tmp_path):
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
