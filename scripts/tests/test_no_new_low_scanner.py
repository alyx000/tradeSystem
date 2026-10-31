"""七天横盘不创新低机械筛选（全 fake，无外网/真实库）。"""
from __future__ import annotations

import sqlite3
import pytest

from providers.base import DataResult
from services.no_new_low import scanner


@pytest.mark.parametrize("capability", [
    "get_stock_universe_as_of", "get_market_daily_quotes", "get_adj_factor",
])
@pytest.mark.parametrize("payload,error,expected", [
    (None, "provider_not_initialized: test", "provider_not_initialized: test"),
    (None, "Token已过期 https://example.invalid/private-account", "Token已过期 [链接已隐藏]"),
    ({"unexpected": "shape"}, "", "invalid_source_payload: expected list"),
])
def test_upstream_error_is_preserved_instead_of_zero_rows(capability, payload, error, expected):
    dates = _dates(8)
    registry = _Registry(dates)
    original = registry.call

    def broken(method, *args):
        if method == capability:
            return DataResult(payload, "fake", error=error)
        return original(method, *args)

    registry.call = broken
    conn = _conn(dates)
    try:
        result = scanner.run_daily(conn, registry, dates[-1], snapshot_market_days=8,
                                   min_market_rows=2)
    finally:
        conn.close()
    assert result["status"] == "source_failed"
    assert result.get("candidate_count") is None
    detail = " ".join(result["failed_sources"].values())
    assert expected in detail
    assert "rows:0" not in detail
    assert "private-account" not in detail


def _dates(count: int = 10) -> list[str]:
    return [f"2026-08-{day:02d}" for day in range(1, count + 1)]


def _bar(code: str, date: str, low: float, *, amount: float = 1_000_000.0) -> dict:
    return {
        "ts_code": f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ",
        "trade_date": date, "open": low + 0.4, "high": low + 1.0,
        "low": low, "close": low + 0.6, "vol": 1000.0, "amount": amount,
    }


def _factor(code: str, date: str, value: float = 1.0) -> dict:
    return {
        "ts_code": f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ",
        "trade_date": date, "adj_factor": value,
    }


class _Registry:
    def __init__(self, dates, *, missing_pass_early=False, missing_pass_target=False,
                 industry_result=None):
        self.dates = dates
        self.calls = []
        self.codes = ("600001", "600002", "000003", "900901")
        self.identity = [
            {"ts_code": "600001.SH", "name": "通过股", "list_date": "20200101", "delist_date": ""},
            {"ts_code": "600002.SH", "name": "破低股", "list_date": "20200101", "delist_date": ""},
            {"ts_code": "000003.SZ", "name": "ST示例", "list_date": "20200101", "delist_date": ""},
            {"ts_code": "900901.SH", "name": "B股", "list_date": "20200101", "delist_date": ""},
        ]
        self.missing_pass_early = missing_pass_early
        self.missing_pass_target = missing_pass_target
        self.industry_result = industry_result or DataResult(
            {"600001.SH": {"name": "通过股", "sw_l2": "电力"}}, "fake:sw")

    def call(self, capability, *args):
        date = args[0] if args else None
        self.calls.append((capability, date))
        if capability == "get_stock_sw_industry_map":
            return self.industry_result
        if capability == "get_stock_universe_as_of":
            return DataResult(self.identity, "fake:universe")
        index = self.dates.index(date)
        if capability == "get_market_daily_quotes":
            rows = []
            for code in self.codes:
                if self.missing_pass_early and code == "600001" and index < 2:
                    continue
                if self.missing_pass_target and code == "600001" and index == len(self.dates) - 1:
                    continue
                # 通过股保持窄幅横盘，不能让测试样本本身成为台阶式上涨。
                low = 10.0 + max(index - 2, 0) * 0.02
                if code == "600002" and index == len(self.dates) - 3:
                    low = 9.5
                rows.append(_bar(code, date, low, amount=2_000_000.0 if code == "600001" else 1_000_000.0))
            return DataResult(rows, "fake:daily")
        if capability == "get_adj_factor":
            rows = []
            for code in self.codes:
                if self.missing_pass_early and code == "600001" and index < 2:
                    continue
                if self.missing_pass_target and code == "600001" and index == len(self.dates) - 1:
                    continue
                rows.append(_factor(code, date))
            return DataResult(rows, "fake:factor")
        raise AssertionError(capability)


