"""安图生物动态五周线：完整日历、前复权、缓存和20交易日期限。"""
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import ANTU_BIO_NEAR_WEEKLY_MA5_20261103_30 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check, run_e2e_test
from services.intraday_monitor.weekly import weekly_history_dates
from tests.test_intraday_monitor_service import _DynamicRegistry, _Registry, _Pusher, _calendar, TZ


DAYS = tuple(date(2026, 7, 1) + timedelta(days=i) for i in range(160))
HOLIDAYS = {date(2026, 9, 25), *(date(2026, 10, i) for i in range(1, 8))}
OPEN = tuple(d.isoformat() for d in DAYS if d.weekday() < 5 and d not in HOLIDAYS)
CLOSED = tuple(d.isoformat() for d in DAYS if d.isoformat() not in OPEN)


def _setup(tmp_path, day="2026-11-03"):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    with sqlite3.connect(db) as c:
        c.execute("alter table trade_calendar add column exchange text not null default 'SSE'")
    r = _DynamicRegistry(price=10)
    r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    r.pre_close = 10
    _history(r, db)
    return r, _Pusher(), db, tmp_path / "state.json"


def _history(r, db):
    plan = weekly_history_dates(r.now.date().isoformat(), 4, db_path=db)
    assert plan is not None
    history, basis = plan
    r.history_dates = history
    # 前两周除权前20元/因子1，后续10元/因子2，前复权全部10元。
    cut = basis[1]
    r.history_closes = [20 if d <= cut else 10 for d in history]
    r.history_factors = [1 if d <= cut else 2 for d in history]


def _run(setup, **kwargs):
    r, p, db, state = setup
    return run_check(r, rules=(RULE,), now=r.now, db_path=db,
                     state_path=state, pusher_factory=lambda: p, **kwargs)


def test_identity_window_and_period_configuration():
    assert (RULE.code, RULE.instrument_name) == ("603658.SH", "安图生物")
    assert RULE.threshold_mode == "intraday_ma" and RULE.threshold_period == "week"
    assert RULE.threshold_window == 5 and RULE.proximity_pct == 1 and RULE.inclusive
    assert RULE.valid_from == date(2026, 11, 3) and RULE.valid_until == date(2026, 11, 30)
    assert RULE in DEFAULT_RULES
    assert len([d for d in OPEN if "2026-11-03" <= d <= "2026-11-30"]) == 20
    for change in ({"threshold_period": "month"}, {"threshold_mode": "previous_close_ma"},
                   {"threshold_mode": "fixed", "threshold": 26}, {"threshold_window": 1}):
        with pytest.raises(ValueError):
            replace(RULE, **change)


def test_four_completed_weeks_and_current_week_are_not_four_last_days(tmp_path):
    r, p, db, state = setup = _setup(tmp_path)
    history, basis = weekly_history_dates("2026-11-03", 4, db_path=db)
    assert basis == ["2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30"]
    assert history[-1] == "2026-11-02" and "2026-10-08" in history
    result = _run(setup)
    event = result["events"][0]
    assert result["status"] == "complete" and event["threshold"] == pytest.approx(10)
    assert event["threshold_basis_dates"] == basis and event["threshold_period"] == "week"
    message = p.messages[0][1]
    assert "五周均线" in message and "4 个已完成交易周" in message
    assert "本周最新价" in message and "4 个已收盘交易日" not in message


@pytest.mark.parametrize("ratio", [0.99, 1.01])
def test_weekly_dynamic_band_inclusive_edges(ratio):
    price = 40 * ratio / (5 - ratio)
    threshold = RULE.resolve_threshold({"price": price}, historical_closes=[10]*4)
    assert RULE.is_active(price, threshold=threshold)


def test_dedupe_reentry_and_intraday_cache_uses_only_current_week_latest(tmp_path):
    r, p, db, state = setup = _setup(tmp_path)
    counts = []
    for price in (10, 10.01, 11, 10):
        r.price = price
        result = _run(setup)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        saved = json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((40+price)/5)
        r.now += timedelta(minutes=3)
    assert counts == [1,0,0,1] and len(r.history_calls) == 2 and len(p.messages) == 2


def test_day_week_rollover_and_same_price_anchor_still_refreshes(tmp_path):
    r, p, db, state = setup = _setup(tmp_path)
    _run(setup)
    for day in ("2026-11-04", "2026-11-09"):
        r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
        _history(r, db)
        result = _run(setup)
        assert result["status"] == "complete" and len(result["events"]) == 1
    assert len(r.history_calls) == 6
    assert json.loads(state.read_text())["rules"][RULE.rule_id]["threshold_basis_dates"][-1] == "2026-11-06"


def test_ex_date_anchor_recomputes_same_day(tmp_path):
    r, p, db, state = setup = _setup(tmp_path)
    _run(setup)
    r.price = r.pre_close = 5
    r.now += timedelta(minutes=3)
    result = _run(setup)
    assert result["status"] == "complete"
    assert json.loads(state.read_text())["rules"][RULE.rule_id]["last_threshold"] == pytest.approx(5)
    assert len(r.history_calls) == 4


