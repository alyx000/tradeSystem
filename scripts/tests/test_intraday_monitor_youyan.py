"""有研硅已下线；显式传入历史规则仅用于能力回归测试。"""
from datetime import datetime, timedelta

import pytest

from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    YOUYAN_SILICON_BREAKOUT_46_14_20260916_30 as RULE,
    FULONGMA_BREAKOUT_14_36_20260916_24 as FULONGMA,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN_DAYS = (16, 17, 18, 21, 22, 23, 24, 28, 29, 30)


@pytest.mark.parametrize("day", [16, 30])
def test_retired_youyan_never_fetches_or_pushes_by_default(tmp_path, day):
    selected = tuple(rule for rule in DEFAULT_RULES if rule.code == RULE.code)
    assert selected == ()
    result, registry, pusher = _run(tmp_path, day, rules=selected)
    assert result["status"] == "no_rules"
    assert registry.call_count == 0 and not pusher.messages


def _run(tmp_path, day=16, price=46.15, rules=(RULE,), registry=None, pusher=None, **kwargs):
    db = _calendar(tmp_path, dates=tuple(f"2026-09-{d}" for d in OPEN_DAYS),
                   closed_dates=tuple(f"2026-09-{d}" for d in (19, 20, 25, 26, 27)))
    registry = registry or _Registry(price=price)
    registry.now = datetime(2026, 9, day, 10, tzinfo=TZ)
    pusher = pusher or _Pusher()
    result = run_check(registry, rules=rules, now=registry.now, db_path=db,
                       state_path=tmp_path / "state.json", pusher_factory=lambda: pusher, **kwargs)
    return result, registry, pusher


@pytest.mark.parametrize("price,expected", [(46.13, False), (46.14, False), (46.15, True)])
def test_strict_threshold(price, expected):
    assert RULE.code == "688432.SH" and RULE.instrument_name == "有研硅"
    assert RULE.is_active(price) is expected


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_ten_open_days_including_last_day(tmp_path, day):
    result, _, pusher = _run(tmp_path, day)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert "有研硅" in pusher.messages[0][1] and "**46.14**元" in pusher.messages[0][1]


@pytest.mark.parametrize("day", [15, 19, 20, 25, 26, 27])
def test_before_start_or_closed_days_never_fetch(tmp_path, day):
    result, registry, pusher = _run(tmp_path, day)
    assert result["status"] in {"non_trade_day", "no_active_rules"}
    assert registry.call_count == 0 and not pusher.messages


def test_october_expiry_and_fulongma_earlier_expiry(tmp_path):
    result, registry, pusher = _run(tmp_path, 30, rules=(FULONGMA, RULE))
    assert [e["rule_id"] for e in result["events"]] == [RULE.rule_id]
    registry.now += timedelta(days=1)
    calls = registry.call_count
    expired = run_check(registry, rules=(FULONGMA, RULE), now=registry.now,
                        db_path=tmp_path / "trade.db", state_path=tmp_path / "state.json",
                        pusher_factory=lambda: pusher)
    assert expired["status"] == "no_active_rules"
    assert registry.call_count == calls


def test_dedupe_and_reentry(tmp_path):
    result, registry, pusher = _run(tmp_path)
    assert len(result["events"]) == 1
    db = tmp_path / "trade.db"
    counts = []
    for price in (46.2, 46.14, 46.15):
        registry.price = price
        registry.now += timedelta(minutes=3)
        result = run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                           state_path=tmp_path / "state.json", pusher_factory=lambda: pusher)
        counts.append(len(result["events"]))
    assert counts == [0, 0, 1]


def test_two_rules_keep_distinct_events(tmp_path):
    result, _, _ = _run(tmp_path, rules=(FULONGMA, RULE))
    assert {e["rule_id"] for e in result["events"]} == {FULONGMA.rule_id, RULE.rule_id}
    assert len({e["event_id"] for e in result["events"]}) == 2
