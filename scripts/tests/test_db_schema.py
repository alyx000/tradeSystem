"""L1: DB Schema 与连接测试。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.connection import get_connection, get_db
from db.schema import EXPECTED_TABLES, init_schema


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    init_schema(c)
    yield c
    c.close()


class TestSchema:
    def test_create_all_tables(self, conn):
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            ).fetchall()
        ]
        for t in EXPECTED_TABLES:
            assert t in tables, f"Missing table: {t}"

    def test_wal_mode_enabled(self, conn):
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_busy_timeout_set(self, conn):
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000

    def test_foreign_keys_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO teacher_notes (teacher_id, date, title) VALUES (9999, '2026-01-01', 'test')"
            )

    def test_schema_idempotent(self, conn):
        init_schema(conn)
        init_schema(conn)
        conn.execute("INSERT INTO teachers (name) VALUES ('test_teacher')")
        init_schema(conn)
        row = conn.execute("SELECT name FROM teachers WHERE name = 'test_teacher'").fetchone()
        assert row is not None

    def test_date_check_constraint_daily_market(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO daily_market (date) VALUES ('not-a-date')"
            )

    def test_date_check_constraint_calendar(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO calendar_events (date, event) VALUES ('bad', 'test')"
            )

    def test_date_check_constraint_trades(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trades (date, stock_code, stock_name, direction, price) "
                "VALUES ('bad', '000001', 'test', '买入', 10.0)"
            )

    def test_date_check_constraint_emotion(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO emotion_cycle (date) VALUES ('bad')")

    def test_date_check_constraint_reviews(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO daily_reviews (date) VALUES ('bad')")


class TestContextManager:
    def test_get_db_commits(self, db_path):
        with get_db(db_path) as c:
            init_schema(c)
            c.execute("INSERT INTO teachers (name) VALUES ('ctx_test')")

        with get_db(db_path) as c:
            row = c.execute("SELECT name FROM teachers WHERE name = 'ctx_test'").fetchone()
            assert row is not None

    def test_get_db_rollback_on_error(self, db_path):
        with get_db(db_path) as c:
            init_schema(c)

        with pytest.raises(ValueError):
            with get_db(db_path) as c:
                c.execute("INSERT INTO teachers (name) VALUES ('rollback_test')")
                raise ValueError("forced error")

        with get_db(db_path) as c:
            row = c.execute("SELECT name FROM teachers WHERE name = 'rollback_test'").fetchone()
            assert row is None
