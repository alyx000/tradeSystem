"""CLI 命令集成测试：使用 TRADE_DB_PATH 隔离测试 DB，不污染生产数据。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from db.cli import _coerce_holdings_cost, _coerce_holdings_shares
from db.connection import get_connection

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

    def test_raw_content_file(self, tmp_db, tmp_path):
        content_file = tmp_path / "ocr.txt"
        content_file.write_text("这是一段很长的 OCR/PDF 提取内容", encoding="utf-8")
        result = _run_cli(
            "add-note", "--teacher", "文件老师",
            "--date", "2026-04-01", "--title", "文件原文",
            "--raw-content-file", str(content_file),
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
