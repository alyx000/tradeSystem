"""科创50及创业板指动态MA5：原始指数点位、期限和告警语义。"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from providers.base import DataResult
from services.intraday_monitor import (
    CHINEXT_BELOW_MA5_20261110_16 as CHINEXT,
    STAR50_BELOW_MA5_20261110_1207 as STAR50,
)
from services.intraday_monitor.guards import previous_open_dates
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Pusher, _calendar, TZ


# 生产窗口另由真实SSE日历核实；这里是隔离的测试日历。
DAYS = tuple(date(2026, 11, 4) + timedelta(days=i) for i in range(35))
OPEN = tuple(d.isoformat() for d in DAYS if d.weekday() < 5)
CLOSED = tuple(d.isoformat() for d in DAYS if d.weekday() >= 5)


@pytest.fixture(params=[STAR50, CHINEXT], ids=["star50", "chinext"])
def rule(request):
    return request.param


class Registry:
    def __init__(self, db, day, rule):
        self.db = db
        self.rule = rule
        self.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
        self.price = 99.0
        self.calls = []
        self.bad = None

    def get_provider(self, name):
        assert name in {"sina", "tushare"}
        return SimpleNamespace(initialize=lambda: True, supports=lambda cap: cap == "get_index_daily_range")

    def call_specific(self, name, cap, *args):
        self.calls.append((name, cap, args))
        if name == "sina":
            assert cap == "get_realtime_quotes" and args == ([self.rule.code],)
            return DataResult([{
                "code": self.rule.code, "price": self.price, "pre_close": 999,
                "quote_date": ("2026-11-09" if self.bad == "stale" else self.now.date().isoformat()),
                "quote_time": self.now.time().isoformat(),
            }], "sina")
        assert cap == "get_index_daily_range", "不得请求股票日线或复权因子"
        dates = previous_open_dates(self.now.date().isoformat(), 4, db_path=self.db)
        assert args == (self.rule.code, dates[0], dates[-1])
        if self.bad == "source":
            return DataResult(None, "tushare", error="模拟源失败")
        rows = [{"trade_date": d, "ts_code": self.rule.code, "close": 100} for d in dates]
        if self.bad == "gap":
            rows.pop()
        elif self.bad == "duplicate":
            rows.append(rows[-1])
        elif self.bad == "identity":
            rows[0]["ts_code"] = "000001.SH"
        elif self.bad == "invalid":
            rows[0]["close"] = float("nan")
        return DataResult(rows, "tushare:index_daily")


def setup(tmp_path, rule, day="2026-11-10"):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    return Registry(db, day, rule), _Pusher(), tmp_path / "state.json"


def run(setup, **kwargs):
    r, p, state = setup
    return run_check(r, rules=(r.rule,), now=r.now, db_path=r.db,
                     state_path=state, pusher_factory=lambda: p, **kwargs)


def test_registered_index_rule_and_open_day_window(rule):
    expected = ("000688.SH", "科创50", date(2026, 12, 7), 20) if rule is STAR50 else (
        "399006.SZ", "创业板指", date(2026, 11, 16), 5,
    )
    code, name, until, days = expected
    assert rule in DEFAULT_RULES
    assert (rule.code, rule.instrument_name, rule.direction) == (code, name, "below")
    assert (rule.valid_from, rule.valid_until) == (date(2026, 11, 10), until)
    assert len([d for d in OPEN if "2026-11-10" <= d <= until.isoformat()]) == days
    assert (rule.provider, rule.threshold_provider) == ("sina", "tushare")
    assert (rule.threshold_mode, rule.threshold_history_mode, rule.threshold_period) == ("intraday_ma", "index", "day")
    assert rule.threshold is None and rule.threshold_window == 5
    assert not rule.inclusive and rule.emit_on_initial_match


@pytest.mark.parametrize("last_day", [False, True], ids=["first", "last"])
def test_first_below_alert_uses_four_prior_raw_closes_and_current_point(tmp_path, rule, last_day):
    day = (rule.valid_until if last_day else rule.valid_from).isoformat()
    s = setup(tmp_path, rule, day)
    out = run(s)
    assert out["status"] == "complete" and out["pushed"]
    event = out["events"][0]
    assert event["threshold"] == pytest.approx(99.8)
    assert event["threshold_source"] == "tushare:index_daily"
    assert event["threshold_anchor_pre_close"] is None
    assert "指数原始点位" in s[1].messages[0][1]
    if day == "2026-11-10":
        assert event["threshold_basis_dates"] == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]


def test_equality_dedup_reentry_cache_and_next_day_refresh(tmp_path, rule):
    s = setup(tmp_path, rule)
    r, p, state = s
    counts = []
    for price in (100, 99, 98, 101, 99):
        r.price = price
        out = run(s)
        assert out["status"] == "complete"
        counts.append(len(out["events"]))
        saved = json.loads(state.read_text())["rules"][rule.rule_id]
        assert saved["last_threshold"] == pytest.approx((400 + price) / 5)
        r.now += timedelta(minutes=3)
    assert counts == [0, 1, 0, 0, 1]
    assert sum(cap == "get_index_daily_range" for _, cap, _ in r.calls) == 1
    r.now = datetime(2026, 11, 11, 10, tzinfo=TZ)
    assert run(s)["pushed"]
    assert sum(cap == "get_index_daily_range" for _, cap, _ in r.calls) == 2


@pytest.mark.parametrize("bad", ["source", "gap", "duplicate", "identity", "invalid", "calendar", "stale"])
def test_missing_or_bad_evidence_never_creates_baseline_or_pushes(tmp_path, rule, bad):
    s = setup(tmp_path, rule)
    r, p, state = s
    r.bad = bad
    if bad == "calendar":
        with sqlite3.connect(r.db) as conn:
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-07'")
    out = run(s)
    assert out["status"] == "source_failed" and not out["events"] and not p.messages
    assert rule.rule_id not in json.loads(state.read_text())["rules"]


@pytest.mark.parametrize("phase", ["before", "weekend", "after"])
def test_inactive_day_never_fetches_or_pushes(tmp_path, rule, phase):
    day = {"before": "2026-11-09", "weekend": "2026-11-14",
           "after": (rule.valid_until + timedelta(days=1)).isoformat()}[phase]
    s = setup(tmp_path, rule, day)
    assert run(s)["status"] in {"no_active_rules", "non_trade_day"}
    assert not s[0].calls and not s[1].messages


def test_dry_run_and_expiry_drops_pending(tmp_path, rule):
    s = setup(tmp_path, rule, rule.valid_until.isoformat())
    r, p, state = s
    assert run(s, dry_run=True)["status"] == "dry_run"
    assert not state.exists() and not p.messages
    p.succeed = False
    assert run(s)["status"] == "push_failed"
    r.now += timedelta(days=1)
    r.calls.clear()
    assert run(s)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"] and not r.calls
