"""L3: 双写测试。"""
from __future__ import annotations

import json

import pytest
import yaml

from db.connection import get_connection, get_db
from db.dual_write import (
    _extract_market_row,
    reconcile_daily_market,
    record_pending,
    retry_pending,
    sync_daily_market_to_db,
)
from db.migrate import migrate
from db import queries as Q


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    with get_db(path) as conn:
        migrate(conn)
    return path


@pytest.fixture
def sample_yaml_data():
    return {
        "indices": {"sh_close": 3285.89, "sh_change_pct": 0.52,
                     "sz_close": 10123.45, "sz_change_pct": 0.78},
        "total_amount": 12345.0,
        "emotion": {"limit_up_count": 85, "limit_down_count": 5,
                     "seal_rate": 78.5, "broken_rate": 21.5, "highest_board": 7},
        "market_breadth": {"advance_count": 3200, "decline_count": 1800},
        "capital_flow": {"northbound_net": 50.3},
    }


class TestSyncDailyMarket:
    def test_sync_success(self, db_path, sample_yaml_data):
        ok = sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=db_path)
        assert ok is True
        with get_db(db_path) as conn:
            row = Q.get_daily_market(conn, "2026-04-01")
        assert row is not None
        assert row["sh_index_close"] == 3285.89
        assert row["total_amount"] == 12345.0

    def test_yaml_and_db_consistency(self, db_path, sample_yaml_data, tmp_path):
        yaml_path = tmp_path / "2026-04-01" / "post-market.yaml"
        yaml_path.parent.mkdir(parents=True)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(sample_yaml_data, f, allow_unicode=True)

        sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=db_path)

        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)

        with get_db(db_path) as conn:
            db_row = Q.get_daily_market(conn, "2026-04-01")

        assert db_row["sh_index_close"] == yaml_data["indices"]["sh_close"]
        assert db_row["total_amount"] == yaml_data["total_amount"]
        assert db_row["limit_up_count"] == yaml_data["emotion"]["limit_up_count"]


class TestPendingWrites:
    def test_db_failure_writes_pending(self, tmp_path, sample_yaml_data, monkeypatch):
        pending_path = tmp_path / "pending_writes.json"
        monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", pending_path)

        bad_db = tmp_path / "nonexistent_dir" / "bad.db"
        ok = sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=bad_db)
        assert ok is False
        assert pending_path.exists()
        items = json.loads(pending_path.read_text())
        assert len(items) == 1
        assert items[0]["table"] == "daily_market"

    def test_retry_succeeds(self, db_path, tmp_path, sample_yaml_data, monkeypatch):
        pending_path = tmp_path / "pending_writes.json"
        monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", pending_path)

        record_pending("daily_market", {"date": "2026-04-01", "total_amount": 99999.0}, "test error")
        assert pending_path.exists()

        succeeded, failed = retry_pending(db_path=db_path)
        assert succeeded == 1
        assert failed == 0

        with get_db(db_path) as conn:
            row = Q.get_daily_market(conn, "2026-04-01")
        assert row is not None


class TestReconcile:
    def test_reconcile_consistent(self, db_path, sample_yaml_data, tmp_path):
        sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=db_path)

        daily_dir = tmp_path / "daily"
        day_dir = daily_dir / "2026-04-01"
        day_dir.mkdir(parents=True)
        with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
            yaml.dump(sample_yaml_data, f, allow_unicode=True)

        diffs = reconcile_daily_market(db_path=db_path, daily_dir=daily_dir)
        assert len(diffs) == 0

    def test_reconcile_detects_mismatch(self, db_path, sample_yaml_data, tmp_path):
        sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=db_path)

        modified = {**sample_yaml_data, "total_amount": 99999.0}
        daily_dir = tmp_path / "daily"
        day_dir = daily_dir / "2026-04-01"
        day_dir.mkdir(parents=True)
        with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
            yaml.dump(modified, f, allow_unicode=True)

        diffs = reconcile_daily_market(db_path=db_path, daily_dir=daily_dir)
        assert len(diffs) >= 1
        assert any(d.get("field") == "total_amount" for d in diffs)

    def test_reconcile_detects_missing(self, db_path, sample_yaml_data, tmp_path):
        daily_dir = tmp_path / "daily"
        day_dir = daily_dir / "2026-04-01"
        day_dir.mkdir(parents=True)
        with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
            yaml.dump(sample_yaml_data, f, allow_unicode=True)

        diffs = reconcile_daily_market(db_path=db_path, daily_dir=daily_dir)
        assert len(diffs) == 1
        assert diffs[0]["issue"] == "missing_in_db"


class TestPremiumBackfill:
    def test_premium_backfill_updates_db(self, db_path, sample_yaml_data):
        sync_daily_market_to_db("2026-04-01", sample_yaml_data, db_path=db_path)
        with get_db(db_path) as conn:
            row = Q.get_daily_market(conn, "2026-04-01")
            assert row["premium_10cm"] is None

            Q.update_premium(conn, "2026-04-01", premium_10cm=2.5, premium_20cm=5.0)
            row = Q.get_daily_market(conn, "2026-04-01")
            assert row["premium_10cm"] == 2.5
            assert row["premium_20cm"] == 5.0
