"""v24 trade_thesis 中间层 schema 迁移测试。

按 plan precious-crunching-ocean.md 阶段 1 自底向上 TDD,5 个微循环 D1-D5。
"""
from __future__ import annotations

import sqlite3
import textwrap

import pytest

from db.connection import get_connection
from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test_v24.db")
    migrate(c)
    yield c
    c.close()


class TestV24TradeThesisTable:
    """D1: trade_thesis 主表 DDL + 23 列 + NOT NULL 约束."""

    def test_migrate_v24_creates_trade_thesis_table(self, conn):
        # v24 必须是当前 schema 版本
        assert CURRENT_SCHEMA_VERSION >= 24
        assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION

        # trade_thesis 表必须存在且列齐全
        cols = {
            row[1]: row
            for row in conn.execute("PRAGMA table_info(trade_thesis)").fetchall()
        }
        expected = {
            "id", "stock_code", "stock_name", "account_id",
            "opened_at", "closed_at", "status",
            "entry_reason", "failure_condition",
            "target_price", "stop_loss",
            "trade_mode", "mode_note", "market_region",
            "sector", "planned_position_pct",
            "plan_id", "notes",
            "created_at", "updated_at", "input_by",
            "reopen_count", "last_reopened_at",
        }
        missing = expected - set(cols.keys())
        assert not missing, f"trade_thesis 缺少列: {missing}"

        # 严格模式必填字段必须 NOT NULL
        # PRAGMA table_info 返回 (cid, name, type, notnull, dflt_value, pk)
        not_null_required = {
            "stock_code", "stock_name", "account_id", "opened_at",
            "status", "entry_reason", "failure_condition",
            "trade_mode", "market_region", "sector",
            "planned_position_pct", "input_by",
            "reopen_count",
        }
        for col_name in not_null_required:
            assert cols[col_name][3] == 1, (
                f"trade_thesis.{col_name} 应为 NOT NULL,实际 notnull={cols[col_name][3]}"
            )

        # 关键可空字段确认为 NULL allowed
        nullable_expected = {
            "closed_at", "target_price", "stop_loss",
            "mode_note", "plan_id", "notes", "last_reopened_at",
        }
        for col_name in nullable_expected:
            assert cols[col_name][3] == 0, (
                f"trade_thesis.{col_name} 应允许 NULL,实际 notnull={cols[col_name][3]}"
            )


class TestV24ThesisReviewTable:
    """D2: thesis_review 复盘表,1:1 关联 trade_thesis."""

    def test_migrate_v24_creates_thesis_review_table(self, conn):
        cols = {
            row[1]: row
            for row in conn.execute("PRAGMA table_info(thesis_review)").fetchall()
        }
        expected = {
            "thesis_id", "executed_as_planned", "exit_trigger",
            "lessons", "discipline_score",
            "realized_pnl_pct", "realized_pnl_amount", "holding_days",
            "created_at", "input_by",
        }
        missing = expected - set(cols.keys())
        assert not missing, f"thesis_review 缺少列: {missing}"

        # thesis_id 是 PK (1:1 关联 trade_thesis)
        assert cols["thesis_id"][5] == 1, "thesis_review.thesis_id 应为 PRIMARY KEY"

        # NOT NULL: thesis_id / executed_as_planned / input_by
        for col_name in ("thesis_id", "executed_as_planned", "input_by"):
            assert cols[col_name][3] == 1, (
                f"thesis_review.{col_name} 应为 NOT NULL,实际 notnull={cols[col_name][3]}"
            )

        # FK 必须挂 trade_thesis
        fks = conn.execute("PRAGMA foreign_key_list(thesis_review)").fetchall()
        # fk row: (id, seq, table, from, to, on_update, on_delete, match)
        fk_targets = {(row[2], row[4]) for row in fks}
        assert ("trade_thesis", "id") in fk_targets, (
            f"thesis_review.thesis_id 应外键挂 trade_thesis.id,实际 fks={fk_targets}"
        )


# ──────────────────────────────────────────────────────────────
# D3-D4: ALTER 兜底 — 旧 v23 库升级到 v24 时,broker_executions / holdings
# 必须通过 ALTER ADD COLUMN 加 thesis_id(避免 "CREATE IF NOT EXISTS 跳过老表")
# ──────────────────────────────────────────────────────────────

