from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from providers.base import DataResult
from services.intraday_monitor.market_scan import (
    LIMIT_UP_AMOUNT_100B_BEFORE_1000,
    MarketScanRule,
    match_limit_up_amount_quote,
    run_market_scan,
)


TZ = ZoneInfo("Asia/Shanghai")


def _quote(
    *,
    code="600000.SH",
    name="浦发银行",
    price=11.0,
    pre_close=10.0,
    amount=10_000_000_000,
    quote_date="2026-08-31",
    quote_time="09:55:00",
):
    return {
        "code": code,
        "name": name,
        "price": price,
        "pre_close": pre_close,
        "amount": amount,
        "quote_date": quote_date,
        "quote_time": quote_time,
    }


class _Registry:
    def __init__(
        self,
        quotes=None,
        *,
        fail_quotes=False,
        list_date="20200101",
        universe_codes=None,
        quote_note="",
    ):
        self.quotes = quotes or [_quote()]
        self.fail_quotes = fail_quotes
        self.quote_calls = 0
        self.list_date = list_date
        self.universe_codes = universe_codes or [quote["code"] for quote in self.quotes]
        self.quote_note = quote_note

    def call(self, capability, *args):
        if capability == "get_stock_basic_list":
            return DataResult(
                data=[
                    {"ts_code": code, "list_date": self.list_date}
                    for code in self.universe_codes
                ],
                source="mock-basic",
            )
        if capability == "get_trade_calendar":
            rows = []
            for day in range(1, 32):
                date = f"2026-08-{day:02d}"
                weekday = datetime.fromisoformat(date).weekday()
                rows.append({"cal_date": date, "is_open": int(weekday < 5)})
            return DataResult(data=rows, source="mock-calendar")
        raise AssertionError(capability)

    def call_specific(self, provider, capability, codes):
        assert provider == "sina"
        assert capability == "get_realtime_quotes"
        self.quote_calls += 1
        if self.fail_quotes:
            return DataResult(data=None, source="sina", error="network")
        return DataResult(data=self.quotes, source="sina", note=self.quote_note)


class _Pusher:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.messages = []

    def initialize(self):
        return True

    def send_markdown(self, title, content):
        self.messages.append((title, content))
        return self.succeed


def _calendar(tmp_path, day="2026-08-31", is_open=1):
    path = tmp_path / "trade.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.execute("INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)", (day, is_open))
    conn.commit()
    conn.close()
    return path


def test_match_uses_inclusive_100_yi_and_official_limit_prices():
    assert match_limit_up_amount_quote(_quote(), LIMIT_UP_AMOUNT_100B_BEFORE_1000)
    assert not match_limit_up_amount_quote(
        _quote(amount=9_999_999_999), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert not match_limit_up_amount_quote(
        _quote(price=10.99), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert not match_limit_up_amount_quote(
        _quote(price=11.01), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )

    assert match_limit_up_amount_quote(
        _quote(code="688001.SH", price=12.0), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert match_limit_up_amount_quote(
        _quote(code="430047.BJ", price=13.0), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert match_limit_up_amount_quote(
        _quote(code="600001.SH", name="ST测试", price=10.5),
        LIMIT_UP_AMOUNT_100B_BEFORE_1000,
    )


def test_match_rejects_funds_and_nonfinite_values():
    assert not match_limit_up_amount_quote(
        _quote(code="510300.SH"), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert not match_limit_up_amount_quote(
        _quote(amount=float("nan")), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )
    assert not match_limit_up_amount_quote(
        _quote(pre_close=float("inf")), LIMIT_UP_AMOUNT_100B_BEFORE_1000
    )


def test_scan_pushes_once_per_stock_per_day(tmp_path):
    registry = _Registry()
    pusher = _Pusher()
    state_path = tmp_path / "market-state.json"
    db_path = _calendar(tmp_path)
    now = datetime(2026, 8, 31, 9, 55, tzinfo=TZ)

    first = run_market_scan(
        registry,
        now=now,
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    second = run_market_scan(
        registry,
        now=now.replace(minute=59),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert first["status"] == "complete" and first["pushed"] is True
    assert len(first["events"]) == 1
    assert second["events"] == [] and second["pushed"] is False
    assert len(pusher.messages) == 1
    assert "100.00 亿元" in pusher.messages[0][1]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pending_events"] == []
    assert len(state["sent_event_ids"]) == 1


def test_push_failure_retries_after_window_without_refetch(tmp_path):
    registry = _Registry()
    pusher = _Pusher(succeed=False)
    state_path = tmp_path / "market-state.json"
    db_path = _calendar(tmp_path)

    failed = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 59, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    pusher.succeed = True
    retried = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 10, 5, tzinfo=TZ),
        state_path=state_path,
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )

    assert failed["status"] == "push_failed"
    assert retried["status"] == "complete" and retried["retry_only"] is True
    assert retried["pushed"] is True
    assert registry.quote_calls == 1


def test_outside_window_and_calendar_gate_do_not_fetch(tmp_path):
    registry = _Registry()
    db_path = _calendar(tmp_path)
    outside = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 29, 59, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=db_path,
    )
    blocked = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 30, tzinfo=TZ),
        state_path=tmp_path / "state2.json",
        db_path=tmp_path / "missing.db",
    )
    assert outside["status"] == "outside_window"
    assert blocked["status"] == "blocked_calendar"
    assert registry.quote_calls == 0


def test_0959_is_allowed_but_1000_is_not(tmp_path):
    db_path = _calendar(tmp_path)
    pusher = _Pusher()
    before_ten = run_market_scan(
        _Registry(quotes=[_quote(quote_time="09:59:30")]),
        now=datetime(2026, 8, 31, 9, 59, 45, tzinfo=TZ),
        state_path=tmp_path / "ten.json",
        db_path=db_path,
        pusher_factory=lambda: pusher,
    )
    after = run_market_scan(
        _Registry(quotes=[_quote(quote_time="10:00:00")]),
        now=datetime(2026, 8, 31, 10, 0, tzinfo=TZ),
        state_path=tmp_path / "after.json",
        db_path=db_path,
    )
    assert before_ten["pushed"] is True
    assert after["status"] == "outside_window"


def test_quote_failure_retries_once_and_fails_closed(tmp_path):
    registry = _Registry(fail_quotes=True)
    result = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
    )
    assert result["status"] == "source_failed"
    assert registry.quote_calls == 2


def test_new_stock_first_five_open_days_is_not_reported(tmp_path):
    # 8/27 上市，8/31 仍在前五个开放日，即使碰到普通理论涨停价也不能叫“涨停板”。
    registry = _Registry(list_date="20260827")
    result = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: _Pusher(),
    )
    assert result["status"] == "complete"
    assert result["events"] == []
    assert result["pushed"] is False


