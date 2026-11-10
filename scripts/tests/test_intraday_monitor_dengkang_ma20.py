"""登康口腔触及动态前复权MA20：隔离验证期限、边界及缺源保护。"""
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import DENGKANG_TOUCH_MA20_20261111_24 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _DynamicRegistry, _Pusher, _calendar, TZ


DAYS = tuple(date(2026, 10, 15) + timedelta(days=i) for i in range(42))
OPEN = tuple(d.isoformat() for d in DAYS if d.weekday() < 5)
CLOSED = tuple(d.isoformat() for d in DAYS if d.weekday() >= 5)
ACTIVE = ("2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17",
          "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24")


def _setup(tmp_path, day=ACTIVE[0]):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    r = _DynamicRegistry(price=10.0)
    r.pre_close = 10.0
    r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    r.history_dates = [d for d in OPEN if d < day][-19:]
    r.history_closes = [20.0] * 10 + [10.0] * 9
    r.history_factors = [1.0] * 10 + [2.0] * 9
    return r, _Pusher(), db, tmp_path / "state.json"


def _run(setup, **kwargs):
    r, p, db, state = setup
    return run_check(r, rules=(RULE,), now=r.now, db_path=db, state_path=state,
                     pusher_factory=lambda: p, **kwargs)


def test_registered_identity_and_ten_open_days():
    assert RULE in DEFAULT_RULES
    assert (RULE.code, RULE.instrument_name, RULE.provider) == ("001328.SZ", "登康口腔", "sina")
    assert (RULE.valid_from, RULE.valid_until) == (date(2026, 11, 11), date(2026, 11, 24))
    assert tuple(d for d in OPEN if RULE.is_effective_on(date.fromisoformat(d))) == ACTIVE
    assert RULE.direction == "below" and RULE.inclusive and RULE.emit_on_initial_match
    assert (RULE.threshold_mode, RULE.threshold_history_mode, RULE.threshold_window) == ("intraday_ma", "stock_qfq", 20)
    assert RULE.threshold is None and RULE.threshold_provider == "tushare"


def test_touch_reentry_and_qfq_ma_updates_from_19_closes(tmp_path):
    setup = _setup(tmp_path)
    r, p, _, state = setup
    counts = []
    for price in (10.01, 10.0, 9.99, 10.01, 9.99):
        r.price = price
        out = _run(setup)
        assert out["status"] == "complete"
        counts.append(len(out["events"]))
        saved = json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((190 + price) / 20)
        r.now += timedelta(minutes=3)
    assert counts == [0, 1, 0, 0, 1]
    assert len(r.history_calls) == 2
    assert all(args == (RULE.code, "2026-10-15", "2026-11-10") for _, args in r.history_calls)
    assert "登康口腔" in p.messages[0][1] and "前复权MA20" in p.messages[0][1]


@pytest.mark.parametrize("bad", ["close", "factor", "stale", "calendar", "pre_close"])
def test_missing_or_bad_evidence_no_alert_or_baseline(tmp_path, bad):
    setup = _setup(tmp_path)
    r, p, db, state = setup
    if bad == "close":
        r.history_closes.pop()
    elif bad == "factor":
        r.history_factors.pop()
    elif bad == "stale":
        r.quote_overrides[RULE.code] = {"quote_date": "2026-11-10"}
    elif bad == "calendar":
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-08'")
    else:
        r.pre_close = 0
    out = _run(setup)
    assert out["status"] == "source_failed" and not out["events"] and not p.messages
    assert RULE.rule_id not in json.loads(state.read_text())["rules"]


@pytest.mark.parametrize("day", ["2026-11-10", "2026-11-14", "2026-11-25"])
def test_outside_window_no_fetch_or_push(tmp_path, day):
    setup = _setup(tmp_path, day)
    assert _run(setup)["status"] in {"no_active_rules", "non_trade_day"}
    assert not setup[0].requested_codes and not setup[0].history_calls and not setup[1].messages


def test_last_day_dry_run_reanchor_and_pending_expiry(tmp_path):
    setup = _setup(tmp_path, ACTIVE[-1])
    r, p, _, state = setup
    r.pre_close = r.price = 5.0
    out = _run(setup, dry_run=True)
    assert out["status"] == "dry_run" and out["events"][0]["threshold"] == pytest.approx(5.0)
    assert not state.exists() and not p.messages
    p.succeed = False
    assert _run(setup)["status"] == "push_failed"
    r.now += timedelta(days=1)
    r.requested_codes.clear()
    assert _run(setup)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"] and not r.requested_codes
