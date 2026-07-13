"""daily_volume_concentration repo 单测:save/get 往返 + JSON 列序列化。"""
from __future__ import annotations

import sqlite3

import pytest

from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate
from db.schema import init_schema
from services.volume_concentration import repo


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _sample_record() -> dict:
    return {
        "date": "2026-05-29",
        "top_n": 20,
        "total_amount_billion": 350.5,
        "market_total_billion": 9800.0,
        "stocks": [
            {"rank": 1, "code": "300750.SZ", "name": "宁德时代", "industry": "电池",
             "amount_billion": 58.3, "change_pct": 3.2, "close": 231.5},
        ],
        "sector_summary": [
            {"industry": "电池", "count": 1, "amount_billion": 58.3,
             "share_in_top_n": 0.166, "codes": ["300750.SZ"]},
        ],
        "source": {"industry_source": "tushare:index_member_all", "industry_coverage": 1.0},
    }


def test_save_and_get_roundtrip():
    """写入后读回,标量字段 + JSON 列(stocks/sector_summary/source)完整还原。"""
    conn = _conn()

    repo.save_concentration(conn, _sample_record())
    got = repo.get_concentration(conn, "2026-05-29")

    assert got is not None
    assert got["date"] == "2026-05-29"
    assert got["top_n"] == 20
    assert got["total_amount_billion"] == 350.5
    assert got["market_total_billion"] == 9800.0
    # JSON 列反序列化为 python 对象
    assert got["stocks"][0]["code"] == "300750.SZ"
    assert got["sector_summary"][0]["share_in_top_n"] == 0.166
    assert got["source"]["industry_coverage"] == 1.0
    # gain_universe 未提供 → 读回空列表（旧记录优雅降级）
    assert got["gain_universe"] == []


def test_save_and_get_gain_universe_roundtrip():
    """gain_universe（区间涨幅原始集）写入后读回完整还原。"""
    conn = _conn()
    record = _sample_record()
    record["gain_universe"] = [
        {"code": "300750.SZ", "name": "宁德时代", "industry": "电池",
         "gain_5d": 8.1, "gain_10d": 12.3, "gain_20d": None},
    ]

    repo.save_concentration(conn, record)
    got = repo.get_concentration(conn, "2026-05-29")

    assert got["gain_universe"][0]["code"] == "300750.SZ"
    assert got["gain_universe"][0]["gain_10d"] == 12.3
    assert got["gain_universe"][0]["gain_20d"] is None


def test_get_missing_date_returns_none():
    conn = _conn()
    assert repo.get_concentration(conn, "2099-01-01") is None


def test_resave_preserves_created_at_and_updates_fields():
    """重跑覆盖:created_at 保留首次值(dec-3:首次/末次审计),非PK字段更新。"""
    conn = _conn()
    conn.execute(
        """INSERT INTO daily_volume_concentration
           (date, top_n, total_amount_billion, stocks_json, sector_summary_json, created_at)
           VALUES ('2026-05-29', 20, 100.0, '[]', '[]', '2020-01-01 00:00:00')"""
    )
    conn.commit()

    repo.save_concentration(conn, _sample_record())  # 同 date 覆盖,total 350.5

    row = conn.execute(
        "SELECT created_at, total_amount_billion FROM daily_volume_concentration WHERE date='2026-05-29'"
    ).fetchone()
    assert row["created_at"] == "2020-01-01 00:00:00"  # created_at 保留
    assert row["total_amount_billion"] == 350.5        # 字段已更新


def test_save_missing_required_field_raises_clear_error():
    """缺必填字段(total_amount_billion)→ 明确 ValueError,不裸 KeyError。"""
    conn = _conn()
    bad = _sample_record()
    del bad["total_amount_billion"]
    with pytest.raises(ValueError):
        repo.save_concentration(conn, bad)


def test_none_and_empty_fields_roundtrip():
    """market_total_billion=None / source=None / 空 list 往返正确。"""
    conn = _conn()
    rec = {
        "date": "2026-05-28",
        "top_n": 20,
        "total_amount_billion": 0.0,
        "market_total_billion": None,
        "stocks": [],
        "sector_summary": [],
        "source": None,
    }
    repo.save_concentration(conn, rec)
    got = repo.get_concentration(conn, "2026-05-28")

    assert got["market_total_billion"] is None
    assert got["stocks"] == []
    assert got["sector_summary"] == []
    assert got["source"] is None
    assert got["total_amount_billion"] == 0.0


def test_get_recent_concentration_range_chronological():
    """取 <= end_date 的最近 N 天,按日期正序(供 trend dense series)。"""
    conn = _conn()
    for d, total in [("2026-05-26", 1.0), ("2026-05-27", 2.0), ("2026-05-28", 3.0), ("2026-05-29", 4.0)]:
        repo.save_concentration(conn, {
            "date": d, "top_n": 20, "total_amount_billion": total,
            "stocks": [], "sector_summary": [], "source": None,
        })

    rows = repo.get_recent_concentration(conn, "2026-05-29", days=2)
    assert [r["date"] for r in rows] == ["2026-05-28", "2026-05-29"]  # 最近2天,正序

    # 上界过滤:end_date 之后的不取
    rows2 = repo.get_recent_concentration(conn, "2026-05-27", days=10)
    assert [r["date"] for r in rows2] == ["2026-05-26", "2026-05-27"]


def test_get_recent_concentration_empty():
    conn = _conn()
    assert repo.get_recent_concentration(conn, "2026-05-29", days=30) == []


def test_get_latest_concentration_returns_latest_n_chronological():
    """无日期参数:取库内最新 N 天,按日期正序(供趋势图,免墙钟/非交易日依赖)。"""
    conn = _conn()
    for d, total in [("2026-05-26", 1.0), ("2026-05-27", 2.0), ("2026-05-28", 3.0), ("2026-05-29", 4.0)]:
        repo.save_concentration(conn, {
            "date": d, "top_n": 20, "total_amount_billion": total,
            "stocks": [], "sector_summary": [], "source": None,
        })

    rows = repo.get_latest_concentration(conn, days=2)
    assert [r["date"] for r in rows] == ["2026-05-28", "2026-05-29"]  # 最新2天,正序


def test_get_latest_concentration_empty():
    conn = _conn()
    assert repo.get_latest_concentration(conn, days=30) == []


def test_migrate_v28_creates_table_on_pre_v28_db():
    """模拟 pre-v28 老库(有表删掉 + 版本回退 27)→ migrate 应重建表并升到 v28。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)  # 先升到最新
    conn.execute("DROP TABLE daily_volume_concentration")
    conn.execute("PRAGMA user_version = 27")
    conn.commit()

    migrate(conn, activate_v40=True)  # v28 块应重建表

    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "daily_volume_concentration" in tables
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION  # == 28
