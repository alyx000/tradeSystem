"""GET /api/market/sector-labels/{date} 只读路由测试。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from db.connection import get_connection
from services.sector_crowding import repo


def _seed_history(
    db_path: str,
    n_days: int = 233,
    *,
    include_second_l2: bool = False,
    omit_second_on_target: bool = False,
) -> str:
    conn = get_connection(db_path)
    start = date(2025, 1, 1)
    target = ""
    try:
        for i in range(n_days):
            target = (start + timedelta(days=i)).isoformat()
            is_last = i == n_days - 1
            sectors = [{
                "code": "801081.SI",
                "name": "半导体",
                "level": "L2",
                "close": 101.0 if is_last else 100.0,
                "amount_billion": 11.0 if is_last else 10.0,
                "share_pct": 1.0,
            }]
            if include_second_l2 and not (is_last and omit_second_on_target):
                sectors.append({
                    "code": "801102.SI",
                    "name": "通信设备",
                    "level": "L2",
                    "close": 88.0,
                    "amount_billion": 8.0,
                    "share_pct": 0.8,
                })
            repo.save_snapshot(conn, {
                "date": target,
                "market_total_billion": 15000.0,
                "sectors": sectors,
                "proxy": None,
                "meta": {"source": "test"},
            })
    finally:
        conn.close()
    return target


def test_sector_labels_empty_shell_when_snapshot_missing(api_client):
    client, _db_path = api_client

    response = client.get("/api/market/sector-labels/2099-01-01")

    assert response.status_code == 200
    assert response.json() == {
        "date": "2099-01-01",
        "available": False,
        "status": "missing_snapshot",
        "definitions": {
            "half_year_ma_window": 144,
            "year_ma_window": 233,
            "resonance_lookback_days": 10,
            "resonance_breakout_window": 20,
            "resonance_rule": "close_and_amount_strictly_above_prior_window_highs",
            "window_unit": "trading_snapshot_days",
        },
        "summary": {
            "total_l2": 0,
            "missing_l2_count": 0,
            "above_half_year_ma": 0,
            "above_year_ma": 0,
            "recent_resonance": 0,
            "year_and_resonance": 0,
            "half_year_ma_insufficient": 0,
            "year_ma_insufficient": 0,
            "resonance_insufficient": 0,
        },
        "items": [],
    }


def test_sector_labels_get_does_not_migrate_or_write(api_client, monkeypatch):
    client, db_path = api_client

    def _forbidden_migrate(_conn):
        raise AssertionError("只读 sector-labels GET 不得执行 migrate")

    monkeypatch.setattr("api.deps.migrate", _forbidden_migrate)
    monitor = sqlite3.connect(db_path)
    try:
        before_schema_version = monitor.execute("PRAGMA schema_version").fetchone()[0]
        before_data_version = monitor.execute("PRAGMA data_version").fetchone()[0]
        before_tables = monitor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

        response = client.get("/api/market/sector-labels/2099-01-01")

        assert response.status_code == 200
        assert monitor.execute("PRAGMA schema_version").fetchone()[0] == before_schema_version
        assert monitor.execute("PRAGMA data_version").fetchone()[0] == before_data_version
        assert monitor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == before_tables
    finally:
        monitor.close()


def test_sector_labels_payload_returns_expected_evidence(api_client):
    client, db_path = api_client
    target = _seed_history(db_path)

    response = client.get(f"/api/market/sector-labels/{target}")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["status"] == "success"
    assert body["summary"] == {
        "total_l2": 1,
        "missing_l2_count": 0,
        "above_half_year_ma": 1,
        "above_year_ma": 1,
        "recent_resonance": 1,
        "year_and_resonance": 1,
        "half_year_ma_insufficient": 0,
        "year_ma_insufficient": 0,
        "resonance_insufficient": 0,
    }
    item = body["items"][0]
    assert item["code"] == "801081.SI"
    assert item["present_on_target"] is True
    assert item["above_half_year_ma"] is True
    assert item["above_year_ma"] is True
    assert item["recent_price_volume_resonance"] is True
    assert item["last_resonance"]["date"] == target


def test_sector_labels_partial_snapshot_keeps_missing_l2_as_unknown(api_client):
    client, db_path = api_client
    target = _seed_history(
        db_path,
        include_second_l2=True,
        omit_second_on_target=True,
    )

    response = client.get(f"/api/market/sector-labels/{target}")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["status"] == "partial"
    assert body["summary"]["total_l2"] == 2
    assert body["summary"]["missing_l2_count"] == 1
    missing = next(item for item in body["items"] if item["code"] == "801102.SI")
    assert missing["present_on_target"] is False
    assert missing["close"] is None
    assert missing["amount_billion"] is None
    assert missing["above_half_year_ma"] is None
    assert missing["above_year_ma"] is None
    assert missing["recent_price_volume_resonance"] is None


def test_sector_labels_api_uses_target_meta_for_legitimate_code_switch(api_client):
    client, db_path = api_client
    target = _seed_history(db_path, include_second_l2=True)
    conn = get_connection(db_path)
    try:
        repo.save_snapshot(conn, {
            "date": target,
            "market_total_billion": 15000.0,
            "sectors": [{
                "code": "801081.SI",
                "name": "半导体",
                "level": "L2",
                "close": 101.0,
                "amount_billion": 11.0,
                "share_pct": 1.0,
            }, {
                "code": "801053.SI",
                "name": "贵金属",
                "level": "L2",
                "close": 102.0,
                "amount_billion": 12.0,
                "share_pct": 1.2,
            }],
            "proxy": None,
            "meta": {
                "l2_expected_codes": ["801081.SI", "801053.SI"],
                "l2_expected_count": 2,
                "l2_observed_count": 2,
                "l2_universe_complete": True,
            },
        })
    finally:
        conn.close()

    body = client.get(f"/api/market/sector-labels/{target}").json()

    assert body["status"] == "success"
    assert body["summary"]["missing_l2_count"] == 0
    assert {item["code"] for item in body["items"]} == {
        "801081.SI",
        "801053.SI",
    }


def test_sector_labels_api_uses_recent_trusted_meta_for_dirty_target(api_client):
    client, db_path = api_client
    conn = get_connection(db_path)
    keep = {
        "code": "801081.SI",
        "name": "半导体",
        "level": "L2",
        "close": 100.0,
        "amount_billion": 10.0,
        "share_pct": 1.0,
    }
    old = {
        "code": "801999.SI",
        "name": "旧分类",
        "level": "L2",
        "close": 80.0,
        "amount_billion": 6.0,
        "share_pct": 0.6,
    }
    new = {
        "code": "801102.SI",
        "name": "通信设备",
        "level": "L2",
        "close": 88.0,
        "amount_billion": 8.0,
        "share_pct": 0.8,
    }
    try:
        repo.save_snapshot(conn, {
            "date": "2026-07-15",
            "market_total_billion": 15000.0,
            "sectors": [keep, old],
            "proxy": None,
            "meta": {"source": "legacy"},
        })
        repo.save_snapshot(conn, {
            "date": "2026-07-16",
            "market_total_billion": 15000.0,
            "sectors": [keep, new],
            "proxy": None,
            "meta": {
                "l2_expected_codes": ["801081.SI", "801102.SI"],
                "l2_expected_count": 2,
                "l2_observed_count": 2,
                "l2_universe_complete": True,
            },
        })
        repo.save_snapshot(conn, {
            "date": "2026-07-17",
            "market_total_billion": 15000.0,
            "sectors": [keep],
            "proxy": None,
            "meta": {"source": "dirty"},
        })
    finally:
        conn.close()

    body = client.get("/api/market/sector-labels/2026-07-17").json()

    assert body["status"] == "partial"
    assert body["summary"]["missing_l2_count"] == 1
    assert {item["code"] for item in body["items"]} == {
        "801081.SI",
        "801102.SI",
    }


def test_sector_labels_api_full_l2_missing_keeps_previous_universe(api_client):
    client, db_path = api_client
    target = _seed_history(db_path, include_second_l2=True)
    conn = get_connection(db_path)
    try:
        repo.save_snapshot(conn, {
            "date": target,
            "market_total_billion": 15000.0,
            "sectors": [{
                "code": "801080.SI",
                "name": "电子",
                "level": "L1",
                "close": 5000.0,
                "amount_billion": 3000.0,
                "share_pct": 20.0,
            }],
            "proxy": None,
            "meta": None,
        })
    finally:
        conn.close()

    body = client.get(f"/api/market/sector-labels/{target}").json()

    assert body["available"] is False
    assert body["status"] == "missing_l2"
    assert body["summary"]["total_l2"] == 2
    assert body["summary"]["missing_l2_count"] == 2
    assert len(body["items"]) == 2
    assert all(item["present_on_target"] is False for item in body["items"])
    assert all(item["above_half_year_ma"] is None for item in body["items"])