# 重建一个不含 thesis_id 列的伪 v23 broker_executions / holdings,
# 然后把 user_version 设为 23 并跑 migrate,验证 v24 ALTER 兜底生效。
_PSEUDO_V23_BROKER_EXECUTIONS = """
DROP TABLE IF EXISTS broker_executions;
CREATE TABLE broker_executions (
    id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT 'default',
    broker_code TEXT,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    exec_time TEXT,
    stock_code_raw TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'A股',
    market_raw TEXT,
    direction TEXT NOT NULL CHECK(direction IN ('buy','sell')),
    direction_raw TEXT NOT NULL,
    shares INTEGER NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    net_amount REAL,
    balance_after INTEGER,
    commission REAL NOT NULL DEFAULT 0,
    stamp_duty REAL NOT NULL DEFAULT 0,
    transfer_fee REAL NOT NULL DEFAULT 0,
    exchange_fee REAL NOT NULL DEFAULT 0,
    regulatory_fee REAL NOT NULL DEFAULT 0,
    other_fees REAL NOT NULL DEFAULT 0,
    total_fees REAL NOT NULL DEFAULT 0,
    broker_contract_no TEXT,
    broker_trade_no TEXT,
    currency TEXT DEFAULT 'CNY',
    raw_payload_json TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_format TEXT NOT NULL,
    source_archive_path TEXT,
    input_by TEXT NOT NULL,
    import_run_id TEXT,
    imported_at TEXT DEFAULT (datetime('now')),
    notes TEXT
);
"""

_PSEUDO_V23_HOLDINGS = """
DROP TABLE IF EXISTS holdings;
CREATE TABLE holdings (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    market TEXT DEFAULT 'A股',
    sector TEXT,
    shares INTEGER,
    entry_date TEXT,
    entry_price REAL,
    current_price REAL,
    stop_loss REAL,
    target_price REAL,
    position_ratio REAL,
    status TEXT DEFAULT 'active',
    entry_reason TEXT,
    note TEXT,
    updated_at TEXT
);
"""


def _make_pseudo_v23_db(db_path):
    """造一个 user_version=23 但 broker_executions/holdings 不含 thesis_id 的库."""
    c = get_connection(db_path)
    migrate(c)  # 先升到最新
    # 用伪 v23 DDL 重建两张表
    c.executescript(_PSEUDO_V23_BROKER_EXECUTIONS)
    c.executescript(_PSEUDO_V23_HOLDINGS)
    c.execute("PRAGMA user_version = 23")
    c.commit()
    return c


class TestV24AlterBrokerExecutions:
    """D3: broker_executions.thesis_id 列 + 索引 + ALTER 兜底."""

    def test_fresh_db_has_thesis_id_column(self, conn):
        """全新库:init_schema 直接建出含 thesis_id 的 broker_executions."""
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(broker_executions)").fetchall()
        }
        assert "thesis_id" in cols, "broker_executions 应含 thesis_id 列"

    def test_idx_be_thesis_index_created(self, conn):
        """idx_be_thesis 索引必须建立."""
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(broker_executions)").fetchall()
        }
        assert "idx_be_thesis" in indexes, (
            f"应有 idx_be_thesis 索引,实际索引: {indexes}"
        )

    def test_alter_pseudo_v23_db_adds_thesis_id(self, tmp_path):
        """模拟旧 v23 库:thesis_id 列不存在,migrate 应通过 ALTER ADD 兜底."""
        db_path = tmp_path / "pseudo_v23.db"
        c = _make_pseudo_v23_db(db_path)

        # 确认伪 v23 状态:无 thesis_id
        cols_before = {
            row[1] for row in c.execute("PRAGMA table_info(broker_executions)").fetchall()
        }
        assert "thesis_id" not in cols_before, "前置条件:伪 v23 库不应有 thesis_id"
        assert get_schema_version(c) == 23

        # migrate 到 v24
        migrate(c, activate_v40=True)

        cols_after = {
            row[1] for row in c.execute("PRAGMA table_info(broker_executions)").fetchall()
        }
        assert "thesis_id" in cols_after, "v24 ALTER 兜底应已加 thesis_id 列"
        assert get_schema_version(c) == CURRENT_SCHEMA_VERSION
        c.close()


