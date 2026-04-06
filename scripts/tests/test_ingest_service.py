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
    assert "daily_info" in names
    assert "limit_step" in names
    assert "limit_cpt_list" in names
    assert "moneyflow_ind_ths" in names
    assert "moneyflow_ind_dc" in names
    assert "moneyflow_mkt_dc" in names
    assert "margin_detail" in names
    assert "anns_d" in names
    assert any(item["enabled_by_default"] is False for item in interfaces)
    daily_basic = next(item for item in interfaces if item["interface_name"] == "daily_basic")
    assert daily_basic["interface_label"] == "盘后核心基础面快照"
    assert daily_basic["stage_label"] == "盘后核心"
    assert daily_basic["enabled_by_default_label"] == "是"


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
    assert result["runs"][0]["interface_label"] == "盘后扩展接口"
    assert result["runs"][0]["status_label"] == "失败"
    assert result["runs"][0]["provider_label"] == "Tushare"
    assert result["errors"][0]["error_message"] == "rate limit"
    assert result["errors"][0]["interface_label"] == "盘后扩展接口"
    assert result["errors"][0]["error_type_label"] == "数据源失败"
    assert result["errors"][0]["retryable_label"] == "可重试"
    assert result["errors"][0]["action_hint"] == "先看原始错误，再决定是重试、切换数据源还是修正接口实现。"
    assert result["errors"][0]["restriction_label"] is None


