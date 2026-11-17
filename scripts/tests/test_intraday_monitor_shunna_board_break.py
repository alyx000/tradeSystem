"""顺钠股份盘中炸板提醒：同日触板后跌离，过期不再取数。"""
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import DEFAULT_RULES, SHUNNA_BOARD_BREAK_20261117 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import TZ, _calendar, _Pusher, _Registry


@pytest.fixture
def tick(tmp_path):
    db = _calendar(tmp_path, dates=("2026-11-16", "2026-11-17", "2026-11-18"))
    registry, pusher = _Registry(), _Pusher()
    registry.pre_close = 9.82
    registry.now = datetime(2026, 11, 17, 10, tzinfo=TZ)

    def run(price, high, *, at=None):
        registry.price = price
        registry.now = at or registry.now
        registry.quote_overrides[RULE.code] = {"name": RULE.instrument_name, "high": high}
        result = run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                           state_path=tmp_path / "state.json", pusher_factory=lambda: pusher)
        registry.now += timedelta(minutes=3)
        return result

    return run, registry, pusher


def test_rule_configuration_and_board_break_episode(tick):
    assert RULE in DEFAULT_RULES
    assert (RULE.instrument_name, RULE.code, RULE.provider) == ("顺钠股份", "000533.SZ", "sina")
    assert RULE.valid_from == RULE.valid_until == date(2026, 11, 17)
    assert RULE.threshold_mode == "daily_up_limit_break"
    assert RULE.threshold is None and not RULE.inclusive and RULE.emit_on_initial_match
    run, registry, pusher = tick
    results = [run(price, high) for price, high in (
        (10.77, 10.79), (10.80, 10.80), (10.79, 10.80),
        (10.77, 10.80), (10.80, 10.80), (10.78, 10.80),
    )]
    assert all(result["status"] == "complete" for result in results)
    assert [len(result["events"]) for result in results] == [0, 0, 1, 0, 0, 1]
    assert results[2]["events"][0]["threshold"] == 10.80
    assert all(codes == [RULE.code] for codes in registry.requested_codes)
    assert len(pusher.messages) == 2 and all("顺钠股份" in message for _, message in pusher.messages)


@pytest.mark.parametrize("day", [16, 18])
def test_outside_one_day_cannot_fetch_or_alert(tick, day):
    run, registry, pusher = tick
    out = run(10.79, 10.80, at=datetime(2026, 11, day, 10, tzinfo=TZ))
    assert out["status"] == "no_active_rules"
    assert not registry.requested_codes and not pusher.messages