class TestV24AlterHoldings:
    """D4: holdings.thesis_id 列 + ALTER 兜底."""

    def test_fresh_db_holdings_has_thesis_id(self, conn):
        """全新库:holdings 表应含 thesis_id 列."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(holdings)").fetchall()}
        assert "thesis_id" in cols, "holdings 应含 thesis_id 列"

    def test_alter_pseudo_v23_holdings_adds_thesis_id(self, tmp_path):
        """模拟旧 v23 库:holdings 不含 thesis_id,migrate 应 ALTER 加列."""
        db_path = tmp_path / "pseudo_v23_h.db"
        c = _make_pseudo_v23_db(db_path)
        cols_before = {row[1] for row in c.execute("PRAGMA table_info(holdings)").fetchall()}
        assert "thesis_id" not in cols_before, "前置:伪 v23 holdings 不应有 thesis_id"

        migrate(c, activate_v40=True)
        cols_after = {row[1] for row in c.execute("PRAGMA table_info(holdings)").fetchall()}
        assert "thesis_id" in cols_after, "v24 ALTER 兜底应已加 holdings.thesis_id"
        c.close()


class TestV24UniqueOpenThesis:
    """D5: 账户隔离 + 同票同账户同时只能有 1 个 open thesis(partial unique 索引)."""

    def _insert_thesis(self, conn, *, code, account, opened_at, status="open"):
        return conn.execute(
            """
            INSERT INTO trade_thesis (
                stock_code, stock_name, account_id, opened_at, status,
                entry_reason, failure_condition, trade_mode, market_region,
                sector, planned_position_pct, input_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, "测试票", account, opened_at, status,
             "主线龙头", "尾盘破板", "break", "a-share",
             "白酒", 0.15, "alyx"),
        )

    def test_unique_index_account_stock_status_blocks_dup_open(self, conn):
        """同账户 + 同票 + 同时 open → 第二条应被 unique 索引拒绝."""
        self._insert_thesis(conn, code="600519", account="A001", opened_at="2026-05-01")
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_thesis(
                conn, code="600519", account="A001", opened_at="2026-05-02"
            )

    def test_different_account_same_stock_both_open_allowed(self, conn):
        """跨账户的同票 open thesis 应允许(账户隔离)."""
        self._insert_thesis(conn, code="600519", account="A001", opened_at="2026-05-01")
        # 跨账户允许
        self._insert_thesis(conn, code="600519", account="A002", opened_at="2026-05-01")
        rows = conn.execute(
            "SELECT account_id FROM trade_thesis WHERE stock_code='600519' AND status='open' ORDER BY account_id"
        ).fetchall()
        assert [r[0] for r in rows] == ["A001", "A002"]

    def test_closed_thesis_does_not_block_new_open(self, conn):
        """同账户 + 同票,旧 thesis closed 后,新开 open 应允许(部分索引仅约束 status='open')."""
        self._insert_thesis(conn, code="600519", account="A001", opened_at="2026-05-01")
        conn.execute(
            "UPDATE trade_thesis SET status='closed', closed_at='2026-05-03' WHERE id = 1"
        )
        # 再开新的 open thesis 应允许
        self._insert_thesis(conn, code="600519", account="A001", opened_at="2026-05-10")
        opens = conn.execute(
            "SELECT COUNT(*) FROM trade_thesis WHERE stock_code='600519' AND account_id='A001' AND status='open'"
        ).fetchone()[0]
        assert opens == 1


_PSEUDO_V26_TRADE_THESIS_OLD_MODES = """
DROP TABLE IF EXISTS trade_thesis;
CREATE TABLE trade_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    opened_at TEXT NOT NULL CHECK(opened_at GLOB '????-??-??'),
    closed_at TEXT CHECK(closed_at IS NULL OR closed_at GLOB '????-??-??'),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    entry_reason TEXT NOT NULL,
    failure_condition TEXT NOT NULL,
    target_price REAL,
    stop_loss REAL,
    trade_mode TEXT NOT NULL CHECK(trade_mode IN (
        'break', 'dip', 'trend', 'scalp', 'swing', 'arbitrage', 'gap_jump', 'other'
    )),
    mode_note TEXT,
    market_region TEXT NOT NULL DEFAULT 'a-share' CHECK(market_region IN ('a-share', 'hk', 'us')),
    sector TEXT NOT NULL,
    planned_position_pct REAL NOT NULL,
    plan_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    input_by TEXT NOT NULL,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    last_reopened_at TEXT
);
"""


class TestV27TradeModeMigration:
    """v27: 旧 v26 trade_thesis CHECK 应接受 sentiment_relay."""

    def test_migrate_v26_table_rebuilds_trade_mode_check_for_sentiment_relay(self, tmp_path):
        db_path = tmp_path / "pseudo_v26_old_modes.db"
        c = get_connection(db_path)
        c.executescript(_PSEUDO_V26_TRADE_THESIS_OLD_MODES)
        c.execute("PRAGMA user_version = 26")
        c.commit()

        migrate(c, activate_v40=True)

        c.execute(
            """
            INSERT INTO trade_thesis (
                stock_code, stock_name, account_id, opened_at,
                entry_reason, failure_condition, trade_mode, market_region,
                sector, planned_position_pct, input_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "601991", "大唐发电", "default", "2026-05-19",
                "情绪转暖下的连板二波接力", "跌破 5 日均线",
                "sentiment_relay", "a-share", "电力、连板二波", 0.3333, "alyx",
            ),
        )
        assert get_schema_version(c) == CURRENT_SCHEMA_VERSION
        c.close()
