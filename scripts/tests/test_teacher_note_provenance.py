from __future__ import annotations

import sqlite3
import threading

import pytest

from db.connection import get_connection
from db.dual_write import record_pending, retry_pending
from db.migrate import (
    CURRENT_SCHEMA_VERSION,
    activate_teacher_note_provenance_v40,
    get_schema_version,
    migrate,
)
from db.schema import init_schema, teacher_note_provenance_indexes_healthy
from db import queries as Q
from services.content_identity import canonical_content_sha256


PROVENANCE_COLUMNS = {
    "source_platform",
    "source_url",
    "source_article_id",
    "published_at",
    "fetched_at",
    "content_sha256",
}

PROVENANCE_INDEXES = {
    "uq_teacher_notes_source_article",
    "uq_teacher_notes_source_url",
    "uq_teacher_notes_content_fallback",
}


def _teacher_note_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")}


def _teacher_note_indexes(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA index_list(teacher_notes)")}


def _create_legacy_teacher_schema(
    conn: sqlite3.Connection,
    *,
    provenance_columns: tuple[str, ...] = (),
) -> None:
    extra = "".join(f",\n        {column} TEXT" for column in provenance_columns)
    conn.executescript(
        f"""
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            platform TEXT,
            schedule TEXT
        );
        CREATE TABLE teacher_notes (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER REFERENCES teachers(id),
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            source_type TEXT DEFAULT 'text',
            input_by TEXT,
            core_view TEXT,
            position_advice TEXT,
            obsidian_path TEXT,
            tags TEXT,
            key_points TEXT,
            sectors TEXT,
            avoid TEXT,
            raw_content TEXT,
            mentioned_stocks TEXT,
            created_at TEXT DEFAULT (datetime('now'))
            {extra}
        );
        """
    )


def test_v40_has_provenance_columns_and_partial_unique_indexes(tmp_path) -> None:
    conn = get_connection(tmp_path / "fresh.db")
    migrate(conn)

    assert CURRENT_SCHEMA_VERSION == 40
    assert get_schema_version(conn) == 40
    assert PROVENANCE_COLUMNS <= _teacher_note_columns(conn)
    assert PROVENANCE_INDEXES <= _teacher_note_indexes(conn)
    conn.close()


def test_user_version_zero_with_application_table_requires_explicit_migration(
    tmp_path,
) -> None:
    conn = get_connection(tmp_path / "unversioned-legacy.db")
    conn.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY)")
    conn.commit()

    with pytest.raises(sqlite3.OperationalError, match="unversioned non-empty"):
        migrate(conn)

    assert get_schema_version(conn) == 0
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teacher_notes'"
    ).fetchone() is None
    conn.close()


def test_v39_fallback_repairs_do_not_create_v40_indexes(tmp_path) -> None:
    conn = get_connection(tmp_path / "v39-partial-provenance.db")
    _create_legacy_teacher_schema(
        conn,
        provenance_columns=tuple(sorted(PROVENANCE_COLUMNS)),
    )
    conn.execute("PRAGMA user_version = 39")
    conn.commit()

    migrate(conn)

    assert get_schema_version(conn) == 39
    assert not (PROVENANCE_INDEXES & _teacher_note_indexes(conn))
    conn.close()


def test_dedicated_v40_activation_rejects_v38_without_mutation(tmp_path) -> None:
    conn = get_connection(tmp_path / "v38-dedicated-rejected.db")
    _create_legacy_teacher_schema(conn)
    conn.execute("PRAGMA user_version = 38")
    conn.commit()
    before_columns = _teacher_note_columns(conn)

    with pytest.raises(sqlite3.OperationalError, match="requires schema version 39 or 40"):
        activate_teacher_note_provenance_v40(conn)

    assert get_schema_version(conn) == 38
    assert _teacher_note_columns(conn) == before_columns
    assert not (PROVENANCE_INDEXES & _teacher_note_indexes(conn))
    conn.close()


