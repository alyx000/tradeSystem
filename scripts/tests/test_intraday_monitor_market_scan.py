from __future__ import annotations

import json
import sqlite3

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from providers.base import DataResult
from services.intraday_monitor.market_universe import SOURCE, MIN_UNIVERSE
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
        pad=True,
    ):
        self.quotes = quotes or [_quote()]
        self.fail_quotes = fail_quotes
        self.quote_calls = 0
        self.list_date = list_date
        self.universe_codes = universe_codes or [quote["code"] for quote in self.quotes]
        self.quote_note = quote_note
        self.basic_calls = 0
        self.pad = pad
        self.extra_rows = []
        self.as_of_override = None
        self.filler = ([f"{601000+i}.SH" for i in range(2000)] +
                       [f"{1000+i:06d}.SZ" for i in range(1900)] +
                       [f"{920000+i}.BJ" for i in range(100)]) if pad else []

    def call(self, capability, *args, **kwargs):
        if capability == "get_stock_universe_as_of":
            self.basic_calls += 1
            assert kwargs.get("exact_day") is True
            fact_day = self.as_of_override or args[0]
            rows = [
                {"ts_code": code, "list_date": self.list_date}
                for code in dict.fromkeys(self.universe_codes + self.filler)
            ] + self.extra_rows
            full, tail = divmod(len(rows), 2000)
            receipt = {"trade_date": fact_day, "page_size": 2000, "selected": len(rows),
                       "pages": {"L": [2000]*full + ([tail] if tail else []) + [0], "D": [0], "P": [0]}}
            return DataResult(data=rows, source=SOURCE, note=json.dumps(receipt))
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
        template = self.quotes[0]
        filler = [dict(template, code=code, name="未触发", price=10, amount=2000)
                  for code in self.filler if code not in self.universe_codes]
        return DataResult(data=self.quotes + filler, source="sina", note=self.quote_note)


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


def test_possible_suspension_is_not_verified_coverage(tmp_path):
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
    assert result["status"] == "partial" and result["matched"] is None
    assert result["verified_matches"] == 1


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

def _run(reg, path, db, now=None, dry=False):
    return run_market_scan(reg, state_path=path, db_path=db, dry_run=dry,
                           now=now or datetime(2026, 8, 31, 9, 55, tzinfo=TZ),
                           pusher_factory=lambda: _Pusher())


def test_one_stock_universe_cannot_report_zero_or_replace_first_good_list(tmp_path):
    path = tmp_path / "state.json"
    db = _calendar(tmp_path)
    bad = _Registry(pad=False)
    first = _run(bad, path, db)
    assert first["status"] == "source_failed" and first["matched"] is None
    assert not list(tmp_path.glob("*.universe-*.json")) and bad.quote_calls == 0
    good = _Registry()
    assert _run(good, path, db)["status"] == "complete"
    # 下一个 CLI 实例复用名单，但用新实时价判断命中。
    later = _Registry(quotes=[_quote(price=10.5)], pad=True)
    result = _run(later, path, db)
    assert later.basic_calls == 0 and later.quote_calls == 1
    assert result["status"] == "complete" and result["matched"] == 0
    assert result["universe_count"] >= MIN_UNIVERSE


@pytest.mark.parametrize("extra", [
    {"ts_code":"600000.SH","list_date":"20200101"},
    {"ts_code":"600001.SZ","list_date":"20200101"},
    {"ts_code":"600099.SH","symbol":"600098","list_date":"20200101"},
    {"ts_code":"600099.SH","exchange":"SZSE","list_date":"20200101"},
    {"ts_code":"600099.SH","list_date":"20260230"},
    {"ts_code":"600099.SH","list_date":"20260901"},
    {"ts_code":"600099.SH"},
    {"ts_code":"600099.SH","trade_date":"20260828","list_date":"20200101"},
])
def test_identity_or_date_invalid_fails_before_quote(tmp_path, extra):
    reg = _Registry()
    reg.extra_rows.append(extra)
    result = _run(reg, tmp_path/"state.json", _calendar(tmp_path))
    assert result["status"] == "source_failed" and reg.quote_calls == 0
    assert not list(tmp_path.glob("*.universe-*.json"))


