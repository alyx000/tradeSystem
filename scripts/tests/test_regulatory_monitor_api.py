"""监管异动 overview API 契约测试。"""
from __future__ import annotations

import json

from db.connection import get_connection
from providers.base import DataResult
from services.regulatory_overview import RegulatoryOverviewService


def _insert_overview_snapshot(db_path: str, *, status: str = "partial") -> None:
    facts = {
        "snapshot_date": "2026-07-23",
        "effective_trading_date": "2026-07-24",
        "status": status,
        "calculation_policy_version": "regulatory-v2",
        "source_status": {
            "stk_alert": {"status": "late", "row_count": 6},
            "stk_shock": {"status": "success", "row_count": 8},
            "stk_high_shock": {"status": "failed", "row_count": 0},
        },
        "monitoring": {
            "current": [
                {
                    "ts_code": "600664.SH",
                    "name": "哈药股份",
                    "monitor_start": "2026-07-24",
                    "monitor_end": "2026-08-06",
                    "evidence_type": "fact",
                }
            ],
            "history": [],
        },
        "hot_deviations": [
            {
                "ts_code": "001258.SZ",
                "name": "立新能源",
                "benchmark_code": "399107.SZ",
                "window_days": 10,
                "deviation_pct": 94.37,
                "evidence_type": "calculation",
            }
        ],
        "trigger_candidates": {
            "today": [],
            "next_day": [
                {
                    "ts_code": "300534.SZ",
                    "target_price": 14.26,
                    "required_pct": 10.91,
                    "evidence_type": "calculation",
                }
            ],
        },
        "recent_high_shocks": [],
    }
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO market_fact_snapshots
            (snapshot_id, biz_date, fact_type, subject_type, subject_code, subject_name,
             facts_json, source_interfaces_json, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-23:regulatory_anomaly_overview:market:CN",
                "2026-07-23",
                "regulatory_anomaly_overview",
                "market",
                "CN",
                "A股监管异动总览",
                json.dumps(facts, ensure_ascii=False),
                json.dumps(["stk_alert", "stk_shock", "stk_high_shock"]),
                "medium",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_overview_returns_snapshot_with_partial_source_state(api_client):
    client, db_path = api_client
    _insert_overview_snapshot(db_path)

    response = client.get(
        "/api/regulatory-monitor/overview",
        params={"date": "2026-07-23"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_date"] == "2026-07-23"
    assert payload["effective_trading_date"] == "2026-07-24"
    assert payload["status"] == "partial"
    assert payload["calculation_policy_version"] == "regulatory-v2"
    assert payload["source_status"]["stk_high_shock"]["status"] == "failed"
    assert payload["monitoring"]["current"][0]["evidence_type"] == "fact"
    assert payload["hot_deviations"][0]["evidence_type"] == "calculation"
    assert payload["trigger_candidates"]["next_day"][0]["target_price"] == 14.26


def test_overview_missing_snapshot_returns_404(api_client):
    client, _ = api_client

    response = client.get(
        "/api/regulatory-monitor/overview",
        params={"date": "2026-07-23"},
    )

    assert response.status_code == 404


def test_overview_invalid_date_returns_422(api_client):
    client, _ = api_client

    for invalid_date in ("20260723", "2026-13-99"):
        response = client.get(
            "/api/regulatory-monitor/overview",
            params={"date": invalid_date},
        )
        assert response.status_code == 422


def test_legacy_regulatory_monitor_api_remains_compatible(api_client):
    client, db_path = api_client
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO stock_regulatory_monitor
            (ts_code, name, regulatory_type, risk_level, reason, publish_date,
             source, risk_score, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "600000.SH",
                "浦发银行",
                1,
                1,
                "停牌核查",
                "2026-07-23",
                "test",
                1.0,
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.get(
        "/api/regulatory-monitor",
        params={"date": "2026-07-23", "type": "1"},
    )

    assert response.status_code == 200
    assert response.json()[0]["ts_code"] == "600000.SH"
    assert response.json()[0]["regulatory_type"] == 1


def test_service_snapshot_round_trips_through_overview_api(api_client):
    client, db_path = api_client
    conn = get_connection(db_path)
    try:
        for trade_date in ("2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"):
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_calendar (date, is_open)
                VALUES (?, 1)
                """,
                (trade_date,),
            )
        for interface_name in ("stk_alert", "stk_shock", "stk_high_shock"):
            payload = {
                "interface_name": interface_name,
                "provider": f"test:{interface_name}",
                "biz_date": "2026-07-23",
                "params": {},
                "rows": [],
                "summary": {"row_count": 0},
            }
            source_meta = {
                "query": {
                    "request": {
                        "start_date": "20260424",
                        "end_date": "20260724",
                    },
                    "snapshot_date": "2026-07-23",
                    "effective_trading_date": "2026-07-24",
                }
            }
            conn.execute(
                """
                INSERT INTO raw_interface_payloads
                (interface_name, provider, stage, biz_date, target_date, raw_table,
                 params_json, payload_json, payload_hash, dedupe_key, status,
                 row_count, source_meta_json)
                VALUES (?, ?, 'post_extended', ?, ?, ?, '{}', ?, ?, ?, 'empty', 0, ?)
                """,
                (
                    interface_name,
                    f"test:{interface_name}",
                    "2026-07-23",
                    "2026-07-23",
                    f"raw_{interface_name}",
                    json.dumps(payload),
                    f"hash-{interface_name}",
                    f"dedupe-{interface_name}",
                    json.dumps(source_meta),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    class _UnusedRegistry:
        def call(self, method, *args, **kwargs):
            if method == "get_trade_calendar":
                return DataResult(
                    data=[
                        {
                            "cal_date": trade_date.replace("-", ""),
                            "is_open": 1,
                        }
                        for trade_date in (
                            "2026-07-21",
                            "2026-07-22",
                            "2026-07-23",
                            "2026-07-24",
                        )
                    ],
                    source="test:calendar",
                )
            raise AssertionError(f"unexpected provider call: {method}")

    produced = RegulatoryOverviewService(
        registry=_UnusedRegistry(),
        db_path=db_path,
    ).build("2026-07-23", persist=True)
    response = client.get(
        "/api/regulatory-monitor/overview",
        params={"date": "2026-07-23"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == produced
    assert set(payload["source_status"]) == {
        "stk_alert",
        "stk_shock",
        "stk_high_shock",
        "limit_step",
    }
    assert payload["source_status"]["stk_alert"]["status"] == "empty"
    assert payload["source_status"]["stk_alert"]["provider"] == "test:stk_alert"