def test_bj_new_stock_second_open_day_uses_30_percent_limit(tmp_path):
    registry = _Registry(
        quotes=[_quote(code="430047.BJ", price=13.0)],
        list_date="20260828",
    )
    result = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: _Pusher(),
    )
    assert result["status"] == "complete"
    assert [event["code"] for event in result["events"]] == ["430047.BJ"]
    assert result["pushed"] is True


def test_partial_quote_coverage_is_visible_but_known_match_still_pushes(tmp_path):
    registry = _Registry(
        quotes=[_quote()],
        universe_codes=["600000.SH", "000001.SZ"],
    )
    pusher = _Pusher()
    result = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: pusher,
    )
    assert result["status"] == "partial"
    assert result["pushed"] is True
    assert "count=1" in result["errors"][0]
    assert len(pusher.messages) == 1


def test_benign_suspended_quote_gap_does_not_degrade_scan(tmp_path):
    registry = _Registry(
        quotes=[_quote()],
        universe_codes=["600000.SH", "000001.SZ"],
        quote_note="000001.SZ(停牌或无最新价)",
    )
    result = run_market_scan(
        registry,
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: _Pusher(),
    )
    assert result["status"] == "complete"


def test_empty_or_unsupported_quote_gap_is_partial(tmp_path):
    db_path = _calendar(tmp_path)
    for index, note in enumerate(
        ("000001.SZ(无数据)", "000001.SZ(新浪不支持或代码非法)"), start=1
    ):
        registry = _Registry(
            quotes=[_quote()],
            universe_codes=["600000.SH", "000001.SZ"],
            quote_note=note,
        )
        result = run_market_scan(
            registry,
            now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
            state_path=tmp_path / f"state-{index}.json",
            db_path=db_path,
            pusher_factory=lambda: _Pusher(),
        )
        assert result["status"] == "partial"
        assert "count=1" in result["errors"][0]


def test_duplicate_quote_rows_produce_one_event(tmp_path):
    quote = _quote()
    result = run_market_scan(
        _Registry(quotes=[quote, dict(quote)]),
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: _Pusher(),
    )
    assert len(result["events"]) == 1


def test_overlapping_market_scan_rules_all_execute(tmp_path):
    second_rule = MarketScanRule(
        rule_id="limit-up-amount-101b-before-1000",
        display_name="10点前101亿成交额涨停板",
        start_time=LIMIT_UP_AMOUNT_100B_BEFORE_1000.start_time,
        end_time=LIMIT_UP_AMOUNT_100B_BEFORE_1000.end_time,
        min_amount_yi=101.0,
    )
    quote = _quote(amount=10_200_000_000)
    pusher = _Pusher()
    result = run_market_scan(
        _Registry(quotes=[quote]),
        rules=(LIMIT_UP_AMOUNT_100B_BEFORE_1000, second_rule),
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=tmp_path / "state.json",
        db_path=_calendar(tmp_path),
        pusher_factory=lambda: pusher,
    )
    assert {event["rule_id"] for event in result["events"]} == {
        LIMIT_UP_AMOUNT_100B_BEFORE_1000.rule_id,
        second_rule.rule_id,
    }
    assert len(pusher.messages) == 1


def test_dry_run_does_not_write_or_push(tmp_path):
    state_path = tmp_path / "state.json"
    pusher = _Pusher()
    result = run_market_scan(
        _Registry(),
        now=datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
        state_path=state_path,
        db_path=_calendar(tmp_path),
        dry_run=True,
        pusher_factory=lambda: pusher,
    )
    assert result["status"] == "dry_run"
    assert len(result["events"]) == 1
    assert not state_path.exists()
    assert pusher.messages == []