def _conn(dates) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trade_calendar(date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.executemany("INSERT INTO trade_calendar VALUES (?,1)", [(date,) for date in dates])
    return conn


def test_run_daily_matches_qfq_seven_following_bars_and_excludes_st_b_share():
    dates = _dates(8)
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, _Registry(dates), dates[-1], snapshot_market_days=8,
            min_market_rows=2, min_join_coverage=0.9, min_target_coverage=0.9,
        )
    finally:
        conn.close()
    assert result["status"] == "ok"
    assert [item["code"] for item in result["candidates"]] == ["600001"]
    assert result["candidates"][0]["anchor_date"] == dates[0]
    assert result["definition"]["logic_version"] == "horizontal_v2"
    assert result["candidates"][0]["base_range_pct"] <= 12
    assert result["candidates"][0]["max_close_deviation_pct"] <= 8
    assert result["candidates"][0]["abs_window_return_pct"] <= 5
    assert result["candidates"][0]["industry"] == "电力"
    assert result["industry"]["status"] == "ok"
    assert result["industry"]["basis"] == "current_membership_snapshot"
    assert result["rejects"]["new_low_within_seven_bars"] == 1
    assert result["rejects"]["st_or_delist"] == 1
    assert result["rejects"]["b_share"] == 1


def test_suspension_dates_do_not_count_as_stock_bars():
    dates = _dates(10)
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, _Registry(dates, missing_pass_early=True), dates[-1], snapshot_market_days=10,
            min_market_rows=2, min_join_coverage=0.9, min_target_coverage=0.7,
        )
    finally:
        conn.close()
    passed = next(item for item in result["candidates"] if item["code"] == "600001")
    assert passed["anchor_date"] == dates[2]
    assert passed["bar_count"] == 8


def test_qfq_prevents_false_pass_across_corporate_action():
    dates = _dates(8)
    bars = [_bar("600001", date, 10.0 if i == 0 else 19.0) for i, date in enumerate(dates)]
    factors = [_factor("600001", date, 2.0 if i == 0 else 1.0) for i, date in enumerate(dates)]
    identity = {"ts_code": "600001.SH", "name": "除权股"}
    candidate, reason = scanner._evaluate_candidate(identity, bars, factors)
    assert candidate is None
    assert reason == "new_low_within_seven_bars"


def test_stair_step_riser_is_rejected_even_without_new_low():
    dates = _dates(8)
    bars = [_bar("600001", date, 10.0 + index * 0.30) for index, date in enumerate(dates)]
    factors = [_factor("600001", date) for date in dates]
    candidate, reason = scanner._evaluate_candidate(
        {"ts_code": "600001.SH", "name": "台阶上涨"}, bars, factors)
    assert candidate is None
    assert reason == "range_too_wide"


def test_large_close_excursion_is_rejected_even_if_price_returns_to_anchor():
    dates = _dates(8)
    lows = [10.0] * 8
    bars = [_bar("600001", date, low) for date, low in zip(dates, lows)]
    bars[0].update({"open": 10.05, "close": 10.10})
    bars[3].update({"open": 10.8, "high": 11.0, "close": 10.95})
    bars[-1].update({"open": 10.1, "high": 10.5, "close": 10.2})
    factors = [_factor("600001", date) for date in dates]
    candidate, reason = scanner._evaluate_candidate(
        {"ts_code": "600001.SH", "name": "冲高回落"}, bars, factors)
    assert candidate is None
    assert reason == "close_band_broken"


def test_end_drift_over_five_percent_is_rejected_inside_other_bands():
    dates = _dates(8)
    bars = [_bar("600001", date, 10.0) for date in dates]
    bars[0].update({"open": 10.05, "close": 10.10})
    bars[-1].update({"open": 10.5, "high": 10.8, "low": 10.4, "close": 10.7})
    factors = [_factor("600001", date) for date in dates]
    candidate, reason = scanner._evaluate_candidate(
        {"ts_code": "600001.SH", "name": "期末漂移"}, bars, factors)
    assert candidate is None
    assert reason == "net_drift_too_large"


