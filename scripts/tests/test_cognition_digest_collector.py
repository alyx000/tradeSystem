from __future__ import annotations
import pytest
from db.connection import get_connection, get_db
from db.migrate import migrate
from services.cognition_service import CognitionService
from services.cognition_digest import collector


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "cog_digest.db"
    conn = get_connection(p)
    migrate(conn)
    conn.close()
    return str(p)


def _add_cog(svc, title, *, category="signal", status="candidate"):
    return svc.add_cognition(
        category=category, title=title, description="desc " + title,
        status=status, input_by="manual",
    )["cognition_id"]


def _seed_teachers(db_path, teachers):
    """teacher_id 有 FK → teachers(id)，且 PRAGMA foreign_keys=1，写实例前必须先建老师行。"""
    with get_db(db_path) as conn:
        migrate(conn)
        for tid, name in teachers:
            conn.execute("INSERT OR IGNORE INTO teachers (id, name) VALUES (?, ?)",
                         (tid, name))


def _backdate_created(db_path, cid, date="2026-01-01 00:00:00"):
    """把 created_at 退到窗口外，隔离 collector「created_at∈窗口 纳新」分支对 wall-clock 的依赖。"""
    with get_db(db_path) as conn:
        conn.execute("UPDATE trading_cognitions SET created_at=? WHERE cognition_id=?",
                     (date, cid))


def test_collect_groups_instances_in_window(db_path):
    svc = CognitionService(db_path)
    _seed_teachers(db_path, [(1, "沈纯"), (2, "李四")])  # 满足 teacher_id FK
    cid = _add_cog(svc, "认知A")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-01",
                     source_type="teacher_note", teacher_id=1,
                     teacher_name_snapshot="沈纯", input_by="manual")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="daily_review", teacher_id=2,
                     teacher_name_snapshot="李四", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.total_instances == 2
    assert len(data.activities) == 1
    act = data.activities[0]
    assert act.cognition_id == cid
    assert len(act.instances) == 2
    assert set(data.teacher_names) == {"沈纯", "李四"}


def test_collect_excludes_out_of_window(db_path):
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知B")
    _backdate_created(db_path, cid)  # created_at 退出窗口，确保仅靠"窗口外实例"判定排除
    svc.add_instance(cognition_id=cid, observed_date="2026-05-20",
                     source_type="teacher_note", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.activities == []
    assert data.total_instances == 0


def test_collect_excludes_deprecated(db_path):
    # 弃用认知排除出 activities，其实例也不计入 total_instances（口径与"活跃认知"一致）
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知C")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-01",
                     source_type="teacher_note", input_by="manual")
    svc.deprecate_cognition(cognition_id=cid, reason="过期", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert all(a.cognition_id != cid for a in data.activities)
    assert data.total_instances == 0


def test_collect_window_endpoints_inclusive(db_path):
    # observed_date 恰好等于 start / end 都应纳入（闭区间端点）
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知D")
    svc.add_instance(cognition_id=cid, observed_date="2026-05-31",
                     source_type="teacher_note", input_by="manual")  # == start
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="daily_review", input_by="manual")  # == end
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.total_instances == 2


def test_collect_is_readonly(db_path):
    # 严重 1 回归：collect 不得写真实库（不 migrate、不 commit、不动 user_version）
    import sqlite3
    before = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    collector.collect(db_path, "2026-05-31", "2026-06-02")
    after = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    assert before == after


def test_ro_connect_rejects_writes(db_path):
    # 强化只读门禁：直接证明 _ro_connect 的连接拒绝任何写（比 user_version 比对更强，codex 中项）
    import sqlite3
    conn = collector._ro_connect(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE trading_cognitions SET title = 'x'")
    finally:
        conn.close()