def test_missing_exchange_or_wrong_source_day_fails(tmp_path):
    path = tmp_path/"state.json"
    db = _calendar(tmp_path)
    reg = _Registry()
    reg.filler = [f"{600001+i}.SH" for i in range(4500)]
    assert _run(reg, path, db)["status"] == "source_failed"
    reg = _Registry()
    reg.as_of_override = "2026-08-28"
    assert _run(reg, path, db)["status"] == "source_failed"
    assert not list(tmp_path.glob("*.universe-*.json"))


def test_prior_delisting_and_b_shares_excluded_from_market(tmp_path):
    reg = _Registry()
    reg.extra_rows = [dict(ts_code="600099.SH", list_date="20000101", delist_date="20260828"),
                      dict(ts_code="900901.SH", list_date="20000101")]
    res = _run(reg, tmp_path/"state.json", _calendar(tmp_path))
    assert res["status"] == "complete" and res["universe_count"] == 4001
    cache = json.loads(next(tmp_path.glob("*.universe-*.json")).read_text())
    assert not {"600099.SH", "900901.SH"}.intersection(cache["listings"])


def test_cache_corruption_refetch_and_next_open_day_does_not_reuse(tmp_path):
    path = tmp_path/"state.json"
    db = _calendar(tmp_path)
    reg = _Registry()
    assert _run(reg, path, db)["status"] == "complete"
    cached = next(tmp_path.glob("*.universe-*.json"))
    cached.write_text("{")
    bad = _Registry(pad=False)
    assert _run(bad, path, db)["status"] == "source_failed"
    assert cached.read_text() == "{"
    assert _run(reg, path, db)["status"] == "complete" and reg.basic_calls == 2
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO trade_calendar VALUES ('2026-09-01',1)")
    reg = _Registry(quotes=[_quote(quote_date="2026-09-01")])
    result = _run(reg, path, db, datetime(2026,9,1,9,55,tzinfo=TZ))
    assert result["status"] == "complete" and reg.basic_calls == 1
    assert len(list(tmp_path.glob("*.universe-*.json"))) == 2


def test_unknown_wrong_or_conflicting_quotes_never_create_match(tmp_path):
    reg = _Registry(quotes=[_quote(), _quote(price=10), _quote(code="600099.SH")],
                    universe_codes=["600000.SH"])
    result = _run(reg, tmp_path/"state.json", _calendar(tmp_path))
    assert result["status"] == "partial" and result["matched"] is None
    assert result["verified_matches"] == 0 and not result["pushed"]


def test_dry_run_no_universe_file_and_no_lock(tmp_path):
    path = tmp_path/"state.json"
    result = _run(_Registry(), path, _calendar(tmp_path), dry=True)
    assert result["status"] == "dry_run"
    assert not list(tmp_path.glob("state*"))


def test_gem_302_real_identity_uses_twenty_percent_gate(tmp_path):
    reg = _Registry(quotes=[_quote(code="302132.SZ", name="中航成飞", price=12)])
    result = _run(reg, tmp_path/"state.json", _calendar(tmp_path))
    assert result["status"] == "complete" and result["events"][0]["up_limit"] == 12
    assert match_limit_up_amount_quote(_quote(code="302132.SZ", name="中航成飞", price=11),
                                       LIMIT_UP_AMOUNT_100B_BEFORE_1000) is None


def test_unknown_new_number_segment_remains_in_denominator(tmp_path):
    reg = _Registry()
    reg.extra_rows.append(dict(ts_code="710001.SH", symbol="710001", market="主板", list_date="20201010"))
    result = _run(reg, tmp_path/"state.json", _calendar(tmp_path))
    assert result["status"] == "partial" and result["verified_matches"] == 1
    assert result["matched"] is None and result["universe_count"] == 4002


def test_exchange_shrink_after_complete_day_is_rejected_even_above_total_floor(tmp_path):
    path = tmp_path/"state.json"
    db = _calendar(tmp_path)
    first = _Registry()
    assert _run(first, path, db)["status"] == "complete"
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO trade_calendar VALUES ('2026-09-01',1)")
    later = _Registry(quotes=[_quote(quote_date="2026-09-01")])
    later.filler += [f"{603000+i}.SH" for i in range(200)]
    later.filler = [c for c in later.filler if not c.endswith(".BJ") or c=="920000.BJ"]
    result = _run(later, path, db, datetime(2026,9,1,9,55,tzinfo=TZ))
    assert result["status"] == "source_failed" and "BJ" in result["errors"][0]
    assert later.quote_calls == 0 and len(list(tmp_path.glob("*.universe-*.json"))) == 1