@pytest.mark.parametrize("source_version", [38, 39])
def test_legacy_migration_preserves_note_and_adds_provenance(
    tmp_path, source_version: int
) -> None:
    conn = get_connection(tmp_path / f"v{source_version}.db")
    _create_legacy_teacher_schema(conn)
    conn.execute(
        "INSERT INTO teachers (id, name, platform) VALUES (7, '旧老师', '微信')"
    )
    conn.execute(
        """
        INSERT INTO teacher_notes
        (id, teacher_id, date, title, source_type, input_by, core_view, raw_content)
        VALUES (11, 7, '2026-07-01', '旧笔记', 'text', 'manual', '旧观点', '旧原文')
        """
    )
    conn.execute(f"PRAGMA user_version = {source_version}")
    conn.commit()

    migrate(conn, activate_v40=True)

    row = conn.execute("SELECT * FROM teacher_notes WHERE id = 11").fetchone()
    assert row is not None
    preserved = {
        "id": 11,
        "teacher_id": 7,
        "date": "2026-07-01",
        "title": "旧笔记",
        "source_type": "text",
        "input_by": "manual",
        "core_view": "旧观点",
        "raw_content": "旧原文",
    }
    assert {key: row[key] for key in preserved} == preserved
    assert all(row[column] is None for column in PROVENANCE_COLUMNS)
    assert get_schema_version(conn) == 40
    assert PROVENANCE_INDEXES <= _teacher_note_indexes(conn)
    conn.close()


def test_init_schema_skips_provenance_indexes_for_legacy_teacher_notes(tmp_path) -> None:
    conn = get_connection(tmp_path / "legacy-init.db")
    _create_legacy_teacher_schema(conn)

    init_schema(conn)

    assert not (PROVENANCE_INDEXES & _teacher_note_indexes(conn))
    conn.close()


def test_v40_drift_repairs_missing_column_and_indexes(tmp_path) -> None:
    conn = get_connection(tmp_path / "v40-drift.db")
    _create_legacy_teacher_schema(
        conn,
        provenance_columns=(
            "source_platform",
            "source_url",
            "source_article_id",
            "published_at",
            "fetched_at",
        ),
    )
    conn.execute("PRAGMA user_version = 40")
    conn.commit()

    activate_teacher_note_provenance_v40(conn)

    assert PROVENANCE_COLUMNS <= _teacher_note_columns(conn)
    assert PROVENANCE_INDEXES <= _teacher_note_indexes(conn)
    assert get_schema_version(conn) == 40
    conn.close()


@pytest.mark.parametrize(
    ("index_name", "replacement_sql"),
    [
        (
            "uq_teacher_notes_source_url",
            """
            CREATE INDEX uq_teacher_notes_source_url
            ON teacher_notes(source_url)
            WHERE source_url IS NOT NULL AND TRIM(source_url) <> ''
            """,
        ),
        (
            "uq_teacher_notes_source_url",
            """
            CREATE UNIQUE INDEX uq_teacher_notes_source_url
            ON teacher_notes(source_url)
            """,
        ),
        (
            "uq_teacher_notes_source_url",
            """
            CREATE UNIQUE INDEX uq_teacher_notes_source_url
            ON teacher_notes(title)
            WHERE source_url IS NOT NULL AND TRIM(source_url) <> ''
            """,
        ),
        (
            "uq_teacher_notes_source_url",
            """
            CREATE UNIQUE INDEX uq_teacher_notes_source_url
            ON teacher_notes(source_url)
            WHERE source_url IS NOT NULL
            """,
        ),
        (
            "uq_teacher_notes_source_article",
            """
            CREATE UNIQUE INDEX uq_teacher_notes_source_article
            ON teacher_notes(source_article_id, source_platform)
            WHERE source_platform IS NOT NULL AND TRIM(source_platform) <> ''
              AND source_article_id IS NOT NULL AND TRIM(source_article_id) <> ''
            """,
        ),
        (
            "uq_teacher_notes_source_url",
            """
            CREATE TABLE decoy_urls (source_url TEXT);
            CREATE UNIQUE INDEX uq_teacher_notes_source_url
            ON decoy_urls(source_url)
            WHERE source_url IS NOT NULL AND TRIM(source_url) <> '';
            """,
        ),
    ],
    ids=["unique", "partial", "columns", "where", "column-order", "other-table"],
)
def test_v40_wrong_same_name_index_requires_explicit_atomic_repair(
    tmp_path,
    index_name: str,
    replacement_sql: str,
) -> None:
    conn = get_connection(tmp_path / f"wrong-{index_name}.db")
    migrate(conn)
    conn.execute(f'DROP INDEX "{index_name}"')
    conn.executescript(replacement_sql)
    conn.commit()
    before_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()[0]

    assert teacher_note_provenance_indexes_healthy(conn) is False
    with pytest.raises(sqlite3.OperationalError, match="v40 drift"):
        migrate(conn)
    after_rejected_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()[0]
    assert after_rejected_sql == before_sql

    activate_teacher_note_provenance_v40(conn)

    assert teacher_note_provenance_indexes_healthy(conn) is True
    assert get_schema_version(conn) == 40
    conn.close()