def test_horizontal_pool_prefers_tighter_shape_before_liquidity():
    tight = {
        "base_range_pct": 6, "max_close_deviation_pct": 3,
        "abs_window_return_pct": 1, "volume_ratio": 1.0, "today_amount_yi": 2,
    }
    loose = {
        "base_range_pct": 10, "max_close_deviation_pct": 6,
        "abs_window_return_pct": 4, "volume_ratio": 1.0, "today_amount_yi": 100,
    }
    assert scanner._pool_business_key(tight) > scanner._pool_business_key(loose)


def test_industry_source_failure_is_visible_but_does_not_invalidate_scan():
    dates = _dates(8)
    registry = _Registry(
        dates,
        industry_result=DataResult(None, "fake:sw", error="industry timeout"),
    )
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, registry, dates[-1], snapshot_market_days=8,
            min_market_rows=2, min_join_coverage=0.9, min_target_coverage=0.9,
        )
    finally:
        conn.close()
    assert result["status"] == "ok"
    assert result["industry"]["status"] == "source_failed"
    assert result["industry"]["retry_count"] == 1
    assert result["candidates"][0]["industry"] == "未分类"


def test_sparse_industry_map_is_partial_and_reports_candidate_coverage():
    candidates = [{"code": "600001"}, {"code": "600002"}]
    registry = _Registry(_dates(8), industry_result=DataResult(
        {"600001.SH": {"sw_l2": "电力"}}, "fake:sw"))
    summary = scanner._enrich_industries(registry, candidates)
    assert summary["status"] == "partial"
    assert summary["matched_rows"] == 1
    assert summary["coverage"] == 0.5
    assert [item["industry"] for item in candidates] == ["电力", "未分类"]


def test_industry_enrichment_cannot_change_fact_order_rank_or_pool_cutoff():
    candidates = [
        {"code": f"600{index:03d}", "horizontal_rank": index + 1}
        for index in range(13)
    ]
    before = [(item["code"], item["horizontal_rank"]) for item in candidates]
    registry = _Registry(_dates(8), industry_result=DataResult(
        {item["code"]: {"sw_l2": f"板块{index}"} for index, item in enumerate(candidates)},
        "fake:sw",
    ))
    summary = scanner._enrich_industries(registry, candidates)
    assert summary["status"] == "ok"
    assert [(item["code"], item["horizontal_rank"]) for item in candidates] == before
    assert [item["code"] for item in candidates[:12]] == [code for code, _ in before[:12]]


def test_malformed_industry_payload_retries_then_marks_source_failed():
    candidates = [{"code": "600001"}]
    registry = _Registry(_dates(8), industry_result=DataResult(
        {"600001.SH": {"sw_l1": "公用事业"}}, "fake:sw"))
    summary = scanner._enrich_industries(registry, candidates)
    assert summary["status"] == "source_failed"
    assert summary["retry_count"] == 1
    assert summary["error"] == "industry_map_has_no_valid_sw_l2"
    assert candidates[0]["industry"] == "未分类"
    assert [call[0] for call in registry.calls] == [
        "get_stock_sw_industry_map", "get_stock_sw_industry_map"]


def _exact_bar(date: str, *, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "ts_code": "600001.SH", "trade_date": date,
        "open": open_, "high": high, "low": low, "close": close,
        "vol": 1000.0, "amount": 100_000.0,
    }


