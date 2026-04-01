"""L1: DB 并发安全测试。"""
from __future__ import annotations

import threading
import time

import pytest

from db.connection import get_connection
from db.schema import init_schema
from db import queries as Q


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "concurrent.db"
    conn = get_connection(path)
    init_schema(conn)
    for d in ["2026-04-01", "2026-04-02", "2026-04-03"]:
        Q.upsert_daily_market(conn, {"date": d, "total_amount": 10000.0})
    conn.commit()
    conn.close()
    return path


def test_concurrent_reads(db_path):
    results = [None, None, None]
    errors = [None, None, None]

    def reader(idx):
        try:
            c = get_connection(db_path)
            rows = Q.get_daily_market_range(c, "2026-04-01", "2026-04-03")
            results[idx] = len(rows)
            c.close()
        except Exception as e:
            errors[idx] = e

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    for e in errors:
        assert e is None, f"Reader error: {e}"
    for r in results:
        assert r == 3


def test_concurrent_read_during_write(db_path):
    write_done = threading.Event()
    read_results = [None, None]
    errors = []

    def writer():
        try:
            c = get_connection(db_path)
            Q.upsert_daily_market(c, {"date": "2026-04-04", "total_amount": 20000.0})
            c.commit()
            write_done.set()
            c.close()
        except Exception as e:
            errors.append(e)
            write_done.set()

    def reader(idx):
        try:
            c = get_connection(db_path)
            rows = Q.get_daily_market_range(c, "2026-04-01", "2026-04-03")
            read_results[idx] = len(rows)
            c.close()
        except Exception as e:
            errors.append(e)

    wt = threading.Thread(target=writer)
    rt1 = threading.Thread(target=reader, args=(0,))
    rt2 = threading.Thread(target=reader, args=(1,))

    wt.start()
    rt1.start()
    rt2.start()
    wt.join(timeout=5)
    rt1.join(timeout=5)
    rt2.join(timeout=5)

    assert not errors, f"Errors: {errors}"
    for r in read_results:
        assert r == 3


def test_concurrent_writes_different_tables(db_path):
    errors = []

    def write_market():
        try:
            c = get_connection(db_path)
            Q.upsert_daily_market(c, {"date": "2026-04-05", "total_amount": 30000.0})
            c.commit()
            c.close()
        except Exception as e:
            errors.append(e)

    def write_teacher():
        try:
            c = get_connection(db_path)
            tid = Q.get_or_create_teacher(c, "并发老师")
            Q.insert_teacher_note(c, teacher_id=tid, date="2026-04-01", title="并发测试")
            c.commit()
            c.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=write_market)
    t2 = threading.Thread(target=write_teacher)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"Errors: {errors}"


def test_write_conflict_retries(db_path):
    errors = []
    success = [False, False]

    def writer(idx, date_str):
        try:
            c = get_connection(db_path)
            Q.upsert_daily_market(c, {"date": date_str, "total_amount": float(idx * 1000)})
            c.commit()
            success[idx] = True
            c.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=(0, "2026-04-06"))
    t2 = threading.Thread(target=writer, args=(1, "2026-04-07"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert all(success), f"Not all writes succeeded. Errors: {errors}"