def test_inspect_marks_permission_restricted_errors(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        (
            "run_anns",
            "anns_d",
            "tushare",
            "post_extended",
            "2026-04-04",
            json.dumps({"trade_date": "20260404"}, ensure_ascii=False),
            "failed",
            0,
            "cli",
        ),
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("run_anns", "anns_d", "2026-04-04", "post_extended", "provider", "您的权限不足", 0),
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    result = service.inspect("2026-04-04")
    error = result["errors"][0]
    assert error["restriction_label"] == "权限受限"
    assert error["restriction_reason"] == "当前账号对该接口没有调用权限，或积分不足。"
    assert error["retryable_label"] == "不可重试"


def test_inspect_and_retry_summary_support_interface_filter(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_margin', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_block', 'block_trade', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_margin', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1),
        ('run_block', 'block_trade', '2026-04-04', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    inspect_payload = service.inspect("2026-04-04", interface_name="margin")
    retry_payload = service.retry_summary(interface_name="margin")

    assert inspect_payload["interface_name"] == "margin"
    assert inspect_payload["run_count"] == 1
    assert inspect_payload["error_count"] == 1
    assert inspect_payload["runs"][0]["interface_name"] == "margin"
    assert inspect_payload["errors"][0]["interface_name"] == "margin"
    assert retry_payload["interface_name"] == "margin"
    assert retry_payload["retryable_count"] == 1
    assert retry_payload["failed_interface_count"] == 1
    assert retry_payload["status_label"] == "承压"
    assert "当前仍有未解决失败" in retry_payload["status_reason"]
    assert retry_payload["groups"][0]["interface_name"] == "margin"


def test_inspect_and_retry_summary_support_stage_filter(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_core', 'daily_basic', 'tushare', 'post_core', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_ext', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_core', 'daily_basic', '2026-04-04', 'post_core', 'network', 'timeout', 1),
        ('run_ext', 'margin', '2026-04-04', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    inspect_payload = service.inspect("2026-04-04", stage="post_extended")
    retry_payload = service.retry_summary(stage="post_extended")

    assert inspect_payload["stage"] == "post_extended"
    assert inspect_payload["run_count"] == 1
    assert inspect_payload["error_count"] == 1
    assert inspect_payload["runs"][0]["stage"] == "post_extended"
    assert inspect_payload["errors"][0]["stage"] == "post_extended"
    assert retry_payload["stage"] == "post_extended"
    assert retry_payload["retryable_count"] == 1
    assert retry_payload["failed_interface_count"] == 1
    assert retry_payload["status_label"] == "承压"
    assert retry_payload["groups"][0]["stage"] == "post_extended"


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
    assert summary["failed_interface_count"] == 1
    assert summary["status_label"] == "承压"
    assert "当前仍有未解决失败" in summary["status_reason"]
    assert summary["groups"][0]["interface_name"] == "margin"
    assert summary["groups"][0]["interface_label"] == "融资融券数据"
    assert summary["groups"][0]["stage_label"] == "盘后扩展"


def test_retry_unresolved_groups_reruns_and_resolves_errors(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES ('run_retry_1', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}',
                'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES ('run_retry_1', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1)
        """
    )
    conn.commit()
    conn.close()

    registry = _FakeRegistry(
        _FakeProvider(
            {"get_margin_data"},
            {
                "get_margin_data": DataResult(
                    data={"trade_date": "2026-04-04", "total_rzye_yi": 100.0, "total_rqye_yi": 10.0, "total_rzrqye_yi": 110.0, "exchanges": []},
                    source="tushare:margin",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.retry_unresolved_groups(input_by="cursor")

    assert result["requested_groups"] == 1
    assert result["attempted_groups"] == 1
    assert result["resolved_errors"] == 1
    assert result["runs"][0]["interface_name"] == "margin"
    assert result["runs"][0]["status"] == "success"

    conn = get_connection(db_path)
    unresolved = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM ingest_errors
        WHERE interface_name = 'margin' AND biz_date = '2026-04-04' AND retryable = 1 AND resolved_at IS NULL
        """
    ).fetchone()
    conn.close()
    assert unresolved["cnt"] == 0


def test_health_summary_returns_failure_rank_and_last_success(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_ok_1', 'margin', 'tushare', 'post_extended', '2026-04-01', '{}', 'success', 10, datetime('now'), 'cli'),
        ('run_fail_1', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_fail_2', 'margin', 'tushare', 'post_extended', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_fail_3', 'block_trade', 'tushare', 'post_extended', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_fail_1', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1),
        ('run_fail_2', 'margin', '2026-04-05', 'post_extended', 'provider', 'rate limit', 1),
        ('run_fail_3', 'block_trade', '2026-04-05', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    summary = service.health_summary(end_date="2026-04-06", days=7)

    assert summary["start_date"] == "2026-03-31"
    assert summary["end_date"] == "2026-04-06"
    assert summary["total_runs"] == 4
    assert summary["total_failures"] == 3
    assert summary["unresolved_failures"] == 3
    assert summary["failed_interface_count"] == 2
    assert summary["never_succeeded_count"] == 1
    assert summary["failure_rate"] == 0.75
    assert summary["status_label"] == "需处理"
    assert "从未成功过的接口" in summary["status_reason"]
    assert summary["top_failed_interfaces"][0]["interface_name"] == "margin"
    assert summary["top_failed_interfaces"][0]["failure_count"] == 2
    assert summary["top_failed_interfaces"][0]["last_success_biz_date"] == "2026-04-01"
    assert summary["top_failed_interfaces"][0]["consecutive_failure_days"] == 2
    assert summary["top_failed_interfaces"][0]["days_since_last_success"] == 5
    assert summary["top_failed_interfaces"][0]["interface_label"] == "融资融券数据"
    assert summary["top_failed_interfaces"][1]["consecutive_failure_days"] == 1
    assert summary["top_failed_interfaces"][1]["days_since_last_success"] is None
    assert summary["daily_failures"][-1]["biz_date"] == "2026-04-05"
    assert summary["daily_failures"][-1]["error_count"] == 2


def test_health_summary_supports_stage_filter(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_core_ok', 'daily_basic', 'tushare', 'post_core', '2026-04-01', '{}', 'success', 10, datetime('now'), 'cli'),
        ('run_core_fail', 'daily_basic', 'tushare', 'post_core', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_ext_fail', 'margin', 'tushare', 'post_extended', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_core_fail', 'daily_basic', '2026-04-05', 'post_core', 'network', 'timeout', 1),
        ('run_ext_fail', 'margin', '2026-04-05', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    summary = service.health_summary(end_date="2026-04-06", days=7, stage="post_core")

    assert summary["stage"] == "post_core"
    assert summary["total_runs"] == 2
    assert summary["total_failures"] == 1
    assert summary["unresolved_failures"] == 1
    assert summary["failed_interface_count"] == 1
    assert summary["never_succeeded_count"] == 0
    assert summary["failure_rate"] == 0.5
    assert summary["status_label"] == "承压"
    assert "当前仍有未解决失败" in summary["status_reason"]
    assert summary["top_failed_interfaces"][0]["interface_name"] == "daily_basic"
    assert summary["top_failed_interfaces"][0]["last_success_biz_date"] == "2026-04-01"
    assert summary["top_failed_interfaces"][0]["consecutive_failure_days"] == 1
    assert summary["top_failed_interfaces"][0]["days_since_last_success"] == 5
    assert summary["daily_failures"] == [{"biz_date": "2026-04-05", "error_count": 1}]


def test_health_summary_supports_interface_filter(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_margin_fail', 'margin', 'tushare', 'post_extended', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_block_ok', 'block_trade', 'tushare', 'post_extended', '2026-04-01', '{}', 'success', 10, datetime('now'), 'cli'),
        ('run_block_fail', 'block_trade', 'tushare', 'post_extended', '2026-04-05', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_margin_fail', 'margin', '2026-04-05', 'post_extended', 'network', 'timeout', 1),
        ('run_block_fail', 'block_trade', '2026-04-05', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    summary = service.health_summary(
        end_date="2026-04-06",
        days=7,
        stage="post_extended",
        interface_name="margin",
    )

    assert summary["stage"] == "post_extended"
    assert summary["interface_name"] == "margin"
    assert summary["total_runs"] == 1
    assert summary["total_failures"] == 1
    assert summary["failed_interface_count"] == 1
    assert summary["status_label"] == "需处理"
    assert summary["top_failed_interfaces"][0]["interface_name"] == "margin"


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


def test_execute_interface_marks_permission_error_non_retryable(tmp_path):
    db_path = tmp_path / "test.db"
    registry = _FakeRegistry(
        _FakeProvider(
            {"get_market_announcements"},
            {
                "get_market_announcements": DataResult(
                    data=None,
                    source="registry",
                    error="所有数据源均失败: tushare: 您的权限不足",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.execute_interface("anns_d", "2026-04-04", input_by="cursor")

    assert result["status"] == "failed"
    conn = get_connection(db_path)
    error = conn.execute(
        "SELECT error_type, retryable FROM ingest_errors WHERE run_id = ?",
        (result["run"]["run_id"],),
    ).fetchone()
    conn.close()

    assert error["error_type"] == "provider"
    assert error["retryable"] == 0


def test_reconcile_stale_runs_marks_old_running_runs_failed(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status, row_count, started_at, triggered_by, input_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run_stale",
            "ths_member",
            "get_ths_member",
            "backfill",
            "2026-04-03",
            "{}",
            "running",
            0,
            "2026-04-06T00:00:00",
            "cli",
            "cursor",
        ),
    )
    conn.commit()
    conn.close()

    service = IngestService(str(db_path))
    result = service.reconcile_stale_runs(stale_minutes=5)

    assert result["reconciled_count"] == 1
    conn = get_connection(db_path)
    run = conn.execute(
        "SELECT status, finished_at, duration_ms, notes FROM ingest_runs WHERE run_id = 'run_stale'"
    ).fetchone()
    error = conn.execute(
        "SELECT retryable, error_message FROM ingest_errors WHERE run_id = 'run_stale'"
    ).fetchone()
    conn.close()

    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert run["duration_ms"] >= 0
    assert "stale running record reconciled" in run["notes"]
    assert error["retryable"] == 0
    assert "stale running record reconciled" in error["error_message"]


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


def test_store_payload_uses_params_in_dedupe_key(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = IngestService(str(db_path))
    interface = {
        "interface_name": "share_float",
        "provider_method": "get_share_float",
        "stage": "backfill",
        "params_policy": "explicit_only",
        "dedupe_keys": ["ts_code", "ann_date", "float_date"],
        "raw_table": "raw_share_float",
        "enabled_by_default": False,
    }

    first_key, _ = service._store_payload(
        interface,
        "2026-04-03",
        provider="fake:share_float",
        params={"ts_code": "300750.SZ", "ann_date": "20260401", "float_date": "20260410"},
        result=DataResult(data=[{"ts_code": "300750.SZ", "float_date": "20260410"}], source="fake"),
    )
    second_key, _ = service._store_payload(
        interface,
        "2026-04-03",
        provider="fake:share_float",
        params={"ts_code": "300750.SZ", "ann_date": "20260402", "float_date": "20260411"},
        result=DataResult(data=[{"ts_code": "300750.SZ", "float_date": "20260411"}], source="fake"),
    )

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT dedupe_key, payload_json
        FROM raw_interface_payloads
        WHERE interface_name = 'share_float'
        ORDER BY dedupe_key
        """
    ).fetchall()
    conn.close()

    assert first_key != second_key
    assert len(rows) == 2
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert {payload["params"]["ann_date"] for payload in payloads} == {"20260401", "20260402"}


def test_limit_cpt_list_run_creates_sector_snapshot_and_entities(tmp_path):
    db_path = tmp_path / "test.db"
    registry = _FakeRegistry(
        _FakeProvider(
            {"get_limit_cpt_list"},
            {
                "get_limit_cpt_list": DataResult(
                    data=[
                        {"ts_code": "885001.TI", "name": "AI应用", "up_num": 12, "amount": 52.3},
                        {"ts_code": "885002.TI", "name": "机器人", "up_num": 9, "amount": 38.8},
                    ],
                    source="fake:limit_cpt_list",
                )
            },
        )
    )
    service = IngestService(str(db_path), registry=registry)

    result = service.execute_interface("limit_cpt_list", "2026-04-03", input_by="cursor")
    assert result["status"] == "success"
    assert result["run"]["entity_count"] == 2
    assert result["run"]["snapshot_ids"]

    conn = get_connection(db_path)
    snapshot = conn.execute(
        """
        SELECT fact_type, subject_name, facts_json
        FROM market_fact_snapshots
        WHERE biz_date = ? AND fact_type = 'strongest_sectors'
        """,
        ("2026-04-03",),
    ).fetchone()
    entities = conn.execute(
        """
        SELECT entity_type, entity_code, entity_name, role
        FROM fact_entities
        WHERE biz_date = ? AND interface_name = 'limit_cpt_list'
        ORDER BY entity_code
        """,
        ("2026-04-03",),
    ).fetchall()
    conn.close()

    assert snapshot["subject_name"] == "最强板块统计"
    facts = json.loads(snapshot["facts_json"])
    assert facts["record_count"] == 2
    assert len(facts["top_sectors"]) == 2
    assert len(entities) == 2
    assert entities[0]["entity_type"] == "sector"
    assert entities[0]["role"] == "top_ranked"
