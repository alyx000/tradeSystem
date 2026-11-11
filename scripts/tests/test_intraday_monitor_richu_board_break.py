"""炸板：当日成交触及过涨停，当前成交价严格低于涨停价。"""
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import TZ, _Pusher, _Registry, _calendar


@pytest.fixture
def monitor(tmp_path):
    rule, = (r for r in DEFAULT_RULES if r.rule_id == "richu-board-break-20261111-12")
    assert (rule.code, rule.instrument_name) == ("603366.SH", "日出东方")
    assert (rule.valid_from, rule.valid_until) == (date(2026, 11, 11), date(2026, 11, 12))
    registry, pusher = _Registry(), _Pusher()
    registry.pre_close = 10.88
    registry.now = datetime(2026, 11, 11, 10, 0, tzinfo=TZ)
    db = _calendar(tmp_path, dates=("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"))
    state = tmp_path / "state.json"

    def tick(price, high, *, at=None, override=None, dry_run=False):
        registry.now = at or registry.now
        registry.price = price
        registry.quote_overrides[rule.code] = {"name": "日出东方", "high": high, **(override or {})}
        result = run_check(
            registry, rules=(rule,), now=registry.now, db_path=db, state_path=state,
            pusher_factory=lambda: pusher, dry_run=dry_run,
        )
        registry.now += timedelta(minutes=3)
        return result

    return registry, pusher, state, tick


def test_no_touch_then_unsampled_touch_dedupe_reseal_and_break_again(monitor):
    _, pusher, _, tick = monitor
    results = [tick(price, high) for price, high in (
        (11.94, 11.96), (11.96, 11.97), (11.92, 11.97), (11.97, 11.97), (11.96, 11.97),
    )]
    assert [r["status"] for r in results] == ["complete"] * 5
    assert [len(r["events"]) for r in results] == [0, 1, 0, 0, 1]
    event = results[1]["events"][0]
    assert event["threshold"] == event["session_high"] == 11.97
    assert len(pusher.messages) == 2
    assert "炸板" in pusher.messages[0][1]
    assert "当日最高价" in pusher.messages[0][1]
    assert "确认为当日断板" not in pusher.messages[0][1]


def test_close_does_not_duplicate_existing_episode_and_next_day_resets(monitor):
    registry, pusher, _, tick = monitor
    assert len(tick(11.95, 11.97)["events"]) == 1
    assert not tick(11.95, 11.97, at=datetime(2026, 11, 11, 15, tzinfo=TZ))["events"]
    registry.pre_close = 11.95  # 13.145 → 13.15，第二天按当日涨停价判断
    assert not tick(13.13, 13.14, at=datetime(2026, 11, 12, 10, tzinfo=TZ))["events"]
    assert tick(13.14, 13.15)["events"][0]["threshold"] == 13.15
    assert len(pusher.messages) == 2


def test_close_can_first_detect_unobserved_break(monitor):
    _, _, _, tick = monitor
    result = tick(11.96, 11.97, at=datetime(2026, 11, 11, 15, tzinfo=TZ))
    assert result["events"][0]["observation_phase"] == "close"


@pytest.mark.parametrize("day", [10, 13])
def test_inactive_days_do_not_fetch(monitor, day):
    registry, pusher, _, tick = monitor
    result = tick(11.96, 11.97, at=datetime(2026, 11, day, 10, tzinfo=TZ))
    assert result["status"] == "no_active_rules"
    assert registry.call_count == 0 and not pusher.messages


@pytest.mark.parametrize("high", [None, 0, 11.95, 11.98, float("nan")])
def test_missing_or_inconsistent_high_fails_closed(monitor, high):
    _, pusher, _, tick = monitor
    result = tick(11.96, high)
    assert result["status"] == "source_failed" and not result["events"]
    assert not pusher.messages


def test_stale_quote_cannot_trigger_and_dry_run_is_read_only(monitor):
    _, pusher, state, tick = monitor
    stale = tick(11.96, 11.97, override={"quote_date": "2026-11-10"}, dry_run=True)
    assert stale["status"] == "source_failed"
    preview = tick(11.96, 11.97, dry_run=True)
    assert preview["status"] == "dry_run" and len(preview["events"]) == 1
    assert not state.exists() and not pusher.messages


def test_older_quote_cannot_rearm_and_duplicate_alert(monitor):
    _, pusher, _, tick = monitor
    assert len(tick(11.96, 11.97)["events"]) == 1
    # 09:59 虽在新鲜度窗口内，但早于已接收的 10:00 炸板行情。
    assert tick(11.97, 11.97, override={"quote_time": "09:59:00"})["status"] == "source_failed"
    assert not tick(11.95, 11.97)["events"]
    assert len(pusher.messages) == 1


def test_regressing_high_cannot_rearm_without_reseal(monitor):
    _, pusher, _, tick = monitor
    assert len(tick(11.96, 11.97)["events"]) == 1
    assert tick(11.95, 11.96)["status"] == "source_failed"
    assert not tick(11.95, 11.97)["events"]
    assert len(pusher.messages) == 1


def test_limit_basis_cannot_drift_within_a_trading_day(monitor):
    _, pusher, _, tick = monitor
    assert len(tick(11.96, 11.97)["events"]) == 1
    assert tick(11.95, 11.97, override={"pre_close": 10.89})["status"] == "source_failed"
    assert not tick(11.94, 11.97)["events"]
    assert len(pusher.messages) == 1


def test_same_timestamp_must_not_rearm_without_a_new_quote(monitor):
    _, pusher, _, tick = monitor
    assert len(tick(11.96, 11.97)["events"]) == 1
    duplicate = tick(11.96, 11.97, override={"quote_time": "10:00:00"})
    assert duplicate["status"] == "complete" and not duplicate["events"]
    assert tick(11.97, 11.97, override={"quote_time": "10:00:00"})["status"] == "source_failed"
    assert not tick(11.94, 11.97)["events"]
    assert len(pusher.messages) == 1


def test_failed_delivery_retries_with_original_touch_evidence(monitor):
    _, pusher, _, tick = monitor
    pusher.succeed = False
    failed = tick(11.96, 11.97)
    assert failed["status"] == "push_failed" and failed["pending_count"] == 1
    pusher.succeed = True
    retried = tick(11.97, 11.97)
    assert retried["status"] == "complete" and retried["pending_count"] == 0
    assert not retried["events"] and pusher.messages[-1] == pusher.messages[0]
