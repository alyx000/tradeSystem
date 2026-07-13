"""sector_correlation_daily schema + migrate v29 单测（阶段1 数据层）。

repo 往返测试在阶段3 collector/repo 落地后追加到本文件。
"""
from __future__ import annotations

import sqlite3

import pytest

from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate
from db.schema import init_schema
from services.sector_correlation import repo

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

    migrate(conn, activate_v40=True)  # v29 块应重建表

    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "sector_correlation_daily" in tables
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION  # == 29


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _sample_record() -> dict:
    return {
        "date": "2026-05-29",
        "windows": [20, 60],
        "top_n": 2,
        "activity_days": 10,
        "sample_days": {"20": 20, "60": 58},
        "base_index": "000001.SH",
        "indices": ["000001.SH", "000688.SH"],
        "sectors": [{"name": "半导体", "type": "industry", "avg_amount_billion": 345.2}],
        "sector_index": {"20": {"半导体": {"000001.SH": {"raw_corr": 0.79, "beta": 1.38, "label": "强同向"}}}},
        "pair_raw": {"20": [{"a": "半导体", "b": "AI", "corr": 0.88, "label": "强同向"}]},
        "pair_excess": {"20": [{"a": "AI", "b": "黄金", "corr": -0.45, "label": "强逆向"}]},
        "meta": {"min_sample": {"20": 15, "60": 20}, "source": "tushare"},
    }


def test_save_and_get_roundtrip():
    conn = _conn()
    repo.save_correlation(conn, _sample_record())
    got = repo.get_correlation(conn, "2026-05-29")

    assert got is not None
    assert got["windows"] == [20, 60]
    assert got["sample_days"] == {"20": 20, "60": 58}
    assert got["indices"] == ["000001.SH", "000688.SH"]
    assert got["sector_index"]["20"]["半导体"]["000001.SH"]["label"] == "强同向"
    assert got["pair_excess"]["20"][0]["corr"] == -0.45
    assert got["meta"]["source"] == "tushare"


def test_get_missing_returns_none():
    assert repo.get_correlation(_conn(), "2099-01-01") is None


def test_save_missing_required_raises():
    conn = _conn()
    bad = _sample_record()
    del bad["base_index"]
    with pytest.raises(ValueError):
        repo.save_correlation(conn, bad)


def test_resave_preserves_created_at():
    conn = _conn()
    conn.execute(
        """INSERT INTO sector_correlation_daily
           (date, windows_json, top_n, base_index, indices_json,
            sectors_json, sector_index_json, pair_raw_json, pair_excess_json, created_at)
           VALUES ('2026-05-29','[20]',1,'000001.SH','[]','[]','{}','{}','{}','2020-01-01 00:00:00')"""
    )
    conn.commit()
    repo.save_correlation(conn, _sample_record())  # 覆盖同 date
    row = conn.execute(
        "SELECT created_at, top_n FROM sector_correlation_daily WHERE date='2026-05-29'"
    ).fetchone()
    assert row["created_at"] == "2020-01-01 00:00:00"  # 保留首次
    assert row["top_n"] == 2                            # 字段已更新


def test_get_recent_chronological():
    conn = _conn()
    for d in ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]:
        rec = _sample_record()
        rec["date"] = d
        repo.save_correlation(conn, rec)
    rows = repo.get_recent_correlation(conn, "2026-05-29", days=2)
    assert [r["date"] for r in rows] == ["2026-05-28", "2026-05-29"]  # 最近2天正序
