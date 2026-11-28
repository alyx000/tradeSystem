"""验证上榜强度、同代码完整收盘收益及历史只读接口对缺口的处理。"""
from copy import deepcopy
from datetime import date, timedelta
import json
import sqlite3

import pytest

from analyzers.sector_rhythm_metrics import archived_rhythm_view, open_window

DAY = "2026-11-27"
KIND = "industry"
NAME = "电子化学品Ⅱ"
CODE = "801086.SI"
SOURCE = "tushare:sw_daily"


@pytest.fixture
def archive():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE trade_calendar(exchange,date,is_open); CREATE TABLE daily_market(date,raw_data);")
    opens = []
    for i in range(19, -1, -1):
        d = date.fromisoformat(DAY) - timedelta(days=i)
        db.execute("INSERT INTO trade_calendar VALUES ('SSE',?,?)", (d.isoformat(), int(d.weekday() < 5)))
        if d.weekday() < 5:
            opens.append(d.isoformat())
    window = opens[-11:]
    prices = {d: 100.0 for d in window}
    for d, price in zip(window[-6:], [100, 103, 104.03, 93.627, 91.75446, 100.929906]):
        prices[d] = price
    ranked = {window[-5]: 3.0, window[-4]: 1.0, DAY: 10.0}
    for d in window:
        rows = [dict(name=f"同源行业{i}", change_pct=0.0) for i in range(30)]
        if d in ranked:
            rows[0] = dict(name=NAME, change_pct=ranked[d])
        raw = {"date": d, "sector_industry": {"_source": SOURCE, "data": rows}}
        envelope = {"date": d, "generated_at": d + "T20:00:00+08:00", "raw_data": raw}
        db.execute("INSERT INTO daily_market VALUES (?, ?)", (d, json.dumps(envelope)))
    quote = {"code": CODE, "name": NAME, "kind": KIND, "as_of": DAY, "status": "partial",
             "daily_source": SOURCE, "daily_bars": [{"time": d, "close": p} for d, p in prices.items()]}
    raw.update(sector_rhythm_industry=[{"name": NAME, "cumulative_pct_5d": 14, "phase": "原阶段"}],
               sector_adjustment_risk={"schema": "sector-adjustment-risk-v1", "date": DAY, "status": "partial",
                                       "generated_at": DAY + "T20:00:00+08:00", "rows": [quote]})
    yield db, raw, window
    db.close()


def first(raw, db):
    return archived_rhythm_view(raw, DAY, db)["sector_rhythm_industry"][0]


def test_archived_returns_and_ranked_days_use_independent_sources(archive):
    db, raw, days = archive
    original = deepcopy(raw)
    db.execute("PRAGMA query_only=ON")
    row = first(raw, db)
    assert raw == original and row["phase"] == "原阶段" and row["cumulative_pct_5d"] == 14
    assert row["period_return_5d_pct"] == 0.93 and row["period_return_10d_pct"] == 0.93
    assert (row["top30_sum_5d_pct"], row["top30_days_5d"], row["ranking_covered_days_5d"]) == (14.0, 3, 5)
    e = row["rhythm_window_evidence"]["5d"]
    assert e["status"] == "complete" and e["observed_closes"] == 6 and e["ts_code"] == CODE
    assert e["start_date"] == days[-6] and e["ranked_dates"] == [days[-5], days[-4], DAY]
    # 已发布的新版证据无需再访问其它库。
    raw["sector_rhythm_industry"] = [row]
    assert first(raw, None) == row


@pytest.mark.parametrize("damage", ["wrong_source", "wrong_kind", "wrong_code", "duplicate_name",
                                     "duplicate_code", "duplicate_bar", "missing_close", "as_of",
                                     "bar_code", "future_bar", "rank_return", "next_day_fetch", "before_close"])
def test_partial_identity_or_day_evidence_never_becomes_return(archive, damage):
    db, raw, days = archive
    block = raw["sector_adjustment_risk"]
    quote = block["rows"][0]
    target = raw["sector_industry"]["data"][0]
    if damage == "wrong_source":
        raw["sector_industry"]["_source"] = "akshare:board_industry"
    elif damage == "wrong_kind":
        quote["kind"] = "concept"
    elif damage == "wrong_code":
        target["ts_code"] = "801081.SI"
    elif damage == "duplicate_name":
        block["rows"].append({**quote, "code": "801081.SI"})
    elif damage == "duplicate_code":
        block["rows"].append({**quote, "name": "同码异名"})
    elif damage == "duplicate_bar":
        quote["daily_bars"].append(quote["daily_bars"][-4])
    elif damage == "missing_close":
        quote["daily_bars"][-4]["close"] = 0
    elif damage == "as_of":
        quote["as_of"] = days[-2]
    elif damage == "bar_code":
        quote["daily_bars"][-5]["ts_code"] = "801081.SI"
    elif damage == "future_bar":
        quote["daily_bars"].append({"time": "2026-11-30", "close": 90})
    elif damage == "rank_return":
        target["change_pct"] = 3
    elif damage == "next_day_fetch":
        block["generated_at"] = "2026-11-28T00:10:00+08:00"
    elif damage == "before_close":
        block["generated_at"] = DAY + "T06:59:00+00:00"
    row = first(raw, db)
    assert row["period_return_5d_pct"] is None and row["rhythm_window_evidence"]["5d"]["reason"]


