"""L1: 交易认知层 Schema 测试（方案 §4、§8 / Phase 1a-B）。

覆盖点：
- 三张新表 / 两个触发器 / 唯一约束 / 迁移版本是否正确落地
- 触发器维护 instance_count / validated_count / invalidated_count / confidence
- UPDATE outcome 后父表统计同步
- 迁移可重复执行（幂等）
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from db.connection import get_connection
from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "cognition.db")
    migrate(c)
    yield c
    c.close()


def _new_cog_id() -> str:
    return "cog_" + uuid.uuid4().hex[:8]


def _new_inst_id() -> str:
    return "inst_" + uuid.uuid4().hex[:8]


def _new_review_id() -> str:
    return "rev_" + uuid.uuid4().hex[:8]


def _insert_cognition(
    conn: sqlite3.Connection,
    *,
    cognition_id: str | None = None,
    category: str = "signal",
    title: str = "测试认知",
) -> str:
    cid = cognition_id or _new_cog_id()
    conn.execute(
        """
        INSERT INTO trading_cognitions (cognition_id, category, title, description)
        VALUES (?, ?, ?, ?)
        """,
        (cid, category, title, f"{title} 的描述"),
    )
    conn.commit()
    return cid


def _insert_instance(
    conn: sqlite3.Connection,
    cognition_id: str,
    *,
    observed_date: str = "2026-04-15",
    source_type: str = "teacher_note",
    source_note_id: int | None = None,
    outcome: str = "pending",
) -> str:
    iid = _new_inst_id()
    conn.execute(
        """
        INSERT INTO cognition_instances
        (instance_id, cognition_id, observed_date, source_type, source_note_id, outcome)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (iid, cognition_id, observed_date, source_type, source_note_id, outcome),
    )
    conn.commit()
    return iid


def _get_parent(conn: sqlite3.Connection, cognition_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM trading_cognitions WHERE cognition_id = ?", (cognition_id,)
    ).fetchone()
    assert row is not None
    return row


# ──────────────────────────────────────────────────────────────
# 结构层：表创建 + 迁移版本 + 幂等
# ──────────────────────────────────────────────────────────────

def test_three_tables_created(conn):
    """迁移后三张新表都存在。"""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for expected in ("trading_cognitions", "cognition_instances", "periodic_reviews"):
        assert expected in tables, f"missing table: {expected}"


def test_schema_version_is_21(conn):
    """CURRENT_SCHEMA_VERSION 升到 21 且 migrate() 已落库。"""
    assert CURRENT_SCHEMA_VERSION == 21
    assert get_schema_version(conn) == 21


def test_migration_idempotent(tmp_path):
    """连续调用 migrate() 两次不报错，版本保持 21。"""
    db_path = tmp_path / "idempotent.db"
    c = get_connection(db_path)
    migrate(c)
    migrate(c)
    migrate(c)
    assert get_schema_version(c) == 21
    c.close()


# ──────────────────────────────────────────────────────────────
# 触发器层：INSERT 实例后重算计数
# ──────────────────────────────────────────────────────────────

def test_trigger_instance_count_increments(conn):
    """插入 2 条 instance，父表 instance_count=2。"""
    cid = _insert_cognition(conn)
    _insert_instance(conn, cid, observed_date="2026-04-14")
    _insert_instance(conn, cid, observed_date="2026-04-15")

    parent = _get_parent(conn, cid)
    assert parent["instance_count"] == 2
    assert parent["validated_count"] == 0
    assert parent["invalidated_count"] == 0


def test_trigger_confidence_below_threshold(conn):
    """有效样本 < 3 时 confidence 固定 0.5（即便已有 validated/invalidated）。"""
    cid = _insert_cognition(conn)
    _insert_instance(conn, cid, observed_date="2026-04-01", outcome="validated")
    _insert_instance(conn, cid, observed_date="2026-04-02", outcome="invalidated")

    parent = _get_parent(conn, cid)
    assert parent["instance_count"] == 2
    assert parent["validated_count"] == 1
    assert parent["invalidated_count"] == 1
    assert parent["confidence"] == 0.5


