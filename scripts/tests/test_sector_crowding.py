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


class TestSchema:
    def test_sector_crowding_daily_table_exists(self, conn):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sector_crowding_daily)")}
        assert {"date", "market_total_billion", "sectors_json", "proxy_json",
                "meta_json", "created_at", "updated_at"} <= cols

    def test_market_total_nullable(self, conn):
        conn.execute(
            "INSERT INTO sector_crowding_daily (date, sectors_json) VALUES ('2026-07-17', '[]')"
        )  # market_total_billion 缺省 NULL 不应报错
