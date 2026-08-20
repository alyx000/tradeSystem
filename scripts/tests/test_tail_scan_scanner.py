from services.tail_scan import scanner


def _q(code, name, pct, amount_yi, price=10.8, high=10.9, low=10.0, pre=10.0, open_=10.2):
    return {"code": code, "name": name, "price": price, "pct_chg": pct,
            "open": open_, "high": high, "low": low, "pre_close": pre,
            "volume": 1e6, "amount": amount_yi * 1e8,
            "quote_date": "2026-07-13", "quote_time": "14:30:00"}


def test_filter_keeps_only_four_conditions():
    quotes = [
        _q("600001.SH", "强势股", 8.0, 25),      # 命中
        _q("600002.SH", "涨幅不够", 6.5, 25),     # 涨幅<7 剔
        _q("600003.SH", "成交额不够", 8.0, 15),   # 成交额<20 剔
        _q("600004.SH", "ST弱势", 8.0, 25),      # 名称ST剔（下条）
        _q("600005.SH", "*ST退", 8.0, 25),       # ST 剔
    ]
    quotes[3]["name"] = "ST退一"
    got = scanner.filter_quotes(quotes, min_pct=7.0, min_amount_yi=20.0)
    assert [c["code"] for c in got] == ["600001.SH"]
    assert got[0]["amount_yi"] == 25.0


def test_filter_excludes_limit_up_across_boards_and_keeps_non_limit_strong_stock():
    quotes = [
        # 主板 10cm：pre=10 → 涨停价 11.0。
        _q("600006.SH", "主板封板", 10.0, 30, price=11.0, high=11.0, low=10.5),
        # 创业板 20cm：pre=10 → 涨停价 12.0。
        _q("300006.SZ", "双创封板", 20.0, 30, price=12.0, high=12.0, low=10.5),
        # 距离主板涨停价仍差 0.03，不能被旧的宽容差误删。
        _q("600008.SH", "临近涨停", 9.7, 30, price=10.97, high=10.98, low=10.1),
        _q("600007.SH", "未封板强势", 8.5, 30, price=10.85, high=10.9, low=10.1),
    ]

    got = scanner.filter_quotes(quotes, min_pct=7.0, min_amount_yi=20.0)

    assert [c["code"] for c in got] == ["600008.SH", "600007.SH"]
    assert all(c["is_limit_up"] is False for c in got)
    assert got[1]["close_pos"] == 0.9375


def test_filter_uses_exchange_cent_rounding_for_limit_up_price():
    # 10.03 × 1.1 = 11.033，交易所 ROUND_HALF_UP 到 11.03；触价后应剔除。
    q = _q("600009.SH", "舍入封板", 9.97, 30, price=11.03, high=11.03,
           low=10.2, pre=10.03)

    got = scanner.filter_quotes([q], min_pct=7.0, min_amount_yi=20.0)

    assert got == []


def test_filter_keeps_theoretical_limit_price_on_confirmed_no_limit_day():
    q = _q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
           low=10.2, pre=10.0)

    got = scanner.filter_quotes(
        [q],
        min_pct=7.0,
        min_amount_yi=20.0,
        no_limit_codes={"688999.SH"},
    )

    assert [c["code"] for c in got] == ["688999.SH"]
    assert got[0]["is_limit_up"] is False


class _Result:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.success = error is None and data is not None
        self.source = "mock"


class _Registry:
    def __init__(self, basic, quotes, calendar=None, calendar_error=None):
        self._basic, self._quotes = basic, quotes
        self._calendar, self._calendar_error = calendar, calendar_error
        self.calls = []
    def call(self, cap, *a):
        self.calls.append((cap, a))
        if cap == "get_stock_basic_list":
            return _Result(self._basic)
        if cap == "get_realtime_quotes":
            return _Result(self._quotes)
        if cap == "get_trade_calendar":
            return _Result(self._calendar, self._calendar_error)
        return _Result(error="unknown")


def test_scan_ok_filters_candidates():
    basic = [
        {"ts_code": "600001.SH", "list_date": "20000101"},
        {"ts_code": "600002.SH", "list_date": "20000101"},
    ]
    quotes = [_q("600001.SH", "强", 8.0, 25), _q("600002.SH", "弱", 3.0, 25)]
    reg = _Registry(basic, quotes)
    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)
    assert res["status"] == "ok"
    assert res["matched"] == 1 and res["candidates"][0]["code"] == "600001.SH"


def test_scan_source_failed_when_no_codes():
    reg = _Registry([], [])
    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)
    assert res["status"] == "source_failed"


def _calendar_rows():
    return [
        {"cal_date": d, "is_open": is_open}
        for d, is_open in (
            ("20260706", 1), ("20260707", 1), ("20260708", 1), ("20260709", 1),
            ("20260710", 1), ("20260711", 0), ("20260712", 0), ("20260713", 1),
        )
    ]


def test_scan_keeps_new_stock_on_fifth_no_limit_open_day():
    basic = [{"ts_code": "688999.SH", "list_date": "20260706"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes, calendar=_calendar_rows())

    res = scanner.scan(reg, "2026-07-10", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "ok"
    assert [c["code"] for c in res["candidates"]] == ["688999.SH"]


def test_scan_excludes_new_stock_at_theoretical_limit_from_sixth_open_day():
    basic = [{"ts_code": "688999.SH", "list_date": "20260706"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes, calendar=_calendar_rows())

    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "ok"
    assert res["candidates"] == []


def test_scan_fails_closed_when_no_limit_calendar_is_unavailable():
    basic = [{"ts_code": "688999.SH", "list_date": "20260706"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes, calendar_error="calendar down")

    res = scanner.scan(reg, "2026-07-10", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "source_failed"
    assert "交易日历获取失败" in res["error"]


def test_scan_fails_closed_when_no_limit_calendar_is_empty():
    basic = [{"ts_code": "688999.SH", "list_date": "20260706"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes, calendar=[])

    res = scanner.scan(reg, "2026-07-10", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "source_failed"
    assert "自然日覆盖不完整" in res["error"]


def test_scan_fails_closed_when_no_limit_calendar_is_truncated():
    basic = [{"ts_code": "688999.SH", "list_date": "20260706"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    truncated = [row for row in _calendar_rows() if row["cal_date"] != "20260708"]
    reg = _Registry(basic, quotes, calendar=truncated)

    res = scanner.scan(reg, "2026-07-10", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "source_failed"
    assert "自然日覆盖不完整" in res["error"]


def test_scan_fails_closed_when_limit_candidate_has_no_listing_date():
    basic = [{"ts_code": "688999.SH"}]
    quotes = [_q("688999.SH", "科创新股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes)

    res = scanner.scan(reg, "2026-07-10", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "source_failed"
    assert "上市日期缺失" in res["error"]


def test_scan_excludes_old_limit_up_without_calling_calendar():
    basic = [{"ts_code": "688999.SH", "list_date": "20260102"}]
    quotes = [_q("688999.SH", "科创老股", 20.0, 30, price=12.0, high=12.0,
                 low=10.2, pre=10.0)]
    reg = _Registry(basic, quotes, calendar_error="calendar down")

    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)

    assert res["status"] == "ok"
    assert res["candidates"] == []
    assert all(cap != "get_trade_calendar" for cap, _ in reg.calls)
