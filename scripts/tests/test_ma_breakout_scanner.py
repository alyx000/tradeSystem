from __future__ import annotations

import sqlite3

from services.ma_breakout import scanner


class _R:
    def __init__(self, data=None, success=True, error=""):
        self.data = data
        self.error = error
        self.success = success and not error


class FakeRegistry:
    def __init__(self, quotes_by_date, sw_map=None, fail_dates=None, trade_days=None,
                 calendar_fail_dates=None, stock_basic_error=""):
        self.quotes_by_date = quotes_by_date
        self.sw_map = sw_map or {}
        self.fail_dates = set(fail_dates or [])
        self.trade_days = set(trade_days or quotes_by_date.keys())
        self.calendar_fail_dates = set(calendar_fail_dates or [])
        self.stock_basic_error = stock_basic_error

    def call(self, name, *args):
        if name == "get_market_daily_quotes":
            date = args[0]
            if date in self.fail_dates:
                return _R(None, success=False, error=f"failed:{date}")
            return _R(self.quotes_by_date.get(date, []))
        if name == "get_stock_sw_industry_map":
            return _R(self.sw_map)
        if name == "is_trade_day":
            if args[0] in self.calendar_fail_dates:
                return _R(None, success=False, error=f"calendar_failed:{args[0]}")
            return _R(args[0] in self.trade_days)
        if name == "get_stock_basic_list":
            if self.stock_basic_error:
                return _R(None, success=False, error=self.stock_basic_error)
            return _R([
                {"ts_code": "002156.SZ", "name": "通富微电", "symbol": "002156"},
                {"ts_code": "600001.SH", "name": "历史趋势龙", "symbol": "600001"},
            ])
        return _R(None, success=False, error=f"unsupported:{name}")


def _row(code, close, amount, name="测试股", pct=1.0):
    return {
        "ts_code": code,
        "code": code,
        "name": name,
        "close": close,
        "amount": amount,
        "pct_chg": pct,
    }


DATES = [
    "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
    "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12",
]


def _quotes_for_hit_and_miss():
    hit_closes = [10.0, 10.0, 10.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 14.0]
    hit_amounts = [100.0] * 9 + [180.0]
    miss_closes = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 13.2, 13.3, 13.4, 13.5]
    miss_amounts = [200.0] * 10
    out = {}
    for i, date in enumerate(DATES):
        out[date] = [
            _row("600001.SH", hit_closes[i], hit_amounts[i], name="命中股份", pct=3.2),
            _row("600002.SH", miss_closes[i], miss_amounts[i], name="未命中", pct=0.8),
        ]
    return out


def test_run_daily_returns_only_ma4_amount_breakout_hits():
    reg = FakeRegistry(
        _quotes_for_hit_and_miss(),
        sw_map={"600001.SH": {"name": "命中股份", "sw_l2": "半导体"}},
    )

    summary = scanner.run_daily(reg, "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=1)

    assert summary["status"] == "ok"
    assert summary["matched_count"] == 1
    assert summary["scanned_count"] == 2
    assert summary["candidates"][0]["code"] == "600001"
    assert summary["candidates"][0]["name"] == "命中股份"
    assert summary["candidates"][0]["sw_l2"] == "半导体"
    assert summary["candidates"][0]["today_amount"] == 180.0
    assert summary["candidates"][0]["amount_ma5"] > 0
    assert summary["candidates"][0]["amount_ma10"] > 0


def test_run_daily_excludes_today_limit_up_hits():
    quotes = _quotes_for_hit_and_miss()
    hit_closes = [10.0, 10.0, 10.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 14.0]
    for date in DATES:
        i = DATES.index(date)
        quotes[date].append(_row("600003.SH", hit_closes[i], 120.0, name="非涨停命中", pct=2.0))
    quotes["2026-06-12"][0]["pct_chg"] = 9.96
    quotes["2026-06-12"][2]["amount"] = 160.0

    summary = scanner.run_daily(
        FakeRegistry(quotes),
        "2026-06-12",
        windows=(5, 10),
        top_n=10,
        min_target_quote_rows=1,
    )

    assert summary["status"] == "ok"
    assert summary["matched_count"] == 1
    assert [c["code"] for c in summary["candidates"]] == ["600003"]


def test_run_daily_excludes_st_limit_up_using_former_leader_name_when_quote_name_missing():
    quotes = {}
    closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 11.0]
    amounts = [100.0] * 9 + [180.0]
    for i, date in enumerate(DATES):
        quotes[date] = [_row("600004.SH", closes[i], amounts[i], name="", pct=1.0)]
    quotes["2026-06-12"][0]["pct_chg"] = 4.92

    summary = scanner.run_daily(
        FakeRegistry(quotes),
        "2026-06-12",
        windows=(5, 10),
        top_n=10,
        min_target_quote_rows=1,
        former_leaders={"600004": {"name": "ST历史龙", "sources": ["trend_leader_pool"]}},
    )

    assert summary["status"] == "ok"
    assert summary["matched_count"] == 0
    assert summary["candidates"] == []


