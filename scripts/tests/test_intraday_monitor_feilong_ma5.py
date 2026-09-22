"""飞龙股份：复用动态均线与同日上穿状态机，不改变其他规则。"""
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from services.intraday_monitor.rules import (
    FEILONG_BREAKOUT_57_16_20260917_1008 as FIXED,
    FEILONG_RECLAIM_MA5_20260921_23 as RULE,
    DEFAULT_RULES,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_ma20_near import _setup, _days


def _setup_ma5(tmp_path, day=date(2026, 9, 21)):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.history_dates = registry.history_dates[-4:]
    registry.history_closes = [20, 20, 10, 10]
    registry.history_factors = [1, 1, 2, 2]
    return registry, pusher, db, state


def _run(registry, pusher, db, state, **kwargs):
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


def test_rule_registered_alongside_fixed_price_and_exact_three_open_days():
    assert RULE in DEFAULT_RULES and FIXED in DEFAULT_RULES
    assert RULE.rule_id != FIXED.rule_id and RULE.code == FIXED.code == "002536.SZ"
    assert RULE.direction == "above" and RULE.inclusive is False
    assert RULE.emit_on_initial_match is False
    assert RULE.threshold_mode == "intraday_ma" and RULE.threshold_window == 5
    assert RULE.threshold_provider == "tushare"
    assert [d.day for d in _days() if d.weekday() < 5 and RULE.is_effective_on(d)] == [21, 22, 23]
    assert FIXED.threshold == 57.16 and FIXED.valid_until == date(2026, 10, 8)


def test_fixed_price_and_ma5_share_quotes_but_keep_independent_alerts(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    registry.pre_close = 57
    registry.history_closes = [114, 114, 57, 57]
    counts = []
    for price in (57.10, 57, 57.01, 57.17):
        registry.price = price
        result = run_check(registry, rules=(FIXED, RULE), now=registry.now,
                           db_path=db, state_path=state, pusher_factory=lambda: pusher)
        assert result["status"] == "complete"
        counts.append([event["rule_id"] for event in result["events"]])
        saved = json.loads(state.read_text())["rules"]
        assert saved[FIXED.rule_id]["last_threshold"] == 57.16
        assert saved[RULE.rule_id]["last_threshold"] == pytest.approx((228 + price) / 5)
        assert registry.requested_codes[-1] == ["002536.SZ"]
        registry.now += timedelta(minutes=3)
    assert counts == [[], [], [RULE.rule_id], [FIXED.rule_id]]
    assert registry.call_count == 4 and len(registry.history_calls) == 2


@pytest.mark.parametrize("day", [21, 22, 23])
def test_all_three_open_days_can_emit_reclaim(tmp_path, day):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, day))
    registry.price = 10
    assert _run(registry, pusher, db, state)["events"] == []
    registry.now += timedelta(minutes=3)
    registry.price = 10.01
    result = _run(registry, pusher, db, state)
    assert result["status"] == "complete" and len(result["events"]) == 1


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
    assert "飞龙股份" in message and "重新站上" in message and "动态前复权MA5" in message
    assert "4 个已收盘交易日（前复权）" in message and "当日最新价" in message


def test_next_day_rebaselines_and_reanchors_ex_date(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    registry.price = 9.9
    _run(registry, pusher, db, state)
    registry.now += timedelta(days=1)
    registry.history_dates = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
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


@pytest.mark.parametrize("day", [18, 19, 20, 24, 25])
def test_invalid_dates_never_fetch_or_push(tmp_path, day):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, day))
    result = _run(registry, pusher, db, state)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert registry.call_count == 0 and registry.history_calls == []
    assert pusher.messages == []


def test_final_day_reclaim_retries_and_dry_run_preserves_state(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, 23))
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


def test_ma_expiry_preserves_fixed_rule_without_history_fetch(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path, date(2026, 9, 24))
    registry.price = 57.17
    result = run_check(registry, rules=(FIXED, RULE), now=registry.now,
                       db_path=db, state_path=state, pusher_factory=lambda: pusher)
    assert result["status"] == "complete"
    assert [event["rule_id"] for event in result["events"]] == [FIXED.rule_id]
    assert registry.call_count == 1 and registry.history_calls == []


def test_ma_missing_history_does_not_block_fixed_alert(tmp_path):
    registry, pusher, db, state = _setup_ma5(tmp_path)
    registry.history_closes.pop()
    registry.price = 57.17
    result = run_check(registry, rules=(FIXED, RULE), now=registry.now,
                       db_path=db, state_path=state, pusher_factory=lambda: pusher)
    # 同股固定规则成功、均线缺源时整批应保留部分成功，不伪装全部失败。
    assert result["status"] == "partial"
    assert [event["rule_id"] for event in result["events"]] == [FIXED.rule_id]
    assert RULE.rule_id not in json.loads(state.read_text())["rules"]
