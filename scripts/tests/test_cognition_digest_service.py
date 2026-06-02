from __future__ import annotations
import pytest
from db.connection import get_connection, get_db
from db.migrate import migrate
from services.cognition_service import CognitionService
from services.cognition_digest import run_window_digest, RenderedCognitionDigest


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "svc.db"
    conn = get_connection(p)
    migrate(conn)
    conn.close()
    return str(p)


def _seed_teachers(db_path, teachers):
    """teacher_id 有 FK → teachers(id)，且 PRAGMA foreign_keys=1，写实例前必须先建老师行。"""
    with get_db(db_path) as conn:
        migrate(conn)
        for tid, name in teachers:
            conn.execute("INSERT OR IGNORE INTO teachers (id, name) VALUES (?, ?)",
                         (tid, name))


def test_run_window_digest_end_to_end_no_llm(db_path):
    svc = CognitionService(db_path)
    _seed_teachers(db_path, [(1, "沈纯")])  # 满足 teacher_id FK
    cid = svc.add_cognition(category="signal", title="认知A",
                            description="d", status="candidate", input_by="manual")["cognition_id"]
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="teacher_note", teacher_id=1,
                     teacher_name_snapshot="沈纯", input_by="manual")
    digest = run_window_digest(db_path, "recent3d", "2026-06-02", no_llm=True)
    assert isinstance(digest, RenderedCognitionDigest)
    assert "认知A" in digest.markdown
    assert digest.stats["active"] == 1
    assert digest.is_empty is False


def test_run_window_digest_empty(db_path):
    digest = run_window_digest(db_path, "weekly", "2026-06-02", no_llm=True)
    assert digest.is_empty is True
    assert "无新增认知沉淀" in digest.markdown