def test_run_daily_filters_to_former_leader_universe_when_provided():
    summary = scanner.run_daily(
        FakeRegistry(_quotes_for_hit_and_miss()),
        "2026-06-12",
        windows=(5, 10),
        top_n=10,
        min_target_quote_rows=1,
        former_leaders={
            "600001": {"sources": ["trend_leader_pool"], "first_seen_date": "2026-06-01"},
        },
    )

    assert summary["status"] == "ok"
    assert summary["leader_universe_count"] == 1
    assert summary["scanned_count"] == 1
    assert summary["matched_count"] == 1
    assert summary["candidates"][0]["code"] == "600001"
    assert summary["candidates"][0]["former_leader_sources"] == ["trend_leader_pool"]
    assert summary["candidates"][0]["former_leader_first_seen"] == "2026-06-01"


def test_load_former_leader_universe_combines_history_sources_before_target_date():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trend_leader_pool ("
        "code TEXT, name TEXT, sw_l2 TEXT, entered_date TEXT, last_seen_date TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO trend_leader_pool VALUES "
        "('600001','历史趋势龙','半导体','2026-06-01','2026-06-05','exited')"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('600002','复盘最票','机器人','情绪龙','2026-06-02','2026-06-04',1)"
    )
    conn.execute(
        "INSERT INTO trend_leader_pool VALUES "
        "('600003','今日才入池','算力','2026-06-12','2026-06-12','active')"
    )

    universe = scanner.load_former_leader_universe(conn, "2026-06-12", include_auto_trend_pool=True)

    assert set(universe) == {"600001", "600002"}
    assert universe["600001"]["sources"] == ["trend_leader_pool"]
    assert universe["600002"]["sources"] == ["leader_tracking"]
    assert universe["600001"]["first_seen_date"] == "2026-06-01"
    assert universe["600002"]["role"] == "情绪龙"


def test_load_former_leader_universe_defaults_to_confirmed_leader_tracking_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trend_leader_pool ("
        "code TEXT, name TEXT, sw_l2 TEXT, entered_date TEXT, last_seen_date TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO trend_leader_pool VALUES "
        "('688772','珠海冠宇','电池','2026-06-12','2026-06-25','exited')"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('300857','协创数据','消费电子','走势引领','2026-06-03','2026-06-03',1)"
    )

    universe = scanner.load_former_leader_universe(conn, "2026-06-26")

    assert set(universe) == {"300857"}
    assert universe["300857"]["sources"] == ["leader_tracking"]


def test_load_former_leader_universe_excludes_user_rejected_non_leaders():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('000681','视觉中国','文化传媒','走势引领','2026-06-01','2026-06-01',1)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('300582','英飞特','光学光电子','走势引领','2026-06-01','2026-06-01',1)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('688041','海光信息','半导体','走势引领','2026-06-01','2026-06-01',1)"
    )

    universe = scanner.load_former_leader_universe(conn, "2026-06-12")

    assert set(universe) == {"688041"}


def test_load_former_leader_universe_uses_recent_window_for_second_wave():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trend_leader_pool ("
        "code TEXT, name TEXT, sw_l2 TEXT, entered_date TEXT, last_seen_date TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO trend_leader_pool VALUES "
        "('600001','近端龙头','半导体','2026-05-20','2026-05-25','exited')"
    )
    conn.execute(
        "INSERT INTO trend_leader_pool VALUES "
        "('600002','太久远','机器人','2026-03-01','2026-03-10','exited')"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('600003','近期仍跟踪','算力','容量中军','2026-03-01','2026-05-28',1)"
    )

    universe = scanner.load_former_leader_universe(conn, "2026-06-12", lookback_days=30)

    assert set(universe) == {"600003"}


def test_load_former_leader_universe_resolves_leader_tracking_names_with_stock_basic():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('通富微电','通富微电','半导体','情绪龙','2026-05-20','2026-05-25',1)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('无法解析','无法解析','机器人','情绪龙','2026-05-20','2026-05-25',1)"
    )
    stats = {}

    universe = scanner.load_former_leader_universe(
        conn,
        "2026-06-12",
        registry=FakeRegistry({}),
        stats=stats,
    )

    assert set(universe) == {"002156"}
    assert universe["002156"]["sources"] == ["leader_tracking"]
    assert stats["unresolved_leader_tracking"] == 1


def test_load_former_leader_universe_exposes_stock_basic_failure_for_name_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('通富微电','通富微电','半导体','情绪龙','2026-05-20','2026-05-25',1)"
    )
    stats = {}

    universe = scanner.load_former_leader_universe(
        conn,
        "2026-06-12",
        registry=FakeRegistry({}, stock_basic_error="rate_limit"),
        stats=stats,
    )

    assert universe == {}
    assert stats["leader_resolution_error"] == "stock_basic_failed:rate_limit"
    assert stats["unresolved_leader_tracking"] == 1


