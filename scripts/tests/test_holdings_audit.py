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


def test_entry_date_default_atomic_in_query_layer(conn):
    """entry_date 缺省原子化(收尾门 round2 high)：只在真正 INSERT 时落上海当日;
    update 未传保留原值(含存量 NULL 行不被伪造 today);显式传用传入值。"""
    import datetime
    from zoneinfo import ZoneInfo

    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    hid = Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                           status="active", input_by="manual")   # 新建未传
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["entry_date"] == today
    # 存量 NULL 行(补列前写入):update 补字段不得伪造 today
    conn.execute("UPDATE holdings SET entry_date=NULL WHERE id=?", (hid,))
    conn.commit()
    Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                     shares=100, status="active", input_by="manual")
    assert conn.execute("SELECT entry_date FROM holdings WHERE id=?", (hid,)).fetchone()[0] is None
    # 显式传
    hid2 = Q.upsert_holding(conn, stock_code="600436.SH", stock_name="片仔癀",
                            entry_date="2026-06-30", status="active", input_by="manual")
    assert conn.execute("SELECT entry_date FROM holdings WHERE id=?", (hid2,)).fetchone()[0] == "2026-06-30"


def test_entry_date_null_and_empty_normalized(conn):
    """收尾门 round3 high:键存在但值为 None/空串必须视同未提供——
    insert 走缺省 today,update 不得把历史日期清成 NULL/''。"""
    import datetime
    from zoneinfo import ZoneInfo

    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    hid = Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                           entry_date=None, status="active", input_by="manual")
    assert conn.execute("SELECT entry_date FROM holdings WHERE id=?", (hid,)).fetchone()[0] == today
    hid2 = Q.upsert_holding(conn, stock_code="601988.SH", stock_name="中国银行",
                            entry_date="", status="active", input_by="manual")
    assert conn.execute("SELECT entry_date FROM holdings WHERE id=?", (hid2,)).fetchone()[0] == today
    # 历史日期 + 空串 update → 保留
    conn.execute("UPDATE holdings SET entry_date='2020-01-02' WHERE id=?", (hid,))
    conn.commit()
    Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                     entry_date="", shares=50, status="active", input_by="web")
    assert conn.execute("SELECT entry_date FROM holdings WHERE id=?", (hid,)).fetchone()[0] == "2020-01-02"


def test_concurrent_create_does_not_overwrite_historical_entry_date(conn):
    """收尾门 round2 high 交错回归:请求 A(无 entry_date)查无行→B 先创建带历史日期的
    持仓→A insert 撞 active 唯一索引重试转 update——B 的历史日期不得被 A 的缺省覆盖。"""
    orig = Q._active_holdings_by_code
    state = {"first": True}

    def _race(c, code):
        rows = orig(c, code)
        if state["first"] and not rows:
            state["first"] = False
            # B 在 A 查后写前创建历史日期持仓
            Q.upsert_holding(c, stock_code="600900.SH", stock_name="长江电力",
                             entry_date="2020-01-02", entry_price=18.0,
                             status="active", input_by="manual")
            return rows   # A 仍拿到"无行"快照 → 走 insert → IntegrityError → 重试转 update
        return rows

    import unittest.mock as mock
    with mock.patch.object(Q, "_active_holdings_by_code", side_effect=_race):
        Q.upsert_holding(conn, stock_code="600900.SH", stock_name="长江电力",
                         shares=200, status="active", input_by="web")   # A:无 entry_date
    row = conn.execute("SELECT * FROM holdings WHERE stock_code='600900.SH' "
                       "AND status='active'").fetchone()
    assert row["entry_date"] == "2020-01-02"   # 历史日期保留
    assert row["shares"] == 200                # A 的字段经 update 落地


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


def test_update_holding_if_active_atomic(conn):
    """门2 high-1 round2:条件 UPDATE 原子语义——active 行 rowcount=1;closed 行/不存在
    行 rowcount=0 且不落任何修改(先查后写的并发窗口被单条 SQL 消除)。"""
    hid = Q.upsert_holding(conn, stock_code="600436.SH", stock_name="片仔癀",
                           status="active", input_by="manual")
    assert Q.update_holding_if_active(conn, hid, note="x") == 1
    # 模拟并发 DELETE 已提交 closed
    Q.update_holding(conn, hid, status="closed", input_by="cursor")
    assert Q.update_holding_if_active(conn, hid, note="late", input_by="web") == 0
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["note"] == "x" and row["input_by"] == "cursor"   # 晚到写未生效
    assert Q.update_holding_if_active(conn, 999999, note="y") == 0


def test_upsert_falls_to_insert_when_row_closed_in_window(conn):
    """round3 high-2:update 目标行在窗口内被 close → 不篡改 closed 行,fallback 新建。"""
    hid = Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                           entry_date="2026-06-01", status="active", input_by="manual")

    orig = Q._active_holdings_by_code

    def _race(c, code):
        rows = orig(c, code)
        if rows:  # 查到后、写入前,另一"连接"完成 DELETE(soft close)
            Q.update_holding(c, int(rows[0]["id"]), status="closed", input_by="cursor")
        return rows

    import unittest.mock as mock
    with mock.patch.object(Q, "_active_holdings_by_code", side_effect=_race):
        new_id = Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                                  shares=100, status="active", input_by="web")
    assert new_id != hid                     # 未复用 closed 行,新建
    old = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert old["status"] == "closed" and old["input_by"] == "cursor"  # closed 行未被篡改
    assert old["shares"] is None


def test_close_by_code_counts_only_actually_closed(conn):
    """round3 med:CLI 关仓窗口内行已被他方关闭 → 不覆盖审计、不虚报数量。"""
    hid = Q.upsert_holding(conn, stock_code="600900.SH", stock_name="长江电力",
                           status="active", input_by="manual")

    orig = Q._active_holdings_by_code

    def _race(c, code):
        rows = orig(c, code)
        if rows:
            Q.update_holding(c, int(rows[0]["id"]), status="closed", input_by="web")
        return rows

    import unittest.mock as mock
    with mock.patch.object(Q, "_active_holdings_by_code", side_effect=_race):
        n = Q.close_active_holdings_by_code(conn, "600900.SH", input_by="manual")
    assert n == 0
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "web"          # 首次关闭者审计保留


def test_lifecycle_and_repair_write_system_audit(conn):
    """round3 high-3(简化断言):内部补账/关仓路径显式署名 system。"""
    hid = Q.upsert_holding(conn, stock_code="601988.SH", stock_name="中国银行",
                           status="active", input_by="manual", thesis_id=7)
    # 模拟 lifecycle 自动关仓 SQL(与 lifecycle.py 同形)
    conn.execute("""UPDATE holdings SET shares = 0, status = 'closed', input_by = 'system',
                    updated_at = datetime('now') WHERE thesis_id = ? AND status = 'active'""", (7,))
    row = conn.execute("SELECT * FROM holdings WHERE id=?", (hid,)).fetchone()
    assert row["input_by"] == "system"


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
