"""CLI 命令集成测试：使用 TRADE_DB_PATH 隔离测试 DB，不污染生产数据。"""
from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from db.cli import _coerce_holdings_cost, _coerce_holdings_shares
from db.connection import get_connection
from db.migrate import migrate
from services.content_identity import canonical_content_sha256

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = SCRIPTS_DIR / "main.py"


def _run_cli(*args: str, tmp_db: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "TRADE_DB_PATH": tmp_db}
    return subprocess.run(
        [sys.executable, str(MAIN_PY), "db", *args],
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR), env=env, input=input_text,
    )


@pytest.fixture
def tmp_db(tmp_path) -> str:
    return str(tmp_path / "test_cli.db")


def _source_note_args(
    *,
    raw_content: str = "公众号原文\n",
    article_id: str = "article-cli-1",
    source_url: str = "https://mp.weixin.qq.com/s/cli-example",
) -> list[str]:
    return [
        "--teacher", "安静拆主线",
        "--date", "2026-07-13",
        "--title", "盘后复盘",
        "--raw-content", raw_content,
        "--source-platform", "wechat_mp",
        "--source-url", source_url,
        "--source-article-id", article_id,
        "--published-at", "2026-07-13T20:00:00+08:00",
        "--fetched-at", "2026-07-13T22:15:00+08:00",
        "--content-sha256", canonical_content_sha256(raw_content),
        "--input-by", "codex_automation",
    ]