def test_load_former_leader_universe_extracts_embedded_codes_from_leader_tracking():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE leader_tracking ("
        "stock_code TEXT, stock_name TEXT, sector TEXT, attribute_type TEXT, "
        "first_seen_date TEXT, last_seen_date TEXT, is_active INTEGER)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('688041 海光信息','海光信息','半导体','容量中军','2026-05-20','2026-05-25',1)"
    )
    conn.execute(
        "INSERT INTO leader_tracking VALUES "
        "('西部材料','西部材料(002149)','有色','情绪龙','2026-05-20','2026-05-25',1)"
    )
    stats = {}

    universe = scanner.load_former_leader_universe(
        conn,
        "2026-06-12",
        registry=FakeRegistry({}, stock_basic_error="rate_limit"),
        stats=stats,
    )

    assert set(universe) == {"688041", "002149"}
    assert stats["unresolved_leader_tracking"] == 0
    assert "leader_resolution_error" not in stats


def test_run_daily_sorts_by_today_amount_and_caps_top_n():
    quotes = _quotes_for_hit_and_miss()
    hit_closes = [10.0, 10.0, 10.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 14.0]
    for date in DATES:
        i = DATES.index(date)
        quotes[date].append(_row("600003.SH", hit_closes[i], 120.0, name="低额命中", pct=2.0))
    quotes["2026-06-12"][2]["amount"] = 160.0

    summary = scanner.run_daily(FakeRegistry(quotes), "2026-06-12", windows=(5, 10), top_n=1, min_target_quote_rows=1)

    assert summary["matched_count"] == 2
    assert [c["code"] for c in summary["candidates"]] == ["600001"]
    assert summary["truncated"] is True


def test_run_daily_source_failed_when_target_quotes_fail():
    reg = FakeRegistry({}, fail_dates={"2026-06-12"})

    summary = scanner.run_daily(reg, "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=1)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "market_daily_quotes:2026-06-12" in summary["source_errors"]


def test_run_daily_reports_insufficient_history_without_false_hit():
    quotes = {}
    for date in DATES[:-1]:
        quotes[date] = [_row("600999.SH", 10.0, 100.0, name="平稳股")]
    quotes["2026-06-12"] = [
        _row("600999.SH", 10.0, 100.0, name="平稳股"),
        _row("600001.SH", 11.5, 180.0, name="新股"),
    ]
    reg = FakeRegistry(quotes)

    summary = scanner.run_daily(reg, "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=1)

    assert summary["status"] == "ok"
    assert summary["matched_count"] == 0
    assert summary["insufficient_count"] == 1


def test_run_daily_source_failed_when_lookback_quote_day_fails():
    reg = FakeRegistry(_quotes_for_hit_and_miss(), fail_dates={"2026-06-11"})

    summary = scanner.run_daily(reg, "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=1)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "market_daily_quotes:2026-06-11" in summary["source_errors"]


def test_run_daily_source_failed_when_target_quotes_below_floor():
    quotes = _quotes_for_hit_and_miss()
    quotes["2026-06-12"] = quotes["2026-06-12"][:1]

    summary = scanner.run_daily(FakeRegistry(quotes), "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=2)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "target_quote_rows_below_floor" in summary["source_errors"]
    assert "quote_rows_below_floor:2026-06-12" in summary["source_errors"]


def test_run_daily_source_failed_when_history_quotes_below_floor():
    quotes = _quotes_for_hit_and_miss()
    quotes["2026-06-11"] = quotes["2026-06-11"][:1]

    summary = scanner.run_daily(FakeRegistry(quotes), "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=2)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "quote_rows_below_floor:2026-06-11" in summary["source_errors"]


def test_run_daily_source_failed_when_target_quotes_empty():
    quotes = _quotes_for_hit_and_miss()
    quotes["2026-06-12"] = []

    summary = scanner.run_daily(FakeRegistry(quotes), "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=2)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "target_quotes_missing" in summary["source_errors"]


def test_run_daily_source_failed_when_history_trade_day_quotes_empty():
    quotes = _quotes_for_hit_and_miss()
    quotes["2026-06-11"] = []

    summary = scanner.run_daily(FakeRegistry(quotes), "2026-06-12", windows=(5, 10), top_n=10, min_target_quote_rows=2)

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "market_daily_quotes_empty:2026-06-11" in summary["source_errors"]


def test_run_daily_source_failed_when_history_weekday_quotes_empty_and_calendar_unknown():
    quotes = _quotes_for_hit_and_miss()
    quotes["2026-06-11"] = []

    summary = scanner.run_daily(
        FakeRegistry(quotes, calendar_fail_dates={"2026-06-11"}),
        "2026-06-12",
        windows=(5, 10),
        top_n=10,
        min_target_quote_rows=2,
    )

    assert summary["status"] == "source_failed"
    assert summary["candidates"] == []
    assert "market_daily_quotes_empty_calendar_unknown:2026-06-11" in summary["source_errors"]
