"""汇顶科技严格突破60.90元，10个开放日；外网与推送全部隔离。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import GOODIX_BREAKOUT_60_90_20261104_17 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ, _Result


ACTIVE = ("2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10",
          "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17")
CLOSED = ("2026-11-07", "2026-11-08", "2026-11-14", "2026-11-15")


def _setup(tmp_path, day="2026-11-04"):
    db = _calendar(tmp_path, dates=("2026-11-03", *ACTIVE, "2026-11-18"), closed_dates=CLOSED)
    registry = _Registry(price=60.91)
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, _Pusher(), db, tmp_path / "state.json"


def _run(setup, **kwargs):
    registry, pusher, db, state = setup
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(60.89, False), (60.90, False), (60.91, True)])
def test_identity_strict_boundary_and_window(price, matched):
    assert (RULE.code, RULE.instrument_name, RULE.provider) == ("603160.SH", "汇顶科技", "sina")
    assert RULE.threshold == 60.90 and RULE.threshold_mode == "fixed"
    assert RULE.direction == "above" and not RULE.inclusive and RULE.emit_on_initial_match
    assert RULE.valid_from == date(2026,11,4) and RULE.valid_until == date(2026,11,17)
    assert RULE in DEFAULT_RULES and len(ACTIVE) == 10
    assert RULE.is_active(price) is matched


@pytest.mark.parametrize("day", ACTIVE)
def test_all_ten_open_days_include_final_day(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] == "complete" and result["pushed"]
    assert setup[0].requested_codes == [[RULE.code]]
    assert len(setup[1].messages) == 1


@pytest.mark.parametrize("day", ("2026-11-03", *CLOSED, "2026-11-18"))
def test_inactive_dates_never_fetch_or_alert(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert not setup[0].requested_codes and not setup[1].messages


def test_initial_equality_dedupe_reentry_and_next_day(tmp_path):
    setup = _setup(tmp_path)
    registry, pusher, db, state = setup
    counts = []
    for price in (60.90, 60.91, 61.10, 60.90, 60.91):
        registry.price = price
        result = _run(setup)
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [0,1,0,0,1]
    message = pusher.messages[0][1]
    assert "汇顶科技" in message and "603160.SH" in message
    assert "已突破监控线 **60.90**元" in message
    registry.now = datetime(2026,11,5,10,tzinfo=TZ)
    assert _run(setup)["pushed"]


@pytest.mark.parametrize("bad", ["calendar", "stale_day", "stale_minute", "future", "source"])
def test_missing_evidence_never_alerts_or_builds_baseline(tmp_path, bad):
    setup = _setup(tmp_path)
    registry, pusher, db, state = setup
    if bad == "calendar":
        setup = registry, pusher, tmp_path / "missing.db", state
    elif bad == "source":
        registry.call_specific = lambda *args: _Result(None, error="模拟源失败")
    else:
        registry.quote_overrides[RULE.code] = {
            "stale_day":{"quote_date":"2026-11-03"},
            "stale_minute":{"quote_time":"09:49:00"},
            "future":{"quote_time":"10:10:00"},
        }[bad]
    result = _run(setup)
    assert result["status"] in {"blocked_calendar", "source_failed"}
    assert not result["events"] and not pusher.messages
    if state.exists(): assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_preview_retry_and_expired_pending(tmp_path):
    setup = _setup(tmp_path, "2026-11-17")
    registry, pusher, db, state = setup
    preview = _run(setup, dry_run=True)
    assert preview["status"] == "dry_run" and preview["events"]
    assert not state.exists() and not pusher.messages
    pusher.send_markdown = lambda **kwargs: False
    assert _run(setup)["status"] == "push_failed"
    registry.now += timedelta(minutes=3)
    pusher.send_markdown = lambda **kwargs: True
    assert _run(setup)["pushed"]
    registry.now += timedelta(minutes=3)
    assert not _run(setup)["pushed"]
    registry.now += timedelta(minutes=3)
    registry.price = 60.90
    _run(setup)
    registry.now += timedelta(minutes=3)
    registry.price = 60.91
    pusher.send_markdown = lambda **kwargs: False
    assert _run(setup)["status"] == "push_failed"
    registry.now = datetime(2026,11,18,10,tzinfo=TZ)
    registry.requested_codes.clear()
    pusher.send_markdown = lambda **kwargs: pytest.fail("过期pending不得推送")
    assert _run(setup)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"]
    assert not registry.requested_codes
