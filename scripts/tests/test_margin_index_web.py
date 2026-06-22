"""margin_index_correlation web_payload + 复盘只读 API 端点。"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.margin_index_correlation import repo, web_payload


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _record(date="2026-06-18"):
    return {
        "date": date, "data_trade_date": date, "windows": [5, 20, 60],
        "indices": [{"pair_key": "total:000001.SH", "margin_key": "total",
                     "index_code": "000001.SH", "index_name": "上证指数", "group": "broad"}],
        "base_index": "000001.SH",
        "lag": {"total:000001.SH": {"best_lag": 0, "best_corr": 0.48, "relation": "同步", "by_lag": {}}},
        "sync_corr": {"total:000001.SH": {"5": {"corr": -0.12, "label": "独立"},
                                          "20": {"corr": 0.57, "label": "弱同向"}}},
        "divergence": {"total:000001.SH": {"5": {"index_cum": 1.2, "margin_cum": 1.0,
                                                 "diverged": False, "type": "无背离", "magnitude": 0.2}}},
        "balance": {"total": {"latest_yi": 29655.12, "dod_pct": 0.43, "pctile_20d": 1.0,
                              "up_streak": 4, "down_streak": 0, "ma20": 29100.0, "vs_ma20": 1.84}},
        "sample_days": {"5": 5, "20": 20, "60": 58},
        "meta": {"source": "tushare:margin", "market_scope": "BSE+SSE+SZSE",
                 "analysis_trade_date": date, "stale": False},
    }


def test_payload_available_false_when_missing(conn):
    out = web_payload.build_daily_payload(conn, "2026-01-01")
    assert out == {"date": "2026-01-01", "available": False}


def test_payload_available_true_with_record(conn):
    repo.save(conn, _record())
    out = web_payload.build_daily_payload(conn, "2026-06-18")
    assert out["available"] is True
    assert out["date"] == "2026-06-18"
    assert out["balance"]["total"]["latest_yi"] == 29655.12
    assert out["divergence"]["total:000001.SH"]["5"]["type"] == "无背离"
    assert out["meta"]["stale"] is False


def test_api_endpoint_returns_payload():
    """只读端点 /api/market/margin-index-correlation/{date}：有/无数据均 200。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from db.connection import get_connection
    from db.migrate import migrate

    client = TestClient(app)
    # 无数据日 → available=False, 200
    r = client.get("/api/market/margin-index-correlation/2099-01-01")
    assert r.status_code == 200
    assert r.json()["available"] is False