def test_trigger_confidence_computed(conn):
    """5 条 validated + 3 条 invalidated → confidence = 5/8 = 0.625。"""
    cid = _insert_cognition(conn)
    for i in range(5):
        _insert_instance(
            conn, cid,
            observed_date=f"2026-03-{i + 1:02d}",
            outcome="validated",
        )
    for i in range(3):
        _insert_instance(
            conn, cid,
            observed_date=f"2026-04-{i + 1:02d}",
            outcome="invalidated",
        )

    parent = _get_parent(conn, cid)
    assert parent["instance_count"] == 8
    assert parent["validated_count"] == 5
    assert parent["invalidated_count"] == 3
    assert parent["confidence"] == pytest.approx(5 / 8)


def test_trigger_after_update_outcome(conn):
    """先插入 pending，UPDATE 为 validated，父表 validated_count 增加。

    同时 instance_count 不因 UPDATE 增加（只有 INSERT 才 +1）。
    """
    cid = _insert_cognition(conn)
    _insert_instance(conn, cid, observed_date="2026-04-10", outcome="pending")
    _insert_instance(conn, cid, observed_date="2026-04-11", outcome="pending")
    _insert_instance(conn, cid, observed_date="2026-04-12", outcome="pending")

    parent = _get_parent(conn, cid)
    assert parent["instance_count"] == 3
    assert parent["validated_count"] == 0

    conn.execute(
        "UPDATE cognition_instances SET outcome='validated' WHERE observed_date='2026-04-10' AND cognition_id=?",
        (cid,),
    )
    conn.execute(
        "UPDATE cognition_instances SET outcome='validated' WHERE observed_date='2026-04-11' AND cognition_id=?",
        (cid,),
    )
    conn.execute(
        "UPDATE cognition_instances SET outcome='invalidated' WHERE observed_date='2026-04-12' AND cognition_id=?",
        (cid,),
    )
    conn.commit()

    parent = _get_parent(conn, cid)
    assert parent["instance_count"] == 3
    assert parent["validated_count"] == 2
    assert parent["invalidated_count"] == 1
    # 3 条有效样本刚达到阈值 → 启用 validated / total 公式
    assert parent["confidence"] == pytest.approx(2 / 3)


# ──────────────────────────────────────────────────────────────
# 约束层：唯一约束
# ──────────────────────────────────────────────────────────────

def test_unique_constraint_on_instances(conn):
    """同 (cognition_id, observed_date, source_type, source_note_id) 第二次插入失败。

    注：仅覆盖 source_note_id 非 NULL 情况；NULL 漏洞由 service 层 existence check 兜底（方案 §4.2）。
    """
    cid = _insert_cognition(conn)
    conn.execute(
        "INSERT INTO teachers (name) VALUES ('测试老师')"
    )
    conn.commit()
    conn.execute(
        """
        INSERT INTO teacher_notes (teacher_id, date, title)
        VALUES (1, '2026-04-15', '测试笔记')
        """
    )
    conn.commit()

    _insert_instance(
        conn, cid,
        observed_date="2026-04-15",
        source_type="teacher_note",
        source_note_id=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_instance(
            conn, cid,
            observed_date="2026-04-15",
            source_type="teacher_note",
            source_note_id=1,
        )


def test_periodic_reviews_unique(conn):
    """同 (period_type, period_start, period_end) 第二次插入失败。"""
    conn.execute(
        """
        INSERT INTO periodic_reviews
        (review_id, period_type, period_start, period_end)
        VALUES (?, 'weekly', '2026-04-07', '2026-04-11')
        """,
        (_new_review_id(),),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO periodic_reviews
            (review_id, period_type, period_start, period_end)
            VALUES (?, 'weekly', '2026-04-07', '2026-04-11')
            """,
            (_new_review_id(),),
        )
