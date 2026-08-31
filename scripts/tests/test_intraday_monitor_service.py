from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import services.intraday_monitor.service as intraday_service
from services.intraday_monitor.service import run_all_checks, run_check, run_e2e_test
from services.intraday_monitor.rules import (
    DEFAULT_RULES,
    GUOCI_MATERIALS_BELOW_67_22_20260831,
    KAILAIYING_BREAKOUT_172_26_20260821_24,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    SSE_COMPOSITE_RECLAIM_3955,
    STAR50_BREAKOUT_1700_20260821_24,
    ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
    THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
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
BOARD_BREAK_RULE = MonitorRule(
    "test-board-break",
    "测试股票",
    "600127.SH",
    None,
    direction="below",
    inclusive=False,
    emit_on_initial_match=True,
    action_label="低于",
    valid_from=date(2026, 8, 19),
    valid_until=date(2026, 8, 20),
    value_label="价格",
    value_unit="元",
    threshold_mode="daily_up_limit",
    threshold_label="当日涨停价",
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


class _HistoryProvider:
    def __init__(self):
        self._initialized = False
        self.initialize_count = 0

    def initialize(self):
        self.initialize_count += 1
        self._initialized = True
        return True

    def supports(self, capability):
        return capability in {
            "get_stock_daily_range",
            "get_stock_adj_factor_range",
        }


class _Registry:
    def __init__(self, price=1571.0):
        self.price = price
        self.pre_close = 100.0
        self.quote_overrides = {}
        self.now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
        self.provider = _Provider()
        self.call_count = 0
        self.requested_codes = []

    def get_provider(self, name):
        return self.provider if name in {"sina", "tonghuashun"} else None

    def call_specific(self, provider, capability, codes):
        assert provider in {"sina", "tonghuashun"}
        assert capability == "get_realtime_quotes"
        self.call_count += 1
        self.requested_codes.append(list(codes))
        rows = []
        for code in codes:
            row = {
                "code": code,
                "name": "测试指数",
                "price": self.price,
                "pre_close": self.pre_close,
                "quote_date": self.now.date().isoformat(),
                "quote_time": self.now.time().isoformat(),
            }
            row.update(self.quote_overrides.get(code, {}))
            rows.append(row)
        return _Result(rows, source="sina" if provider == "sina" else "tonghuashun:realhead_v6")


class _ThsRegistry(_Registry):
    def get_provider(self, name):
        return self.provider if name == "tonghuashun" else None

    def call_specific(self, provider, capability, codes):
        assert provider == "tonghuashun"
        assert capability == "get_realtime_quotes"
        self.call_count += 1
        self.requested_codes.append(list(codes))
        return _Result(
            [
                {
                    "code": "883421.THS",
                    "name": "同花顺全A(沪深)",
                    "price": self.price,
                    "pre_close": self.pre_close,
                    "pct_chg": 99.0,
                    "quote_date": self.now.date().isoformat(),
                    "quote_time": self.now.time().isoformat(),
                }
            ],
            source="tonghuashun:realhead_v6",
        )


class _DynamicRegistry(_Registry):
    def __init__(self, price=102.0):
        super().__init__(price=price)
        self.pre_close = 104.0
        self.history_provider = _HistoryProvider()
        self.history_calls = []
        self.history_dates = [
            "2026-08-24",
            "2026-08-25",
            "2026-08-26",
            "2026-08-27",
            "2026-08-28",
        ]
        self.history_closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        self.history_factors = [1.0] * 5

    def get_provider(self, name):
        if name == "tushare":
            return self.history_provider
        return super().get_provider(name)

    def call_specific(self, provider, capability, *args):
        if provider in {"sina", "tonghuashun"}:
            return super().call_specific(provider, capability, *args)
        assert provider == "tushare"
        self.history_calls.append((capability, args))
        if capability == "get_stock_daily_range":
            rows = [
                {"trade_date": day, "close": close}
                for day, close in zip(self.history_dates, self.history_closes)
            ]
            return _Result(rows, source="tushare:daily")
        if capability == "get_stock_adj_factor_range":
            rows = [
                {"trade_date": day, "adj_factor": factor}
                for day, factor in zip(self.history_dates, self.history_factors)
            ]
            return _Result(rows, source="tushare:adj_factor")
        raise AssertionError(capability)


class _FailingQuoteRegistry(_Registry):
    def call_specific(self, provider, capability, codes):
        assert provider == "sina"
        assert capability == "get_realtime_quotes"
        self.call_count += 1
        self.requested_codes.append(list(codes))
        return _Result(data=None, error="quote source unavailable")


class _Pusher:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.messages = []

    def initialize(self):
        return True

    def send_markdown(self, title, content):
        self.messages.append((title, content))
        return self.succeed


def _calendar(
    tmp_path,
    *,
    is_open=1,
    dates=("2026-08-03",),
    closed_dates=(),
):
    path = tmp_path / "trade.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.executemany(
        "INSERT INTO trade_calendar VALUES (?, ?)",
        [(day, is_open) for day in dates],
    )
    conn.executemany(
        "INSERT INTO trade_calendar VALUES (?, 0)",
        [(day,) for day in closed_dates],
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


def test_daily_drop_rule_compares_computed_pct_and_renders_index_point(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-31",))
    state_path = tmp_path / "state.json"
    registry = _ThsRegistry(price=95.99)
    registry.pre_close = 100.0
    registry.now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    pusher = _Pusher()

    result = run_check(
        registry,
        rules=(THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,),
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["price"] == 95.99
    assert event["value"] == pytest.approx(-4.01)
    assert event["value_mode"] == "daily_pct_change"
    assert "最新单日涨跌幅 **-4.01000000**%" in pusher.messages[0][1]
    assert "单日涨跌幅监控线 **-4.00000000**%" in pusher.messages[0][1]
    assert "最新点位：95.990" in pusher.messages[0][1]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["rules"][THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT.rule_id][
        "last_value"
    ] == pytest.approx(-4.01)


def test_daily_drop_equal_minus_four_does_not_trigger(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-31",))
    registry = _ThsRegistry(price=96.0)
    registry.pre_close = 100.0
    registry.now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    pusher = _Pusher()

    result = run_check(
        registry,
        rules=(THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,),
        now=registry.now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["events"] == []
    assert pusher.messages == []


def test_daily_drop_invalid_pre_close_fails_closed(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-31",))
    registry = _ThsRegistry(price=95.0)
    registry.pre_close = 0.0
    registry.now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)

    result = run_check(
        registry,
        rules=(THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,),
        now=registry.now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )

    assert result["status"] == "source_failed"
    assert any("前收盘价" in error for error in result["errors"])


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


def test_pending_delivery_retries_even_when_next_quote_fetch_fails(tmp_path):
    db_path = _calendar(tmp_path)
    state_path = tmp_path / "state.json"
    first_registry = _Registry(price=1571.0)
    pusher = _Pusher(succeed=False)
    now = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)

    failed_push = run_check(
        first_registry,
        rules=(BREACH_RULE,),
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    pusher.succeed = True
    failed_registry = _FailingQuoteRegistry(price=1571.0)
    failed_registry.now = now + timedelta(minutes=5)
    recovered_delivery = run_check(
        failed_registry,
        rules=(BREACH_RULE,),
        now=failed_registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert failed_push["status"] == "push_failed"
    assert failed_push["pending_count"] == 1
    assert recovered_delivery["status"] == "source_failed"
    assert recovered_delivery["events"] == []
    assert recovered_delivery["pushed"] is True
    assert recovered_delivery["pending_count"] == 0
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
        STAR50_BREAKOUT_1700_20260821_24,
        KAILAIYING_BREAKOUT_172_26_20260821_24,
        GUOCI_MATERIALS_BELOW_67_22_20260831,
        ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
        THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
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
    assert registry.call_count == 2
    assert registry.requested_codes == [
        ["000001.SH", "603629.SH"],
        ["883421.THS"],
    ]


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
        assert registry.requested_codes == [["000001.SH"], ["883421.THS"]]


def test_default_rules_fetch_temporary_breakouts_only_during_two_trade_day_window(tmp_path):
    db_path = _calendar(
        tmp_path,
        dates=(
            "2026-08-19",
            "2026-08-20",
            "2026-08-21",
            "2026-08-24",
            "2026-08-25",
        ),
    )
    for day in (19, 20, 21, 24, 25):
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
        expected = (
            ["000001.SH", "000688.SH", "002821.SZ"]
            if day in (21, 24)
            else ["000001.SH"]
        )
        assert registry.requested_codes == [expected, ["883421.THS"]]


def test_guoci_rule_is_strict_and_only_active_on_20260831(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-31", "2026-09-01"))
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry = _Registry(price=67.22)
    registry.now = now

    equal = run_check(
        registry,
        rules=(GUOCI_MATERIALS_BELOW_67_22_20260831,),
        now=now,
        state_path=tmp_path / "guoci.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 67.21
    registry.now = now + timedelta(minutes=5)
    below = run_check(
        registry,
        rules=(GUOCI_MATERIALS_BELOW_67_22_20260831,),
        now=registry.now,
        state_path=tmp_path / "guoci.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    expired = run_check(
        _Registry(price=60.0),
        rules=(GUOCI_MATERIALS_BELOW_67_22_20260831,),
        now=datetime(2026, 9, 1, 10, 0, tzinfo=TZ),
        state_path=tmp_path / "expired.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert equal["events"] == []
    assert below["events"][0]["threshold"] == 67.22
    assert "国瓷材料" in pusher.messages[0][1]
    assert expired["status"] == "no_active_rules"


def test_previous_close_ma5_is_qfq_strict_cached_and_fail_closed(tmp_path):
    dates = (
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
    )
    db_path = _calendar(
        tmp_path,
        dates=dates,
        closed_dates=("2026-08-29", "2026-08-30"),
    )
    state_path = tmp_path / "zhongke.json"
    registry = _DynamicRegistry(price=102.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry.now = now

    equal = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 101.99
    registry.now = now + timedelta(minutes=5)
    below = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert equal["status"] == "complete"
    assert equal["events"] == []
    assert len(below["events"]) == 1
    event = below["events"][0]
    assert event["threshold"] == 102.0
    assert event["threshold_mode"] == "previous_close_ma"
    assert event["threshold_basis_dates"] == list(dates[:-1])
    assert event["threshold_source"] == (
        "tushare:daily+tushare:adj_factor+sina:pre_close_anchor"
    )
    assert len(registry.history_calls) == 2
    assert registry.history_provider.initialize_count == 1
    assert "中科飞测" in pusher.messages[0][1]
    assert "前5个已收盘交易日" in pusher.messages[0][1]
    assert "前复权" in pusher.messages[0][1]

    registry.pre_close = None
    registry.now = now + timedelta(minutes=10)
    warm_cache_missing_anchor = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    assert warm_cache_missing_anchor["status"] == "source_failed"
    assert "坐标锚定" in warm_cache_missing_anchor["errors"][0]
    assert len(registry.history_calls) == 2

    registry.pre_close = 52.0
    registry.price = 51.0
    registry.now = now + timedelta(minutes=15)
    changed_anchor = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    assert changed_anchor["status"] == "complete"
    assert changed_anchor["events"] == []
    assert len(registry.history_calls) == 4
    changed_state = json.loads(state_path.read_text(encoding="utf-8"))
    changed_rule_state = changed_state["rules"][
        ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902.rule_id
    ]
    assert changed_rule_state["last_threshold"] == 51.0
    assert changed_rule_state["threshold_anchor_pre_close"] == 52.0

    broken_registry = _DynamicRegistry(price=90.0)
    broken_registry.now = now
    broken_registry.history_dates.pop(2)
    failed = run_check(
        broken_registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=tmp_path / "broken.json",
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )
    assert failed["status"] == "source_failed"
    assert "缺少开放日" in failed["errors"][0]

    missing_anchor_registry = _DynamicRegistry(price=90.0)
    missing_anchor_registry.pre_close = None
    missing_anchor_registry.now = now
    missing_anchor = run_check(
        missing_anchor_registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=tmp_path / "missing-anchor.json",
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )
    assert missing_anchor["status"] == "source_failed"
    assert "坐标锚定" in missing_anchor["errors"][0]

    ex_date_registry = _DynamicRegistry(price=51.0)
    ex_date_registry.pre_close = 52.0
    ex_date_registry.now = now
    ex_date_state = tmp_path / "ex-date.json"
    ex_date = run_check(
        ex_date_registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=ex_date_state,
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )
    assert ex_date["status"] == "complete"
    assert ex_date["events"] == []
    ex_state = json.loads(ex_date_state.read_text(encoding="utf-8"))
    assert ex_state["rules"][ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902.rule_id][
        "last_threshold"
    ] == 51.0

    extra_bar_registry = _DynamicRegistry(price=90.0)
    extra_bar_registry.now = now
    extra_bar_registry.history_dates.append("2026-08-29")
    extra_bar_registry.history_closes.append(105.0)
    extra_bar_registry.history_factors.append(1.0)
    extra_bar = run_check(
        extra_bar_registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=tmp_path / "extra-bar.json",
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )
    assert extra_bar["status"] == "source_failed"
    assert "非预期交易日 2026-08-29" in extra_bar["errors"][0]


def test_previous_close_ma5_rejects_incomplete_natural_day_calendar(tmp_path):
    db_path = _calendar(
        tmp_path,
        dates=(
            "2026-08-21",
            "2026-08-24",
            "2026-08-25",
            "2026-08-27",
            "2026-08-28",
            "2026-08-31",
        ),
        closed_dates=(
            "2026-08-22",
            "2026-08-23",
            "2026-08-29",
            "2026-08-30",
        ),
    )
    registry = _DynamicRegistry(price=90.0)
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=tmp_path / "missing-calendar-day.json",
        db_path=db_path,
        pusher_factory=lambda: _Pusher(),
    )

    assert result["status"] == "source_failed"
    assert "开放日历缺失或不可读" in result["errors"][0]
    assert registry.history_calls == []


def test_default_rules_batch_new_codes_and_resolve_ma_on_20260831(tmp_path):
    dates = (
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
    )
    db_path = _calendar(
        tmp_path,
        dates=dates,
        closed_dates=("2026-08-29", "2026-08-30"),
    )
    registry = _DynamicRegistry(price=4000.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert registry.requested_codes == [
        ["000001.SH", "300285.SZ", "688361.SH"],
        ["883421.THS"],
    ]
    assert len(registry.history_calls) == 2


def test_previous_close_ma_alert_displays_mill_precision(tmp_path):
    dates = (
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
    )
    db_path = _calendar(
        tmp_path,
        dates=dates,
        closed_dates=("2026-08-29", "2026-08-30"),
    )
    registry = _DynamicRegistry(price=67.22)
    registry.pre_close = 67.23
    registry.history_closes = [67.21, 67.22, 67.22, 67.23, 67.23]
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        rules=(ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,),
        now=now,
        state_path=tmp_path / "mill-precision.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["events"][0]["threshold"] == pytest.approx(67.222)
    assert "最新价格 **67.22**元" in pusher.messages[0][1]
    assert "**67.222**元" in pusher.messages[0][1]


def test_star50_breakout_is_strict_initially_emits_and_rearms(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-21",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1700.01)
    pusher = _Pusher()
    now = datetime(2026, 8, 21, 10, 0, tzinfo=TZ)
    registry.now = now
    rules = (STAR50_BREAKOUT_1700_20260821_24,)

    initial = run_check(
        registry,
        rules=rules,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.now = now + timedelta(minutes=5)
    continuous = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 1700.0
    registry.now = now + timedelta(minutes=10)
    equal = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 1700.01
    registry.now = now + timedelta(minutes=15)
    reentered = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert len(initial["events"]) == 1
    assert initial["events"][0]["rule_id"] == STAR50_BREAKOUT_1700_20260821_24.rule_id
    assert continuous["events"] == []
    assert equal["events"] == []
    assert len(reentered["events"]) == 1
    assert len(pusher.messages) == 2
    assert "科创50" in pusher.messages[0][1]
    assert "已突破监控线 **1700.00**" in pusher.messages[0][1]


def test_kailaiying_breakout_is_strict_initially_emits_and_rearms(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-21",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=172.27)
    pusher = _Pusher()
    now = datetime(2026, 8, 21, 10, 0, tzinfo=TZ)
    registry.now = now
    rules = (KAILAIYING_BREAKOUT_172_26_20260821_24,)

    initial = run_check(
        registry,
        rules=rules,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.now = now + timedelta(minutes=5)
    continuous = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 172.26
    registry.now = now + timedelta(minutes=10)
    equal = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 172.27
    registry.now = now + timedelta(minutes=15)
    reentered = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert len(initial["events"]) == 1
    assert initial["events"][0]["rule_id"] == rules[0].rule_id
    assert initial["events"][0]["threshold"] == 172.26
    assert continuous["events"] == []
    assert equal["events"] == []
    assert len(reentered["events"]) == 1
    assert len(pusher.messages) == 2
    assert "凯莱英" in pusher.messages[0][1]
    assert "最新价格 **172.27**元" in pusher.messages[0][1]
    assert "已突破监控线 **172.26**元" in pusher.messages[0][1]


def test_board_break_rule_uses_daily_limit_and_rearms_after_reseal(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=7.82)
    registry.pre_close = 7.11
    pusher = _Pusher()
    now = datetime(2026, 8, 19, 13, 0, tzinfo=TZ)
    registry.now = now
    rules = (BOARD_BREAK_RULE,)

    sealed = run_check(
        registry,
        rules=rules,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 7.81
    registry.now = now + timedelta(minutes=5)
    broken = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.now = now + timedelta(minutes=10)
    still_broken = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 7.82
    registry.now = now + timedelta(minutes=15)
    run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 7.81
    registry.now = now + timedelta(minutes=20)
    broken_again = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert sealed["events"] == []
    assert len(broken["events"]) == 1
    assert broken["events"][0]["threshold"] == 7.82
    assert broken["events"][0]["threshold_mode"] == "daily_up_limit"
    assert still_broken["events"] == []
    assert len(broken_again["events"]) == 1
    assert len(pusher.messages) == 2
    assert "测试股票" in pusher.messages[0][1]
    assert "最新价格 **7.81**元" in pusher.messages[0][1]
    assert "低于当日涨停价 **7.82**元" in pusher.messages[0][1]
    assert "当前未封涨停" in pusher.messages[0][1]
    assert "最终是否断板以收盘为准" in pusher.messages[0][1]


def test_board_break_sends_distinct_close_confirmation_after_intraday_alert(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=7.81)
    registry.pre_close = 7.11
    pusher = _Pusher()
    rules = (BOARD_BREAK_RULE,)

    intraday_now = datetime(2026, 8, 19, 14, 55, tzinfo=TZ)
    registry.now = intraday_now
    intraday = run_check(
        registry,
        rules=rules,
        now=intraday_now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    close_now = datetime(2026, 8, 19, 15, 0, 4, tzinfo=TZ)
    registry.now = close_now
    close = run_check(
        registry,
        rules=rules,
        now=close_now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.now = datetime(2026, 8, 19, 15, 0, 30, tzinfo=TZ)
    repeated = run_check(
        registry,
        rules=rules,
        now=registry.now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert intraday["events"][0]["observation_phase"] == "intraday"
    assert close["events"][0]["observation_phase"] == "close"
    assert repeated["events"] == []
    assert len(pusher.messages) == 2
    assert "最终是否断板以收盘为准" in pusher.messages[0][1]
    assert "[事实·收盘]" in pusher.messages[1][1]
    assert "确认为当日断板" in pusher.messages[1][1]


def test_close_does_not_accept_1459_quote_and_retries_with_1500_quote(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=7.81)
    registry.pre_close = 7.11
    pusher = _Pusher()
    rules = (BOARD_BREAK_RULE,)

    intraday_now = datetime(2026, 8, 19, 14, 59, 30, tzinfo=TZ)
    registry.now = datetime(2026, 8, 19, 14, 59, 0, tzinfo=TZ)
    intraday = run_check(
        registry,
        rules=rules,
        now=intraday_now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    close_now = datetime(2026, 8, 19, 15, 0, 4, tzinfo=TZ)
    stale_close = run_check(
        registry,
        rules=rules,
        now=close_now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    state_after_stale = json.loads(state_path.read_text(encoding="utf-8"))
    registry.now = datetime(2026, 8, 19, 15, 0, 0, tzinfo=TZ)
    fresh_close = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 19, 15, 4, 0, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert intraday["status"] == "complete"
    assert stale_close["status"] == "source_failed"
    assert "收盘行情尚未就绪" in stale_close["errors"][0]
    assert state_after_stale["rules"][rules[0].rule_id]["close_confirmed"] is False
    assert fresh_close["events"][0]["observation_phase"] == "close"
    assert len(pusher.messages) == 2


def test_same_quote_timestamp_has_distinct_intraday_and_close_event_ids(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=7.81)
    registry.pre_close = 7.11
    registry.now = datetime(2026, 8, 19, 15, 0, 0, tzinfo=TZ)
    pusher = _Pusher()
    rules = (BOARD_BREAK_RULE,)

    intraday = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 19, 14, 59, 59, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    close = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 19, 15, 0, 4, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert intraday["events"][0]["quote_at"] == close["events"][0]["quote_at"]
    assert intraday["events"][0]["event_id"] != close["events"][0]["event_id"]
    assert len(pusher.messages) == 2


def test_post_close_finalization_fetches_fixed_and_dynamic_rules(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    registry = _Registry(price=4000.0)
    pusher = _Pusher()
    now = datetime(2026, 8, 19, 15, 4, tzinfo=TZ)
    registry.now = datetime(2026, 8, 19, 15, 0, tzinfo=TZ)

    result = run_check(
        registry,
        rules=(SSE_COMPOSITE_RECLAIM_3955, BOARD_BREAK_RULE),
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert registry.requested_codes == [["000001.SH", "600127.SH"]]


def test_fixed_rule_uses_1500_snapshot_in_close_grace_window_once(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-21",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1699.99)
    pusher = _Pusher()
    rules = (STAR50_BREAKOUT_1700_20260821_24,)

    intraday_now = datetime(2026, 8, 21, 14, 55, tzinfo=TZ)
    registry.now = intraday_now
    intraday = run_check(
        registry,
        rules=rules,
        now=intraday_now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.price = 1700.01
    registry.now = datetime(2026, 8, 21, 15, 0, tzinfo=TZ)
    close_grace = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 21, 15, 4, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    repeated = run_check(
        registry,
        rules=rules,
        now=datetime(2026, 8, 21, 15, 5, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    saved = json.loads(state_path.read_text(encoding="utf-8"))

    assert intraday["events"] == []
    assert len(close_grace["events"]) == 1
    assert close_grace["events"][0]["observation_phase"] == "intraday"
    assert repeated["status"] == "complete"
    assert repeated["events"] == []
    assert saved["rules"][rules[0].rule_id]["active"] is True
    assert saved["rules"][rules[0].rule_id]["last_quote_at"].endswith("15:00:00+08:00")
    assert len(pusher.messages) == 1


def test_fixed_rule_rejects_1459_snapshot_without_advancing_rule_state(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-21",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1700.01)
    registry.now = datetime(2026, 8, 21, 14, 59, 59, tzinfo=TZ)
    pusher = _Pusher()

    result = run_check(
        registry,
        rules=(STAR50_BREAKOUT_1700_20260821_24,),
        now=datetime(2026, 8, 21, 15, 4, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    saved = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["status"] == "source_failed"
    assert "收盘行情尚未就绪" in result["errors"][0]
    assert STAR50_BREAKOUT_1700_20260821_24.rule_id not in saved["rules"]
    assert pusher.messages == []


def test_close_grace_window_stops_at_1506_without_fetch_or_state(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-21",))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=1700.01)
    registry.now = datetime(2026, 8, 21, 15, 0, tzinfo=TZ)
    pusher = _Pusher()

    result = run_check(
        registry,
        rules=(STAR50_BREAKOUT_1700_20260821_24,),
        now=datetime(2026, 8, 21, 15, 6, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "outside_session"
    assert registry.requested_codes == []
    assert state_path.exists() is False
    assert pusher.messages == []


def test_board_break_first_observation_below_limit_pushes_and_recalculates_next_day(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19", "2026-08-20"))
    state_path = tmp_path / "state.json"
    registry = _Registry(price=7.81)
    registry.pre_close = 7.11
    pusher = _Pusher()
    day_one = datetime(2026, 8, 19, 13, 0, tzinfo=TZ)
    registry.now = day_one
    rules = (BOARD_BREAK_RULE,)

    first = run_check(
        registry,
        rules=rules,
        now=day_one,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    registry.pre_close = 7.82
    registry.price = 8.59
    day_two = datetime(2026, 8, 20, 9, 30, tzinfo=TZ)
    registry.now = day_two
    second = run_check(
        registry,
        rules=rules,
        now=day_two,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert first["events"][0]["threshold"] == 7.82
    assert second["events"][0]["threshold"] == 8.60
    assert len(pusher.messages) == 2


def test_board_break_missing_pre_close_fails_closed(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    registry = _Registry(price=7.81)
    registry.quote_overrides["600127.SH"] = {"pre_close": None}
    pusher = _Pusher()
    now = datetime(2026, 8, 19, 13, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        rules=(BOARD_BREAK_RULE,),
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "source_failed"
    assert "无法根据前收盘价计算当日涨停价" in result["errors"][0]
    assert pusher.messages == []


def test_default_check_ignores_cancelled_board_break_quote_overrides(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    registry = _Registry(price=4000.0)
    for code in ("600127.SH", "603395.SH", "000505.SZ"):
        registry.quote_overrides[code] = {"pre_close": None}
    pusher = _Pusher()
    now = datetime(2026, 8, 19, 13, 0, tzinfo=TZ)
    registry.now = now

    result = run_check(
        registry,
        now=now,
        state_path=tmp_path / "state.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["quotes_checked"] == 2
    assert result["errors"] == []
    assert registry.requested_codes == [["000001.SH"], ["883421.THS"]]
    assert pusher.messages == []


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


def test_e2e_test_preserves_daily_pct_boundary_precision(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-31",))
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry = _ThsRegistry(price=95.996)
    registry.pre_close = 100.0
    registry.now = now
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
        now=now,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["pushed"] is True
    assert result["events"][0]["value"] == -4.004
    content = pusher.messages[0][1]
    assert "实时单日涨跌幅 **-4.00400000**%" in content
    assert "正式监控线仍为 **-4.00000000**%" in content
    assert "实时点位 **95.996**" in content


def test_e2e_test_resolves_dynamic_board_break_threshold(tmp_path):
    db_path = _calendar(tmp_path, dates=("2026-08-19",))
    now = datetime(2026, 8, 19, 13, 0, tzinfo=TZ)
    registry = _Registry(price=7.81)
    registry.pre_close = 7.11
    registry.now = now
    registry.quote_overrides["600127.SH"] = {"name": "测试股票"}
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BOARD_BREAK_RULE,
        now=now,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["production_threshold"] == 7.82
    assert registry.requested_codes == [["600127.SH"]]
    assert "正式监控线仍为 **7.82**元" in pusher.messages[0][1]


def test_e2e_test_resolves_previous_close_ma_without_state_access(tmp_path, monkeypatch):
    dates = (
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
    )
    db_path = _calendar(
        tmp_path,
        dates=dates,
        closed_dates=("2026-08-29", "2026-08-30"),
    )
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    registry = _DynamicRegistry(price=120.0)
    registry.now = now
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
        rule=ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
        now=now,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "complete"
    assert result["production_threshold"] == 102.0
    assert result["production_threshold_basis_dates"] == list(dates[:-1])
    assert result["production_threshold_anchor_pre_close"] == 104.0
    assert result["production_threshold_source"] == (
        "tushare:daily+tushare:adj_factor+sina:pre_close_anchor"
    )
    assert len(registry.history_calls) == 2
    assert "正式监控线仍为 **102.000**元" in pusher.messages[0][1]


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


def test_e2e_test_rejects_inactive_rule_before_fetch(tmp_path):
    registry = _Registry(price=7.81)
    pusher = _Pusher()

    result = run_e2e_test(
        registry,
        input_by="pytest",
        confirm_real_push=True,
        rule=BOARD_BREAK_RULE,
        now=datetime(2026, 8, 21, 10, 0, tzinfo=TZ),
        db_path=tmp_path / "missing.db",
        pusher_factory=lambda: pusher,
    )

    assert result["status"] == "inactive_rule"
    assert result["pushed"] is False
    assert registry.call_count == 0
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


def test_run_all_checks_combines_receipts_and_surfaces_market_failure(monkeypatch):
    import services.intraday_monitor.market_scan as market_scan

    monkeypatch.setattr(
        intraday_service,
        "run_check",
        lambda registry, dry_run: {
            "status": "complete",
            "events": [{"event_id": "threshold"}],
            "errors": [],
            "quotes_checked": 1,
            "pushed": False,
        },
    )
    monkeypatch.setattr(
        market_scan,
        "run_market_scan",
        lambda registry, dry_run: {
            "status": "source_failed",
            "events": [],
            "errors": ["全市场行情失败"],
            "pushed": False,
        },
    )

    result = run_all_checks(object())

    assert result["status"] == "source_failed"
    assert result["events"] == [{"event_id": "threshold"}]
    assert result["errors"] == ["market_scan: 全市场行情失败"]
    assert result["threshold_monitor"]["status"] == "complete"
    assert result["market_scan"]["status"] == "source_failed"


def test_run_all_checks_keeps_matching_calendar_failure_status(monkeypatch):
    import services.intraday_monitor.market_scan as market_scan

    blocked = {
        "status": "blocked_calendar",
        "events": [],
        "errors": ["交易日历缺失"],
        "pushed": False,
    }
    monkeypatch.setattr(intraday_service, "run_check", lambda registry, dry_run: blocked)
    monkeypatch.setattr(market_scan, "run_market_scan", lambda registry, dry_run: blocked)

    assert run_all_checks(object())["status"] == "blocked_calendar"
