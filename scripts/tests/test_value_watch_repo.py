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


def test_append_concurrent_threads_no_lost_keys(tmp_path):
    """门2 G2 high-2(round2 真交错):两线程两连接各追加 20 个不同键,读改写周期真实
    重叠(busy_timeout=5s 下 BEGIN IMMEDIATE 串行化);非原子实现在此负载下大概率丢键。"""
    import threading

    db = tmp_path / "vw_conc.db"
    c0 = get_connection(db)
    init_schema(c0)
    repo.upsert_daily(c0, "2026-07-21", {}, 1)
    c0.close()

    start = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(prefix: str):
        conn = get_connection(db)
        try:
            start.wait()
            for i in range(20):
                repo.append_sent_events(conn, "2026-07-21", [f"v1:{prefix}:{i}"])
        except Exception as e:   # noqa: BLE001
            errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = get_connection(db)
    snap = repo.get_snapshot(check, "2026-07-21")
    expected = {f"v1:{p}:{i}" for p in ("a", "b") for i in range(20)}
    assert set(snap["sent_events"]) == expected   # 40 键无一丢失
    check.close()


def test_append_rejects_dirty_transaction(conn):
    """round2 high:调用方有未提交写入时 append 必须抛错而非越权 commit——
    否则调用方本想回滚的 marker 会被永久提交。"""
    repo.upsert_daily(conn, "2026-07-21", {}, 1)
    conn.execute("INSERT INTO teachers (name) VALUES ('marker-t')")   # 未提交
    assert conn.in_transaction
    with pytest.raises(RuntimeError):
        repo.append_sent_events(conn, "2026-07-21", ["v1:k"])
    conn.rollback()
    row = conn.execute("SELECT 1 FROM teachers WHERE name='marker-t'").fetchone()
    assert row is None   # marker 可回滚,未被越权提交


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
