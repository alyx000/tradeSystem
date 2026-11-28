"""一字首开序列按实现日连线，沿用旧标量并独立核验口径。"""
import json
import sqlite3
from datetime import date, timedelta

import pytest
import yaml

from db import queries as Q
from db.connection import get_connection
from db.schema import init_schema


@pytest.fixture
def database(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    conn = get_connection(directory / "trade.db")
    init_schema(conn)
    for i in range(12):
        day = date(2026, 7, 22) + timedelta(days=i)
        Q.upsert_trade_calendar(conn, [{"date": day.isoformat(), "is_open": int(day.weekday() < 5)}])
    yield conn
    conn.close()


def receipt(start, end, median, count, *, split=True):
    group = {"count": count}
    if count:
        group.update(premium_median=median, detail=[{"t_opened": True} if split else {"is_yizi": True}])
    block = {"prev_date": start, "trade_date": end, "first_board": {"count": 10}, "yizi_first_open": group}
    if split:
        block.update(yizi_continued={"count": 0}, yizi_undetermined=0)
    return block


def seed(conn, day, scalar=None, block=None, snapshot=None, **extra):
    doc = {"date": day, "generated_at": day + "T20:00:00", "raw_data": {"date": day}, **extra}
    if block is not None:
        doc["premium_backfill"] = block
    if snapshot is not None:
        doc["raw_data"]["style_factors"] = {"premium_snapshot": {"yizi_first_open": snapshot}}
    Q.upsert_daily_market(conn, {"date": day, "premium_first_open": scalar, "raw_data": json.dumps(doc)})


def series(conn, start, end):
    return Q.get_style_factors_series(conn, ["premium_first_open"], start, end)


def test_adjacent_values_with_different_cohorts_and_missing_session(database):
    conn = database
    seed(conn, "2026-07-24", block=receipt("2026-07-24", "2026-07-27", 1.57, 7, split=False))
    seed(conn, "2026-07-27", 1.57, receipt("2026-07-27", "2026-07-28", 9.92, 1))
    seed(conn, "2026-07-28", 9.92, receipt("2026-07-28", "2026-07-29", 0., 3))
    seed(conn, "2026-07-29", 0.)
    seed(conn, "2026-07-31", 2.)
    rows = series(conn, "2026-07-27", "2026-07-31")
    assert [(r["premium_first_open"], r["premium_first_open_basis"], r["premium_first_open_count"],
             r["premium_first_open_break_before"]) for r in rows] == [
        (1.57, "legacy_unsplit", 7, False), (9.92, "opened_only", 1, True),
        (0., "opened_only", 3, False), (2., "unverified", None, True)]
    assert list(Q.get_style_factors_series(conn, ["premium_10cm"], "2026-07-27", "2026-07-27")[0]) == ["date", "premium_10cm"]


def test_file_fallback_validates_same_envelope_and_proves_zero_samples(database, tmp_path):
    conn = database
    source, day = "2026-07-27", "2026-07-28"
    seed(conn, source)
    seed(conn, day)
    directory = tmp_path / "daily" / source
    directory.mkdir(parents=True)
    path = directory / "post-market.yaml"
    doc = {"date": source, "generated_at": source + "T20:00:00", "premium_backfill": receipt(source, day, None, 0)}
    path.write_text(yaml.safe_dump(doc) + "raw_data:\n  deliberately: [truncated", encoding="utf8")
    row, = series(conn, day, day)
    assert row["premium_first_open_basis"] == "opened_only" and row["premium_first_open_count"] == 0
    assert row["premium_first_open"] is None
    doc["generated_at"] = source + "T22:00:00"
    path.write_text(yaml.safe_dump(doc), encoding="utf8")
    assert series(conn, day, day)[0]["premium_first_open_basis"] == "unverified"
    doc["generated_at"] = source + "T20:00:00"
    path.write_text(yaml.safe_dump(doc), encoding="utf8")
    seed(conn, source, block={"prev_date": source, "trade_date": "2026-07-29"})
    assert series(conn, day, day)[0]["premium_first_open_count"] is None


@pytest.mark.parametrize("conflict", ["date", "median", "snapshot", "no_split", "bad_group", "calendar", "null_count"])
def test_conflicting_receipt_or_missing_proof_keeps_archived_value(database, conflict):
    conn = database
    source, day = "2026-07-27", "2026-07-28"
    block = receipt(source, day, 3.5, 4)
    snapshot = {"count": 4, "premium_median": 3.5}
    if conflict == "date":
        block["trade_date"] = "2026-07-29"
    elif conflict == "median":
        block["yizi_first_open"]["premium_median"] = 4.5
    elif conflict == "snapshot":
        snapshot["count"] = 3
    elif conflict == "no_split":
        del block["yizi_continued"]
    elif conflict == "bad_group":
        block["yizi_first_open"]["detail"] = None
    elif conflict == "calendar":
        conn.execute("DELETE FROM trade_calendar WHERE date=?", (day,))
    else:
        block["yizi_first_open"]["count"] = None
    seed(conn, source, block=block)
    seed(conn, day, 3.5, snapshot=snapshot)
    row, = series(conn, day, day)
    assert (row["premium_first_open"], row["premium_first_open_basis"], row["premium_first_open_count"]) == (
        3.5, "unverified", 4 if conflict == "no_split" else None)


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 45), reason="SQLite older than JSON5 validation")
def test_unrelated_historical_nan_does_not_discard_valid_receipt(database):
    source, day = "2026-07-27", "2026-07-28"
    seed(database, source, block=receipt(source, day, 3., 2), unrelated=float("nan"))
    seed(database, day, 3.)
    assert series(database, day, day)[0]["premium_first_open_basis"] == "opened_only"



@pytest.mark.parametrize("count,undetermined,status", [(0, 1, "complete"), (0, 0, "partial"), (3, 1, "complete")])
def test_partial_quotes_or_undetermined_cannot_be_an_exhaustive_sample(database, count, undetermined, status):
    source, day = "2026-07-27", "2026-07-28"
    median = 4.0 if count else None
    block = receipt(source, day, median, count)
    block.update(yizi_undetermined=undetermined, _coverage={"status": status})
    seed(database, source, block=block)
    seed(database, day, median)
    row, = series(database, day, day)
    assert row["premium_first_open_basis"] == "opened_only"
    assert row["premium_first_open_count"] == count and row["premium_first_open_partial"] is True
