"""sector_correlation_daily schema + migrate v29 单测（阶段1 数据层）。

repo 往返测试在阶段3 collector/repo 落地后追加到本文件。
"""
from __future__ import annotations

import sqlite3

from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate
from db.schema import init_schema

EXPECTED_COLS = {
    "date", "windows_json", "top_n", "activity_days", "sample_days_json",
    "base_index", "indices_json", "sectors_json",
    "sector_index_json", "pair_raw_json", "pair_excess_json",
    "meta_json", "created_at", "updated_at",
}


def test_init_schema_creates_sector_correlation_daily():
    """init_schema 后表存在且关键列齐。"""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sector_correlation_daily)").fetchall()}
    assert cols, "sector_correlation_daily 表未创建"
    assert EXPECTED_COLS <= cols, f"缺列: {EXPECTED_COLS - cols}"


def test_migrate_v29_creates_table_on_pre_v29_db():
    """模拟 pre-v29 老库(删表 + 版本回退 28)→ migrate 应重建表并升到 v29。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)  # 先升到最新
    conn.execute("DROP TABLE sector_correlation_daily")
    conn.execute("PRAGMA user_version = 28")
    conn.commit()

    migrate(conn)  # v29 块应重建表

    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "sector_correlation_daily" in tables
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION  # == 29