def test_v40_drift_index_failure_rolls_back_whole_repair(tmp_path) -> None:
    conn = get_connection(tmp_path / "v40-duplicate.db")
    _create_legacy_teacher_schema(
        conn,
        provenance_columns=tuple(sorted(PROVENANCE_COLUMNS)),
    )
    conn.execute("INSERT INTO teachers (id, name) VALUES (1, '重复老师')")
    for note_id, article_id in ((1, "article-a"), (2, "article-b")):
        conn.execute(
            """
            INSERT INTO teacher_notes
            (id, teacher_id, date, title, raw_content, source_platform,
             source_url, source_article_id, published_at, fetched_at, content_sha256)
            VALUES (?, 1, '2026-07-01', ?, '正文', 'wechat_mp',
                    'https://mp.weixin.qq.com/s/same', ?,
                    '2026-07-01T20:00:00+08:00',
                    '2026-07-01T21:00:00+08:00', ?)
            """,
            (note_id, f"笔记{note_id}", article_id, f"hash-{note_id}"),
        )
    conn.execute("PRAGMA user_version = 40")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        activate_teacher_note_provenance_v40(conn)

    assert not (PROVENANCE_INDEXES & _teacher_note_indexes(conn))
    assert get_schema_version(conn) == 40
    conn.close()


def test_healthy_v40_migrate_does_not_commit_caller_transaction(tmp_path) -> None:
    db_path = tmp_path / "healthy-v40.db"
    conn = get_connection(db_path)
    migrate(conn)
    teacher_id = conn.execute(
        "INSERT INTO teachers (name) VALUES ('事务老师')"
    ).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO teacher_notes (teacher_id, date, title) VALUES (?, '2026-07-01', '未提交')",
        (teacher_id,),
    )

    migrate(conn)
    conn.rollback()

    count = conn.execute(
        "SELECT COUNT(*) FROM teacher_notes WHERE title = '未提交'"
    ).fetchone()[0]
    assert count == 0
    conn.close()


def _service_api():
    try:
        from services.teacher_note_service import (
            TeacherNoteProvenanceConflict,
            create_teacher_note_idempotent,
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"teacher note provenance service is missing: {exc}")
    return TeacherNoteProvenanceConflict, create_teacher_note_idempotent


def _source_payload(
    *,
    raw_content: str = "原文\n",
    article_id: str = "article-1",
    source_url: str = "https://mp.weixin.qq.com/s/example",
    title: str = "盘后复盘",
    note_date: str = "2026-07-13",
) -> dict[str, object]:
    return {
        "date": note_date,
        "title": title,
        "raw_content": raw_content,
        "source_platform": "wechat_mp",
        "source_url": source_url,
        "source_article_id": article_id,
        "published_at": "2026-07-13T20:00:00+08:00",
        "fetched_at": "2026-07-13T22:15:00+08:00",
        "content_sha256": canonical_content_sha256(raw_content),
        "input_by": "codex_automation",
    }


@pytest.fixture
def service_conn(tmp_path):
    conn = get_connection(tmp_path / "service.db")
    migrate(conn)
    yield conn
    conn.close()


def test_source_note_write_is_idempotent_by_article_id(service_conn) -> None:
    _, create_note = _service_api()
    payload = _source_payload()

    first = create_note(
        service_conn, teacher_name="安静拆主线", payload=payload
    )
    duplicate = create_note(
        service_conn, teacher_name="安静拆主线", payload=payload
    )

    assert first.created is True
    assert duplicate.note_id == first.note_id
    assert duplicate.created is False
    assert duplicate.matched_by == "source_article_id"
    assert service_conn.execute(
        "SELECT COUNT(*) FROM teacher_notes"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.pop("source_url"), "complete"),
        (lambda p: p.__setitem__("published_at", "2026-07-13T20:00:00"), "offset"),
        (lambda p: p.__setitem__("fetched_at", "2026-07-13T22:15:00"), "offset"),
        (lambda p: p.__setitem__("date", "2026-07-14"), "Asia/Shanghai"),
        (lambda p: p.__setitem__("content_sha256", "0" * 64), "hash"),
        (lambda p: p.pop("input_by"), "input_by"),
    ],
)
def test_source_note_rejects_invalid_provenance_bundle(
    service_conn, mutate, message: str
) -> None:
    _, create_note = _service_api()
    payload = _source_payload()
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        create_note(service_conn, teacher_name="安静拆主线", payload=payload)

    assert service_conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == 0
    assert service_conn.execute("SELECT COUNT(*) FROM teacher_notes").fetchone()[0] == 0


