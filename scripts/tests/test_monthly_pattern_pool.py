"""月线模式股票池的事实表、运行审计与 episode 状态机测试。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate, set_schema_version
from db.schema import init_schema
from services.monthly_pattern import pool


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_schema(connection)
    return connection


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _record(
    conn: sqlite3.Connection,
    date: str,
    *,
    status: str = "technical_candidate",
    strategy_type: str = "fundamental_monthly_trend",
    **overrides,
) -> str:
    values = {
        "stock_code": "600000.SH",
        "stock_name": "浦发银行",
        "strategy_type": strategy_type,
        "status": status,
        "signal_month": "2026-06",
        "date": date,
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "technical_evidence": {"ma_alignment": True},
        "financial_evidence": {"roe_waa": 18.0},
        "source_meta": {"bar_source": "tushare"},
    }
    values.update(overrides)
    return pool.record(conn, **values)


def test_schema_creates_all_monthly_pattern_tables(conn: sqlite3.Connection) -> None:
    assert {
        "month_end",
        "stock_code",
        "stock_name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "source",
        "fetched_at",
    } <= _table_columns(conn, "monthly_pattern_bars")
    assert {
        "stock_code",
        "report_period",
        "financial_ann_date",
        "version_visible_date",
        "version_observed_at",
        "snapshot_hash",
        "fina_indicator_json",
        "balancesheet_json",
        "income_json",
        "source_meta_json",
        "created_at",
    } <= _table_columns(conn, "monthly_pattern_financial_snapshots")
    assert {
        "scan_date",
        "signal_month",
        "status",
        "input_by",
        "source_status_json",
        "counts_json",
        "error",
        "created_at",
        "updated_at",
    } <= _table_columns(conn, "monthly_pattern_runs")
    assert {
        "stock_code",
        "stock_name",
        "strategy_type",
        "status",
        "signal_month",
        "entered_date",
        "last_seen_date",
        "exited_date",
        "exit_reason",
        "report_period",
        "financial_ann_date",
        "technical_evidence_json",
        "financial_evidence_json",
        "source_meta_json",
        "created_at",
        "updated_at",
    } <= _table_columns(conn, "monthly_pattern_pool")


def test_monthly_bars_primary_key_blocks_duplicate_raw_fact(
    conn: sqlite3.Connection,
) -> None:
    values = (
        "2026-06-30",
        "600000.SH",
        None,  # Tushare monthly 不带名称；候选入池前再批量补名
        10.0,
        12.0,
        9.5,
        11.5,
        1000.0,
        12000.0,
        1.2345,
        "tushare.daily",
        "2026-07-01T08:00:00+08:00",
    )
    conn.execute(
        """
        INSERT INTO monthly_pattern_bars (
            month_end, stock_code, stock_name, open, high, low, close,
            volume, amount, adj_factor, source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO monthly_pattern_bars (
                month_end, stock_code, stock_name, open, high, low, close,
                volume, amount, adj_factor, source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    assert "qfq_close" not in _table_columns(conn, "monthly_pattern_bars")


def test_financial_snapshot_keeps_announcement_revision_history(
    conn: sqlite3.Connection,
) -> None:
    def insert_revision(ann_date: str, roe: float) -> None:
        conn.execute(
            """
            INSERT INTO monthly_pattern_financial_snapshots (
                stock_code, report_period, financial_ann_date,
                version_visible_date, version_observed_at, snapshot_hash,
                fina_indicator_json, balancesheet_json, income_json, source_meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "600000.SH",
                "2025-12-31",
                ann_date,
                ann_date,
                f"{ann_date}T00:00:00",
                "a" * 64,
                json.dumps({"roe_waa": roe}),
                "{}",
                "{}",
                json.dumps({"source": "tushare"}),
            ),
        )

    insert_revision("2026-03-28", 18.0)
    insert_revision("2026-04-15", 17.5)

    rows = conn.execute(
        """
        SELECT financial_ann_date, fina_indicator_json
        FROM monthly_pattern_financial_snapshots
        ORDER BY financial_ann_date
        """
    ).fetchall()
    assert [row["financial_ann_date"] for row in rows] == [
        "2026-03-28",
        "2026-04-15",
    ]
    assert json.loads(rows[0]["fina_indicator_json"])["roe_waa"] == 18.0

    with pytest.raises(sqlite3.IntegrityError):
        insert_revision("2026-04-15", 17.0)


def test_run_status_distinguishes_true_empty_from_source_failure(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """
        INSERT INTO monthly_pattern_runs (
            scan_date, signal_month, status, input_by, source_status_json, counts_json
        ) VALUES (?, ?, 'complete', 'pytest', ?, ?)
        """,
        (
            "2026-07-01",
            "2026-06",
            json.dumps({"bars": "success", "financials": "success"}),
            json.dumps({"matched": 0}),
        ),
    )
    conn.execute(
        """
        INSERT INTO monthly_pattern_runs (
            scan_date, signal_month, status, input_by,
            source_status_json, counts_json, error
        ) VALUES (?, ?, 'failed', 'pytest', ?, ?, ?)
        """,
        (
            "2026-07-02",
            "2026-06",
            json.dumps({"bars": "source_failed"}),
            "{}",
            "bars_source_failed",
        ),
    )

    rows = conn.execute(
        "SELECT scan_date, status, error FROM monthly_pattern_runs ORDER BY scan_date"
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {"scan_date": "2026-07-01", "status": "complete", "error": None},
        {
            "scan_date": "2026-07-02",
            "status": "failed",
            "error": "bars_source_failed",
        },
    ]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO monthly_pattern_runs (
                scan_date, signal_month, status, source_status_json, counts_json
            ) VALUES ('2026-07-03', '2026-06', 'empty', '{}', '{}')
            """
        )


def test_pool_same_day_record_is_idempotent_and_json_roundtrips(
    conn: sqlite3.Connection,
) -> None:
    assert _record(conn, "2026-07-01") == "entered"
    assert _record(conn, "2026-07-01") == "refreshed"

    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_pool").fetchone()[0] == 1
    row = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert row is not None
    assert row["stock_code"] == "600000"
    assert row["status"] == "technical_candidate"
    assert row["technical_evidence"] == {"ma_alignment": True}
    assert row["financial_evidence"]["roe_waa"] == 18.0
    assert row["source_meta"]["bar_source"] == "tushare"
    assert "technical_evidence_json" not in row


def test_pool_enforces_forward_transitions_and_date_monotonicity(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "2026-07-01")
    assert _record(conn, "2026-07-02", status="fundamental_verified") == "transitioned"
    assert _record(conn, "2026-07-03", status="active") == "transitioned"
    assert _record(conn, "2026-07-04", status="risk") == "transitioned"
    assert _record(conn, "2026-07-05", status="active") == "transitioned"

    assert _record(conn, "2026-07-02", status="risk") == "stale"
    row = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert row is not None
    assert row["status"] == "active"
    assert row["last_seen_date"] == "2026-07-05"

    with pytest.raises(ValueError, match="invalid monthly pattern pool transition"):
        _record(conn, "2026-07-06", status="fundamental_verified")


def test_pool_unique_open_episode_is_scoped_by_stock_and_strategy(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "2026-07-01")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO monthly_pattern_pool (
                stock_code, stock_name, strategy_type, status, signal_month,
                entered_date, last_seen_date
            ) VALUES (
                '600000', '浦发银行', 'fundamental_monthly_trend', 'active',
                '2026-07', '2026-07-02', '2026-07-02'
            )
            """
        )

    assert (
        _record(
            conn,
            "2026-07-01",
            strategy_type="theme_monthly_attack",
            status="active",
        )
        == "entered"
    )
    assert len(pool.list_pool(conn)) == 2


def test_pool_exit_then_later_reentry_preserves_episode_history(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "2026-07-01", status="active")
    assert pool.mark_exited(
        conn,
        "600000.SH",
        "fundamental_monthly_trend",
        date="2026-07-10",
        reason="月线结构失效",
    )
    assert pool.get_open(conn, "600000", "fundamental_monthly_trend") is None

    assert _record(conn, "2026-08-03", status="technical_candidate") == "entered"
    rows = pool.list_pool(
        conn,
        stock_code="600000.SH",
        strategy_type="fundamental_monthly_trend",
    )
    assert len(rows) == 2
    assert rows[0]["status"] == "exited"
    assert rows[0]["exited_date"] == "2026-07-10"
    assert rows[0]["exit_reason"] == "月线结构失效"
    assert rows[1]["status"] == "technical_candidate"
    assert rows[1]["entered_date"] == "2026-08-03"


def test_pool_stale_exit_is_noop(conn: sqlite3.Connection) -> None:
    _record(conn, "2026-07-10", status="active")

    assert not pool.mark_exited(
        conn,
        "600000",
        "fundamental_monthly_trend",
        date="2026-07-09",
        reason="stale",
    )
    assert pool.get_open(conn, "600000", "fundamental_monthly_trend") is not None


def test_pool_stale_reentry_after_exit_does_not_create_zombie_episode(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "2026-07-10", status="active")
    pool.mark_exited(
        conn,
        "600000",
        "fundamental_monthly_trend",
        date="2026-07-12",
        reason="月线结构失效",
    )

    assert _record(conn, "2026-07-11", status="technical_candidate") == "stale"
    rows = pool.list_pool(conn)
    assert len(rows) == 1
    assert rows[0]["status"] == "exited"
    assert rows[0]["last_seen_date"] == "2026-07-12"


def test_pool_writes_leave_commit_and_rollback_to_caller(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "2026-07-01", status="active")
    conn.rollback()
    assert pool.list_pool(conn) == []

    _record(conn, "2026-07-01", status="active")
    conn.commit()
    pool.mark_exited(
        conn,
        "600000",
        "fundamental_monthly_trend",
        date="2026-07-10",
        reason="月线结构失效",
    )
    conn.rollback()
    row = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert row is not None
    assert row["status"] == "active"


def test_migrate_repairs_missing_monthly_pattern_tables_without_version_drift() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    set_schema_version(conn, CURRENT_SCHEMA_VERSION)
    for table in (
        "monthly_pattern_pool",
        "monthly_pattern_runs",
        "monthly_pattern_financial_snapshots",
        "monthly_pattern_bars",
    ):
        conn.execute(f"DROP TABLE {table}")
    conn.commit()

    migrate(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'monthly_pattern_%'
            """
        )
    }
    assert tables == {
        "monthly_pattern_bars",
        "monthly_pattern_bar_manifests",
        "monthly_pattern_financial_snapshots",
        "monthly_pattern_pool",
        "monthly_pattern_runs",
    }
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION


