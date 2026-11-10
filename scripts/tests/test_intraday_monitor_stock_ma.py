"""个股动态前复权均线：隔离验证期限、含等于/严格边界及缺源保护。"""
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import (
    DENGKANG_TOUCH_MA20_20261111_24 as DENGKANG,
    JOINN_BELOW_MA5_20261111_17 as JOINN,
)
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _DynamicRegistry, _Pusher, _calendar, TZ


DAYS = tuple(date(2026, 10, 15) + timedelta(days=i) for i in range(42))
OPEN = tuple(d.isoformat() for d in DAYS if d.weekday() < 5)
CLOSED = tuple(d.isoformat() for d in DAYS if d.weekday() >= 5)
ACTIVE = ("2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17",
          "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")


@pytest.fixture(params=[DENGKANG, JOINN], ids=["dengkang", "joinn"])
def rule(request):
    return request.param


def _setup(tmp_path, rule, day=ACTIVE[0]):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    r = _DynamicRegistry(price=10.0)
    r.pre_close = 10.0
    r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    count = rule.threshold_window - 1
    r.history_dates = [d for d in OPEN if d < day][-count:]
    r.history_closes = [20.0] + [10.0] * (count - 1)
    r.history_factors = [1.0] + [2.0] * (count - 1)
    return r, _Pusher(), db, tmp_path / "state.json"


def _run(setup, rule, **kwargs):
    r, p, db, state = setup
    return run_check(r, rules=(rule,), now=r.now, db_path=db, state_path=state,
                     pusher_factory=lambda: p, **kwargs)


def test_registered_identity_and_open_day_window(rule):
    expected = ("001328.SZ", "登康口腔", date(2026, 11, 24), 10, 20, True) if rule is DENGKANG else (
        "603127.SH", "昭衍新药", date(2026, 11, 17), 5, 5, False,
    )
    code, name, until, days, window, inclusive = expected
    assert rule in DEFAULT_RULES
    assert (rule.code, rule.instrument_name, rule.provider) == (code, name, "sina")
    assert (rule.valid_from, rule.valid_until) == (date(2026, 11, 11), until)
    assert tuple(d for d in OPEN if rule.is_effective_on(date.fromisoformat(d))) == ACTIVE[:days]
    assert rule.direction == "below" and rule.inclusive is inclusive and rule.emit_on_initial_match
    assert (rule.threshold_mode, rule.threshold_history_mode, rule.threshold_window) == ("intraday_ma", "stock_qfq", window)
    assert rule.threshold is None and rule.threshold_provider == "tushare"


def test_threshold_boundary_reentry_and_qfq_ma_updates(tmp_path, rule):
    setup = _setup(tmp_path, rule)
    r, p, _, state = setup
    counts = []
    for price in (10.01, 10.0, 9.99, 9.98, 10.01, 9.99):
        r.price = price
        out = _run(setup, rule)
        assert out["status"] == "complete"
        counts.append(len(out["events"]))
        saved = json.loads(state.read_text())["rules"][rule.rule_id]
        assert saved["last_threshold"] == pytest.approx((10 * (rule.threshold_window - 1) + price) / rule.threshold_window)
        r.now += timedelta(minutes=3)
    assert counts == ([0, 1, 0, 0, 0, 1] if rule is DENGKANG else [0, 0, 1, 0, 0, 1])
    assert len(r.history_calls) == 2
    first_date = "2026-10-15" if rule is DENGKANG else "2026-11-05"
    assert all(args == (rule.code, first_date, "2026-11-10") for _, args in r.history_calls)
    assert rule.instrument_name in p.messages[0][1] and f"前复权MA{rule.threshold_window}" in p.messages[0][1]


@pytest.mark.parametrize("bad", ["close", "factor", "stale", "calendar", "pre_close"])
def test_missing_or_bad_evidence_no_alert_or_baseline(tmp_path, rule, bad):
    setup = _setup(tmp_path, rule)
    r, p, db, state = setup
    if bad == "close":
        r.history_closes.pop()
    elif bad == "factor":
        r.history_factors.pop()
    elif bad == "stale":
        r.quote_overrides[rule.code] = {"quote_date": "2026-11-10"}
    elif bad == "calendar":
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-08'")
    else:
        r.pre_close = 0
    out = _run(setup, rule)
    assert out["status"] == "source_failed" and not out["events"] and not p.messages
    assert rule.rule_id not in json.loads(state.read_text())["rules"]


@pytest.mark.parametrize("phase", ["before", "weekend", "after"])
def test_outside_window_no_fetch_or_push(tmp_path, rule, phase):
    day = {"before": "2026-11-10", "weekend": "2026-11-14",
           "after": (rule.valid_until + timedelta(days=1)).isoformat()}[phase]
    setup = _setup(tmp_path, rule, day)
    assert _run(setup, rule)["status"] in {"no_active_rules", "non_trade_day"}
    assert not setup[0].requested_codes and not setup[0].history_calls and not setup[1].messages


def test_last_day_dry_run_reanchor_and_pending_expiry(tmp_path, rule):
    setup = _setup(tmp_path, rule, rule.valid_until.isoformat())
    r, p, _, state = setup
    r.pre_close = 5.0
    r.price = 5.0 if rule is DENGKANG else 4.99
    out = _run(setup, rule, dry_run=True)
    expected_ma = (5 * (rule.threshold_window - 1) + r.price) / rule.threshold_window
    assert out["status"] == "dry_run" and out["events"][0]["threshold"] == pytest.approx(expected_ma)
    assert not state.exists() and not p.messages
    p.succeed = False
    assert _run(setup, rule)["status"] == "push_failed"
    r.now += timedelta(days=1)
    r.requested_codes.clear()
    assert _run(setup, rule)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"] and not r.requested_codes
