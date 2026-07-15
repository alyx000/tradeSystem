"""leader_tracking 表的单元测试。"""
from __future__ import annotations

import sqlite3

import pytest

from db.connection import get_connection
from db.migrate import migrate
from db.queries import (
    deactivate_stale_leaders,
    get_active_leaders,
    upsert_leader_tracking,
)


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    migrate(c)
    yield c
    c.close()


class TestUpsertLeaderTracking:
    def test_insert_new(self, conn):
        rid = upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-10",
            current_phase="启动",
        )
        assert rid > 0
        leaders = get_active_leaders(conn)
        assert len(leaders) == 1
        assert leaders[0]["stock_code"] == "688041"
        assert leaders[0]["consecutive_days"] == 1
        assert leaders[0]["first_seen_date"] == "2026-04-10"

    def test_upsert_existing_increments_days(self, conn):
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-10",
        )
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-11",
            current_phase="主升",
        )
        leaders = get_active_leaders(conn)
        assert len(leaders) == 1
        assert leaders[0]["consecutive_days"] == 2
        assert leaders[0]["last_seen_date"] == "2026-04-11"
        assert leaders[0]["current_phase"] == "主升"

    def test_same_date_no_increment(self, conn):
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-10",
        )
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-10",
        )
        leaders = get_active_leaders(conn)
        assert leaders[0]["consecutive_days"] == 1

    def test_older_backfill_preserves_last_seen_count_activity_and_latest_metadata(self, conn):
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="趋势中军",
            seen_date="2026-07-15",
            current_phase="主升",
            notes="最新说明",
        )
        deactivate_stale_leaders(conn, before_date="2026-07-16")

        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息旧称",
            sector="AI算力",
            attribute_type="趋势中军",
            seen_date="2026-07-14",
            current_phase="启动",
            notes="历史说明",
        )

        row = conn.execute(
            "SELECT * FROM leader_tracking WHERE stock_code = '688041'"
        ).fetchone()
        assert row["first_seen_date"] == "2026-07-14"
        assert row["last_seen_date"] == "2026-07-15"
        assert row["consecutive_days"] == 1
        assert row["is_active"] == 0
        assert row["stock_name"] == "海光信息"
        assert row["current_phase"] == "主升"
        assert row["notes"] == "最新说明"

    def test_different_sector_creates_new(self, conn):
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-10",
        )
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="国产AI",
            attribute_type="基本面最正宗",
            seen_date="2026-04-10",
        )
        leaders = get_active_leaders(conn)
        assert len(leaders) == 2


class TestDeactivateStaleLeaders:
    def test_deactivate(self, conn):
        upsert_leader_tracking(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            sector="AI算力",
            attribute_type="走势引领",
            seen_date="2026-04-05",
        )
        upsert_leader_tracking(
            conn,
            stock_code="601138",
            stock_name="工业富联",
            sector="海外AI链",
            attribute_type="容量最大",
            seen_date="2026-04-11",
        )
        count = deactivate_stale_leaders(conn, before_date="2026-04-10")
        assert count == 1
        leaders = get_active_leaders(conn)
        assert len(leaders) == 1
        assert leaders[0]["stock_code"] == "601138"
