from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import services.intraday_monitor.service as intraday_service
from services.intraday_monitor.service import run_check, run_e2e_test
from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    SSE_COMPOSITE_RECLAIM_3955,
    MonitorRule,
)


TZ = ZoneInfo("Asia/Shanghai")

BREACH_RULE = MonitorRule(
    "test-below-1572",
    "测试指数",
    "000688.SH",
    1572.0,
)
RECLAIM_RULE = MonitorRule(
    "test-reclaim-1582",
    "测试指数",
    "000688.SH",
    1582.0,
    direction="above",
    inclusive=True,
    emit_on_initial_match=False,
    action_label="收复",
)
TEST_RULES = (BREACH_RULE, RECLAIM_RULE)


class _Result:
    def __init__(self, data=None, error=None, source="sina"):
        self.data = data
        self.error = error
        self.source = source
        self.success = error is None and data is not None


class _Provider:
    def initialize(self):
        return True


class _Registry:
    def __init__(self, price=1571.0):
        self.price = price
        self.now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
        self.provider = _Provider()
        self.call_count = 0
        self.requested_codes = []

    def get_provider(self, name):
        return self.provider if name == "sina" else None

    def call_specific(self, provider, capability, codes):
        assert provider == "sina"
        assert capability == "get_realtime_quotes"
        self.call_count += 1
        self.requested_codes.append(list(codes))
        return _Result([
            {
                "code": code,
                "name": "测试指数",
                "price": self.price,
                "quote_date": self.now.date().isoformat(),
                "quote_time": self.now.time().isoformat(),
            }
            for code in codes
        ])


class _Pusher:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.messages = []

    def initialize(self):
        return True

    def send_markdown(self, title, content):
        self.messages.append((title, content))
        return self.succeed


def _calendar(tmp_path, *, is_open=1, dates=("2026-08-03",)):
    path = tmp_path / "trade.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.executemany(
        "INSERT INTO trade_calendar VALUES (?, ?)",
        [(day, is_open) for day in dates],
    )
    conn.commit()
    conn.close()
    return path


def _run(registry, pusher, state_path, db_path, now):
    registry.now = now
    return run_check(
        registry,
        rules=TEST_RULES,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )


def test_initial_breach_pushes_once_and_persists_state(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1571.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    first = _run(registry, pusher, state_path, db_path, now)
    second = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))

    assert first["status"] == "complete"
    assert len(first["events"]) == 1
    assert second["status"] == "complete"
    assert second["events"] == []
    assert len(pusher.messages) == 1
    assert "1571.00" in pusher.messages[0][1]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pending_events"] == []
    assert state["rules"][BREACH_RULE.rule_id]["active"] is True


def test_recovery_then_second_breach_pushes_again(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1571.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    _run(registry, pusher, state_path, db_path, now)
    registry.price = 1573.0
    recovered = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))
    registry.price = 1571.5
    rebreach = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=10))

    assert recovered["events"] == []
    assert len(rebreach["events"]) == 1
    assert len(pusher.messages) == 2


def test_reclaim_1582_only_pushes_after_observed_below_to_at_or_above(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1583.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    initial_above = _run(registry, pusher, state_path, db_path, now)
    registry.price = 1581.0
    below = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))
    registry.price = 1582.0
    reclaimed = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=10))
    still_above = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=15))

    assert initial_above["events"] == []
    assert below["events"] == []
    assert len(reclaimed["events"]) == 1
    assert reclaimed["events"][0]["rule_id"] == RECLAIM_RULE.rule_id
    assert reclaimed["events"][0]["action_text"] == "收复"
    assert reclaimed["events"][0]["price"] == 1582.0
    assert still_above["events"] == []
    assert len(pusher.messages) == 1
    assert "已收复监控线 **1582.00**" in pusher.messages[0][1]