def test_source_note_trims_identity_and_stores_canonical_content(service_conn) -> None:
    _, create_note = _service_api()
    payload = _source_payload(raw_content="  原文\r\n\r\n")
    payload["source_platform"] = "  wechat_mp "
    payload["source_url"] = " https://mp.weixin.qq.com/s/example "
    payload["source_article_id"] = " article-1 "
    payload["content_sha256"] = canonical_content_sha256("  原文\r\n\r\n")

    result = create_note(
        service_conn, teacher_name="安静拆主线", payload=payload
    )

    row = service_conn.execute(
        "SELECT * FROM teacher_notes WHERE id = ?", (result.note_id,)
    ).fetchone()
    assert row["source_platform"] == "wechat_mp"
    assert row["source_url"] == "https://mp.weixin.qq.com/s/example"
    assert row["source_article_id"] == "article-1"
    assert row["raw_content"] == "原文\n"


def test_same_source_with_changed_content_is_a_conflict(service_conn) -> None:
    conflict_type, create_note = _service_api()
    create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(),
    )
    changed = _source_payload(raw_content="内容已经变化")

    with pytest.raises(conflict_type, match="source_content_changed"):
        create_note(service_conn, teacher_name="安静拆主线", payload=changed)


def test_article_id_and_url_matching_different_notes_is_ambiguous(service_conn) -> None:
    conflict_type, create_note = _service_api()
    create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(article_id="article-a", source_url="https://mp.weixin.qq.com/s/a"),
    )
    create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(
            raw_content="第二篇",
            article_id="article-b",
            source_url="https://mp.weixin.qq.com/s/b",
            title="第二篇",
        ),
    )
    ambiguous = _source_payload(
        article_id="article-a",
        source_url="https://mp.weixin.qq.com/s/b",
    )

    with pytest.raises(conflict_type, match="ambiguous_provenance"):
        create_note(service_conn, teacher_name="安静拆主线", payload=ambiguous)


def test_source_note_falls_back_to_teacher_date_title_and_hash(service_conn) -> None:
    _, create_note = _service_api()
    first = create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(article_id="article-a", source_url="https://mp.weixin.qq.com/s/a"),
    )
    duplicate = create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(article_id="article-b", source_url="https://mp.weixin.qq.com/s/b"),
    )

    assert duplicate.note_id == first.note_id
    assert duplicate.created is False
    assert duplicate.matched_by == "content_fallback"


def test_manual_notes_without_provenance_keep_repeat_insert_semantics(service_conn) -> None:
    _, create_note = _service_api()
    payload = {
        "date": "2026-07-13",
        "title": "人工笔记",
        "raw_content": "人工原文",
        "input_by": "manual",
    }

    first = create_note(service_conn, teacher_name="人工老师", payload=payload)
    second = create_note(service_conn, teacher_name="人工老师", payload=payload)

    assert first.created is True
    assert second.created is True
    assert first.note_id != second.note_id


def test_duplicate_does_not_create_a_different_teacher(service_conn) -> None:
    _, create_note = _service_api()
    first = create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(),
    )

    duplicate = create_note(
        service_conn,
        teacher_name="误写老师名",
        payload=_source_payload(),
    )

    assert duplicate.note_id == first.note_id
    assert duplicate.created is False
    assert service_conn.execute(
        "SELECT COUNT(*) FROM teachers WHERE name = '误写老师名'"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("field", ["date", "raw_content", "source_url"])
def test_sourced_note_audit_identity_cannot_be_updated(service_conn, field: str) -> None:
    _, create_note = _service_api()
    result = create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(),
    )

    with pytest.raises(ValueError, match="immutable"):
        Q.update_teacher_note(service_conn, result.note_id, **{field: "changed"})


def test_manual_note_date_and_raw_content_remain_editable(service_conn) -> None:
    _, create_note = _service_api()
    result = create_note(
        service_conn,
        teacher_name="人工老师",
        payload={"date": "2026-07-01", "title": "人工", "input_by": "manual"},
    )

    Q.update_teacher_note(
        service_conn,
        result.note_id,
        date="2026-07-02",
        raw_content="补充原文",
    )

    row = service_conn.execute(
        "SELECT date, raw_content FROM teacher_notes WHERE id = ?",
        (result.note_id,),
    ).fetchone()
    assert row["date"] == "2026-07-02"
    assert row["raw_content"] == "补充原文"


