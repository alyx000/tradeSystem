"""holdings 审计闭环：input_by 列 + 各写路径落值 + entry_date 保留语义。

spec: docs/superpowers/specs/2026-07-21-value-watch-design.md（v8）
- query 层 input_by 可选、缺省 "system"（列不留 NULL）；审计强制在 CLI/API 边界。
- holdings-add --entry-date 缺省 None：仅插入新行落 today，更新已有行保留原值。
"""
from __future__ import annotations

import sqlite3

import pytest

from db import queries as Q
from db.connection import get_connection
from db.schema import init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


def test_upsert_holding_insert_writes_input_by(conn):
    hid = Q.upsert_holding(conn, stock_code="600900.SH", stock_name="长江电力",
                           entry_price=25.0, entry_date="2026-07-01",
                           status="active", input_by="manual")
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "manual"
    assert row["entry_date"] == "2026-07-01"


def test_upsert_holding_update_overwrites_input_by_keeps_entry_date(conn):
    hid = Q.upsert_holding(conn, stock_code="600900.SH", stock_name="长江电力",
                           entry_price=25.0, entry_date="2026-07-01",
                           status="active", input_by="manual")
    # 二次 upsert 命中同 active 行：input_by 覆盖为本次操作方，entry_date 未传保留原值
    hid2 = Q.upsert_holding(conn, stock_code="600900.SH", stock_name="长江电力",
                            shares=1000, status="active", input_by="web")
    assert hid2 == hid
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "web"
    assert row["entry_date"] == "2026-07-01"


def test_close_active_holdings_writes_input_by(conn):
    Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                     status="active", input_by="manual")
    n = Q.close_active_holdings_by_code(conn, "601398.SH", input_by="web")
    assert n == 1
    row = conn.execute("SELECT * FROM holdings WHERE stock_code='601398.SH'").fetchone()
    assert row["status"] == "closed"
    assert row["input_by"] == "web"


def test_insert_without_input_by_falls_to_system(conn):
    """query 层缺省 system(列不留 NULL)；审计强制在 CLI/API 边界，内部路径不炸。"""
    hid = Q.upsert_holding(conn, stock_code="600436.SH", stock_name="片仔癀", status="active")
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "system"


def test_holdings_add_cli_entry_date_semantics():
    """--entry-date 缺省语义(spec v5 严重2)：新行未传落 today；更新未传保留原值(返 None=不更新)。"""
    import datetime
    from db import cli as db_cli

    today = datetime.date.today().isoformat()
    assert db_cli.resolve_entry_date_for_upsert(None, existing_entry_date=None) == today
    assert db_cli.resolve_entry_date_for_upsert(None, existing_entry_date="2026-07-01") is None
    assert db_cli.resolve_entry_date_for_upsert("2026-06-30", existing_entry_date="2026-07-01") == "2026-06-30"


def test_ensure_backfills_input_by_column_on_old_table():
    """ALTER 兜底：老表（无 input_by 列）经 _ensure_holdings_audit_columns 后列存在。"""
    from db.migrate import _ensure_holdings_audit_columns

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE holdings (
        id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
        status TEXT DEFAULT 'active', updated_at TEXT, thesis_id INTEGER,
        entry_date TEXT, entry_price REAL)""")
    _ensure_holdings_audit_columns(c)
    cols = {row[1] for row in c.execute("PRAGMA table_info(holdings)").fetchall()}
    assert "input_by" in cols
    c.close()
