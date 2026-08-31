from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.intraday_summary.analytics import analyze_interval, current_breadth, market_tone
from services.intraday_summary.schedule import slot_for_time


TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(prices: list[float], amounts: list[float], *, daily: list[float]) -> dict:
    return {
        "stocks": {
            f"{index:06d}.SZ": {
                "name": f"股票{index}",
                "price": price,
                "pct_chg": daily[index],
                "amount": amounts[index],
            }
            for index, price in enumerate(prices)
        },
        "indices": {
            code: {"name": code, "price": 1000.0, "pct_chg": 0.0}
            for code in ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH")
        },
    }


def test_interval_statistics_and_industry_rankings():
    previous = _snapshot([10.0] * 16, [1e8] * 16, daily=[0.0] * 16)
    current = _snapshot(
        [10.2] * 8 + [9.8] * 8,
        [1.1e8] * 16,
        daily=[2.0] * 8 + [-2.0] * 8,
    )
    industries = {
        f"{index:06d}.SZ": "强行业" if index < 8 else "弱行业"
        for index in range(16)
    }

    result = analyze_interval(previous, current, industries)

    assert result["status"] == "complete"
    assert result["up"] == 8
    assert result["down"] == 8
    assert result["amount_yi"] == 1.6
    assert result["sectors"]["strongest"][0]["name"] == "强行业"
    assert result["sectors"]["weakest"][0]["name"] == "弱行业"
    assert market_tone(result) == "半小时内涨跌分化，未形成单边扩散"


def test_current_breadth_keeps_zero_distinct_from_missing():
    snapshot = _snapshot([10.0, 10.0, 10.0], [1, 1, 1], daily=[1.0, 0.0, -1.0])
    result = current_breadth(snapshot)
    assert result == {
        "up": 1,
        "down": 1,
        "flat": 1,
        "valid": 3,
        "up_ratio_pct": 33.33,
        "median_pct": 0.0,
        "mean_pct": 0.0,
        "strong_5pct": 0,
        "weak_5pct": 0,
    }


def test_interval_coverage_failure_is_not_an_empty_market():
    previous = _snapshot([10.0] * 100, [1] * 100, daily=[0] * 100)
    current = _snapshot([10.0] * 90, [2] * 90, daily=[0] * 90)
    result = analyze_interval(previous, current, {})
    assert result["status"] == "coverage_failed"
    assert result["coverage_pct"] == 90.0
    assert "覆盖不足" in result["error"]


def test_missing_baseline_index_downgrades_interval_to_partial():
    previous = _snapshot([10.0] * 16, [1e8] * 16, daily=[0.0] * 16)
    current = _snapshot([10.1] * 16, [1.1e8] * 16, daily=[1.0] * 16)
    previous["indices"] = {
        "000001.SH": {"price": 1000},
        "399001.SZ": {"price": 1000},
        "399006.SZ": {"price": 1000},
    }
    current["indices"] = {
        **previous["indices"],
        "000688.SH": {"price": 1000},
    }
    industries = {f"{index:06d}.SZ": "行业" for index in range(16)}

    result = analyze_interval(previous, current, industries)

    assert result["status"] == "partial"
    assert result["index_error"] == "宽基指数两点覆盖不足（3/4）"


def test_amount_regression_is_missing_not_zero():
    previous = _snapshot([10.0] * 16, [2e8] * 16, daily=[0.0] * 16)
    current = _snapshot([10.1] * 16, [1e8] * 16, daily=[1.0] * 16)
    industries = {f"{index:06d}.SZ": "行业" for index in range(16)}

    result = analyze_interval(previous, current, industries)

    assert result["status"] == "partial"
    assert result["amount_yi"] is None
    assert result["amount_coverage_pct"] == 0.0
    assert "覆盖不足" in result["amount_error"]


def test_slot_schedule_includes_close_grace_but_not_lunch():
    morning_close = datetime(2026, 8, 31, 11, 34, tzinfo=TZ)
    assert slot_for_time(morning_close).label == "11:30"
    assert slot_for_time(morning_close + timedelta(minutes=2)) is None
    assert slot_for_time(datetime(2026, 8, 31, 12, 0, tzinfo=TZ)) is None
    assert slot_for_time(datetime(2026, 8, 31, 15, 5, tzinfo=TZ)).label == "15:00"