def _create_versioned_db(path: str, version: int = 39) -> None:
    conn = get_connection(path)
    migrate(conn)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def _create_legacy_v39_teacher_db(
    path: str | Path,
    *,
    provenance_columns: tuple[str, ...] = (),
) -> None:
    extra_columns = "".join(
        f"            {column} TEXT,\n" for column in provenance_columns
    )
    conn = get_connection(path)
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
{extra_columns}
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO teachers (id, name) VALUES (1, '旧老师');
        INSERT INTO teacher_notes
            (teacher_id, date, title, core_view, key_points, sectors, avoid, raw_content)
        VALUES
            (1, '2026-07-01', '旧笔记', '旧观点', '[]', '[]', '[]', '旧原文');
        PRAGMA user_version = 39;
        """
    )
    conn.commit()
    conn.close()


def _read_user_version(path: str | Path) -> int:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# ── 管理命令 ──────────────────────────────────────────────────────

class TestInit:
    def test_init(self, tmp_db):
        result = _run_cli("init", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "初始化完成" in result.stdout


class TestSync:
    def test_sync_no_pending(self, tmp_db):
        _run_cli("init", tmp_db=tmp_db)
        result = _run_cli("sync", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "重试完成" in result.stdout


class TestBackupMigrate:
    def test_backup_requires_input_by(self, tmp_db, tmp_path):
        _create_versioned_db(tmp_db)
        result = _run_cli(
            "backup", "--output", str(tmp_path / "backup.db"),
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "--input-by" in result.stderr

    def test_backup_is_exclusive_private_verified_and_read_only_for_source(
        self, tmp_db, tmp_path
    ):
        _create_versioned_db(tmp_db)
        backup_path = tmp_path / "nested" / "trade-v39.db"

        result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert backup_path.is_file()
        assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
        assert receipt["backup_path"] == str(backup_path.resolve())
        assert receipt["source_version"] == 39
        assert receipt["integrity_check"] == "ok"
        assert len(receipt["sha256"]) == 64
        assert receipt["mode"] == "0600"
        assert _read_user_version(tmp_db) == 39
        assert _read_user_version(backup_path) == 39

        before = backup_path.read_bytes()
        repeated = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert repeated.returncode != 0
        assert backup_path.read_bytes() == before

    @pytest.mark.parametrize("backup_kind", ["missing", "corrupt"])
    def test_migrate_rejects_unverified_backup_before_writing_source(
        self, tmp_db, tmp_path, backup_kind
    ):
        _create_versioned_db(tmp_db)
        backup_path = tmp_path / "required-backup.db"
        if backup_kind == "corrupt":
            backup_path.write_bytes(b"not sqlite")

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert _read_user_version(tmp_db) == 39

    def test_migrate_requires_input_by(self, tmp_db, tmp_path):
        _create_versioned_db(tmp_db)
        result = _run_cli(
            "migrate", "--require-backup", str(tmp_path / "backup.db"),
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "--input-by" in result.stderr

    def test_migrate_rejects_backup_from_a_different_schema_version(
        self, tmp_db, tmp_path
    ):
        _create_versioned_db(tmp_db, version=39)
        backup_path = tmp_path / "unrelated-v38.db"
        _create_versioned_db(str(backup_path), version=38)
        backup_path.chmod(0o600)

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert "版本" in result.stderr
        assert _read_user_version(tmp_db) == 39

    def test_migrate_rejects_backup_from_different_same_version_database(
        self, tmp_db, tmp_path
    ):
        _create_versioned_db(tmp_db, version=39)
        other_db = tmp_path / "other-v39.db"
        _create_versioned_db(str(other_db), version=39)
        conn = get_connection(other_db)
        conn.execute("INSERT INTO teachers (name) VALUES ('另一个数据库')")
        conn.commit()
        conn.close()
        backup_path = tmp_path / "other-v39-backup.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=str(other_db),
        )
        assert backup_result.returncode == 0, backup_result.stderr

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert "快照" in result.stderr
        assert _read_user_version(tmp_db) == 39

    def test_migrate_rejects_source_changed_after_backup(self, tmp_db, tmp_path):
        _create_versioned_db(tmp_db, version=39)
        backup_path = tmp_path / "before-change.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert backup_result.returncode == 0, backup_result.stderr
        conn = get_connection(tmp_db)
        conn.execute("INSERT INTO teachers (name) VALUES ('备份后新增')")
        conn.commit()
        conn.close()

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert "快照" in result.stderr
        assert _read_user_version(tmp_db) == 39

    def test_migrate_rejects_backup_mode_other_than_0600(self, tmp_db, tmp_path):
        _create_versioned_db(tmp_db, version=39)
        backup_path = tmp_path / "permissive-backup.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert backup_result.returncode == 0, backup_result.stderr
        backup_path.chmod(0o644)

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert "0600" in result.stderr
        assert _read_user_version(tmp_db) == 39

    def test_v40_migrate_ignores_unrelated_v39_drift_view(
        self, tmp_db, tmp_path
    ):
        _create_legacy_v39_teacher_db(tmp_db)
        conn = get_connection(tmp_db)
        conn.execute(
            "CREATE VIEW daily_review_factor_score_requests "
            "AS SELECT 'legacy-view' AS marker"
        )
        conn.commit()
        view_sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='view' AND name='daily_review_factor_score_requests'"
        ).fetchone()[0]
        conn.close()
        backup_path = tmp_path / "v39-with-unrelated-view.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert backup_result.returncode == 0, backup_result.stderr

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode == 0, result.stderr
        assert _read_user_version(tmp_db) == 40
        conn = get_connection(tmp_db)
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")
            }
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(teacher_notes)")
            }
            after_view = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='view' AND name='daily_review_factor_score_requests'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert {
            "source_platform", "source_url", "source_article_id",
            "published_at", "fetched_at", "content_sha256",
        } <= columns
        assert {
            "uq_teacher_notes_source_article",
            "uq_teacher_notes_source_url",
            "uq_teacher_notes_content_fallback",
        } <= indexes
        assert after_view == view_sql

    def test_v40_internal_index_failure_rolls_back_version_columns_and_indexes(
        self, tmp_db, tmp_path
    ):
        _create_legacy_v39_teacher_db(
            tmp_db,
            provenance_columns=("source_url",),
        )
        conn = get_connection(tmp_db)
        conn.execute(
            "UPDATE teacher_notes SET source_url='https://example.test/duplicate'"
        )
        conn.execute(
            """
            INSERT INTO teacher_notes
                (teacher_id, date, title, core_view, source_url)
            VALUES
                (1, '2026-07-02', '重复来源', '另一条观点',
                 'https://example.test/duplicate')
            """
        )
        conn.commit()
        before_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")
        }
        conn.close()
        backup_path = tmp_path / "v39-duplicate-url.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert backup_result.returncode == 0, backup_result.stderr

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode != 0
        assert _read_user_version(tmp_db) == 39
        conn = get_connection(tmp_db)
        try:
            after_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")
            }
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(teacher_notes)")
            }
        finally:
            conn.close()
        assert after_columns == before_columns
        assert not {
            "uq_teacher_notes_source_article",
            "uq_teacher_notes_source_url",
            "uq_teacher_notes_content_fallback",
        } & indexes

    def test_migrate_requires_verified_backup_and_reports_v40(self, tmp_db, tmp_path):
        _create_versioned_db(tmp_db)
        backup_path = tmp_path / "trade-v39.db"
        backup_result = _run_cli(
            "backup", "--output", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )
        assert backup_result.returncode == 0, backup_result.stderr
        backup_sha = json.loads(backup_result.stdout)["sha256"]

        result = _run_cli(
            "migrate", "--require-backup", str(backup_path),
            "--input-by", "codex_automation", "--json",
            tmp_db=tmp_db,
        )

        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt == {
            "before_version": 39,
            "after_version": 40,
            "changed": True,
            "backup_path": str(backup_path.resolve()),
            "backup_sha256": backup_sha,
            "verified_backup_mode": "0600",
            "source_snapshot_sha256": backup_sha,
            "verified_columns": True,
            "verified_indexes": True,
            "input_by": "codex_automation",
        }
        assert _read_user_version(tmp_db) == 40
        assert _read_user_version(backup_path) == 39


class TestReadOnlyEntryPoints:
    def test_query_notes_does_not_activate_v40_on_v39_database(self, tmp_db):
        _create_legacy_v39_teacher_db(tmp_db)

        result = _run_cli("query-notes", "--keyword", "旧观点", tmp_db=tmp_db)

        assert result.returncode == 0, result.stderr
        assert "旧笔记" in result.stdout
        assert _read_user_version(tmp_db) == 39
        conn = get_connection(tmp_db)
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")
            }
        finally:
            conn.close()
        assert "source_url" not in columns


# ── 老师观点 ──────────────────────────────────────────────────────

class TestAddNote:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "测试老师",
            "--date", "2026-04-01", "--title", "CLI测试笔记",
            "--core-view", "这是一条测试笔记",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入笔记" in result.stdout

    def test_new_teacher_auto_created(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "新老师_CLI",
            "--date", "2026-04-01", "--title", "自动创建",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0

    def test_missing_required_field(self, tmp_db):
        result = _run_cli("add-note", "--teacher", "test", tmp_db=tmp_db)
        assert result.returncode != 0

    def test_missing_input_by_is_rejected(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "测试老师",
            "--date", "2026-04-01", "--title", "缺审计字段",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "--input-by" in result.stderr

    def test_source_provenance_is_idempotent(self, tmp_db):
        args = _source_note_args()

        first = _run_cli("add-note", *args, tmp_db=tmp_db)
        duplicate = _run_cli("add-note", *args, tmp_db=tmp_db)

        assert first.returncode == 0, first.stderr
        assert "created=true" in first.stdout
        assert duplicate.returncode == 0, duplicate.stderr
        assert "created=false" in duplicate.stdout
        assert "deduplicated_by=source_article_id" in duplicate.stdout
        conn = get_connection(tmp_db)
        rows = conn.execute(
            "SELECT * FROM teacher_notes WHERE source_article_id='article-cli-1'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["source_platform"] == "wechat_mp"
        assert rows[0]["raw_content"] == "公众号原文\n"

    def test_source_duplicate_has_no_attachment_or_watchlist_side_effects(
        self, tmp_db, tmp_path
    ):
        attachment = tmp_path / "dedupe-attachment.txt"
        attachment.write_text("attachment", encoding="utf-8")
        stocks = json.dumps([
            {"code": "688998", "name": "幂等测试", "tier": "tier2_watch"},
        ])
        args = [
            *_source_note_args(article_id="article-cli-side-effect"),
            "--attachment", str(attachment),
            "--stocks", stocks,
            "--sync-watchlist-from-stocks",
        ]

        first = _run_cli("add-note", *args, tmp_db=tmp_db)
        duplicate = _run_cli("add-note", *args, tmp_db=tmp_db)

        assert first.returncode == 0, first.stderr
        assert duplicate.returncode == 0, duplicate.stderr
        assert "created=false" in duplicate.stdout
        conn = get_connection(tmp_db)
        assert conn.execute("SELECT COUNT(*) FROM teacher_notes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM note_attachments").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE stock_code='688998'"
        ).fetchone()[0] == 1
        conn.close()

    def test_same_source_changed_content_is_nonzero(self, tmp_db):
        first = _run_cli("add-note", *_source_note_args(), tmp_db=tmp_db)
        assert first.returncode == 0, first.stderr

        changed = _run_cli(
            "add-note", *_source_note_args(raw_content="被修改的原文"),
            tmp_db=tmp_db,
        )

        assert changed.returncode != 0
        assert "source_content_changed" in changed.stderr

    def test_single_attachment(self, tmp_db, tmp_path):
        img = tmp_path / "note.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        result = _run_cli(
            "add-note", "--teacher", "附件老师",
            "--date", "2026-04-01", "--title", "单附件",
            "--source-type", "image",
            "--attachment", str(img),
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "附件 1 个" in result.stdout

    def test_multi_attachment(self, tmp_db, tmp_path):
        imgs = []
        for i in range(3):
            p = tmp_path / f"img{i}.jpg"
            p.write_bytes(b"\xff\xd8\xff")
            imgs.append(str(p))
        result = _run_cli(
            "add-note", "--teacher", "多图老师",
            "--date", "2026-04-01", "--title", "多附件测试",
            "--source-type", "mixed",
            "--core-view", "图文并茂",
            "--attachment", *imgs,
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "附件 3 个" in result.stdout

    def test_nonexistent_attachment_skipped(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "老师",
            "--date", "2026-04-01", "--title", "不存在附件",
            "--attachment", "/nonexistent/file.jpg",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "附件" not in result.stdout or "附件 0 个" not in result.stdout

    def test_raw_content_file(self, tmp_db, tmp_path):
        content_file = tmp_path / "ocr.txt"
        content_file.write_text("这是一段很长的 OCR/PDF 提取内容", encoding="utf-8")
        result = _run_cli(
            "add-note", "--teacher", "文件老师",
            "--date", "2026-04-01", "--title", "文件原文",
            "--raw-content-file", str(content_file),
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT raw_content FROM teacher_notes WHERE title = ?",
            ("文件原文",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "这是一段很长的 OCR/PDF 提取内容"

    def test_raw_content_stdin(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "标准输入老师",
            "--date", "2026-04-01", "--title", "标准输入原文",
            "--raw-content-file", "-",
            "--input-by", "manual",
            tmp_db=tmp_db,
            input_text="通过 stdin 写入的长文本",
        )
        assert result.returncode == 0
        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT raw_content FROM teacher_notes WHERE title = ?",
            ("标准输入原文",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "通过 stdin 写入的长文本"

    def test_raw_content_and_file_are_mutually_exclusive(self, tmp_db, tmp_path):
        content_file = tmp_path / "ocr.txt"
        content_file.write_text("内容", encoding="utf-8")
        result = _run_cli(
            "add-note", "--teacher", "老师",
            "--date", "2026-04-01", "--title", "冲突参数",
            "--raw-content", "直接参数",
            "--raw-content-file", str(content_file),
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "--raw-content-file" in result.stderr
        assert "not allowed with argument --raw-content" in result.stderr


class TestQueryNotes:
    def test_keyword_search(self, tmp_db):
        _run_cli(
            "add-note", "--teacher", "搜索测试老师",
            "--date", "2026-04-01", "--title", "锂电板块分析CLI",
            "--core-view", "锂电储能看好",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        result = _run_cli("query-notes", "--keyword", "锂电", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "锂电" in result.stdout

    def test_no_match(self, tmp_db):
        result = _run_cli("query-notes", "--keyword", "完全不存在的关键词XYZ123", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未找到" in result.stdout


class TestUpdateNote:
    def test_update_structured_fields(self, tmp_db):
        add = _run_cli(
            "add-note", "--teacher", "测试老师",
            "--date", "2026-04-01", "--title", "旧标题",
            "--core-view", "旧观点",
            "--key-points", '["旧要点A","旧要点B"]',
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert add.returncode == 0
        conn = get_connection(tmp_db)
        note_id = conn.execute(
            "SELECT id FROM teacher_notes WHERE title = ?", ("旧标题",)
        ).fetchone()[0]
        conn.close()

        result = _run_cli(
            "update-note", "--id", str(note_id),
            "--title", "新标题",
            "--core-view", "新观点",
            "--key-points", '["新要点A","新要点B","新要点C"]',
            "--sectors", '["AI","半导体"]',
            "--input-by", "codex_automation",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已更新老师笔记" in result.stdout

        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT title, core_view, key_points, sectors, input_by FROM teacher_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        conn.close()
        assert row["title"] == "新标题"
        assert row["core_view"] == "新观点"
        assert json.loads(row["key_points"]) == ["新要点A", "新要点B", "新要点C"]
        assert json.loads(row["sectors"]) == ["AI", "半导体"]
        assert row["input_by"] == "codex_automation"

    def test_delete_requires_yes(self, tmp_db):
        add = _run_cli(
            "add-note", "--teacher", "测试老师",
            "--date", "2026-04-01", "--title", "待删",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        assert add.returncode == 0
        conn = get_connection(tmp_db)
        note_id = conn.execute("SELECT id FROM teacher_notes WHERE title = ?", ("待删",)).fetchone()[0]
        conn.close()

        result = _run_cli(
            "delete-note", "--id", str(note_id), "--input-by", "codex_automation",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0

        result = _run_cli(
            "delete-note", "--id", str(note_id), "--input-by", "codex_automation", "--yes",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        conn = get_connection(tmp_db)
        row = conn.execute("SELECT id FROM teacher_notes WHERE id = ?", (note_id,)).fetchone()
        conn.close()
        assert row is None


# ── 行业 / 宏观 ───────────────────────────────────────────────────

class TestAddIndustry:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-industry", "--sector", "AI算力",
            "--date", "2026-04-01",
            "--content", "AI板块持续活跃，资金流入明显",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入行业信息" in result.stdout
        assert "AI算力" in result.stdout

    def test_with_all_fields(self, tmp_db):
        result = _run_cli(
            "add-industry", "--sector", "锂电池",
            "--date", "2026-04-01",
            "--content", "宁德出货量超预期",
            "--info-type", "研报",
            "--source", "华泰证券",
            "--confidence", "高",
            "--tags", '["锂电","储能"]',
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "id=" in result.stdout
        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT input_by FROM industry_info WHERE sector_name = ?",
            ("锂电池",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "cursor"

    def test_missing_required(self, tmp_db):
        result = _run_cli("add-industry", "--sector", "AI", tmp_db=tmp_db)
        assert result.returncode != 0


class TestUpdateIndustry:
    def test_update_structured_fields(self, tmp_db):
        add = _run_cli(
            "add-industry", "--sector", "AI算力",
            "--date", "2026-04-01",
            "--content", "旧内容",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert add.returncode == 0
        conn = get_connection(tmp_db)
        info_id = conn.execute(
            "SELECT id FROM industry_info WHERE sector_name = ?", ("AI算力",)
        ).fetchone()[0]
        conn.close()

        result = _run_cli(
            "update-industry", "--id", str(info_id),
            "--sector", "半导体设备",
            "--content", "新内容",
            "--info-type", "订单",
            "--confidence", "中",
            "--tags", '["半导体","订单"]',
            "--input-by", "codex_automation",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已更新行业信息" in result.stdout

        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT sector_name, content, info_type, confidence, tags, input_by FROM industry_info WHERE id = ?",
            (info_id,),
        ).fetchone()
        conn.close()
        assert row["sector_name"] == "半导体设备"
        assert row["content"] == "新内容"
        assert row["info_type"] == "订单"
        assert row["confidence"] == "中"
        assert json.loads(row["tags"]) == ["半导体", "订单"]
        assert row["input_by"] == "codex_automation"

    def test_delete_requires_yes(self, tmp_db):
        add = _run_cli(
            "add-industry", "--sector", "AI算力",
            "--date", "2026-04-01",
            "--content", "待删内容",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert add.returncode == 0
        conn = get_connection(tmp_db)
        info_id = conn.execute("SELECT id FROM industry_info WHERE sector_name = ?", ("AI算力",)).fetchone()[0]
        conn.close()

        result = _run_cli(
            "delete-industry", "--id", str(info_id), "--input-by", "codex_automation",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0

        result = _run_cli(
            "delete-industry", "--id", str(info_id), "--input-by", "codex_automation", "--yes",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        conn = get_connection(tmp_db)
        row = conn.execute("SELECT id FROM industry_info WHERE id = ?", (info_id,)).fetchone()
        conn.close()
        assert row is None


class TestAddMacro:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-macro", "--category", "货币政策",
            "--date", "2026-04-01",
            "--title", "央行降准25BP",
            "--content", "全面降准释放流动性约6000亿",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入宏观信息" in result.stdout
        assert "央行降准25BP" in result.stdout

    def test_with_impact(self, tmp_db):
        result = _run_cli(
            "add-macro", "--category", "财政",
            "--date", "2026-04-01",
            "--title", "专项债扩容",
            "--content", "新增专项债额度3万亿",
            "--impact", "利好股市，尤其基建板块",
            "--tags", '["财政","基建"]',
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        conn = get_connection(tmp_db)
        row = conn.execute(
            "SELECT input_by FROM macro_info WHERE title = ?",
            ("专项债扩容",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "cursor"


# ── 持仓池 ────────────────────────────────────────────────────────

class TestHoldings:
    def test_add_basic(self, tmp_db):
        result = _run_cli(
            "holdings-add", "--code", "300750",
            "--name", "宁德时代",
            "--shares", "200",
            "--price", "85.0",
            "--sector", "锂电",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已添加持仓" in result.stdout
        assert "宁德时代" in result.stdout

    def test_list_shows_active(self, tmp_db):
        _run_cli(
            "holdings-add", "--code", "000001",
            "--name", "平安银行", "--price", "10.5",
            tmp_db=tmp_db,
        )
        result = _run_cli("holdings-list", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "平安银行" in result.stdout

    def test_remove_sets_closed(self, tmp_db):
        _run_cli(
            "holdings-add", "--code", "688041",
            "--name", "海光信息", "--shares", "100", "--price", "220.0",
            tmp_db=tmp_db,
        )
        result = _run_cli("holdings-remove", "--code", "688041", "--input-by", "cursor", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "已移除持仓" in result.stdout

        result2 = _run_cli("holdings-list", tmp_db=tmp_db)
        assert "海光信息" not in result2.stdout

    def test_remove_nonexistent(self, tmp_db):
        result = _run_cli("holdings-remove", "--code", "999999", "--input-by", "cursor", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未找到" in result.stdout

    def test_remove_requires_input_by(self, tmp_db):
        result = _run_cli("holdings-remove", "--code", "688041", tmp_db=tmp_db)
        assert result.returncode == 2
        assert "--input-by" in result.stderr

    def test_list_empty(self, tmp_db):
        result = _run_cli("holdings-list", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "无持仓" in result.stdout

    def test_add_same_normalized_code_updates_existing_active(self, tmp_db):
        r1 = _run_cli(
            "holdings-add", "--code", "300750",
            "--name", "宁德时代旧", "--shares", "100", "--price", "80.0",
            tmp_db=tmp_db,
        )
        r2 = _run_cli(
            "holdings-add", "--code", "300750.SZ",
            "--name", "宁德时代新", "--shares", "200", "--price", "85.0",
            tmp_db=tmp_db,
        )
        assert r1.returncode == 0
        assert r2.returncode == 0

        with get_connection(tmp_db) as conn:
            rows = conn.execute(
                "SELECT stock_code, stock_name, shares, entry_price, status FROM holdings ORDER BY id"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["stock_code"] == "300750.SZ"
        assert rows[0]["stock_name"] == "宁德时代新"
        assert rows[0]["shares"] == 200
        assert rows[0]["entry_price"] == 85.0
        assert rows[0]["status"] == "active"

    def test_remove_closes_suffix_variant(self, tmp_db):
        _run_cli(
            "holdings-add", "--code", "688041.SH",
            "--name", "海光信息", "--shares", "100", "--price", "220.0",
            tmp_db=tmp_db,
        )
        result = _run_cli("holdings-remove", "--code", "688041", "--input-by", "cursor", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "共 1 条置为 closed" in result.stdout

        with get_connection(tmp_db) as conn:
            row = conn.execute("SELECT status FROM holdings WHERE stock_code = '688041.SH'").fetchone()
        assert row["status"] == "closed"

    def test_entry_reason_and_note_stored_and_shown(self, tmp_db):
        result = _run_cli(
            "holdings-add", "--code", "688041",
            "--name", "海光信息",
            "--shares", "100", "--price", "220.0",
            "--entry-reason", "国产AI链龙头，主线初期",
            "--note", "止损参考前低215",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0

        with get_connection(tmp_db) as conn:
            row = conn.execute(
                "SELECT entry_reason, note FROM holdings WHERE stock_code = '688041'"
            ).fetchone()
        assert row["entry_reason"] == "国产AI链龙头，主线初期"
        assert row["note"] == "止损参考前低215"

        list_result = _run_cli("holdings-list", tmp_db=tmp_db)
        assert list_result.returncode == 0
        assert "买入原因" in list_result.stdout
        assert "国产AI链龙头" in list_result.stdout
        assert "备注" in list_result.stdout
        assert "止损参考前低215" in list_result.stdout

    def test_entry_reason_only_no_note(self, tmp_db):
        _run_cli(
            "holdings-add", "--code", "300750",
            "--name", "宁德时代",
            "--entry-reason", "锂电主线反弹",
            tmp_db=tmp_db,
        )
        list_result = _run_cli("holdings-list", tmp_db=tmp_db)
        assert "买入原因" in list_result.stdout
        assert "锂电主线反弹" in list_result.stdout
        assert "备注" not in list_result.stdout

    def test_add_can_link_thesis_id(self, tmp_db):
        result = _run_cli(
            "holdings-add", "--code", "002245",
            "--name", "蔚蓝锂芯",
            "--shares", "600",
            "--price", "28.32",
            "--thesis-id", "1",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0

        with get_connection(tmp_db) as conn:
            row = conn.execute(
                "SELECT thesis_id FROM holdings WHERE stock_code = '002245'"
            ).fetchone()
        assert row["thesis_id"] == 1


class TestHoldingsImportYaml:
    def test_imports_rows_and_values(self, tmp_db, tmp_path):
        yml = tmp_path / "h.yaml"
        yml.write_text(
            "holdings:\n"
            '  - code: "300750"\n'
            '    name: "宁德时代"\n'
            "    shares: 100\n"
            "    cost: 80.5\n"
            '    sector: "锂电"\n',
            encoding="utf-8",
        )
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "导入 1 条" in result.stdout
        assert "跳过" not in result.stdout

        with get_connection(tmp_db) as conn:
            row = conn.execute(
                "SELECT stock_code, stock_name, shares, entry_price, sector, status FROM holdings WHERE status = 'active'"
            ).fetchone()
        assert row is not None
        assert row["stock_code"] == "300750"
        assert row["stock_name"] == "宁德时代"
        assert row["shares"] == 100
        assert row["entry_price"] == 80.5
        assert row["sector"] == "锂电"

    def test_skips_invalid_rows_with_counts(self, tmp_db, tmp_path):
        yml = tmp_path / "bad.yaml"
        yml.write_text(
            "holdings:\n"
            '  - code: "300750"\n'
            '    name: "好"\n'
            "    shares: 10\n"
            "    cost: 1.0\n"
            '  - code: "688001"\n'
            '    name: "坏股数"\n'
            '    shares: "not_int"\n'
            "    cost: 1.0\n"
            "  - code: \"\"\n"
            '    name: "空代码"\n'
            "    shares: 1\n"
            "    cost: 1.0\n"
            '  - code: "000001"\n'
            '    name: "坏成本"\n'
            "    shares: 100\n"
            '    cost: "x"\n',
            encoding="utf-8",
        )
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "导入 1 条" in result.stdout
        assert "跳过 3 条" in result.stdout
        assert "empty_code" in result.stdout
        assert "invalid_shares" in result.stdout
        assert "invalid_cost" in result.stdout

        with get_connection(tmp_db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM holdings WHERE status = 'active'").fetchone()[0]
        assert n == 1

    def test_root_not_dict_skips_without_crash(self, tmp_db, tmp_path):
        yml = tmp_path / "root_list.yaml"
        yml.write_text("- a\n- b\n", encoding="utf-8")
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "根须为映射" in result.stdout

    def test_holdings_not_list_skips(self, tmp_db, tmp_path):
        yml = tmp_path / "holdings_scalar.yaml"
        yml.write_text('holdings: "oops"\n', encoding="utf-8")
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "holdings 须为序列" in result.stdout

    def test_non_mapping_list_item_skipped(self, tmp_db, tmp_path):
        yml = tmp_path / "mixed.yaml"
        yml.write_text(
            "holdings:\n"
            '  - code: "300750"\n'
            '    name: "宁德"\n'
            "    shares: 10\n"
            "    cost: 1.0\n"
            "  - plain_string\n",
            encoding="utf-8",
        )
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "导入 1 条" in result.stdout
        assert "not_mapping" in result.stdout
        assert "跳过 1 条" in result.stdout

    def test_scientific_notation_string_shares(self, tmp_db, tmp_path):
        yml = tmp_path / "sci.yaml"
        yml.write_text(
            "holdings:\n"
            '  - code: "300750"\n'
            '    name: "宁德"\n'
            '    shares: "1e2"\n'
            "    cost: 80.0\n",
            encoding="utf-8",
        )
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "导入 1 条" in result.stdout
        with get_connection(tmp_db) as conn:
            row = conn.execute("SELECT shares FROM holdings WHERE stock_code = '300750'").fetchone()
        assert row["shares"] == 100

    def test_imports_entry_reason_and_note(self, tmp_db, tmp_path):
        yml = tmp_path / "with_reason.yaml"
        yml.write_text(
            "holdings:\n"
            '  - code: "300750"\n'
            '    name: "宁德时代"\n'
            "    shares: 100\n"
            "    cost: 80.0\n"
            '    entry_reason: "主线龙头，初期启动"\n'
            '    note: "注意量能"\n'
            '  - code: "688041"\n'
            '    name: "海光信息"\n'
            "    shares: 50\n"
            "    cost: 220.0\n"
            '    reason: "国产AI链兼容兜底"\n',
            encoding="utf-8",
        )
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "导入 2 条" in result.stdout

        with get_connection(tmp_db) as conn:
            r1 = conn.execute(
                "SELECT entry_reason, note FROM holdings WHERE stock_code = '300750'"
            ).fetchone()
            r2 = conn.execute(
                "SELECT entry_reason FROM holdings WHERE stock_code = '688041'"
            ).fetchone()
        assert r1["entry_reason"] == "主线龙头，初期启动"
        assert r1["note"] == "注意量能"
        assert r2["entry_reason"] == "国产AI链兼容兜底"

    def test_yaml_syntax_error_does_not_crash(self, tmp_db, tmp_path):
        yml = tmp_path / "broken.yaml"
        yml.write_text("holdings: [\n", encoding="utf-8")
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "YAML 解析失败" in result.stdout

    def test_invalid_utf8_file_does_not_crash(self, tmp_db, tmp_path):
        yml = tmp_path / "bad_enc.yaml"
        yml.write_bytes(b"\xff\xfe\xff\x28")
        result = _run_cli("holdings-import-yaml", "--file", str(yml), tmp_db=tmp_db)
        assert result.returncode == 0
        assert "编码损坏" in result.stdout or "非 UTF-8" in result.stdout


class TestCoerceHoldingsYaml:
    def test_shares_scientific_string(self):
        v, err = _coerce_holdings_shares("1e2")
        assert err == ""
        assert v == 100

    def test_cost_accepts_int_like(self):
        v, err = _coerce_holdings_cost(80)
        assert err == ""
        assert v == 80.0


# ── 关注池 ────────────────────────────────────────────────────────

class TestWatchlist:
    def test_add_requires_input_by(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "宁德时代CLI", "--tier", "tier1_core",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "--input-by" in result.stderr

    def test_add_records_input_by(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "宁德时代CLI", "--tier", "tier1_core",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0

        with get_connection(tmp_db) as conn:
            row = conn.execute(
                "SELECT input_by FROM watchlist WHERE stock_code = ?",
                ("300750",),
            ).fetchone()
        assert row["input_by"] == "cursor"

    def test_add_basic(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "宁德时代CLI", "--tier", "tier1_core",
            "--reason", "龙头测试",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已添加到关注池" in result.stdout

    def test_add_invalid_tier(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "test", "--tier", "invalid_tier",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0

    def test_remove(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "000002",
            "--name", "万科", "--tier", "tier2_watch",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        result = _run_cli(
            "watchlist-remove", "--code", "000002",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已从关注池移除" in result.stdout

    def test_remove_nonexistent(self, tmp_db):
        result = _run_cli(
            "watchlist-remove", "--code", "999999",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "未在关注池中找到" in result.stdout

    def test_update_tier(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "600519",
            "--name", "贵州茅台", "--tier", "tier2_watch",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        result = _run_cli(
            "watchlist-update", "--code", "600519",
            "--tier", "tier1_core",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已更新" in result.stdout
        assert "tier1_core" in result.stdout

    def test_update_no_fields(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "000858",
            "--name", "五粮液", "--tier", "tier3_sector",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        result = _run_cli(
            "watchlist-update", "--code", "000858",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "未指定" in result.stdout

    def test_list(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "601318",
            "--name", "中国平安", "--tier", "tier1_core",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        result = _run_cli("watchlist-list", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "中国平安" in result.stdout

    def test_list_by_tier(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "601166",
            "--name", "兴业银行", "--tier", "tier2_watch",
            "--input-by", "cursor",
            tmp_db=tmp_db,
        )
        result = _run_cli("watchlist-list", "--tier", "tier2_watch", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "兴业银行" in result.stdout


# ── 交易记录 ──────────────────────────────────────────────────────

class TestAddTrade:
    def test_buy(self, tmp_db):
        result = _run_cli(
            "add-trade", "--code", "300750",
            "--name", "宁德时代",
            "--direction", "buy",
            "--price", "85.0",
            "--date", "2026-04-01",
            "--shares", "200",
            "--reason", "板块龙头首阴",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "买入" in result.stdout
        assert "宁德时代" in result.stdout

    def test_sell_with_pnl(self, tmp_db):
        result = _run_cli(
            "add-trade", "--code", "300750",
            "--name", "宁德时代",
            "--direction", "sell",
            "--price", "92.0",
            "--date", "2026-04-03",
            "--pnl-pct", "8.2",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "卖出" in result.stdout

    def test_invalid_direction(self, tmp_db):
        result = _run_cli(
            "add-trade", "--code", "300750", "--name", "X",
            "--direction", "hold", "--price", "10", "--date", "2026-04-01",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0


# ── 日历 & 黑名单 ─────────────────────────────────────────────────

class TestAddCalendar:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-calendar",
            "--date", "2026-04-10",
            "--event", "美联储议息会议",
            "--category", "财经",
            "--impact", "high",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入日历事件" in result.stdout
        assert "美联储议息会议" in result.stdout

    def test_missing_event(self, tmp_db):
        result = _run_cli("add-calendar", "--date", "2026-04-01", tmp_db=tmp_db)
        assert result.returncode != 0


class TestBlacklistAdd:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "blacklist-add",
            "--code", "000001",
            "--name", "某问题股",
            "--reason", "财务造假嫌疑",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已加入黑名单" in result.stdout

    def test_with_until(self, tmp_db):
        result = _run_cli(
            "blacklist-add",
            "--code", "000002", "--name", "临时回避",
            "--reason", "解禁压力",
            "--until", "2026-06-30",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0


# ── 统一搜索 ──────────────────────────────────────────────────────

class TestDbSearch:
    def _seed(self, tmp_db: str):
        _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "AI算力板块观点",
            "--core-view", "AI算力主线持续",
            "--input-by", "manual",
            tmp_db=tmp_db,
        )
        _run_cli(
            "add-industry", "--sector", "AI算力",
            "--date", "2026-04-01",
            "--content", "AI服务器需求超预期",
            tmp_db=tmp_db,
        )

    def test_search_all(self, tmp_db):
        self._seed(tmp_db)
        result = _run_cli("db-search", "--keyword", "AI算力", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "共找到" in result.stdout
        import re
        m = re.search(r"共找到 (\d+) 条", result.stdout)
        assert m, "未能从输出中解析结果数量"
        assert int(m.group(1)) >= 1, f"期望至少 1 条结果，实际 {m.group(1)} 条"

    def test_search_notes_only(self, tmp_db):
        self._seed(tmp_db)
        result = _run_cli("db-search", "--keyword", "AI算力", "--type", "notes", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "老师笔记" in result.stdout

    def test_no_match(self, tmp_db):
        result = _run_cli("db-search", "--keyword", "完全不存在XYZ999", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未找到" in result.stdout
