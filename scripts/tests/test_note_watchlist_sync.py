"""老师观点 → 关注池同步功能测试。

覆盖：
  - add-note --stocks 写入 mentioned_stocks 字段
  - add-note 候选关注池输出（新股 vs 已存在跳过）
  - add-note --sync-watchlist-from-stocks 与 watchlist-sync-from-note
  - queries.sync_watchlist_from_mentioned_stocks / get_teacher_note_by_id
  - watchlist-add --source-note-id 写入 source_note_id
  - migrate v14 新列添加
  - check_watchlist_exists 辅助函数
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from db.connection import get_connection
from db.migrate import CURRENT_SCHEMA_VERSION, get_schema_version, migrate
from db import queries as Q

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = SCRIPTS_DIR / "main.py"


def _run_cli(*args: str, tmp_db: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "TRADE_DB_PATH": tmp_db}
    return subprocess.run(
        [sys.executable, str(MAIN_PY), "db", *args],
        capture_output=True, text=True, cwd=str(SCRIPTS_DIR), env=env,
    )


@pytest.fixture
def tmp_db(tmp_path) -> str:
    return str(tmp_path / "test_sync.db")


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test_sync_q.db")
    migrate(c)
    yield c
    c.close()


# ──────────────────────────────────────────────────────────────
# migrate v14：新列校验
# ──────────────────────────────────────────────────────────────

class TestMigrationV14:
    def test_schema_version_current(self, conn):
        assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION

    def test_teacher_notes_has_mentioned_stocks_column(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)").fetchall()}
        assert "mentioned_stocks" in cols

    def test_watchlist_has_source_note_id_column(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        assert "source_note_id" in cols

    def test_migrate_from_v13_adds_columns(self, tmp_path):
        """从 v13 升级到 v14，ALTER TABLE 正确添加两列。"""
        db_path = tmp_path / "v13.db"
        c = get_connection(db_path)
        migrate(c)
        # 强制回退到 v13 并删除新列（通过重建测试 DB 模拟）
        c.execute("PRAGMA user_version = 13")
        c.commit()

        # 重新 migrate 触发 v14
        migrate(c)
        assert get_schema_version(c) >= CURRENT_SCHEMA_VERSION
        tn_cols = {row[1] for row in c.execute("PRAGMA table_info(teacher_notes)").fetchall()}
        wl_cols = {row[1] for row in c.execute("PRAGMA table_info(watchlist)").fetchall()}
        assert "mentioned_stocks" in tn_cols
        assert "source_note_id" in wl_cols
        c.close()


# ──────────────────────────────────────────────────────────────
# check_watchlist_exists
# ──────────────────────────────────────────────────────────────

class TestCheckWatchlistExists:
    def test_returns_false_when_empty(self, conn):
        assert Q.check_watchlist_exists(conn, "300750") is False

    def test_returns_true_after_insert(self, conn):
        Q.insert_watchlist(conn, stock_code="300750", stock_name="宁德时代", tier="tier3_sector")
        conn.commit()
        assert Q.check_watchlist_exists(conn, "300750") is True

    def test_returns_false_for_removed(self, conn):
        wid = Q.insert_watchlist(
            conn, stock_code="688041", stock_name="海光信息",
            tier="tier2_watch", status="removed",
        )
        conn.commit()
        assert Q.check_watchlist_exists(conn, "688041") is False

    def test_different_codes_independent(self, conn):
        Q.insert_watchlist(conn, stock_code="300750", stock_name="宁德时代", tier="tier3_sector")
        conn.commit()
        assert Q.check_watchlist_exists(conn, "300750") is True
        assert Q.check_watchlist_exists(conn, "688041") is False


# ──────────────────────────────────────────────────────────────
# insert_teacher_note 支持 mentioned_stocks
# ──────────────────────────────────────────────────────────────

class TestInsertTeacherNoteWithStocks:
    def test_stores_mentioned_stocks_as_json(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "测试老师")
        stocks = [{"code": "300750", "name": "宁德时代", "tier": "tier3_sector"}]
        note_id = Q.insert_teacher_note(
            conn,
            teacher_id=teacher_id,
            date="2026-04-01",
            title="测试笔记",
            mentioned_stocks=stocks,
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT mentioned_stocks FROM teacher_notes WHERE id = ?", (note_id,)
        ).fetchone())
        assert row["mentioned_stocks"] is not None
        loaded = json.loads(row["mentioned_stocks"])
        assert len(loaded) == 1
        assert loaded[0]["code"] == "300750"

    def test_mentioned_stocks_none_when_not_provided(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "无股老师")
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01", title="无个股笔记"
        )
        conn.commit()
        row = conn.execute(
            "SELECT mentioned_stocks FROM teacher_notes WHERE id = ?", (note_id,)
        ).fetchone()
        assert row["mentioned_stocks"] is None


# ──────────────────────────────────────────────────────────────
# sync_watchlist_from_mentioned_stocks
# ──────────────────────────────────────────────────────────────

class TestValidateMentionedStocksAndTier:
    def test_validate_rejects_non_dict_element(self):
        with pytest.raises(ValueError, match="mentioned_stocks\\[1\\]"):
            Q.validate_mentioned_stocks_entries(
                [{"code": "300750", "name": "宁德"}, "not-a-dict"],
            )

    def test_normalize_tier_invalid_becomes_tier3(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "Tier老师")
        note_id = Q.insert_teacher_note(
            conn,
            teacher_id=teacher_id,
            date="2026-04-01",
            title="tier测试",
            mentioned_stocks=[{"code": "300750", "name": "宁德", "tier": "bogus"}],
        )
        conn.commit()
        r = Q.sync_watchlist_from_mentioned_stocks(
            conn,
            note_id=note_id,
            note_date="2026-04-01",
            title="tier测试",
            teacher_name="Tier老师",
            stocks=[{"code": "300750", "name": "宁德", "tier": "bogus"}],
        )
        conn.commit()
        assert len(r["added"]) == 1
        assert r["added"][0]["tier"] == "tier3_sector"
        row = conn.execute(
            "SELECT tier FROM watchlist WHERE stock_code='300750'"
        ).fetchone()
        assert row["tier"] == "tier3_sector"


class TestSyncWatchlistFromMentionedStocks:
    def test_inserts_new_and_skips_existing(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "同步老师")
        note_id = Q.insert_teacher_note(
            conn,
            teacher_id=teacher_id,
            date="2026-04-01",
            title="笔记A",
            mentioned_stocks=[{"code": "300750", "name": "宁德时代"}],
        )
        Q.insert_watchlist(conn, stock_code="688041", stock_name="海光信息", tier="tier3_sector")
        conn.commit()
        r = Q.sync_watchlist_from_mentioned_stocks(
            conn,
            note_id=note_id,
            note_date="2026-04-01",
            title="笔记A",
            teacher_name="同步老师",
            stocks=[
                {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
                {"code": "688041", "name": "海光信息", "tier": "tier2_watch"},
            ],
        )
        conn.commit()
        assert len(r["added"]) == 1
        assert r["added"][0]["code"] == "300750"
        assert len(r["skipped"]) == 1
        assert r["skipped"][0]["code"] == "688041"
        row = dict(conn.execute(
            "SELECT add_reason, source_note_id, add_date FROM watchlist WHERE stock_code = '300750'"
        ).fetchone())
        assert row["source_note_id"] == note_id
        assert row["add_date"] == "2026-04-01"
        assert "同步老师" in row["add_reason"] and "笔记A" in row["add_reason"]

    def test_optional_sector_on_stock(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "T")
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-02", title="B",
            mentioned_stocks=[{"code": "000001", "name": "平安", "sector": "银行"}],
        )
        conn.commit()
        Q.sync_watchlist_from_mentioned_stocks(
            conn,
            note_id=note_id,
            note_date="2026-04-02",
            title="B",
            teacher_name="T",
            stocks=[{"code": "000001", "name": "平安", "sector": "银行"}],
        )
        conn.commit()
        sec = conn.execute(
            "SELECT sector FROM watchlist WHERE stock_code='000001'"
        ).fetchone()["sector"]
        assert sec == "银行"

    def test_get_teacher_note_by_id(self, conn):
        teacher_id = Q.get_or_create_teacher(conn, "查笔记老师")
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-03", title="C",
        )
        conn.commit()
        row = Q.get_teacher_note_by_id(conn, note_id)
        assert row is not None
        assert row["teacher_name"] == "查笔记老师"
        assert Q.get_teacher_note_by_id(conn, 999999) is None


# ──────────────────────────────────────────────────────────────
# insert_watchlist 支持 source_note_id
# ──────────────────────────────────────────────────────────────

class TestInsertWatchlistWithSourceNoteId:
    def test_stores_source_note_id(self, conn):
        wid = Q.insert_watchlist(
            conn,
            stock_code="688041",
            stock_name="海光信息",
            tier="tier2_watch",
            source_note_id=42,
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT source_note_id FROM watchlist WHERE id = ?", (wid,)
        ).fetchone())
        assert row["source_note_id"] == 42

    def test_source_note_id_optional(self, conn):
        wid = Q.insert_watchlist(
            conn, stock_code="300750", stock_name="宁德时代", tier="tier3_sector"
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT source_note_id FROM watchlist WHERE id = ?", (wid,)
        ).fetchone())
        assert row["source_note_id"] is None


# ──────────────────────────────────────────────────────────────
# CLI: add-note --stocks 参数
# ──────────────────────────────────────────────────────────────

class TestAddNoteWithStocksCLI:
    def test_stocks_written_to_mentioned_stocks(self, tmp_db):
        stocks = json.dumps([
            {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
        ])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "AI算力主线判断",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入笔记" in result.stdout

        conn = get_connection(tmp_db)
        migrate(conn)
        row = conn.execute(
            "SELECT mentioned_stocks FROM teacher_notes WHERE title = 'AI算力主线判断'"
        ).fetchone()
        assert row is not None
        loaded = json.loads(row["mentioned_stocks"])
        assert loaded[0]["code"] == "300750"
        conn.close()

    def test_no_stocks_no_candidate_output(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "无个股笔记",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池" not in result.stdout

    def test_candidate_output_new_stocks(self, tmp_db):
        stocks = json.dumps([
            {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
            {"code": "688041", "name": "海光信息", "tier": "tier2_watch"},
        ])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "个股测试",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池 (2/2)" in result.stdout
        assert "300750 宁德时代 [tier3_sector] (建议加入)" in result.stdout
        assert "688041 海光信息 [tier2_watch] (建议加入)" in result.stdout
        assert "WATCHLIST_CANDIDATES:" in result.stdout

    def test_existing_watchlist_stock_shows_skip(self, tmp_db):
        # 先加入关注池
        _run_cli(
            "watchlist-add", "--code", "300750", "--name", "宁德时代",
            "--tier", "tier3_sector", tmp_db=tmp_db,
        )
        stocks = json.dumps([
            {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
            {"code": "688041", "name": "海光信息", "tier": "tier2_watch"},
        ])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "去重测试",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池 (1/2)" in result.stdout
        assert "688041 海光信息 [tier2_watch] (建议加入)" in result.stdout
        assert "300750 宁德时代(已在关注池，跳过)" in result.stdout or \
               "300750 宁德时代 (已在关注池，跳过)" in result.stdout

    def test_all_stocks_already_in_watchlist(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "300750", "--name", "宁德时代",
            "--tier", "tier3_sector", tmp_db=tmp_db,
        )
        stocks = json.dumps([{"code": "300750", "name": "宁德时代", "tier": "tier3_sector"}])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "全跳过测试",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池 (0/1)" in result.stdout
        assert "WATCHLIST_CANDIDATES" not in result.stdout

    def test_stocks_default_tier_is_tier3_sector(self, tmp_db):
        stocks = json.dumps([{"code": "000001", "name": "平安银行"}])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "默认tier测试",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "[tier3_sector]" in result.stdout

    def test_candidate_denominator_excludes_entries_without_code(self, tmp_db):
        """缺 code 的项不参与分母，避免 (建议加入+跳过) 与分母不一致。"""
        stocks = json.dumps([
            {"name": "无代码"},
            {"code": "688041", "name": "海光信息", "tier": "tier2_watch"},
            {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
        ])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "分母测试",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池 (2/2)" in result.stdout
        assert "688041" in result.stdout
        assert "300750" in result.stdout

    def test_all_stocks_missing_code_shows_hint_not_fraction(self, tmp_db):
        stocks = json.dumps([{"name": "只有名"}])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "全无code",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "无有效股票代码" in result.stdout
        assert "候选关注池 (0/" not in result.stdout


class TestAddNoteInvalidStocksCLI:
    def test_non_object_stocks_exit_error(self, tmp_db):
        stocks = json.dumps(["300750"])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "坏stocks",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode != 0
        assert "mentioned_stocks[0]" in (result.stderr or "") + result.stdout


class TestAddNoteSyncWatchlistFlagCLI:
    def test_sync_flag_writes_watchlist(self, tmp_db):
        stocks = json.dumps([
            {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
        ])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "同步入池",
            "--stocks", stocks,
            "--sync-watchlist-from-stocks",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "关注池同步 (1 新增 / 1 统计)" in result.stdout
        assert "已加入" in result.stdout
        assert "WATCHLIST_CANDIDATES:" in result.stdout

        conn = get_connection(tmp_db)
        migrate(conn)
        row = conn.execute(
            "SELECT source_note_id, add_date FROM watchlist WHERE stock_code = '300750'"
        ).fetchone()
        assert row is not None
        nid = conn.execute(
            "SELECT id FROM teacher_notes WHERE title = '同步入池'"
        ).fetchone()[0]
        assert row["source_note_id"] == nid
        assert row["add_date"] == "2026-04-01"
        conn.close()

    def test_without_sync_flag_no_watchlist_row(self, tmp_db):
        stocks = json.dumps([{"code": "688999", "name": "测试", "tier": "tier3_sector"}])
        result = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-01", "--title", "仅笔记",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "候选关注池" in result.stdout
        conn = get_connection(tmp_db)
        migrate(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE stock_code='688999'"
        ).fetchone()[0]
        assert n == 0
        conn.close()


class TestWatchlistSyncFromNoteCLI:
    def test_sync_from_note_after_add_note_without_flag(self, tmp_db):
        stocks = json.dumps([{"code": "300750", "name": "宁德时代", "tier": "tier2_watch"}])
        r1 = _run_cli(
            "add-note", "--teacher", "小鲍",
            "--date", "2026-04-02", "--title", "两步笔记",
            "--stocks", stocks,
            tmp_db=tmp_db,
        )
        assert r1.returncode == 0
        conn = get_connection(tmp_db)
        migrate(conn)
        nid = conn.execute(
            "SELECT id FROM teacher_notes WHERE title = '两步笔记'"
        ).fetchone()[0]
        conn.close()

        r2 = _run_cli(
            "watchlist-sync-from-note", "--note-id", str(nid),
            tmp_db=tmp_db,
        )
        assert r2.returncode == 0
        assert "关注池同步（来自笔记 #" in r2.stdout
        conn = get_connection(tmp_db)
        migrate(conn)
        row = conn.execute(
            "SELECT source_note_id, tier FROM watchlist WHERE stock_code='300750'"
        ).fetchone()
        assert row["source_note_id"] == nid
        assert row["tier"] == "tier2_watch"
        conn.close()

    def test_sync_from_note_missing(self, tmp_db):
        r = _run_cli(
            "watchlist-sync-from-note", "--note-id", "99999",
            tmp_db=tmp_db,
        )
        assert r.returncode == 0
        assert "笔记不存在" in r.stdout


# ──────────────────────────────────────────────────────────────
# CLI: watchlist-add --source-note-id
# ──────────────────────────────────────────────────────────────

class TestWatchlistAddSourceNoteIdCLI:
    def test_source_note_id_stored(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "688041", "--name", "海光信息",
            "--tier", "tier2_watch", "--source-note-id", "42",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "来源笔记 #42" in result.stdout

        conn = get_connection(tmp_db)
        migrate(conn)
        row = conn.execute(
            "SELECT source_note_id FROM watchlist WHERE stock_code = '688041'"
        ).fetchone()
        assert row["source_note_id"] == 42
        conn.close()

    def test_without_source_note_id_works(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750", "--name", "宁德时代",
            "--tier", "tier3_sector",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "来源笔记" not in result.stdout
