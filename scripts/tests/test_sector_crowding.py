"""sector_crowding 全链路测试（schema/repo/analyzer/collector/service/formatter）。"""
import json
import sqlite3

import pytest

from db.schema import init_schema


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


from services.sector_crowding import repo


def _rec(date, total=15000.0, sectors=None):
    return {
        "date": date,
        "market_total_billion": total,
        "sectors": sectors if sectors is not None else [
            {"code": "801080.SI", "name": "电子", "level": "L1",
             "close": 5000.0, "amount_billion": 3000.0, "share_pct": 20.0},
        ],
        "proxy": None,
        "meta": {"source": "tushare"},
    }


class TestSchema:
    def test_sector_crowding_daily_table_exists(self, conn):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sector_crowding_daily)")}
        assert {"date", "market_total_billion", "sectors_json", "proxy_json",
                "meta_json", "created_at", "updated_at"} <= cols

    def test_market_total_nullable(self, conn):
        conn.execute(
            "INSERT INTO sector_crowding_daily (date, sectors_json) VALUES ('2026-07-17', '[]')"
        )  # market_total_billion 缺省 NULL 不应报错


class TestRepo:
    def test_save_and_get_roundtrip(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["sectors"][0]["code"] == "801080.SI"
        assert got["market_total_billion"] == 15000.0

    def test_upsert_idempotent_keeps_created_at(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        created = repo.get_snapshot(conn, "2026-07-17")["created_at"]
        repo.save_snapshot(conn, _rec("2026-07-17", total=16000.0))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["market_total_billion"] == 16000.0
        assert got["created_at"] == created

    def test_market_total_none_persists_null(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17", total=None))
        assert repo.get_snapshot(conn, "2026-07-17")["market_total_billion"] is None

    def test_get_recent_ascending(self, conn):
        for d in ("2026-07-15", "2026-07-16", "2026-07-17"):
            repo.save_snapshot(conn, _rec(d))
        rows = repo.get_recent(conn, "2026-07-17", days=2)
        assert [r["date"] for r in rows] == ["2026-07-16", "2026-07-17"]

    def test_missing_required_raises(self, conn):
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, {"date": "2026-07-17"})  # 缺 sectors
