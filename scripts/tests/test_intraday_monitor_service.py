from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import services.intraday_monitor.service as intraday_service
from services.intraday_monitor.service import run_check, run_e2e_test
from services.intraday_monitor.rules import MonitorRule


TZ = ZoneInfo("Asia/Shanghai")


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

    def get_provider(self, name):
        return self.provider if name == "sina" else None

    def call_specific(self, provider, capability, codes):
        assert provider == "sina"
        assert capability == "get_realtime_quotes"
        self.call_count += 1
        return _Result([
            {
                "code": code,
                "name": "科创50",
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
    assert state["rules"]["star50-below-1572"]["active"] is True


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


def test_confirmed_non_trade_day_skips_without_fetch_or_state(tmp_path):
    db_path = _calendar(tmp_path, is_open=0)
    registry = _Registry(price=1571.0)
    result = run_check(
        registry,
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


def test_e2e_test_rejects_stale_quote_without_push(tmp_path):
    db_path = _calendar(tmp_path)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    registry = _Registry(price=1600.0)
    registry.now = now - timedelta(minutes=11)
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
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
        now=datetime(2026, 8, 3, 10, 0, tzinfo=TZ),
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "push_failed"
    assert result["pushed"] is False
    assert len(pusher.messages) == 1