def test_horizontal_threshold_equalities_are_inclusive():
    dates = _dates(8)
    identity = {"ts_code": "600001.SH", "name": "边界股"}
    factors = [_factor("600001", date) for date in dates]

    range_bars = [
        _exact_bar(date, open_=105, high=112 if index == 3 else 106, low=100, close=105)
        for index, date in enumerate(dates)
    ]
    range_candidate, range_reason = scanner._evaluate_candidate(identity, range_bars, factors)
    assert range_reason == "matched"
    assert abs(range_candidate["base_range_pct"] - 12.0) < 1e-9

    deviation_bars = [
        _exact_bar(date, open_=100, high=108 if index == 3 else 101, low=99,
                   close=108 if index == 3 else 100)
        for index, date in enumerate(dates)
    ]
    deviation_candidate, deviation_reason = scanner._evaluate_candidate(
        identity, deviation_bars, factors)
    assert deviation_reason == "matched"
    assert abs(deviation_candidate["max_close_deviation_pct"] - 8.0) < 1e-9

    positive_bars = [
        _exact_bar(date, open_=100, high=105, low=99, close=105 if index == 7 else 100)
        for index, date in enumerate(dates)
    ]
    positive_candidate, positive_reason = scanner._evaluate_candidate(identity, positive_bars, factors)
    assert positive_reason == "matched"
    assert abs(positive_candidate["window_return_pct"] - 5.0) < 1e-9

    negative_bars = [
        _exact_bar(date, open_=100 if index == 0 else 96, high=101, low=94 if index == 0 else 94.5,
                   close=100 if index == 0 else (95 if index == 7 else 96))
        for index, date in enumerate(dates)
    ]
    negative_candidate, negative_reason = scanner._evaluate_candidate(identity, negative_bars, factors)
    assert negative_reason == "matched"
    assert abs(negative_candidate["window_return_pct"] + 5.0) < 1e-9


def test_target_day_missing_joint_bar_is_not_reported_with_stale_amount():
    dates = _dates(9)
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, _Registry(dates, missing_pass_target=True), dates[-1], snapshot_market_days=9,
            min_market_rows=2, min_join_coverage=0.7, min_target_coverage=0.7,
        )
    finally:
        conn.close()
    assert all(item["code"] != "600001" for item in result["candidates"])
    assert result["rejects"]["target_day_no_joint_bar"] == 1


def test_missing_calendar_is_source_failed_not_empty():
    dates = _dates(7)
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, _Registry(dates), dates[-1], snapshot_market_days=8,
            min_market_rows=2,
        )
    finally:
        conn.close()
    assert result["status"] == "source_failed"
    assert "trade_calendar" in result["failed_sources"]


def test_source_coverage_failure_is_not_reported_as_empty():
    dates = _dates(8)
    registry = _Registry(dates)
    original = registry.call

    def broken(capability, *args):
        result = original(capability, *args)
        date = args[0] if args else None
        if capability == "get_adj_factor" and date == dates[3]:
            return DataResult([], "fake:factor")
        return result

    registry.call = broken
    conn = _conn(dates)
    try:
        result = scanner.run_daily(
            conn, registry, dates[-1], snapshot_market_days=8,
            min_market_rows=2, min_join_coverage=0.9, min_target_coverage=0.9,
        )
    finally:
        conn.close()
    assert result["status"] == "source_failed"
    assert result.get("candidate_count") is None


def test_missing_calendar_middle_day_is_not_treated_as_closed():
    conn = _conn(_dates(9))
    try:
        conn.execute("DELETE FROM trade_calendar WHERE date=?", (_dates(9)[3],))
        assert scanner._recent_trade_dates(conn, _dates(9)[-1], 8) == []
    finally:
        conn.close()


def test_explicit_closed_calendar_day_is_allowed():
    conn = _conn(_dates(9))
    try:
        conn.execute("UPDATE trade_calendar SET is_open=0 WHERE date=?", (_dates(9)[3],))
        assert len(scanner._recent_trade_dates(conn, _dates(9)[-1], 8)) == 8
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["daily", "factor"])
def test_duplicate_code_cannot_hide_behind_invalid_first_row(kind):
    date = _dates(1)[0]
    if kind == "daily":
        valid = _bar("600001", date, 10)
        invalid = dict(valid, close=None)
        mapped, error = scanner._daily_map([invalid, valid], date, 1)
    else:
        valid = _factor("600001", date)
        invalid = dict(valid, adj_factor=None)
        mapped, error = scanner._factor_map([invalid, valid], date, 1)
    assert mapped is None
    assert "duplicate_or_invalid_code" in error
