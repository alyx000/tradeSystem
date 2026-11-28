"""逐股炸板池只发布可核验数量，日线对照单列。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzers import broken_board_pool as b

DAY = "2026-11-27"
NOW = datetime(2026, 11, 27, 20, tzinfo=b.TZ)


def packet(rows):
    return {"rc": 0, "data": {"pool": rows, "tc": len(rows), "qdate": 20261127}}


def member(code="600001", name="个股"):
    return {"c": code, "n": name, "fbt": 92502, "p": 6500, "ztp": 6800, "amount": 120_000, "zbc": 2}


def take(payload):
    return b.collect(DAY, fetch=lambda _: payload, now=NOW)


def test_fields_come_from_same_single_request():
    calls = []
    rows = [member(), member("000010", "*ST个股")]
    def fetch(day):
        calls.append(day)
        return packet(rows)
    result = b.collect(DAY, fetch=fetch, now=NOW)
    assert calls == [DAY]
    assert (result["status"], result["count"], result["count_ex_st"]) == ("complete", 2, 1)
    assert result["stocks"][0] == dict(code="600001.SH", name="个股", first_seal_time="09:25:02", break_times=2,
                                       last_price=6.5, up_limit=6.8, amount_yuan=120000, industry=None)
    assert result["source_response"] == packet(rows)


@pytest.mark.parametrize("bad", [None, {"rc": 0, "data": None}, {"rc": 1, "data": {"pool": []}},
                                 {"rc": 0, "data": {"tc": 1, "pool": [], "qdate": 20261127}},
                                 {"rc": False, "data": {"tc": 0, "pool": [], "qdate": 20261127}},
                                 {"rc": 0, "data": {"tc": 0, "pool": [], "qdate": 20261126}},
                                 packet([member(), member()]), packet([dict(member(), p=6800)])])
def test_source_failure_is_unknown_not_zero(bad):
    block = take(bad)
    assert block["status"] == "source_failed" and block["count"] is None
    lu = {"stocks": [{"code": "000001.SZ"}], "count": 1}
    b.apply_rates(lu, block, lu["stocks"])
    assert lu["seal_rate_pct"] is None and lu["broken_rate_pct_ex_st"] is None


def test_verified_empty_and_undefined_denominator():
    block = take(packet([]))
    assert block["status"] == "complete" and block["count"] == 0
    lu = {"stocks": [{"code": "000001.SZ"}], "count": 1}
    b.apply_rates(lu, block, [])
    assert lu["seal_rate_pct"] == 100 and lu["broken_rate_pct"] == 0
    assert lu["seal_rate_pct_ex_st"] is None


def test_counts_and_rates_are_disjoint_and_st_matched():
    block = take(packet([member(), member("000010", "*ST个股")]))
    lu = {"stocks": [{"code": "000001.SZ"}, {"code": "000021.SZ"}], "count": 2}
    b.apply_rates(lu, block, lu["stocks"])
    assert lu["broken_rate_pct"] == 50 and lu["broken_rate_pct_ex_st"] == 33.3
    for stocks, total in [([{"code": "600001.SH"}], 1), ([{"code": "600002.SH"}], 2),
                          ([{"code": "000001.SZ"}]*2, 2)]:
        lu = {"stocks": stocks, "count": total}
        b.apply_rates(lu, block, stocks)
        assert lu["seal_rate_pct"] is None and lu["seal_rate_gap"]


def test_partial_keeps_missing_fields_null_and_verified_previous():
    complete = take(packet([member()]))
    partial = take(packet([dict(member(), fbt=None, zbc=None)]))
    assert partial["count"] == 1 and partial["status"] == "partial"
    assert partial["stocks"][0]["first_seal_time"] is None and partial["stocks"][0]["break_times"] is None
    recovered = b.prefer_verified(partial, complete)
    assert recovered["source_sha256"] == complete["source_sha256"] and recovered["latest_attempt"]["status"] == "partial"
    tamper = deepcopy(complete)
    tamper["source_response"]["data"]["qdate"] = 20261126
    assert b.prefer_verified(partial, tamper) is partial


def test_before_close_does_not_call_source():
    now = datetime(2026, 11, 27, 15, 30, tzinfo=b.TZ)
    block = b.collect(DAY, now=now, fetch=lambda _: pytest.fail("未到盘后"))
    assert block["status"] == "source_failed"


def test_legacy_archive_zero_independent_of_market_fields(tmp_path):
    from tools import broken_board_pool as cli
    assert cli.touch_evidence(tmp_path, DAY)["count"] is None
    assert b.archived_post(tmp_path, DAY) == {}


def test_quiet_period_and_invalid_prior_provenance_are_rejected():
    block = take(packet([dict(member(), fbt=92700)]))
    assert block["status"] == "partial" and block["stocks"][0]["first_seal_time"] is None
    for invalid in (dict(block, source="other"), dict(block, fetched_at="2026-11-27T13:00:00+08:00"),
                    dict(block, fetched_at="2026-11-27T20:00:00")):
        assert b.readback(invalid, DAY) is None


def test_partial_cannot_overwrite_verified_different_field():
    original = take(packet([dict(member(), fbt=None)]))
    partial = take(packet([dict(member(), zbc=None)]))
    result = b.prefer_verified(partial, original)
    assert result["stocks"][0]["break_times"] == 2
    assert result["latest_attempt"]["source_sha256"] == partial["source_sha256"]
    better = take(packet([member()]))
    assert b.prefer_verified(better, original) == better


def test_dual_write_honors_null_rates_for_new_scope_and_keeps_legacy_fallback():
    from db.dual_write import _extract_market_row
    raw = {"limit_up": {"count": 2, "stocks": [], "seal_rate_pct": 100, "broken_rate_pct": 0},
           "emotion": {"seal_rate": 96, "broken_rate": 4}, b.KEY: take(packet([]))}
    row = _extract_market_row(DAY, {"raw_data": raw})
    assert row["seal_rate"] is None and row["broken_rate"] is None
    del raw[b.KEY]
    old = _extract_market_row(DAY, {"raw_data": raw})
    assert old["seal_rate"] == 100 and old["broken_rate"] == 0


def test_akshare_bare_codes_cannot_double_count_same_stock():
    block = take(packet([member()]))
    for sealed in (["600001"], ["600002","600002.SH"], ["not-a-code"], ["600002.SZ"]):
        stocks = [{"code": c} for c in sealed]
        lu = {"stocks": stocks, "count": len(stocks)}
        b.apply_rates(lu, block, stocks)
        assert lu["seal_rate_pct"] is None and "seal_rate_gap" in lu


def test_st_excluded_tushare_seals_and_inclusive_broken_pool_require_matched_denominators():
    broken = take(packet([member("000010", "*ST个股"), member("000020", "测试")]))
    stocks = [{"code": "000001.SZ"}]
    lu = {"stocks": stocks, "count": 1}
    b.apply_rates(lu, broken, stocks, sealed_source="tushare:limit_list_d")
    assert (lu["broken_count"], lu["broken_count_ex_st"]) == (2, 1)
    assert lu["seal_rate_pct"] is None and lu["seal_rate_pct_ex_st"] == 50
    assert lu["seal_rate_scope"]["sealed_scope"] == "收盘涨停源榜剔ST"
