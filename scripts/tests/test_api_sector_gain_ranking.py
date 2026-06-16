"""GET /api/market/sector-gain-ranking/{date} 只读路由测试。

读 daily_volume_concentration.gain_universe → 5/10/20 日三档板块区间涨幅排名。
板块按板块内涨幅最大个股降序;无记录/旧记录返三档空列表。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fastapi.testclient import TestClient

from db.connection import get_connection
from db.migrate import migrate
from services.volume_concentration import repo


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_sgr.db"
    conn = get_connection(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    from api.main import app
    return TestClient(app)


def _seed(db_path, date, universe):
    conn = get_connection(db_path)
    repo.save_concentration(conn, {
        "date": date, "top_n": 20, "total_amount_billion": 100.0,
        "stocks": [], "sector_summary": [], "source": {"industry_coverage": 1.0},
        "gain_universe": universe,
    })
    conn.close()


def test_empty_shell_when_no_record(client):
    resp = client.get("/api/market/sector-gain-ranking/2099-01-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"date": "2099-01-01", "rankings": {"5d": [], "10d": [], "20d": []}}


def test_ranking_payload_shape_and_order(client, db_path):
    _seed(db_path, "2026-05-29", [
        {"code": "A.SZ", "name": "甲", "industry": "电池",
         "gain_5d": 12.0, "gain_10d": 3.0, "gain_20d": 1.0},
        {"code": "B.SZ", "name": "乙", "industry": "白酒Ⅱ",
         "gain_5d": 8.0, "gain_10d": 9.0, "gain_20d": 20.0},
        {"code": "C.SZ", "name": "丙", "industry": "未分类",
         "gain_5d": 99.0, "gain_10d": 99.0, "gain_20d": 99.0},
    ])

    resp = client.get("/api/market/sector-gain-ranking/2026-05-29")
    assert resp.status_code == 200
    body = resp.json()

    assert body["date"] == "2026-05-29"
    assert set(body["rankings"].keys()) == {"5d", "10d", "20d"}
    # 5日:电池(12) > 白酒(8)
    assert [s["industry"] for s in body["rankings"]["5d"]] == ["电池", "白酒Ⅱ"]
    # 20日:白酒(20) > 电池(1)
    assert [s["industry"] for s in body["rankings"]["20d"]] == ["白酒Ⅱ", "电池"]
    # 未分类不进排名
    assert all(s["industry"] != "未分类" for s in body["rankings"]["5d"])
    # 板块项结构
    top = body["rankings"]["5d"][0]
    assert top["max_gain"] == 12.0
    assert top["stocks"][0]["name"] == "甲" and top["stocks"][0]["gain"] == 12.0


def test_old_record_without_gain_universe_returns_empty(client, db_path):
    # 旧记录(无 gain_universe)→ 读回 [] → 空壳
    conn = get_connection(db_path)
    repo.save_concentration(conn, {
        "date": "2026-05-28", "top_n": 20, "total_amount_billion": 100.0,
        "stocks": [], "sector_summary": [], "source": None,
    })
    conn.close()

    resp = client.get("/api/market/sector-gain-ranking/2026-05-28")
    assert resp.json()["rankings"] == {"5d": [], "10d": [], "20d": []}
