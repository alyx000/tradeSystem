"""L2: 采集底座服务测试。"""
from __future__ import annotations

import json

from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.ingest_service import IngestService


class _FakeProvider:
    name = "fake"

    def __init__(self, supported: set[str], results: dict[str, DataResult]):
        self._supported = supported
        self._results = results

    def supports(self, method_name: str) -> bool:
        return method_name in self._supported


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider):
        self.providers = [provider]
        self._provider = provider

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        return self._provider._results[method_name]


def test_list_interfaces_returns_registry_entries(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = IngestService(str(db_path))
    interfaces = service.list_interfaces()
    names = {item["interface_name"] for item in interfaces}
    assert "daily_basic" in names
    assert "block_trade" in names
    assert any(item["enabled_by_default"] is False for item in interfaces)


def test_inspect_returns_runs_and_errors(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by, input_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
        """,
        (
            "run_1",
            "block_trade",
            "tushare",
            "post_extended",
            "2026-04-04",
            json.dumps({"trade_date": "20260404"}, ensure_ascii=False),
            "failed",
            0,
            "cli",
            "cursor",
        ),
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("run_1", "block_trade", "2026-04-04", "post_extended", "provider", "rate limit", 1),
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    result = service.inspect("2026-04-04")
    assert result["run_count"] == 1
    assert result["error_count"] == 1
    assert result["runs"][0]["interface_name"] == "block_trade"
    assert result["errors"][0]["error_message"] == "rate limit"


def test_retry_summary_only_counts_unresolved_retryable_errors(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES ('run_1', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}',
                'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES ('run_1', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable, resolved_at)
        VALUES ('run_1', 'margin', '2026-04-04', 'post_extended', 'provider', 'resolved', 1, datetime('now'))
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    summary = service.retry_summary()
    assert summary["retryable_count"] == 1
    assert summary["groups"][0]["interface_name"] == "margin"


def test_execute_stage_records_runs(tmp_path):
    db_path = tmp_path / "test.db"
    registry = _FakeRegistry(
        _FakeProvider(
            {"get_northbound"},
            {
                "get_northbound": DataResult(
                    data={"north_money": 100.0, "net_buy_billion": 1.23},
                    source="fake:get_northbound",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.execute_stage("post_core", "2026-04-04", input_by="cursor")
    assert result["status"] == "ok"
    assert result["recorded_runs"] >= 1

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT interface_name, status, input_by FROM ingest_runs WHERE biz_date = ? ORDER BY interface_name",
        ("2026-04-04",),
    ).fetchall()
    payloads = conn.execute(
        "SELECT interface_name, status, row_count FROM raw_interface_payloads ORDER BY interface_name"
    ).fetchall()
    snapshots = conn.execute(
        "SELECT fact_type, subject_type, subject_code, facts_json FROM market_fact_snapshots ORDER BY fact_type"
    ).fetchall()
    errors = conn.execute(
        "SELECT interface_name, error_message FROM ingest_errors ORDER BY interface_name"
    ).fetchall()
    conn.close()

    assert len(rows) == result["recorded_runs"]
    assert any(row["status"] == "success" for row in rows)
    assert rows[0]["input_by"] == "cursor"
    assert any(item["interface_name"] == "moneyflow_hsgt" for item in payloads)
    assert any(item["fact_type"] == "capital_flow" for item in snapshots)
    assert any(item["interface_name"] == "daily_basic" for item in errors)


def test_execute_interface_records_single_run(tmp_path):
    db_path = tmp_path / "test.db"
    service = IngestService(str(db_path))

    result = service.execute_interface("block_trade", "2026-04-04", input_by="openclaw")
    assert result["status"] == "failed"
    assert result["run"]["interface_name"] == "block_trade"

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT interface_name, stage, input_by, status FROM ingest_runs WHERE run_id = ?",
        (result["run"]["run_id"],),
    ).fetchone()
    error = conn.execute(
        "SELECT error_message FROM ingest_errors WHERE run_id = ?",
        (result["run"]["run_id"],),
    ).fetchone()
    conn.close()

    assert row["interface_name"] == "block_trade"
    assert row["stage"] == "post_extended"
    assert row["input_by"] == "openclaw"
    assert row["status"] == "failed"
    assert "provider" in error["error_message"] or "未实现" in error["error_message"]


def test_successful_margin_run_creates_snapshot(tmp_path):
    db_path = tmp_path / "test.db"
    registry = _FakeRegistry(
        _FakeProvider(
            {"get_margin_data"},
            {
                "get_margin_data": DataResult(
                    data={
                        "trade_date": "2026-04-03",
                        "exchanges": [{"exchange_id": "SSE", "rzrqye_yi": 13149.44}],
                        "total_rzye_yi": 13028.37,
                        "total_rqye_yi": 121.06,
                        "total_rzrqye_yi": 13149.44,
                    },
                    source="fake:margin",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.execute_interface("margin", "2026-04-03", input_by="cursor")
    assert result["status"] == "success"
    assert result["run"]["snapshot_ids"]

    conn = get_connection(db_path)
    snapshot = conn.execute(
        "SELECT fact_type, subject_type, subject_code, facts_json FROM market_fact_snapshots WHERE biz_date = ?",
        ("2026-04-03",),
    ).fetchone()
    conn.close()

    assert snapshot["fact_type"] == "margin_stats"
    facts = json.loads(snapshot["facts_json"])
    assert facts["total_rzrqye_yi"] == 13149.44


def test_top_inst_run_creates_snapshot_and_fact_entities(tmp_path):
    db_path = tmp_path / "test.db"
    registry = _FakeRegistry(
        _FakeProvider(
            {"get_dragon_tiger"},
            {
                "get_dragon_tiger": DataResult(
                    data=[
                        {
                            "code": "300750.SZ",
                            "name": "宁德时代",
                            "reason": "日涨幅偏离值达7%",
                            "buy_amount": 2.5e8,
                            "sell_amount": 1.1e8,
                            "net_amount": 1.4e8,
                        },
                        {
                            "code": "002594.SZ",
                            "name": "比亚迪",
                            "reason": "连续三个交易日涨幅偏离值累计达20%",
                            "buy_amount": 1.8e8,
                            "sell_amount": 1.6e8,
                            "net_amount": 0.2e8,
                        },
                    ],
                    source="fake:dragon_tiger",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.execute_interface("top_inst", "2026-04-03", input_by="cursor")
    assert result["status"] == "success"
    assert result["run"]["entity_count"] == 2
    assert result["run"]["snapshot_ids"]

    conn = get_connection(db_path)
    snapshot = conn.execute(
        """
        SELECT fact_type, subject_name, facts_json
        FROM market_fact_snapshots
        WHERE biz_date = ? AND fact_type = 'watchlist_context'
        """,
        ("2026-04-03",),
    ).fetchone()
    entities = conn.execute(
        """
        SELECT entity_type, entity_code, entity_name, role, attributes_json
        FROM fact_entities
        WHERE biz_date = ? AND interface_name = 'top_inst'
        ORDER BY entity_code
        """,
        ("2026-04-03",),
    ).fetchall()
    conn.close()

    assert snapshot["subject_name"] == "龙虎榜机构席位"
    facts = json.loads(snapshot["facts_json"])
    assert facts["record_count"] == 2
    assert facts["positive_net_count"] == 2
    assert len(entities) == 2
    assert entities[0]["entity_type"] == "stock"
    assert entities[0]["role"] == "top_ranked"
    attrs = json.loads(entities[0]["attributes_json"])
    assert "net_amount" in attrs
