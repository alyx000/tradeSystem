"""value_watch_daily 快照表 + sent_events 通知账本 repo 层。

spec v8：一天一行 JSON 快照；sent_events_json 只增不删；已发事件全集=全表并集；
payload allow_nan=False fail-fast。
"""
from __future__ import annotations

import pytest

from db.connection import get_connection
from db.schema import init_schema
from services.value_watch import repo


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "vw.db")
    init_schema(c)
    yield c
    c.close()


def test_upsert_and_ledger_union(conn):
    repo.upsert_daily(conn, "2026-07-20", {"a": 1}, 1)
    repo.append_sent_events(conn, "2026-07-20", ["v1:x:1"])
    repo.upsert_daily(conn, "2026-07-21", {"a": 2}, 1)
    repo.append_sent_events(conn, "2026-07-21", ["v1:x:2"])
    assert repo.load_sent_ledger(conn) == {"v1:x:1", "v1:x:2"}


def test_same_day_rerun_keeps_sent_events(conn):
    repo.upsert_daily(conn, "2026-07-21", {"a": 1}, 1)
    repo.append_sent_events(conn, "2026-07-21", ["v1:k1"])
    repo.upsert_daily(conn, "2026-07-21", {"a": 2}, 1)          # 重跑刷新 payload
    repo.append_sent_events(conn, "2026-07-21", ["v1:k2"])       # 合并不覆盖
    snap = repo.get_snapshot(conn, "2026-07-21")
    assert snap["payload"] == {"a": 2}
    assert set(snap["sent_events"]) == {"v1:k1", "v1:k2"}


def test_append_dedupes(conn):
    repo.upsert_daily(conn, "2026-07-21", {}, 1)
    repo.append_sent_events(conn, "2026-07-21", ["v1:k1"])
    repo.append_sent_events(conn, "2026-07-21", ["v1:k1", "v1:k2"])   # k1 重复append 去重
    snap = repo.get_snapshot(conn, "2026-07-21")
    assert sorted(snap["sent_events"]) == ["v1:k1", "v1:k2"]


def test_append_missing_row_raises(conn):
    """行不存在抛错(调用序契约:必须先 upsert_daily)——静默跳过会吞掉丢账本 bug。"""
    with pytest.raises(ValueError):
        repo.append_sent_events(conn, "2026-07-21", ["v1:k1"])


def test_append_concurrent_connections_no_lost_keys(tmp_path):
    """门2 G2 high-2:两连接并发追加不同键,BEGIN IMMEDIATE 串行化后两键都在。"""
    db = tmp_path / "vw_conc.db"
    c1 = get_connection(db)
    init_schema(c1)
    repo.upsert_daily(c1, "2026-07-21", {}, 1)
    c2 = get_connection(db)
    repo.append_sent_events(c1, "2026-07-21", ["v1:a"])
    repo.append_sent_events(c2, "2026-07-21", ["v1:b"])
    snap = repo.get_snapshot(c1, "2026-07-21")
    assert sorted(snap["sent_events"]) == ["v1:a", "v1:b"]
    c1.close(); c2.close()


def test_get_snapshot_latest_and_missing(conn):
    assert repo.get_snapshot(conn, None) is None
    repo.upsert_daily(conn, "2026-07-20", {"a": 1}, 1)
    repo.upsert_daily(conn, "2026-07-21", {"a": 2}, 1)
    snap = repo.get_snapshot(conn, None)
    assert snap["date"] == "2026-07-21"
    assert snap["logic_version"] == 1
    assert repo.get_snapshot(conn, "2026-07-19") is None


def test_nan_rejected(conn):
    with pytest.raises(ValueError):
        repo.upsert_daily(conn, "2026-07-21", {"bad": float("nan")}, 1)