def _process_scan(path, db, gate, out):
    reg = _Registry()
    gate.wait(timeout=15)
    result = _run(reg, path, db)
    out.put((reg.basic_calls, reg.quote_calls, result["status"], result["pushed"]))


def test_two_cli_processes_share_only_universe(tmp_path):
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    gate, out = ctx.Event(), ctx.Queue()
    path, db = tmp_path/"state.json", _calendar(tmp_path)
    processes = [ctx.Process(target=_process_scan, args=(path, db, gate, out)) for _ in range(2)]
    for process in processes:
        process.start()
    gate.set()
    rows = [out.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sum(r[0] for r in rows) == 1 and sum(r[1] for r in rows) == 2
    assert all(r[2]=="complete" for r in rows) and sum(r[3] for r in rows) == 1


@pytest.mark.parametrize("mode", ["repeat", "short_pages", "bad_date", "empty_error", "wrong_status", "valid"])
def test_tushare_exact_daily_receipt_and_ignored_offset_fail_closed(mode):
    import pandas as pd
    from providers.tushare_provider import TushareProvider

    class Pro:
        calls = []
        def stock_basic(self, **kwargs):
            state, offset = kwargs["list_status"], kwargs["offset"]
            self.calls.append((state, offset))
            if state == "P" and mode == "empty_error":
                return None
            if state != "L":
                return pd.DataFrame()
            if mode != "repeat" and mode != "short_pages" and offset >= 2:
                return pd.DataFrame()
            code = 600000 if mode == "repeat" else 600000 + offset
            return pd.DataFrame([dict(ts_code=f"{code}.SH", list_status="D" if mode=="wrong_status" else state,
                                      list_date="20260230" if mode=="bad_date" else "20000101")])
    provider = TushareProvider()
    provider.pro = Pro()
    provider._initialized = True
    result = provider.get_stock_universe_as_of("2026-08-31", exact_day=True)
    if mode == "valid":
        assert result.success
        assert json.loads(result.note)["pages"] == {"L":[1,1,0],"D":[0],"P":[0]}
    else:
        assert not result.success
    if mode == "repeat":
        assert len(provider.pro.calls) == 2
    if mode == "short_pages":
        assert len(provider.pro.calls) == 16


def test_first_cold_day_uses_archived_exchange_lower_bounds(tmp_path):
    path=tmp_path/"state.json"
    db=_calendar(tmp_path,"2026-08-28")
    reg=_Registry()
    quotes=[dict(ts_code=code,trade_date="20260828") for code in reg.filler]
    old_day="2026-08-28"
    block=dict(trade_date=old_day,coverage={"valid_count":len(quotes)},
               evidence={"quote_source":"tushare:daily","quotes":quotes})
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO trade_calendar VALUES (?,?)",
                         [("2026-08-29",0),("2026-08-30",0),("2026-08-31",1)])
        conn.execute("CREATE TABLE daily_market (date TEXT,raw_data TEXT)")
        conn.execute("INSERT INTO daily_market VALUES (?,?)",(old_day,json.dumps({
            "date":old_day,"raw_data":{"date":old_day,"zhong_prefix_effect":block}})))
    reg.filler=[c for c in reg.filler if not c.endswith(".BJ") or c=="920000.BJ"]
    reg.filler += [f"{603000+i}.SH" for i in range(200)]
    bad=_run(reg,path,db)
    assert bad["status"]=="source_failed" and "BJ" in bad["errors"][0]
    assert not list(tmp_path.glob("*.universe-*.json"))
    good=_run(_Registry(),path,db)
    assert good["status"]=="complete"
    receipt=json.loads(next(tmp_path.glob("*.universe-*.json")).read_text())["receipt"]
    assert receipt["reference"]=="archived_daily_quotes_lower_bound"
    assert receipt["previous_exchange_counts"]["BJ"]==100
