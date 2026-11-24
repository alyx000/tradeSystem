"""工商银行A股严格跌破监控，隔离行情和钉钉。"""
from datetime import date, datetime

from services.intraday_monitor import DEFAULT_RULES, ICBC_BELOW_8_88_20261125_1201 as RULE
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN = ("2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27",
        "2026-11-30", "2026-12-01", "2026-12-02")


def test_icbc_five_open_days_strict_threshold_and_repeat_alerts(tmp_path):
    assert (RULE.instrument_name, RULE.code, RULE.provider) == ("工商银行", "601398.SH", "sina")
    assert RULE.threshold_mode == "fixed" and RULE.threshold == 8.88
    assert RULE.direction == "below" and not RULE.inclusive and RULE.emit_on_initial_match
    assert RULE in DEFAULT_RULES
    assert tuple(d for d in OPEN if RULE.is_effective_on(date.fromisoformat(d))) == OPEN[1:-1]

    registry, pusher = _Registry(), _Pusher()
    db = _calendar(tmp_path, dates=OPEN, closed_dates=("2026-11-28", "2026-11-29"))
    state = tmp_path / "state.json"

    def tick(day, mm, price, status, alerts=0):
        registry.now = datetime.fromisoformat(f"{day}T10:{mm}:00").replace(tzinfo=TZ)
        registry.price = price
        n = registry.call_count
        result = run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                           state_path=state, pusher_factory=lambda: pusher)
        assert result["status"] == status
        assert registry.call_count == n + (status == "complete")
        assert len(result["events"]) == alerts
        assert all(e["rule_id"] == RULE.rule_id and e["threshold"] == 8.88 for e in result["events"])

    tick("2026-11-24", "00", 8.87, "no_active_rules")
    tick("2026-11-25", "00", 8.89, "complete")
    tick("2026-11-25", "03", 8.88, "complete")
    tick("2026-11-25", "06", 8.87, "complete", 1)
    tick("2026-11-25", "09", 8.86, "complete")
    tick("2026-11-25", "12", 8.88, "complete")
    tick("2026-11-25", "15", 8.87, "complete", 1)
    tick("2026-11-28", "00", 8.87, "non_trade_day")
    tick("2026-12-01", "00", 8.87, "complete", 1)
    tick("2026-12-02", "00", 8.87, "no_active_rules")
    assert len(pusher.messages) == 3
    assert "工商银行" in pusher.messages[0][1] and "**8.88**元" in pusher.messages[0][1]


def test_icbc_stale_sina_quote_does_not_alert(tmp_path):
    registry, pusher = _Registry(price=8.87), _Pusher()
    registry.now = datetime(2026, 11, 25, 10, tzinfo=TZ)
    registry.quote_overrides[RULE.code] = {"quote_date": "2026-11-24"}
    result = run_check(registry, rules=(RULE,), now=registry.now,
                       db_path=_calendar(tmp_path, dates=OPEN), state_path=tmp_path / "state.json",
                       pusher_factory=lambda: pusher)
    assert result["status"] == "source_failed" and not result["events"] and not pusher.messages