def test_migrate_repairs_legacy_monthly_run_audit_column() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    set_schema_version(conn, CURRENT_SCHEMA_VERSION)
    conn.execute("ALTER TABLE monthly_pattern_runs DROP COLUMN input_by")
    conn.commit()

    migrate(conn)

    assert "input_by" in _table_columns(conn, "monthly_pattern_runs")
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION


def test_migrate_rebuilds_legacy_financial_snapshot_identity_without_data_loss() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    set_schema_version(conn, CURRENT_SCHEMA_VERSION)
    conn.execute("DROP INDEX idx_monthly_pattern_financial_stock_period")
    conn.execute("DROP TABLE monthly_pattern_financial_snapshots")
    conn.execute(
        """
        CREATE TABLE monthly_pattern_financial_snapshots (
            stock_code TEXT NOT NULL,
            report_period TEXT NOT NULL,
            financial_ann_date TEXT NOT NULL,
            fina_indicator_json TEXT NOT NULL DEFAULT '{}',
            balancesheet_json TEXT NOT NULL DEFAULT '{}',
            income_json TEXT NOT NULL DEFAULT '{}',
            source_meta_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (stock_code, report_period, financial_ann_date)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO monthly_pattern_financial_snapshots (
            stock_code, report_period, financial_ann_date,
            fina_indicator_json, balancesheet_json, income_json, source_meta_json
        ) VALUES ('600000', '2025-12-31', '2026-03-28', ?, '{}', '{}', '{}')
        """,
        (json.dumps({"roe_waa": 18.0}),),
    )
    conn.execute(
        """
        INSERT INTO monthly_pattern_financial_snapshots (
            stock_code, report_period, financial_ann_date,
            fina_indicator_json, balancesheet_json, income_json, source_meta_json
        ) VALUES ('600001', '2025-12-31', '2026-03-28', ?, '{}', '{}', '{}')
        """,
        (json.dumps({"roe_waa": 9.0, "update_flag": "1"}),),
    )
    conn.commit()

    migrate(conn)

    assert {
        "version_visible_date",
        "snapshot_hash",
    } <= _table_columns(conn, "monthly_pattern_financial_snapshots")
    rows = conn.execute(
        """
        SELECT stock_code, version_visible_date, snapshot_hash, fina_indicator_json
        FROM monthly_pattern_financial_snapshots
        ORDER BY stock_code
        """
    ).fetchall()
    assert rows[0]["version_visible_date"] == "2026-03-28"
    assert len(rows[0]["snapshot_hash"]) == 64
    assert json.loads(rows[0]["fina_indicator_json"])["roe_waa"] == 18.0
    assert rows[1]["version_visible_date"] == date.today().isoformat()
    assert json.loads(rows[1]["fina_indicator_json"])["roe_waa"] == 9.0
    assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION
