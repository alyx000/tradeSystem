from services.tail_scan import scanner


def _q(code, name, pct, amount_yi, price=11.0, high=11.0, low=10.0, pre=10.0, open_=10.2):
    return {"code": code, "name": name, "price": price, "pct_chg": pct,
            "open": open_, "high": high, "low": low, "pre_close": pre,
            "volume": 1e6, "amount": amount_yi * 1e8,
            "quote_date": "2026-07-13", "quote_time": "14:30:00"}


def test_filter_keeps_only_triple_condition():
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


def test_filter_computes_limit_up_and_close_pos():
    # 主板 10cm：pre=10 → 涨停价 11.0；price=11.0 → is_limit_up True
    q = _q("600006.SH", "封板", 10.0, 30, price=11.0, high=11.0, low=10.5, pre=10.0)
    got = scanner.filter_quotes([q], min_pct=7.0, min_amount_yi=20.0)
    assert got[0]["is_limit_up"] is True
    # close_pos = (price-low)/(high-low) = (11-10.5)/(11-10.5)=1.0
    assert got[0]["close_pos"] == 1.0


class _Result:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.success = error is None and data is not None
        self.source = "mock"


class _Registry:
    def __init__(self, basic, quotes):
        self._basic, self._quotes = basic, quotes
    def call(self, cap, *a):
        if cap == "get_stock_basic_list":
            return _Result(self._basic)
        if cap == "get_realtime_quotes":
            return _Result(self._quotes)
        return _Result(error="unknown")


def test_scan_ok_filters_candidates():
    basic = [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}]
    quotes = [_q("600001.SH", "强", 8.0, 25), _q("600002.SH", "弱", 3.0, 25)]
    reg = _Registry(basic, quotes)
    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)
    assert res["status"] == "ok"
    assert res["matched"] == 1 and res["candidates"][0]["code"] == "600001.SH"


def test_scan_source_failed_when_no_codes():
    reg = _Registry([], [])
    res = scanner.scan(reg, "2026-07-13", min_pct=7.0, min_amount_yi=20.0)
    assert res["status"] == "source_failed"