def test_service_does_not_commit_or_rollback_caller_transaction(service_conn) -> None:
    conflict_type, create_note = _service_api()
    created = create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(),
    )
    service_conn.rollback()
    assert service_conn.execute(
        "SELECT COUNT(*) FROM teacher_notes WHERE id = ?", (created.note_id,)
    ).fetchone()[0] == 0

    create_note(
        service_conn,
        teacher_name="安静拆主线",
        payload=_source_payload(),
    )
    service_conn.commit()
    service_conn.execute("INSERT INTO teachers (name) VALUES ('调用方未提交')")
    with pytest.raises(conflict_type):
        create_note(
            service_conn,
            teacher_name="安静拆主线",
            payload=_source_payload(raw_content="变化内容"),
        )
    assert service_conn.execute(
        "SELECT COUNT(*) FROM teachers WHERE name = '调用方未提交'"
    ).fetchone()[0] == 1


def test_concurrent_source_writes_recover_from_unique_race(tmp_path) -> None:
    _, create_note = _service_api()
    db_path = tmp_path / "concurrent.db"
    setup = get_connection(db_path)
    migrate(setup)
    setup.close()
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def writer() -> None:
        conn = get_connection(db_path)
        try:
            barrier.wait(timeout=5)
            result = create_note(
                conn,
                teacher_name="并发老师",
                payload=_source_payload(),
            )
            conn.commit()
            results.append(result)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
            conn.rollback()
        finally:
            conn.close()

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert len(results) == 2
    assert {result.created for result in results} == {True, False}
    assert len({result.note_id for result in results}) == 1
    check = get_connection(db_path)
    assert check.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == 1
    assert check.execute("SELECT COUNT(*) FROM teacher_notes").fetchone()[0] == 1
    check.close()


def test_retry_pending_uses_idempotent_service_for_complete_provenance(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "pending.db"
    pending_path = tmp_path / "pending.json"
    monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", pending_path)
    setup = get_connection(db_path)
    migrate(setup)
    setup.close()
    payload = _source_payload(raw_content="  待重试原文\r\n")
    record_pending(
        "teacher_notes",
        {"teacher_name": "安静拆主线", **payload},
        "initial failure",
    )

    succeeded, failed = retry_pending(db_path=db_path)

    assert (succeeded, failed) == (1, 0)
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM teacher_notes").fetchone()
    assert row["raw_content"] == "待重试原文\n"
    assert row["source_article_id"] == "article-1"
    conn.close()


def test_retry_pending_treats_duplicate_provenance_as_success(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "pending-duplicate.db"
    pending_path = tmp_path / "pending-duplicate.json"
    monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", pending_path)
    conn = get_connection(db_path)
    migrate(conn)
    _, create_note = _service_api()
    create_note(conn, teacher_name="安静拆主线", payload=_source_payload())
    conn.commit()
    conn.close()
    record_pending(
        "teacher_notes",
        {"teacher_name": "安静拆主线", **_source_payload()},
        "retry",
    )

    succeeded, failed = retry_pending(db_path=db_path)

    assert (succeeded, failed) == (1, 0)
    check = get_connection(db_path)
    assert check.execute("SELECT COUNT(*) FROM teacher_notes").fetchone()[0] == 1
    check.close()


def test_retry_pending_rejects_partial_provenance_but_keeps_legacy_notes(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "pending-mixed.db"
    pending_path = tmp_path / "pending-mixed.json"
    monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", pending_path)
    setup = get_connection(db_path)
    migrate(setup)
    setup.close()
    record_pending(
        "teacher_notes",
        {
            "teacher_name": "部分来源",
            "date": "2026-07-13",
            "title": "不能静默降级",
            "source_url": "https://mp.weixin.qq.com/s/partial",
        },
        "retry",
    )
    record_pending(
        "teacher_notes",
        {
            "teacher_name": "历史老师",
            "date": "2026-07-13",
            "title": "历史无来源",
            "input_by": "manual",
        },
        "retry",
    )

    succeeded, failed = retry_pending(db_path=db_path)

    assert (succeeded, failed) == (1, 1)
    conn = get_connection(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM teacher_notes WHERE title = '历史无来源'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM teacher_notes WHERE title = '不能静默降级'"
    ).fetchone()[0] == 0
    conn.close()