def test_midweek_anchor_differs_from_friday_and_cache_does_not_rescale_weeks(tmp_path):
    r, p, db, state = setup = _setup(tmp_path, "2026-11-04")
    _, basis = weekly_history_dates("2026-11-04", 4, db_path=db)
    prices = dict(zip(basis, (8, 9, 10, 12)))
    prices.update({"2026-11-02": 14, "2026-11-03": 16})
    r.history_closes = [prices.get(d, 10) for d in r.history_dates]
    r.history_factors = [1] * len(r.history_dates)
    r.pre_close = 16
    assert _run(setup)["status"] == "complete"
    # 昨收锚是周二16，不是上周五12；错误使用周五会得到12.4，而非9.8。
    assert json.loads(state.read_text())["rules"][RULE.rule_id]["last_threshold"] == pytest.approx(9.8)
    r.price = 10.5
    r.now += timedelta(minutes=3)
    assert _run(setup)["status"] == "complete"
    assert json.loads(state.read_text())["rules"][RULE.rule_id]["last_threshold"] == pytest.approx(9.9)
    assert len(r.history_calls) == 2


@pytest.mark.parametrize("bad", ["calendar_gap", "weekly_close_gap", "anchor_gap", "factor_gap", "duplicate", "bad_anchor", "stale"])
def test_incomplete_evidence_never_alerts_or_builds_baseline(tmp_path, bad):
    r, p, db, state = setup = _setup(tmp_path)
    if bad == "calendar_gap":
        with sqlite3.connect(db) as c:
            c.execute("delete from trade_calendar where date='2026-10-11'")
    elif bad in {"weekly_close_gap", "anchor_gap", "duplicate"}:
        if bad == "duplicate":
            r.history_dates[-1] = r.history_dates[-2]
        else:
            index = r.history_dates.index("2026-10-16") if bad == "weekly_close_gap" else -1
            r.history_dates.pop(index); r.history_closes.pop(index); r.history_factors.pop(index)
    elif bad == "factor_gap":
        r.history_factors.pop()
    elif bad == "bad_anchor":
        r.pre_close = 0
    else:
        r.quote_overrides[RULE.code] = {"quote_date": "2026-11-02"}
    result = _run(setup)
    assert result["status"] == "source_failed" and not result["events"] and not p.messages
    if state.exists():
        assert RULE.rule_id not in json.loads(state.read_text())["rules"]


@pytest.mark.parametrize("day", ["2026-11-02", "2026-11-07", "2026-12-01"])
def test_outside_window_or_weekend_does_not_fetch(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert setup[0].call_count == 0 and not setup[1].messages


def test_final_day_preview_and_push_retry(tmp_path):
    setup = _setup(tmp_path, "2026-11-30")
    result = _run(setup, dry_run=True)
    assert len(result["events"]) == 1 and not setup[3].exists() and not setup[1].messages
    setup[1].succeed = False
    assert _run(setup)["status"] == "push_failed"
    setup[1].succeed = True
    setup[0].now += timedelta(minutes=3)
    result = _run(setup)
    assert result["status"] == "complete" and not result["events"]


@pytest.mark.parametrize("day", [d for d in OPEN if "2026-11-03" <= d <= "2026-11-30"])
def test_every_effective_open_day(tmp_path, day):
    result = _run(_setup(tmp_path, day))
    assert result["status"] == "complete" and len(result["events"]) == 1


def test_mock_e2e_does_not_write_formal_state(tmp_path):
    r, p, db, state = _setup(tmp_path)
    result = run_e2e_test(r, rule=RULE, now=r.now, db_path=db, confirm_real_push=True,
                          input_by="pytest", pusher_factory=lambda: p)
    assert result["status"] == "complete" and result["production_threshold"] == pytest.approx(10)
    assert len(result["production_threshold_basis_dates"]) == 4 and not state.exists()


def test_pending_expires_without_leaking_through_other_rule(tmp_path):
    setup = _setup(tmp_path, "2026-11-30")
    setup[1].succeed = False
    first = _run(setup)
    assert first["status"] == "push_failed"
    r = _Registry(price=3900)
    r.now = datetime(2026, 12, 1, 10, tzinfo=TZ)
    setup[1].succeed = True
    setup[1].messages.clear()
    result = run_check(r, rules=(RULE, SSE_COMPOSITE_RECLAIM_3955), now=r.now,
                       db_path=setup[2], state_path=setup[3], pusher_factory=lambda: setup[1])
    assert result["status"] == "complete" and not setup[1].messages
    assert first["events"][0]["event_id"] in json.loads(setup[3].read_text())["expired_event_ids"]


def test_holiday_short_week_last_open_day_and_fully_closed_week_skipped(tmp_path):
    setup = _setup(tmp_path, "2026-10-06")
    plan = weekly_history_dates("2026-10-12", 4, db_path=setup[2])
    assert plan[1][-2:] == ["2026-09-30", "2026-10-09"]
    # 本周周五若为休市日，使用周四，并非写死周五。
    with sqlite3.connect(setup[2]) as c:
        c.execute("update trade_calendar set is_open=0 where date='2026-10-09'")
    assert weekly_history_dates("2026-10-12", 4, db_path=setup[2])[1][-1] == "2026-10-08"
    with sqlite3.connect(setup[2]) as c:
        c.execute("update trade_calendar set is_open=0 where date='2026-10-08'")
    assert weekly_history_dates("2026-10-12", 4, db_path=setup[2])[1][-1] == "2026-09-30"
