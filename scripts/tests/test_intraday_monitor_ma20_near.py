"""动态均线附近区间：计算、去重、缓存失效和七交易日期限。"""
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor.rules import CHANGCHUN_GAS_NEAR_MA20_20260914_22 as RULE
from services.intraday_monitor.service import run_check, run_e2e_test
from tests.test_intraday_monitor_service import _DynamicRegistry, _Pusher, _calendar, TZ


def _days():
    return [date(2026, 8, 1) + timedelta(days=i) for i in range(55)]


def _history(registry, day):
    registry.history_dates = [d.isoformat() for d in _days() if d < day and d.weekday() < 5][-19:]
    # 历史除权前后的原始价不同，前复权后均应为10元。
    registry.history_closes = [20.0] * 10 + [10.0] * 9
    registry.history_factors = [1.0] * 10 + [2.0] * 9


def _setup(tmp_path, day=date(2026, 9, 14)):
    db = _calendar(
        tmp_path,
        dates=tuple(d.isoformat() for d in _days() if d.weekday() < 5),
        closed_dates=tuple(d.isoformat() for d in _days() if d.weekday() >= 5),
    )
    registry = _DynamicRegistry(price=10.0)
    registry.pre_close = 10.0
    registry.now = datetime(day.year, day.month, day.day, 10, tzinfo=TZ)
    _history(registry, day)
    return registry, _Pusher(), db, tmp_path / "state.json"


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


def test_dynamic_ma_uses_19_history_closes_plus_current_price():
    assert RULE.resolve_threshold({"price": 11}, historical_closes=[10] * 19) == pytest.approx(10.05)
    with pytest.raises(ValueError):
        RULE.resolve_threshold({"price": 11}, historical_closes=[10] * 20)
    with pytest.raises(ValueError):
        RULE.resolve_threshold({"price": float("nan")}, historical_closes=[10] * 19)


@pytest.mark.parametrize("price,expected", [(99, True), (101, True), (100, True), (98.999, False), (101.001, False)])
def test_near_band_inclusive_and_stable_at_both_edges(price, expected):
    assert RULE.is_active(price, threshold=100) is expected


@pytest.mark.parametrize("ratio", [0.99, 1.01])
def test_dynamic_band_edges_after_recomputing_ma(ratio):
    # 解 p / ((190+p)/20) = ratio，不以四舍五入后的均线作为比较线。
    price = 190 * ratio / (20 - ratio)
    threshold = RULE.resolve_threshold({"price": price}, historical_closes=[10] * 19)
    assert RULE.is_active(price, threshold=threshold) is True


def test_failed_near_alert_retries_without_creating_duplicate(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    pusher.succeed = False
    first = _run(registry, pusher, db, state)
    assert first["status"] == "push_failed" and len(first["events"]) == 1
    original_id = first["events"][0]["event_id"]
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    second = _run(registry, pusher, db, state)
    assert second["status"] == "complete" and second["events"] == []
    saved = json.loads(state.read_text())
    assert saved["pending_events"] == []
    assert original_id in saved["sent_event_ids"]


@pytest.mark.parametrize("tolerance", [None, True, -1, 0, 100, float("nan"), float("inf")])
def test_near_rule_rejects_invalid_tolerance(tolerance):
    with pytest.raises(ValueError):
        replace(RULE, proximity_pct=tolerance)


def test_near_rule_rejects_ambiguous_configuration():
    for changes in ({"direction": "below"}, {"threshold_window": 1}, {"threshold_window": True},
                    {"value_mode": "daily_pct_change"}, {"threshold_provider": ""}):
        with pytest.raises(ValueError):
            replace(RULE, **changes)


def test_dynamic_qfq_ma_updates_during_cached_ticks_and_dedupes(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    counts = []
    for price in (10.2, 10.1, 10.05, 9.8, 10):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        saved = json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((190 + price) / 20)
        registry.now += timedelta(minutes=3)
    assert counts == [0, 1, 0, 0, 1]
    assert len(registry.history_calls) == 2
    assert len(pusher.messages) == 2
    message = pusher.messages[0][1]
    assert "长春燃气" in message and "MA±1%" in message
    assert "当前偏离" in message and "19 个已收盘交易日（前复权）" in message
    assert "当日最新价" in message


def test_initial_in_band_and_next_day_refetch_and_ex_date_anchor(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    assert len(_run(registry, pusher, db, state)["events"]) == 1
    registry.now += timedelta(days=1)
    _history(registry, registry.now.date())
    # 当日再除权：历史样本须锚定盘口昨收，不能直接比较旧价格坐标。
    registry.pre_close = registry.price = 5.0
    result = _run(registry, pusher, db, state)
    assert result["events"][0]["threshold"] == pytest.approx(5.0)
    assert len(registry.history_calls) == 4


def test_anchor_change_and_invalid_cached_price_force_history_refresh(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    _run(registry, pusher, db, state)
    registry.pre_close = registry.price = 5.0
    registry.now += timedelta(minutes=3)
    _run(registry, pusher, db, state)
    assert len(registry.history_calls) == 4
    payload = json.loads(state.read_text())
    payload["rules"][RULE.rule_id].pop("last_price")
    state.write_text(json.dumps(payload))
    registry.now += timedelta(minutes=3)
    assert _run(registry, pusher, db, state)["status"] == "complete"
    assert len(registry.history_calls) == 6


@pytest.mark.parametrize("broken", ["daily_gap", "factor_gap", "duplicate", "calendar_gap", "stale", "bad_anchor"])
def test_incomplete_evidence_never_creates_alert(tmp_path, broken):
    registry, pusher, db, state = _setup(tmp_path)
    if broken == "daily_gap":
        registry.history_closes.pop()
    elif broken == "factor_gap":
        registry.history_factors.pop()
    elif broken == "duplicate":
        registry.history_dates[-1] = registry.history_dates[-2]
    elif broken == "calendar_gap":
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-12'")
    elif broken == "stale":
        registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-11"}
    else:
        registry.pre_close = 0
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed"
    assert result["events"] == [] and pusher.messages == []


@pytest.mark.parametrize("day", [11, 13, 19, 20, 23])
def test_outside_seven_trading_days_does_not_fetch(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, date(2026, 9, day))
    result = _run(registry, pusher, db, state)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert registry.call_count == 0 and registry.history_calls == []
    assert pusher.messages == []


def test_exact_seven_open_days_and_final_day(tmp_path):
    active_days = [d for d in _days() if d.weekday() < 5 and RULE.is_effective_on(d)]
    assert [d.day for d in active_days] == [14, 15, 16, 17, 18, 21, 22]
    registry, pusher, db, state = _setup(tmp_path, date(2026, 9, 22))
    assert len(_run(registry, pusher, db, state)["events"]) == 1


def test_dry_run_keeps_state_and_push_untouched(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    result = _run(registry, pusher, db, state, dry_run=True)
    assert result["status"] == "dry_run" and len(result["events"]) == 1
    assert not state.exists() and pusher.messages == []


def test_e2e_dynamic_ma_is_isolated_and_resolves_real_rule(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    result = run_e2e_test(registry, rule=RULE, now=registry.now, db_path=db,
                          input_by="pytest", confirm_real_push=True, pusher_factory=lambda: pusher)
    assert result["status"] == "complete" and result["pushed"] is True
    assert result["production_threshold"] == pytest.approx(10.0)
    assert len(result["production_threshold_basis_dates"]) == 19
    assert result["production_proximity_pct"] == 1.0
    assert "正式触发范围为均线±1%" in pusher.messages[0][1]
    assert not state.exists()