def test_partial_calendar_and_rank_coverage_are_independent(archive):
    db, raw, days = archive
    db.execute("DELETE FROM daily_market WHERE date=?", (days[-4],))
    row = first(raw, db)
    assert row["period_return_5d_pct"] == 0.93
    assert row["ranking_covered_days_5d"] == 4 and row["top30_days_5d"] == 2
    assert row["rhythm_window_evidence"]["5d"]["ranking_missing_dates"] == [days[-4]]
    db.execute("DELETE FROM trade_calendar WHERE date=?", (days[-7],))
    row = first(raw, db)
    assert row["period_return_5d_pct"] == 0.93 and row["period_return_10d_pct"] is None
    db.execute("DELETE FROM trade_calendar WHERE date=?", (days[-2],))
    assert first(raw, db)["period_return_5d_pct"] is None


def test_concept_without_comparable_series_and_legacy_without_calendar(archive):
    db, raw, _ = archive
    raw["sector_rhythm_concept"] = [{"name": NAME, "cumulative_pct_5d": 23}]
    raw["sector_concept"] = {"_source": "akshare:board_concept", "data": [{"name": NAME, "change_pct": 10}]}
    row = archived_rhythm_view(raw, DAY, db)["sector_rhythm_concept"][0]
    assert row["period_return_5d_pct"] is None and row["rhythm_window_evidence"]["5d"]["reason"]
    row = first(raw, None)
    assert row["period_return_5d_pct"] is None and row["top30_days_5d"] is None
    assert row["cumulative_pct_5d"] == 14
    assert open_window("2026-11-28", 6, conn=db)[0] == []


@pytest.mark.parametrize("damage", ["partial", "source_failed", "failed", "bad_json", "late", "wrong_row_day"])
def test_one_bad_historical_ranking_does_not_hide_other_days(archive, damage):
    db, raw, days = archive
    day = days[-4]
    (blob,) = db.execute("SELECT raw_data FROM daily_market WHERE date=?", (day,)).fetchone()
    env = json.loads(blob)
    if damage == "late":
        env["generated_at"] = "2026-11-28T22:00:00+08:00"
    elif damage == "wrong_row_day":
        env["raw_data"]["sector_industry"]["data"][0]["trade_date"] = DAY
    else:
        env["raw_data"]["sector_industry"]["status"] = damage
    db.execute("UPDATE daily_market SET raw_data=? WHERE date=?", ("{" if damage == "bad_json" else json.dumps(env), day))
    row = first(raw, db)
    assert row["period_return_5d_pct"] == 0.93
    assert row["top30_sum_5d_pct"] == 13.0 and row["ranking_covered_days_5d"] == 4
    assert row["rhythm_window_evidence"]["5d"]["ranking_missing_dates"] == [day]


@pytest.mark.parametrize("damage", ["date", "not_finite", "wrong_code", "wrong_level"])
def test_saved_window_with_invalid_evidence_is_not_returned_as_verified(archive, damage):
    db, raw, days = archive
    row = first(raw, db)
    raw["sector_rhythm_industry"] = [row]
    evidence = row["rhythm_window_evidence"]["5d"]
    if damage == "date":
        evidence["end_date"] = days[-2]
    elif damage == "not_finite":
        row["period_return_5d_pct"] = float("nan")
    elif damage == "wrong_code":
        evidence["ts_code"] = "801086.TI"
    else:
        evidence["end_close"] = -1
    assert first(raw, None)["period_return_5d_pct"] is None


def test_observed_membership_survives_missing_ranked_day_move(archive):
    db, raw, days = archive
    date_key = days[-4]
    (blob,) = db.execute("SELECT raw_data FROM daily_market WHERE date=?", (date_key,)).fetchone()
    env = json.loads(blob)
    env["raw_data"]["sector_industry"]["data"][0]["change_pct"] = None
    db.execute("UPDATE daily_market SET raw_data=? WHERE date=?", (json.dumps(env), date_key))
    row = first(raw, db)
    assert row["top30_days_5d"] == 3 and row["ranking_covered_days_5d"] == 5
    assert row["top30_sum_5d_pct"] is None and row["period_return_5d_pct"] == 0.93
    assert row["rhythm_window_evidence"]["5d"]["ranked_change_missing_dates"] == [date_key]


def test_same_source_concept_has_verified_return_independent_of_prior_rank_coverage(archive):
    db, raw, _ = archive
    base = raw["sector_adjustment_risk"]["rows"][0]
    raw["sector_adjustment_risk"]["rows"] = [{**base, "kind": "concept", "code": "885905.TI",
                                               "name": "注册制次新股", "daily_source": "tushare:ths_daily"}]
    raw["sector_rhythm_concept"] = [{"name": "注册制次新股"}]
    raw["sector_concept"] = {"_source": "tushare:ths_daily",
                             "data": [{"name": "注册制次新股", "ts_code": "885905.TI", "change_pct": 10}]}
    row = archived_rhythm_view(raw, DAY, db)["sector_rhythm_concept"][0]
    assert row["period_return_5d_pct"] == 0.93 and row["ranking_covered_days_5d"] == 1
    assert row["rhythm_window_evidence"]["5d"]["ts_code"] == "885905.TI"