def test_reclaim_rearms_after_falling_below_again(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1581.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    _run(registry, pusher, state_path, db_path, now)
    registry.price = 1582.5
    first_reclaim = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))
    registry.price = 1581.5
    _run(registry, pusher, state_path, db_path, now + timedelta(minutes=10))
    registry.price = 1582.0
    second_reclaim = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=15))

    assert len(first_reclaim["events"]) == 1
    assert len(second_reclaim["events"]) == 1
    assert len(pusher.messages) == 2


def test_equal_threshold_does_not_alert(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1572.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    result = _run(registry, pusher, tmp_path / "state.json", db_path, now)

    assert result["status"] == "complete"
    assert result["events"] == []
    assert pusher.messages == []


def test_failed_push_stays_pending_and_retries(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1571.0)
    pusher = _Pusher(succeed=False)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    failed = _run(registry, pusher, state_path, db_path, now)
    pusher.succeed = True
    retried = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))

    assert failed["status"] == "push_failed"
    assert failed["pending_count"] == 1
    assert retried["status"] == "complete"
    assert retried["events"] == []
    assert retried["pending_count"] == 0
    assert retried["pushed"] is True
    assert len(pusher.messages) == 2


def test_stale_quote_fails_closed(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1571.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    registry.now = now - timedelta(minutes=11)
    result = run_check(
        registry,
        rules=TEST_RULES,
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    assert result["status"] == "source_failed"
    assert "陈旧" in result["errors"][0]
    assert pusher.messages == []


def test_calendar_missing_blocks_before_fetch(tmp_path):
    missing_day_db = tmp_path / "trade.db"
    conn = sqlite3.connect(missing_day_db)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.commit()
    conn.close()
    registry = _Registry(price=1571.0)
    result = run_check(
        registry,
        rules=TEST_RULES,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=missing_day_db,
    )
    assert result["status"] == "blocked_calendar"
    assert not (tmp_path / "state.json").exists()


def test_dry_run_has_no_state_or_push_side_effect(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1571.0)
    pusher = _Pusher()
    state_path = tmp_path / "state.json"
    result = run_check(
        registry,
        rules=TEST_RULES,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        dry_run=True,
        pusher_factory=lambda: pusher,
    )
    assert result["status"] == "dry_run"
    assert len(result["events"]) == 1
    assert not state_path.exists()
    assert pusher.messages == []


def test_multiple_rules_on_same_provider_share_one_quote_request(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=100.0)
    pusher = _Pusher()
    rules = (
        MonitorRule("r1", "指数一", "000688.SH", 101.0),
        MonitorRule("r2", "指数二", "000001.SH", 102.0),
    )
    result = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    assert result["status"] == "complete"
    assert len(result["events"]) == 2
    assert registry.call_count == 1
    assert len(pusher.messages) == 1


def test_default_sse_rule_pushes_only_after_observed_below_to_3955(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=3956.0)
    pusher = _Pusher()
    state_path = tmp_path / "state.json"
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    initial_above = run_check(
        registry,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 3954.0
    registry.now = now + timedelta(minutes=5)
    below = run_check(
        registry,
        now=now + timedelta(minutes=5),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 3955.0
    registry.now = now + timedelta(minutes=10)
    standing = run_check(
        registry,
        now=now + timedelta(minutes=10),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.now = now + timedelta(minutes=15)
    still_above = run_check(
        registry,
        now=now + timedelta(minutes=15),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert DEFAULT_RULES == (
        SSE_COMPOSITE_RECLAIM_3955,
        LITONG_ELECTRONICS_BELOW_123_92_20260811,
    )
    assert initial_above["events"] == []
    assert below["events"] == []
    assert len(standing["events"]) == 1
    assert standing["events"][0]["rule_id"] == SSE_COMPOSITE_RECLAIM_3955.rule_id
    assert standing["events"][0]["action_text"] == "站上"
    assert standing["events"][0]["price"] == 3955.0
    assert still_above["status"] == "complete"
    assert still_above["events"] == []
    assert len(pusher.messages) == 1
    assert "上证指数" in pusher.messages[0][1]
    assert "已站上监控线 **3955.00**" in pusher.messages[0][1]


def test_litong_rule_is_not_fetched_before_its_effective_day(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-10",))
    registry = _Registry(price=123.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)

    result = run_check(
        registry,
        rules=(LITONG_ELECTRONICS_BELOW_123_92_20260811,),
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "no_active_rules"
    assert result["rules_configured"] == 1
    assert result["rules_checked"] == 0
    assert registry.call_count == 0
    assert pusher.messages == []
    assert not (tmp_path / "state.json").exists()


def test_litong_rule_is_strict_and_pushes_on_20260811_only(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-11",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=123.92)
    pusher = _Pusher()
    now = datetime(2026, 8, 11, 10, 0, tzinfo=TZ)
    registry.now = now

    equal = run_check(
        registry,
        rules=(LITONG_ELECTRONICS_BELOW_123_92_20260811,),
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 123.91
    registry.now = now + timedelta(minutes=5)
    below = run_check(
        registry,
        rules=(LITONG_ELECTRONICS_BELOW_123_92_20260811,),
        now=now + timedelta(minutes=5),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert equal["status"] == "complete"
    assert equal["events"] == []
    assert len(below["events"]) == 1
    event = below["events"][0]
    assert event["rule_id"] == "litong-electronics-below-123-92-20260811"
    assert event["price"] == 123.91
    assert event["threshold"] == 123.92
    assert len(pusher.messages) == 1
    assert "利通电子" in pusher.messages[0][1]
    assert "最新价格 **123.91**元" in pusher.messages[0][1]
    assert "跌破监控线 **123.92**元" in pusher.messages[0][1]


def test_litong_rule_is_not_fetched_after_expiry(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-12",))
    registry = _Registry(price=123.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)

    result = run_check(
        registry,
        rules=(LITONG_ELECTRONICS_BELOW_123_92_20260811,),
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "no_active_rules"
    assert registry.call_count == 0
    assert pusher.messages == []


def test_default_rules_fetch_litong_only_on_its_effective_day(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-11",))
    registry = _Registry(price=4000.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 11, 10, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert registry.call_count == 1
    assert registry.requested_codes == [["000001.SH", "603629.SH"]]


def test_default_rules_fetch_only_sse_before_and_after_litong_day(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-10", "2026-08-12"))

    for day in (10, 12):
        registry = _Registry(price=4000.0)
        pusher = _Pusher()
        now = datetime(2026, 8, day, 10, 0, tzinfo=TZ)
        registry.now = now

        result = run_check(
            registry,
            now=now,
            state_path=tmp_path / f"state-{day}.json",
            db_path=db_path,
            pusher_factory=lambda: pusher,
        )

        assert result["status"] == "complete"
        assert registry.requested_codes == [["000001.SH"]]


def test_non_finite_price_fails_closed_without_resetting_active_state(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1571.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    _run(registry, pusher, state_path, db_path, now)
    registry.price = float("nan")
    invalid = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=5))
    registry.price = 1571.5
    still_below = _run(registry, pusher, state_path, db_path, now + timedelta(minutes=10))

    assert invalid["status"] == "source_failed"
    assert "非有限" in invalid["errors"][0]
    assert still_below["events"] == []
    assert len(pusher.messages) == 1


def test_cross_day_pending_expires_and_new_day_breach_alerts_once(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-03", "2026-08-04"))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1571.0)
    pusher = _Pusher(succeed=False)
    day_one = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    failed = _run(registry, pusher, state_path, db_path, day_one)
    assert failed["pending_count"] == 1
    pusher.succeed = True
    day_two = datetime(2026, 8, 4, 10, 0, tzinfo=TZ)
    next_day = _run(registry, pusher, state_path, db_path, day_two)

    assert next_day["status"] == "complete"
    assert len(next_day["events"]) == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["expired_event_ids"]) == 1
    assert state["pending_events"] == []


def test_retired_rule_pending_event_never_pushes_after_other_rule_is_enabled(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    retired_event = {
        "event_id": "retired-event",
        "rule_id": "retired-rule",
        "instrument_name": "已下线指数",
        "code": "000688.SH",
        "threshold": 1572.0,
        "direction": "below",
        "action_text": "跌破",
        "price": 1571.0,
        "quote_at": now.isoformat(),
        "source": "sina",
    }
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_fetch_at": None,
                "rules": {},
                "pending_events": [retired_event],
                "sent_event_ids": [],
                "expired_event_ids": [],
            }
        ),
        encoding="utf-8",
    )
    registry = _Registry(price=200.0)
    pusher = _Pusher()
    active_rule = MonitorRule("new-rule", "新指数", "000001.SH", 100.0)

    result = run_check(
        registry,
        rules=(active_rule,),
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["events"] == []
    assert result["pushed"] is False
    assert pusher.messages == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pending_events"] == []
    assert state["expired_event_ids"] == ["retired-event"]


def test_disabled_tick_retires_pending_before_same_rule_id_is_reenabled(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    retired_event = {
        "event_id": "same-rule-retired-event",
        "rule_id": BREACH_RULE.rule_id,
        "instrument_name": "已下线指数",
        "code": BREACH_RULE.code,
        "threshold": BREACH_RULE.threshold,
        "direction": "below",
        "action_text": "跌破",
        "price": 1571.0,
        "quote_at": now.isoformat(),
        "source": "sina",
    }
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_fetch_at": None,
                "rules": {},
                "pending_events": [retired_event],
                "sent_event_ids": [],
                "expired_event_ids": [],
            }
        ),
        encoding="utf-8",
    )
    registry = _Registry(price=2000.0)
    pusher = _Pusher()

    disabled = run_check(
        registry,
        rules=(),
        now=now,
        state_path=state_path,
        db_path=tmp_path / "missing.db",
        pusher_factory=lambda: pusher,
    )
    reenabled = run_check(
        registry,
        rules=(BREACH_RULE,),
        now=now + timedelta(minutes=5),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert disabled["status"] == "no_rules"
    assert disabled["retired_pending_count"] == 1
    assert reenabled["status"] == "complete"
    assert reenabled["events"] == []
    assert reenabled["pushed"] is False
    assert pusher.messages == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pending_events"] == []
    assert state["expired_event_ids"] == ["same-rule-retired-event"]


def test_confirmed_non_trade_day_skips_without_fetch_or_state(tmp_path):
    db_path = _calendar(tmp_path, is_open=0)
    registry = _Registry(price=1571.0)
    result = run_check(
        registry,
        rules=TEST_RULES,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=db_path,
    )
    assert result["status"] == "non_trade_day"
    assert registry.call_count == 0
    assert not (tmp_path / "state.json").exists()


def test_previous_day_and_future_quotes_fail_closed(tmp_path):
    db_path = _calendar(tmp_path)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    for quote_now, expected in (
        (now - timedelta(days=1), "日期陈旧"),
        (now + timedelta(minutes=3), "超前"),
    ):
        registry = _Registry(price=1571.0)
        registry.now = quote_now
        result = run_check(
            registry,
            rules=TEST_RULES,
            now=now,
            state_path=tmp_path / f"state-{expected}.json",
            db_path=db_path,
            pusher_factory=lambda: _Pusher(),
        )
        assert result["status"] == "source_failed"
        assert expected in result["errors"][0]


def test_e2e_test_uses_fresh_real_quote_and_never_calls_state_layer(tmp_path, monkeypatch):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1600.0)
    pusher = _Pusher()
    def fail_state_access(*args, **kwargs):
        raise AssertionError("e2e-test 不得访问正式状态层")

    monkeypatch.setattr(intraday_service, "load_state", fail_state_access)
    monkeypatch.setattr(intraday_service, "save_state", fail_state_access)
    monkeypatch.setattr(intraday_service, "locked_state", fail_state_access)

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BREACH_RULE,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["pushed"] is True
    assert result["production_threshold"] == 1572.0
    assert result["events"][0]["price"] == 1600.0
    assert result["events"][0]["threshold"] > 1600.0
    assert len(pusher.messages) == 1
    title, content = pusher.messages[0]
    assert "测试" in title
    assert "真实行情 → 阈值判断 → 钉钉送达" in content
    assert "正式监控线仍为 **1572.00**" in content
    assert "pytest" in content


def test_e2e_test_preserves_stock_price_label_and_unit(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-11",))
    now = datetime(2026, 8, 11, 10, 0, tzinfo=TZ)
    registry = _Registry(price=125.0)
    registry.now = now
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=LITONG_ELECTRONICS_BELOW_123_92_20260811,
        now=now,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["pushed"] is True
    assert len(pusher.messages) == 1
    content = pusher.messages[0][1]
    assert "实时价格 **125.00**元" in content
    assert "本次临时测试线 **126.00**元" in content
    assert "正式监控线仍为 **123.92**元" in content


def test_e2e_test_rejects_blank_input_by_before_fetch(tmp_path):
    registry = _Registry(price=1600.0)
    result = run_e2e_test(
        registry,
        input_by="   ",
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=tmp_path / "missing.db",
    )

    assert result["status"] == "invalid_input"
    assert "不能为空" in result["errors"][0]
    assert registry.call_count == 0


def test_e2e_test_requires_explicit_real_push_authorization_before_fetch(tmp_path):
    for denied_value in (False, None, 1, "false", "true"):
        registry = _Registry(price=1600.0)
        pusher = _Pusher()

        result = run_e2e_test(
            registry,
            input_by="pytest",
            confirm_real_push=denied_value,
            now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
            db_path=tmp_path / "missing.db",
            pusher_factory=lambda: pusher,
        )

        assert result["status"] == "authorization_required"
        assert result["pushed"] is False
        assert "--confirm-real-push" in result["errors"][0]
        assert registry.call_count == 0
        assert pusher.messages == []


def test_e2e_test_rejects_stale_quote_without_push(tmp_path):
    db_path = _calendar(tmp_path)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    registry = _Registry(price=1600.0)
    registry.now = now - timedelta(minutes=11)
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BREACH_RULE,
        now=now,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "source_failed"
    assert "陈旧" in result["errors"][0]
    assert pusher.messages == []


def test_e2e_test_outside_session_does_not_fetch_or_push(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1600.0)
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BREACH_RULE,
        now=datetime(2026, 8, 3, 8, 0, tzinfo=TZ),
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "outside_session"
    assert registry.call_count == 0
    assert pusher.messages == []


def test_e2e_test_reports_push_failure(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1600.0)
    pusher = _Pusher(succeed=False)

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BREACH_RULE,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "push_failed"
    assert result["pushed"] is False
    assert len(pusher.messages) == 1


def test_e2e_test_without_active_rule_does_not_fetch_or_push(tmp_path, monkeypatch):
    registry = _Registry(price=1600.0)
    pusher = _Pusher()
    monkeypatch.setattr(intraday_service, "DEFAULT_RULES", ())

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=tmp_path / "missing.db",
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "no_rules"
    assert result["pushed"] is False
    assert registry.call_count == 0
    assert pusher.messages == []


def test_e2e_test_uses_first_registered_rule_when_monitoring_is_reenabled(
    tmp_path,
    monkeypatch,
):
    db_path = _calendar(tmp_path)
    registry = _Registry(price=1600.0)
    pusher = _Pusher()
    monkeypatch.setattr(intraday_service, "DEFAULT_RULES", (BREACH_RULE,))

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["production_threshold"] == BREACH_RULE.threshold
    assert len(pusher.messages) == 1
