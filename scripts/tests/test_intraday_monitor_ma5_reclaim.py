"""长春燃气：复用动态均线与同日上穿状态机，不改变其他规则。"""
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from services.intraday_monitor.rules import (
    CHANGCHUN_GAS_NEAR_MA20_20260914_22 as MA20,
    CHANGCHUN_GAS_RECLAIM_MA5_20260914_22 as RULE,
    DEFAULT_RULES,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_ma20_near import _setup, _days


def _setup_ma5(tmp_path, day=date(2026, 9, 14)):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.history_dates = registry.history_dates[-4:]
    registry.history_closes = [20, 20, 10, 10]
    registry.history_factors = [1, 1, 2, 2]
    return registry, pusher, db, state


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


def test_rule_registered_alongside_ma20_and_exact_seven_open_days():
    assert RULE in DEFAULT_RULES and MA20 in DEFAULT_RULES
    assert RULE.rule_id != MA20.rule_id and RULE.code == MA20.code == "600333.SH"
    assert RULE.direction == "above" and RULE.inclusive is False
    assert RULE.emit_on_initial_match is False
    assert RULE.threshold_mode == "intraday_ma" and RULE.threshold_window == 5
    assert [d.day for d in _days() if d.weekday() < 5 and RULE.is_effective_on(d)] == [
        14, 15, 16, 17, 18, 21, 22,
    ]


def test_ma5_and_ma20_keep_independent_thresholds_and_alerts(tmp_path, monkeypatch):
    registry, pusher, db, state = _setup(tmp_path)
    original_call = registry.call_specific

    def bounded_history(provider, capability, *args):
        result = original_call(provider, capability, *args)
        if provider == "tushare":
            result.data = [row for row in result.data if args[1] <= row["trade_date"] <= args[2]]
        return result

    monkeypatch.setattr(registry, "call_specific", bounded_history)
    counts = []
    for price in (10, 10.1, 10.2):
        registry.price = price
        result = run_check(registry, rules=(MA20, RULE), now=registry.now,
                           db_path=db, state_path=state, pusher_factory=lambda: pusher)
        assert result["status"] == "complete"
        counts.append([event["rule_id"] for event in result["events"]])
        saved = json.loads(state.read_text())["rules"]
        assert saved[MA20.rule_id]["last_threshold"] == pytest.approx((190 + price) / 20)
        assert saved[RULE.rule_id]["last_threshold"] == pytest.approx((40 + price) / 5)
        registry.now += timedelta(minutes=3)
    assert counts == [[MA20.rule_id], [RULE.rule_id], []]


@pytest.mark.parametrize("price,active", [(9.99, False), (10, False), (10.01, True)])
def test_dynamic_ma5_strict_boundary(price, active):
    ma = RULE.resolve_threshold({"price": price}, historical_closes=[10] * 4)
    assert ma == pytest.approx((40 + price) / 5)
    assert RULE.is_active(price, threshold=ma) is active


@pytest.mark.parametrize("direction,inclusive,expected", [
    ("above", False, False), ("below", False, False),
    ("above", True, True), ("below", True, True),
])
@pytest.mark.parametrize("threshold", [10 - 2e-15, 10 + 2e-15])
def test_dynamic_ma_roundoff_cannot_change_equality(direction, inclusive, expected, threshold):
    rule = replace(RULE, direction=direction, inclusive=inclusive)
    assert rule.is_active(10, threshold=threshold) is expected


def test_cached_price_sequence_returns_to_equal_without_false_reclaim(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    for price in (10, 9.51, 9.50, 10):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete" and result["events"] == []
        registry.now += timedelta(minutes=3)
    assert not pusher.messages
    registry.price = 10.01
    assert len(_run(registry, pusher, db, state)["events"]) == 1


def test_initial_above_suppressed_equal_then_reclaim_and_dedupe(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    counts = []
    for price in (10.1, 10.2, 10, 10.01, 10.2, 9.9, 10.1):
        registry.price = price
        result = _run(registry, pusher, db, state)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        saved = json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((40 + price) / 5)
        assert len(saved["threshold_basis_dates"]) == 4
        registry.now += timedelta(minutes=3)
    assert counts == [0, 0, 0, 1, 0, 0, 1]
    assert len(registry.history_calls) == 2
    assert len(pusher.messages) == 2
    message = pusher.messages[0][1]
    assert "长春燃气" in message and "重新站上" in message and "动态前复权MA5" in message
    assert "4 个已收盘交易日（前复权）" in message and "当日最新价" in message


def test_next_day_rebaselines_and_reanchors_ex_date(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    registry.price = 9.9
    _run(registry, pusher, db, state)
    registry.now += timedelta(days=1)
    registry.history_dates = ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"]
    registry.pre_close = 5
    registry.price = 5.1
    assert _run(registry, pusher, db, state)["events"] == []
    saved = json.loads(state.read_text())["rules"][RULE.rule_id]
    assert saved["last_threshold"] == pytest.approx(5.02)
    registry.now += timedelta(minutes=3)
    registry.price = 5
    assert _run(registry, pusher, db, state)["events"] == []
    registry.now += timedelta(minutes=3)
    registry.price = 5.1
    assert len(_run(registry, pusher, db, state)["events"]) == 1
    assert len(registry.history_calls) == 4


@pytest.mark.parametrize("broken", ["history_gap", "factor_gap", "stale", "bad_anchor"])
def test_missing_evidence_cannot_create_baseline_or_alert(tmp_path, broken):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    if broken == "history_gap":
        registry.history_closes.pop()
    elif broken == "factor_gap":
        registry.history_factors.pop()
    elif broken == "stale":
        registry.quote_overrides[RULE.code] = {"quote_date": "2026-09-11"}
    else:
        registry.pre_close = 0
    result = _run(registry, pusher, db, state)
    assert result["status"] == "source_failed" and result["events"] == []
    assert RULE.rule_id not in json.loads(state.read_text())["rules"]
    assert pusher.messages == []


@pytest.mark.parametrize("day", [13, 19, 20, 23])
def test_invalid_dates_never_fetch_or_push(tmp_path, day):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, day))
    result = _run(registry, pusher, db, state)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert registry.call_count == 0 and registry.history_calls == []
    assert pusher.messages == []


def test_final_day_reclaim_retries_and_dry_run_preserves_state(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, 22))
    registry.price = 10
    _run(registry, pusher, db, state)
    before = state.read_bytes()
    registry.now += timedelta(minutes=3)
    registry.price = 10.1
    preview = _run(registry, pusher, db, state, dry_run=True)
    assert len(preview["events"]) == 1
    assert state.read_bytes() == before and not pusher.messages
    pusher.succeed = False
    failed = _run(registry, pusher, db, state)
    assert failed["status"] == "push_failed" and len(failed["events"]) == 1
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retried = _run(registry, pusher, db, state)
    assert retried["status"] == "complete" and retried["events"] == []
    saved = json.loads(state.read_text())
    assert not saved["pending_events"]
    assert failed["events"][0]["event_id"] in saved["sent_event_ids"]
