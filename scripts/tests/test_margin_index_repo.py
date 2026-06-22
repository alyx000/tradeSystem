"""margin_index_correlation.repo：UPSERT 幂等 + 保留 created_at + JSON 编解码。"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.margin_index_correlation import repo


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _record(date="2026-06-19"):
    return {
        "date": date,
        "data_trade_date": date,
        "windows": [5, 20, 60],
        "indices": ["000001.SH", "399001.SZ"],
        "base_index": "000001.SH",
        "lag": {"000001.SH": {"best_lag": 2, "relation": "两融滞后"}},
        "sync_corr": {"20": {"000001.SH": {"corr": 0.66, "label": "弱同向"}}},
        "divergence": {"5": {"000001.SH": {"diverged": True, "type": "涨指两融降"}}},
        "balance": {"total": {"latest_yi": 18000.0, "up_streak": 3}},
        "sample_days": {"5": 5, "20": 20, "60": 58},
        "meta": {"source": "tushare:margin", "stale": False},
    }


def test_save_and_get_roundtrip(conn):
    repo.save(conn, _record())
    got = repo.get(conn, "2026-06-19")
    assert got is not None
    assert got["lag"]["000001.SH"]["relation"] == "两融滞后"
    assert got["divergence"]["5"]["000001.SH"]["type"] == "涨指两融降"
    assert got["balance"]["total"]["latest_yi"] == 18000.0
    assert got["meta"]["stale"] is False
    assert got["data_trade_date"] == "2026-06-19"


def test_upsert_preserves_created_at(conn):
    repo.save(conn, _record())
    first = conn.execute(
        "SELECT created_at FROM margin_index_correlation_daily WHERE date='2026-06-19'"
    ).fetchone()["created_at"]
    rec2 = _record()
    rec2["meta"]["stale"] = True
    repo.save(conn, rec2)
    row = conn.execute(
        "SELECT created_at, updated_at FROM margin_index_correlation_daily WHERE date='2026-06-19'"
    ).fetchone()
    assert row["created_at"] == first  # created_at 不变
    assert repo.get(conn, "2026-06-19")["meta"]["stale"] is True  # 内容已覆盖
    # 单日单行
    assert conn.execute("SELECT COUNT(*) FROM margin_index_correlation_daily").fetchone()[0] == 1


def test_missing_required_raises(conn):
    bad = _record()
    del bad["base_index"]
    with pytest.raises(ValueError):
        repo.save(conn, bad)


def test_get_missing_returns_none(conn):
    assert repo.get(conn, "2026-01-01") is None


def test_get_recent_ascending(conn):
    for d in ["2026-06-17", "2026-06-18", "2026-06-19"]:
        repo.save(conn, _record(d))
    recent = repo.get_recent(conn, "2026-06-19", 2)
    assert [r["date"] for r in recent] == ["2026-06-18", "2026-06-19"]
