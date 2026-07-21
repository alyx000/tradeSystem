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


def test_update_without_input_by_preserves_existing(conn):
    """update 分支未显式传 input_by 不得改写既有值(内部补账路径防静默覆盖为 system)。"""
    hid = Q.upsert_holding(conn, stock_code="601288.SH", stock_name="农业银行",
                           status="active", input_by="manual")
    Q.upsert_holding(conn, stock_code="601288.SH", stock_name="农业银行",
                     shares=500, status="active")   # 无 input_by
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "manual"


def test_insert_without_input_by_falls_to_system(conn):
    """query 层缺省 system(列不留 NULL)；审计强制在 CLI/API 边界，内部路径不炸。"""
    hid = Q.upsert_holding(conn, stock_code="600436.SH", stock_name="片仔癀", status="active")
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "system"


def test_holdings_add_cli_entry_date_semantics():
    """--entry-date 缺省语义(spec v5 严重2 + 门1 M1)：按"是否有既有行"判断，
    存量 NULL entry_date 行不得被盖成 today(NULL=缺数据而非错数据)。"""
    import datetime
    from zoneinfo import ZoneInfo

    from db import cli as db_cli

    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    assert db_cli.resolve_entry_date_for_upsert(None, has_existing_row=False) == today
    assert db_cli.resolve_entry_date_for_upsert(None, has_existing_row=True) is None  # 含 NULL 行
    assert db_cli.resolve_entry_date_for_upsert("2026-06-30", has_existing_row=True) == "2026-06-30"


def test_entry_date_arg_rejects_bad_format():
    """--entry-date 格式校验：空串/斜杠格式 fail-fast(门1 L1)。"""
    import argparse as _ap

    from db import cli as db_cli

    assert db_cli._entry_date_arg("2026-07-01") == "2026-07-01"
    for bad in ("", "2026/07/01", "20260701", "not-a-date"):
        with pytest.raises(_ap.ArgumentTypeError):
            db_cli._entry_date_arg(bad)


def test_holdings_add_required_input_by_parse_error():
    """省略 --input-by 必须被 argparse 拒绝(门1 B-4:防 required 被静默改回可选)。"""
    import argparse as _ap

    from db.cli import register_db_subparser

    parser = _ap.ArgumentParser()
    register_db_subparser(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit):
        parser.parse_args(["db", "holdings-add", "--code", "300750", "--name", "N"])


def _cli_args(**over):
    import argparse as _ap
    base = dict(code="601988.SH", name="中国银行", shares=None, price=None, sector=None,
                stop_loss=None, market="A股", entry_reason=None, note=None,
                thesis_id=None, entry_date=None, input_by="manual")
    base.update(over)
    return _ap.Namespace(**base)


def test_cmd_holdings_add_wiring(conn, monkeypatch):
    """_cmd_holdings_add 真实链路(门1 M5)：新建落 today；二次补 shares 保留 entry_date
    (含存量 NULL 行不被伪造日期，M1 回归)。"""
    import contextlib
    import datetime
    from zoneinfo import ZoneInfo

    from db import cli as db_cli

    @contextlib.contextmanager
    def _fake_get_db():
        yield conn

    monkeypatch.setattr(db_cli, "get_db", _fake_get_db)
    monkeypatch.setattr(db_cli, "migrate", lambda c: None)

    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    db_cli._cmd_holdings_add(_cli_args(price=5.0))
    row = conn.execute("SELECT * FROM holdings WHERE stock_code='601988.SH'").fetchone()
    assert row["entry_date"] == today and row["input_by"] == "manual"

    db_cli._cmd_holdings_add(_cli_args(shares=1000, input_by="cursor"))
    row = conn.execute("SELECT * FROM holdings WHERE stock_code='601988.SH'").fetchone()
    assert row["entry_date"] == today          # 更新未传保留原值
    assert row["shares"] == 1000 and row["input_by"] == "cursor"

    # 存量 NULL 行(补列前写入):补字段不得伪造 today
    Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                     status="active", input_by="manual")
    conn.execute("UPDATE holdings SET entry_date=NULL WHERE stock_code='601398.SH'")
    db_cli._cmd_holdings_add(_cli_args(code="601398.SH", name="工商银行", note="补备注"))
    row = conn.execute("SELECT * FROM holdings WHERE stock_code='601398.SH'").fetchone()
    assert row["entry_date"] is None and row["note"] == "补备注"


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
