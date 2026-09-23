from copy import deepcopy
from datetime import date, timedelta

import pytest

from services.trend_leader import observations as O


def bars():
    rows = []
    for i in range(30):
        rows.append(dict(trade_date=(date(2026, 7, 1)+timedelta(days=i)).isoformat(),
                         open=10., close=10., high=10.1, low=9.9, vol=100., pct_chg=0.))
    rows[-3].update(open=10., close=10.5, high=10.55, low=9.95, vol=200.)
    rows[-2].update(open=10.5, close=10.5, high=10.6, low=10.4, vol=120.)
    rows[-1].update(open=10.45, close=10.3, high=10.5, low=10.2, vol=50.)
    return rows


def analyze(rows, support=True):
    return O.analyze(rows, rows[-1]["trade_date"], dict(supported=support))


def test_launch_then_pullback_with_supported_sector():
    r = analyze(bars())
    assert r["state"] == "pullback_observed"
    assert r["launch_date"] == "2026-07-28" and r["bars_since_launch"] == 2
    assert r["launch_volume_ratio"] == 2


def test_launch_day_is_not_pullback():
    rows = bars()[:-2]
    prefix = [dict(rows[0], trade_date=(date(2026, 6, 29)+timedelta(days=i)).isoformat()) for i in range(2)]
    rows = prefix + rows
    assert analyze(rows)["state"] == "launched"


@pytest.mark.parametrize("count", [26, 27, 28, 29, 30])
def test_full_twenty_bar_window_requires_thirty_bars(count):
    rows = bars()[:count]
    for row in rows:
        row.update(open=10., close=10., high=10.1, low=9.9, vol=100.)
    launch = count - 20
    rows[launch].update(open=10., close=10.5, high=10.6, low=9.9, vol=200.)
    for row in rows[launch+1:]:
        row.update(open=10.5, close=10.5, high=10.6, low=10.4, vol=100.)
    result = analyze(rows)
    if count < 30:
        assert result["status"] == "partial"
        assert result["state"] == "missing_data"
    else:
        assert result["status"] == "complete"
        assert result["launch_date"] == rows[launch]["trade_date"]
        assert result["state"] == "tracking"


def test_no_launch_and_structural_invalidation_not_revived():
    rows = bars()
    rows[-3]["vol"] = 100.
    assert analyze(rows)["state"] == "no_launch"
    rows = bars()
    rows[-2]["low"] = 9.8
    assert analyze(rows)["state"] == "invalidated"


@pytest.mark.parametrize("supported,state", [(None, "sector_unverified"), (False, "support_weakened")])
def test_sector_boundaries(supported, state):
    result = analyze(bars(), supported)
    assert result["state"] == state
    assert result["status"] == ("partial" if supported is None else "complete")


def test_bad_prices_and_stale_history():
    rows = bars()
    assert O.analyze(rows, "2026-07-31", {})["state"] == "missing_data"
    rows[-1]["vol"] = float("nan")
    assert analyze(rows)["state"] == "missing_data"


def test_qfq_includes_open_and_rejects_duplicate_factors():
    rows = bars()
    factors = [dict(trade_date=r["trade_date"], adj_factor=1.) for r in rows]
    factors[-1]["adj_factor"] = 2.
    adjusted = O.prepare(rows, factors, rows[-1]["trade_date"])
    assert adjusted[0]["open"] == 5 and adjusted[0]["close"] == 5
    factors.append(factors[0])
    assert O.prepare(rows, factors, rows[-1]["trade_date"]) == []
    assert O.prepare(rows, [], rows[-1]["trade_date"]) == []


def test_future_and_duplicate_bars_rejected_before_adjustment():
    rows = bars()
    factors = [dict(trade_date=r["trade_date"], adj_factor=1.) for r in rows]
    assert O.prepare(rows, factors, "2026-07-29") == []
    rows.insert(1, deepcopy(rows[0]))
    assert O.prepare(rows, factors, "2026-07-30") == []
