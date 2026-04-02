"""CLI 命令集成测试：使用 TRADE_DB_PATH 隔离测试 DB，不污染生产数据。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from db.connection import get_connection

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
    return str(tmp_path / "test_cli.db")


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


# ── 老师观点 ──────────────────────────────────────────────────────

class TestAddNote:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "测试老师",
            "--date", "2026-04-01", "--title", "CLI测试笔记",
            "--core-view", "这是一条测试笔记",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已录入笔记" in result.stdout

    def test_new_teacher_auto_created(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "新老师_CLI",
            "--date", "2026-04-01", "--title", "自动创建",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0

    def test_missing_required_field(self, tmp_db):
        result = _run_cli("add-note", "--teacher", "test", tmp_db=tmp_db)
        assert result.returncode != 0

    def test_single_attachment(self, tmp_db, tmp_path):
        img = tmp_path / "note.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        result = _run_cli(
            "add-note", "--teacher", "附件老师",
            "--date", "2026-04-01", "--title", "单附件",
            "--source-type", "image",
            "--attachment", str(img),
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
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "附件 3 个" in result.stdout

    def test_nonexistent_attachment_skipped(self, tmp_db):
        result = _run_cli(
            "add-note", "--teacher", "老师",
            "--date", "2026-04-01", "--title", "不存在附件",
            "--attachment", "/nonexistent/file.jpg",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "附件" not in result.stdout or "附件 0 个" not in result.stdout


class TestQueryNotes:
    def test_keyword_search(self, tmp_db):
        _run_cli(
            "add-note", "--teacher", "搜索测试老师",
            "--date", "2026-04-01", "--title", "锂电板块分析CLI",
            "--core-view", "锂电储能看好",
            tmp_db=tmp_db,
        )
        result = _run_cli("query-notes", "--keyword", "锂电", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "锂电" in result.stdout

    def test_no_match(self, tmp_db):
        result = _run_cli("query-notes", "--keyword", "完全不存在的关键词XYZ123", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未找到" in result.stdout


# ── 行业 / 宏观 ───────────────────────────────────────────────────

class TestAddIndustry:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-industry", "--sector", "AI算力",
            "--date", "2026-04-01",
            "--content", "AI板块持续活跃，资金流入明显",
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
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "id=" in result.stdout

    def test_missing_required(self, tmp_db):
        result = _run_cli("add-industry", "--sector", "AI", tmp_db=tmp_db)
        assert result.returncode != 0


class TestAddMacro:
    def test_basic(self, tmp_db):
        result = _run_cli(
            "add-macro", "--category", "货币政策",
            "--date", "2026-04-01",
            "--title", "央行降准25BP",
            "--content", "全面降准释放流动性约6000亿",
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
            tmp_db=tmp_db,
        )
        assert result.returncode == 0


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
        result = _run_cli("holdings-remove", "--code", "688041", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "已移除持仓" in result.stdout

        result2 = _run_cli("holdings-list", tmp_db=tmp_db)
        assert "海光信息" not in result2.stdout

    def test_remove_nonexistent(self, tmp_db):
        result = _run_cli("holdings-remove", "--code", "999999", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未找到" in result.stdout

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
        result = _run_cli("holdings-remove", "--code", "688041", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "共 1 条置为 closed" in result.stdout

        with get_connection(tmp_db) as conn:
            row = conn.execute("SELECT status FROM holdings WHERE stock_code = '688041.SH'").fetchone()
        assert row["status"] == "closed"


# ── 关注池 ────────────────────────────────────────────────────────

class TestWatchlist:
    def test_add_basic(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "宁德时代CLI", "--tier", "tier1_core",
            "--reason", "龙头测试",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已添加到关注池" in result.stdout

    def test_add_invalid_tier(self, tmp_db):
        result = _run_cli(
            "watchlist-add", "--code", "300750",
            "--name", "test", "--tier", "invalid_tier",
            tmp_db=tmp_db,
        )
        assert result.returncode != 0

    def test_remove(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "000002",
            "--name", "万科", "--tier", "tier2_watch",
            tmp_db=tmp_db,
        )
        result = _run_cli("watchlist-remove", "--code", "000002", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "已从关注池移除" in result.stdout

    def test_remove_nonexistent(self, tmp_db):
        result = _run_cli("watchlist-remove", "--code", "999999", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未在关注池中找到" in result.stdout

    def test_update_tier(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "600519",
            "--name", "贵州茅台", "--tier", "tier2_watch",
            tmp_db=tmp_db,
        )
        result = _run_cli(
            "watchlist-update", "--code", "600519",
            "--tier", "tier1_core",
            tmp_db=tmp_db,
        )
        assert result.returncode == 0
        assert "已更新" in result.stdout
        assert "tier1_core" in result.stdout

    def test_update_no_fields(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "000858",
            "--name", "五粮液", "--tier", "tier3_sector",
            tmp_db=tmp_db,
        )
        result = _run_cli("watchlist-update", "--code", "000858", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "未指定" in result.stdout

    def test_list(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "601318",
            "--name", "中国平安", "--tier", "tier1_core",
            tmp_db=tmp_db,
        )
        result = _run_cli("watchlist-list", tmp_db=tmp_db)
        assert result.returncode == 0
        assert "中国平安" in result.stdout

    def test_list_by_tier(self, tmp_db):
        _run_cli(
            "watchlist-add", "--code", "601166",
            "--name", "兴业银行", "--tier", "tier2_watch",
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
