"""截图压力位与方盛延期：严格边界、分档去重及交易日衔接。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from services.intraday_monitor import (
    SSE_COMPOSITE_BREAKOUT_3870_20261012_20,
    SSE_COMPOSITE_BREAKOUT_3930_20261012_20,
    CHINEXT_BREAKOUT_3140_20261012_20,
    STAR200_BREAKOUT_1400_20261012_20,
    CSI1000_BREAKOUT_7340_20261012_20,
    FANGSHENG_REACH_11_11_20261013_21,
    FANGSHENG_REACH_11_11_20260921_1012,
    SSE_COMPOSITE_RECLAIM_3955,
    DEFAULT_RULES,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


PRESSURE_RULES = (
    SSE_COMPOSITE_BREAKOUT_3870_20261012_20,
    SSE_COMPOSITE_BREAKOUT_3930_20261012_20,
    CHINEXT_BREAKOUT_3140_20261012_20,
    STAR200_BREAKOUT_1400_20261012_20,
    CSI1000_BREAKOUT_7340_20261012_20,
)
EXPECTED = (
    ("上证指数", "000001.SH", 3870),
    ("上证指数", "000001.SH", 3930),
    ("创业板指", "399006.SZ", 3140),
    ("科创200", "000699.SH", 1400),
    ("中证1000", "000852.SH", 7340),
)
OPEN_DAYS = tuple(f"2026-10-{d:02d}" for d in (12, 13, 14, 15, 16, 19, 20))
EXTENSION_DAYS = tuple(f"2026-10-{d:02d}" for d in (13, 14, 15, 16, 19, 20, 21))
FANGSHENG_RULES = (FANGSHENG_REACH_11_11_20260921_1012, FANGSHENG_REACH_11_11_20261013_21)


def _setup(tmp_path, day="2026-10-12"):
    db = _calendar(tmp_path, dates=("2026-10-09", *OPEN_DAYS, "2026-10-21", "2026-10-22"),
                   closed_dates=("2026-10-17", "2026-10-18"))
    registry, pusher = _Registry(), _Pusher()
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, pusher, db, tmp_path / "state.json"


def _run(registry, pusher, db, state, rules, **kwargs):
    return run_check(registry, rules=rules, now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("rule,expected", zip(PRESSURE_RULES, EXPECTED))
@pytest.mark.parametrize("delta,matched", [(-0.01, False), (0, False), (0.01, True)])
def test_index_identity_strict_boundary_and_registration(rule, expected, delta, matched):
    assert (rule.instrument_name, rule.code, rule.threshold) == expected
    assert rule.direction == "above" and not rule.inclusive and rule.emit_on_initial_match
    assert rule.provider == "sina" and rule.threshold_mode == "fixed"
    assert rule.threshold_label == "压力位" and rule.value_unit == "点"
    assert rule.is_active(expected[2] + delta) is matched
    assert rule.valid_from == date(2026, 10, 12) and rule.valid_until == date(2026, 10, 20)
    assert DEFAULT_RULES.count(rule) == 1


@pytest.mark.parametrize("rule", PRESSURE_RULES)
@pytest.mark.parametrize("day", OPEN_DAYS)
def test_all_seven_open_days_and_final_day(tmp_path, rule, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.price = rule.threshold + 0.01
    result = _run(registry, pusher, db, state, (rule,))
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert result["events"][0]["rule_id"] == rule.rule_id
    assert registry.call_count == 1 and len(pusher.messages) == 1
    assert "压力位" in pusher.messages[0][1] and rule.instrument_name in pusher.messages[0][1]


@pytest.mark.parametrize("rule", PRESSURE_RULES)
@pytest.mark.parametrize("day,status", [("2026-10-09", "no_active_rules"),
    ("2026-10-17", "non_trade_day"), ("2026-10-18", "non_trade_day"),
    ("2026-10-21", "no_active_rules")])
def test_weekends_prestart_and_expiry_never_fetch_or_push(tmp_path, rule, day, status):
    registry, pusher, db, state = _setup(tmp_path, day)
    result = _run(registry, pusher, db, state, (rule,))
    assert result["status"] == status and not result["events"]
    assert registry.call_count == 0 and not pusher.messages


def test_two_sse_tiers_are_independent_and_shared_quote_is_fetched_once(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    for rule in PRESSURE_RULES[2:]:
        registry.quote_overrides[rule.code] = {"price": rule.threshold}
    low, high = [r.rule_id for r in PRESSURE_RULES[:2]]
    expected = ([], [low], [high], [], [high], [], [low, high])
    for price, ids in zip((3870, 3880, 3940, 3930, 3940, 3860, 3940), expected):
        registry.price = price
        result = _run(registry, pusher, db, state, PRESSURE_RULES)
        assert result["status"] == "complete"
        assert [e["rule_id"] for e in result["events"]] == ids
        assert result["quotes_checked"] == 4
        assert registry.requested_codes[-1].count("000001.SH") == 1
        registry.now += timedelta(minutes=3)
    assert len(pusher.messages) == 4


def test_initial_above_all_tiers_dedupes_and_resets_next_day(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.price = 3930.01
    for rule in PRESSURE_RULES[2:]:
        registry.quote_overrides[rule.code] = {"price": rule.threshold + 0.01}
    assert len(_run(registry, pusher, db, state, PRESSURE_RULES)["events"]) == 5
    registry.now += timedelta(minutes=3)
    assert not _run(registry, pusher, db, state, PRESSURE_RULES)["events"]
    registry.now = datetime(2026, 10, 13, 10, tzinfo=TZ)
    assert len(_run(registry, pusher, db, state, PRESSURE_RULES)["events"]) == 5


def test_existing_sse_3955_semantics_are_unchanged(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    rules = (*PRESSURE_RULES[:2], SSE_COMPOSITE_RECLAIM_3955)
    registry.price = 3960
    initial = _run(registry, pusher, db, state, rules)
    assert [e["rule_id"] for e in initial["events"]] == [r.rule_id for r in PRESSURE_RULES[:2]]
    registry.now += timedelta(minutes=3)
    registry.price = 3954
    assert not _run(registry, pusher, db, state, rules)["events"]
    registry.now += timedelta(minutes=3)
    registry.price = 3955
    result = _run(registry, pusher, db, state, rules)
    assert [e["rule_id"] for e in result["events"]] == [SSE_COMPOSITE_RECLAIM_3955.rule_id]


@pytest.mark.parametrize("rule", PRESSURE_RULES)
def test_old_quote_fails_closed(tmp_path, rule):
    registry, pusher, db, state = _setup(tmp_path)
    registry.price = rule.threshold + 1
    registry.quote_overrides[rule.code] = {"quote_date": "2026-10-09"}
    result = _run(registry, pusher, db, state, (rule,))
    assert result["status"] == "source_failed" and not result["events"] and not pusher.messages


def test_missing_calendar_fails_closed(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.now = datetime(2026, 10, 15, 10, tzinfo=TZ)
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing = _calendar(missing_dir, dates=("2026-10-12",))
    result = _run(registry, pusher, missing, state, PRESSURE_RULES)
    assert result["status"] == "blocked_calendar"
    assert registry.call_count == 0 and not pusher.messages


def test_preview_does_not_write_or_push_and_failed_push_retries(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.price = 3870.01
    rule = PRESSURE_RULES[0]
    assert len(_run(registry, pusher, db, state, (rule,), dry_run=True)["events"]) == 1
    assert not state.exists() and not pusher.messages
    pusher.succeed = False
    failed = _run(registry, pusher, db, state, (rule,))
    assert failed["status"] == "push_failed"
    pusher.succeed = True
    registry.now += timedelta(minutes=3)
    retry = _run(registry, pusher, db, state, (rule,))
    assert retry["status"] == "complete" and not retry["events"]
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and failed["events"][0]["event_id"] in saved["sent_event_ids"]


@pytest.mark.parametrize("price,matched", [(11.10, False), (11.11, True), (11.12, True)])
def test_fangsheng_extension_retains_inclusive_price(price, matched):
    rule = FANGSHENG_RULES[1]
    assert rule.code == "603998.SH" and rule.threshold == 11.11
    assert rule.inclusive and rule.emit_on_initial_match
    assert rule.is_active(price) is matched
    assert rule.valid_from == date(2026, 10, 13) and rule.valid_until == date(2026, 10, 21)
    assert FANGSHENG_RULES[0].valid_until == date(2026, 10, 12)
    assert all(r in DEFAULT_RULES for r in FANGSHENG_RULES)


@pytest.mark.parametrize("day", EXTENSION_DAYS)
def test_fangsheng_extra_seven_sessions_trigger(tmp_path, day):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.price = 11.11
    result = _run(registry, pusher, db, state, FANGSHENG_RULES)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert result["events"][0]["rule_id"] == FANGSHENG_RULES[1].rule_id
    assert registry.requested_codes == [["603998.SH"]] and len(pusher.messages) == 1


@pytest.mark.parametrize("day,index", [("2026-10-12", 0), ("2026-10-13", 1),
    ("2026-10-21", 1), ("2026-10-22", None)])
def test_fangsheng_old_new_handoff_has_no_gap_or_double_alert(tmp_path, day, index):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.price = 11.11
    effective = [r for r in DEFAULT_RULES if r.code == "603998.SH" and r.is_effective_on(registry.now.date())]
    assert effective == ([] if index is None else [FANGSHENG_RULES[index]])
    result = _run(registry, pusher, db, state, FANGSHENG_RULES)
    assert len(result["events"]) == (0 if index is None else 1)
    assert registry.call_count == (0 if index is None else 1)


def test_fangsheng_old_failed_event_is_not_replayed_on_extension_day(tmp_path):
    registry, pusher, db, state = _setup(tmp_path)
    registry.price = 11.11
    pusher.succeed = False
    old = _run(registry, pusher, db, state, FANGSHENG_RULES)
    assert old["status"] == "push_failed"
    pusher.succeed = True
    registry.now = datetime(2026, 10, 13, 10, tzinfo=TZ)
    new = _run(registry, pusher, db, state, FANGSHENG_RULES)
    assert new["status"] == "complete" and len(new["events"]) == 1
    saved = json.loads(state.read_text())
    assert old["events"][0]["event_id"] in saved["expired_event_ids"]
    assert not saved["pending_events"]


@pytest.mark.parametrize("rule", (*PRESSURE_RULES, FANGSHENG_RULES[1]))
def test_cli_selects_each_new_rule_without_sending(rule):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", rule.rule_id,
                             "--input-by", "pytest", "--confirm-real-push"])
    assert args.rule_id == rule.rule_id


@pytest.mark.parametrize("day,enabled", [("2026-10-09", False), ("2026-10-12", True),
    ("2026-10-13", True), ("2026-10-20", True), ("2026-10-21", False)])
def test_default_batch_only_uses_new_index_rules_inside_window(tmp_path, day, enabled):
    registry, pusher, db, state = _setup(tmp_path, day)
    registry.price = 7340.01
    rules = tuple(r for r in DEFAULT_RULES if r.threshold_mode == "fixed")
    result = _run(registry, pusher, db, state, rules)
    assert result["status"] == "complete"
    new_ids = {r.rule_id for r in PRESSURE_RULES}
    assert ({e["rule_id"] for e in result["events"]} & new_ids) == (new_ids if enabled else set())
    requested = [code for batch in registry.requested_codes for code in batch]
    # 上证3955长期规则仍取同一代码；新科创/创业板/中证代码则到期停止。
    assert requested.count("000001.SH") == 1
    for code in ("399006.SZ", "000699.SH", "000852.SH"):
        assert (code in requested) is enabled


def test_expired_pressure_pending_does_not_leak_through_long_term_rule(tmp_path):
    registry, pusher, db, state = _setup(tmp_path, "2026-10-20")
    registry.price = 3870.01
    rules = (PRESSURE_RULES[0], SSE_COMPOSITE_RECLAIM_3955)
    pusher.succeed = False
    failed = _run(registry, pusher, db, state, rules)
    assert failed["status"] == "push_failed"
    before = len(pusher.messages)
    pusher.succeed = True
    registry.now = datetime(2026, 10, 21, 10, tzinfo=TZ)
    expired = _run(registry, pusher, db, state, rules)
    assert expired["status"] == "complete" and not expired["events"]
    assert len(pusher.messages) == before
    saved = json.loads(state.read_text())
    assert failed["events"][0]["event_id"] in saved["expired_event_ids"]
    assert not saved["pending_events"]
