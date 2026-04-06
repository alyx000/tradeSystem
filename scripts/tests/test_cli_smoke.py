"""Smoke 测试：验证 skills 引用的所有 CLI 命令签名当前仍可被 argparse 解析。

不连 DB，不启动子进程，纯 argparse.parse_args() 验证。
任何命令名/参数重命名都会让此测试立即失败，在 CI 阶段拦截问题。

对应 skills INDEX：
  record-notes     → add-note, add-industry, add-macro
  portfolio-manager → holdings-*, watchlist-*, add-trade, blacklist-add
  daily-review     → query-notes, db-search
  market-tasks     → (main.py pre/post，不在 db 子命令下，不在此测试)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def _build_db_parser() -> argparse.ArgumentParser:
    """仅构建 db 子命令解析器，不触发任何真实操作。"""
    from db.cli import register_db_subparser
    parser = argparse.ArgumentParser(prog="main.py")
    subparsers = parser.add_subparsers(dest="command")
    register_db_subparser(subparsers)
    return parser


def _build_main_parser() -> argparse.ArgumentParser:
    """构建 main.py 顶层解析器，校验 ingest/plan/knowledge 命令签名。"""
    from main import build_parser
    return build_parser()


# ── skill: record-notes ───────────────────────────────────────────

RECORD_NOTES_COMMANDS = [
    # add-note 文字场景
    ["db", "add-note", "--teacher", "小鲍", "--date", "2026-04-01", "--title", "观点标题"],
    # add-note 带核心观点和标签
    ["db", "add-note", "--teacher", "小鲍", "--date", "2026-04-01", "--title", "T",
     "--core-view", "主升结束", "--tags", '["AI","连板"]', "--input-by", "openclaw"],
    # add-note 带结构化要点、板块、仓位建议
    ["db", "add-note", "--teacher", "小鲍", "--date", "2026-04-01", "--title", "T",
     "--key-points", '["要点A","要点B"]', "--sectors", '["AI","锂电"]',
     "--position-advice", "控制仓位至3成"],
    # add-note 单附件
    ["db", "add-note", "--teacher", "X", "--date", "2026-04-01", "--title", "T",
     "--source-type", "image", "--attachment", "/tmp/a.jpg"],
    # add-note 多附件
    ["db", "add-note", "--teacher", "X", "--date", "2026-04-01", "--title", "T",
     "--source-type", "mixed", "--attachment", "/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"],
    # add-industry 最简
    ["db", "add-industry", "--sector", "AI算力", "--date", "2026-04-01", "--content", "C"],
    # add-industry 完整
    ["db", "add-industry", "--sector", "锂电", "--date", "2026-04-01", "--content", "C",
     "--info-type", "研报", "--source", "华泰", "--confidence", "高",
     "--tags", '["锂电","储能"]'],
    # add-macro 最简
    ["db", "add-macro", "--category", "货币政策", "--date", "2026-04-01",
     "--title", "降准", "--content", "C"],
    # add-macro 完整
    ["db", "add-macro", "--category", "财政", "--date", "2026-04-01",
     "--title", "T", "--content", "C",
     "--source", "新华社", "--impact", "利好", "--tags", '["财政"]'],
]

# ── skill: portfolio-manager ──────────────────────────────────────

PORTFOLIO_COMMANDS = [
    # holdings
    ["db", "holdings-add", "--code", "300750", "--name", "宁德时代", "--price", "85.0"],
    ["db", "holdings-add", "--code", "300750", "--name", "N",
     "--shares", "200", "--price", "85.0", "--sector", "锂电",
     "--stop-loss", "80.0", "--market", "A股", "--note", "龙头"],
    ["db", "holdings-remove", "--code", "300750"],
    ["db", "holdings-list"],
    # watchlist
    ["db", "watchlist-add", "--code", "300750", "--name", "宁德时代", "--tier", "tier1_core"],
    ["db", "watchlist-add", "--code", "000001", "--name", "平安银行",
     "--tier", "tier2_watch", "--reason", "破位反弹", "--sector", "银行", "--note", "观察"],
    ["db", "watchlist-remove", "--code", "300750"],
    ["db", "watchlist-update", "--code", "300750", "--tier", "tier1_core"],
    ["db", "watchlist-update", "--code", "300750", "--status", "tracking"],
    ["db", "watchlist-update", "--code", "300750", "--note", "升级观察"],
    ["db", "watchlist-list"],
    ["db", "watchlist-list", "--tier", "tier1_core"],
    ["db", "watchlist-list", "--status", "watching"],
    # add-trade
    ["db", "add-trade", "--code", "300750", "--name", "宁德时代",
     "--direction", "buy", "--price", "85.0", "--date", "2026-04-01"],
    ["db", "add-trade", "--code", "300750", "--name", "宁德时代",
     "--direction", "sell", "--price", "92.0", "--date", "2026-04-03",
     "--shares", "200", "--sector", "锂电", "--pnl-pct", "8.2"],
    # blacklist
    ["db", "blacklist-add", "--code", "000001", "--name", "问题股"],
    ["db", "blacklist-add", "--code", "000002", "--name", "临时回避",
     "--reason", "解禁压力", "--until", "2026-06-30"],
]

# ── skill: daily-review ───────────────────────────────────────────

DAILY_REVIEW_COMMANDS = [
    ["db", "query-notes", "--keyword", "锂电"],
    ["db", "query-notes", "--keyword", "AI", "--teacher", "小鲍"],
    ["db", "query-notes", "--keyword", "AI",
     "--from", "2026-04-01", "--to", "2026-04-30"],
    ["db", "db-search", "--keyword", "AI算力"],
    ["db", "db-search", "--keyword", "降准", "--type", "macro"],
    ["db", "db-search", "--keyword", "锂电", "--type", "notes",
     "--from", "2026-04-01", "--to", "2026-04-30"],
]

# ── skill: (管理命令，所有 skill 可能用到) ─────────────────────────

MANAGEMENT_COMMANDS = [
    ["db", "add-calendar", "--date", "2026-04-10", "--event", "美联储议息"],
    ["db", "add-calendar", "--date", "2026-04-10", "--event", "E",
     "--category", "财经", "--impact", "high", "--note", "注意"],
    ["db", "init"],
    ["db", "sync"],
    ["db", "reconcile"],
]

ALL_SKILL_COMMANDS = (
    RECORD_NOTES_COMMANDS
    + PORTFOLIO_COMMANDS
    + DAILY_REVIEW_COMMANDS
    + MANAGEMENT_COMMANDS
)

ARCHITECTURE_COMMANDS = [
    ["ingest", "run", "--stage", "post_core", "--date", "2026-04-04"],
    ["ingest", "run", "--stage", "post_core", "--date", "2026-04-04", "--input-by", "openclaw"],
    ["ingest", "run-interface", "--name", "block_trade", "--date", "2026-04-04"],
    ["ingest", "run-interface", "--name", "block_trade", "--date", "2026-04-04", "--input-by", "cursor"],
    ["ingest", "list-interfaces"],
    ["ingest", "inspect", "--date", "2026-04-04"],
    ["ingest", "inspect", "--date", "2026-04-04", "--stage", "post_extended"],
    ["ingest", "inspect", "--date", "2026-04-04", "--interface", "margin"],
    ["ingest", "retry"],
    ["ingest", "retry", "--stage", "post_extended"],
    ["ingest", "retry", "--interface", "margin"],
    ["ingest", "health", "--date", "2026-04-04"],
    ["ingest", "health", "--date", "2026-04-04", "--days", "14", "--limit", "5", "--stage", "post_extended"],
    ["ingest", "reconcile", "--stale-minutes", "5"],
    ["plan", "draft", "--date", "2026-04-04"],
    ["plan", "show-draft", "--draft-id", "draft_1"],
    ["plan", "confirm", "--draft-id", "draft_1", "--date", "2026-04-07"],
    ["plan", "diagnose", "--plan-id", "plan_1", "--date", "2026-04-07"],
    ["plan", "review", "--plan-id", "plan_1", "--date", "2026-04-07"],
    ["knowledge", "add-note", "--title", "资料标题", "--content", "AI算力回流，关注300750.SZ"],
    ["knowledge", "list"],
    ["knowledge", "draft-from-asset", "--asset-id", "asset_1", "--date", "2026-04-10"],
]


@pytest.mark.parametrize("cmd", ALL_SKILL_COMMANDS,
                         ids=[" ".join(c[:4]) for c in ALL_SKILL_COMMANDS])
def test_command_parseable(cmd: list[str]) -> None:
    """每条 skill 引用的命令都必须能被 argparse 成功解析。"""
    parser = _build_db_parser()
    try:
        parser.parse_args(cmd)
    except SystemExit as e:
        pytest.fail(
            f"命令解析失败（argparse 退出码 {e.code}）: {' '.join(cmd)}\n"
            "请检查 cli.py 中对应子命令的参数定义，并同步更新 .cursor/skills/INDEX.md"
        )


def test_all_skill_subcommands_registered() -> None:
    """验证 INDEX.md 中列出的所有子命令名称都已在 argparse 中注册。"""
    expected_subcommands = {
        # record-notes
        "add-note", "add-industry", "add-macro",
        # portfolio-manager
        "holdings-add", "holdings-remove", "holdings-list",
        "watchlist-add", "watchlist-remove", "watchlist-update", "watchlist-list",
        "add-trade", "blacklist-add",
        # daily-review
        "query-notes", "db-search",
        # management
        "add-calendar", "init", "sync", "reconcile",
    }
    parser = _build_db_parser()
    db_subparser = None
    for action in parser._subparsers._actions:  # type: ignore[union-attr]
        if hasattr(action, "_name_parser_map"):
            db_sub_action = action._name_parser_map.get("db")
            if db_sub_action:
                for sub_action in db_sub_action._subparsers._actions:
                    if hasattr(sub_action, "_name_parser_map"):
                        db_subparser = sub_action
                        break
            break

    assert db_subparser is not None, "db 子解析器未找到"
    registered = set(db_subparser._name_parser_map.keys())

    missing = expected_subcommands - registered
    assert not missing, (
        f"以下子命令在 INDEX.md 中登记但 cli.py 未实现: {missing}\n"
        "请在 cli.py 中添加，或从 INDEX.md 中移除"
    )


@pytest.mark.parametrize("cmd", ARCHITECTURE_COMMANDS,
                         ids=[" ".join(c[:3]) for c in ARCHITECTURE_COMMANDS])
def test_architecture_command_parseable(cmd: list[str]) -> None:
    parser = _build_main_parser()
    try:
        parser.parse_args(cmd)
    except SystemExit as e:
        pytest.fail(
            f"顶层命令解析失败（argparse 退出码 {e.code}）: {' '.join(cmd)}\n"
            "请检查 main.py 中 ingest/plan/knowledge 子命令定义，并同步更新 skills 文档"
        )


def test_ingest_inspect_json_includes_health_status(tmp_path, monkeypatch, capsys) -> None:
    from db.connection import get_connection
    from db.migrate import migrate
    from main import build_parser, cmd_ingest

    db_path = tmp_path / "cli_ingest.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES ('run_margin', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}',
                'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES ('run_margin', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1)
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    parser = build_parser()
    args = parser.parse_args(
        ["ingest", "inspect", "--date", "2026-04-04", "--stage", "post_extended", "--interface", "margin", "--json"]
    )
    cmd_ingest({}, args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["stage"] == "post_extended"
    assert payload["interface_name"] == "margin"
    assert payload["status_label"] == "需处理"
    assert "从未成功过的接口" in payload["status_reason"]
    assert payload["health"]["status_label"] == "需处理"
    assert payload["health"]["stage"] == "post_extended"
    assert payload["health"]["interface_name"] == "margin"
    assert payload["health"]["top_failed_interfaces"][0]["interface_name"] == "margin"


def test_ingest_retry_json_includes_status_and_filters(tmp_path, monkeypatch, capsys) -> None:
    from db.connection import get_connection
    from db.migrate import migrate
    from main import build_parser, cmd_ingest

    db_path = tmp_path / "cli_retry.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_margin', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli'),
        ('run_block', 'block_trade', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES
        ('run_margin', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1),
        ('run_block', 'block_trade', '2026-04-04', 'post_extended', 'provider', 'rate limit', 1)
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    parser = build_parser()
    args = parser.parse_args(
        ["ingest", "retry", "--stage", "post_extended", "--interface", "margin", "--json"]
    )
    cmd_ingest({}, args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["stage"] == "post_extended"
    assert payload["interface_name"] == "margin"
    assert payload["retryable_count"] == 1
    assert payload["failed_interface_count"] == 1
    assert payload["status_label"] == "承压"
    assert payload["groups"][0]["interface_name"] == "margin"


def test_ingest_health_json_includes_summary_and_filters(tmp_path, monkeypatch, capsys) -> None:
    from db.connection import get_connection
    from db.migrate import migrate
    from main import build_parser, cmd_ingest

    db_path = tmp_path / "cli_health.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO ingest_runs
        (run_id, interface_name, provider, stage, biz_date, params_json, status,
         row_count, started_at, triggered_by)
        VALUES
        ('run_core_ok', 'daily_basic', 'tushare', 'post_core', '2026-04-01', '{}', 'success', 10, datetime('now'), 'cli'),
        ('run_ext_fail', 'margin', 'tushare', 'post_extended', '2026-04-04', '{}', 'failed', 0, datetime('now'), 'cli')
        """
    )
    conn.execute(
        """
        INSERT INTO ingest_errors
        (run_id, interface_name, biz_date, stage, error_type, error_message, retryable)
        VALUES ('run_ext_fail', 'margin', '2026-04-04', 'post_extended', 'network', 'timeout', 1)
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    parser = build_parser()
    args = parser.parse_args(
        ["ingest", "health", "--date", "2026-04-04", "--days", "7", "--limit", "5", "--stage", "post_extended", "--json"]
    )
    cmd_ingest({}, args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["stage"] == "post_extended"
    assert payload["days"] == 7
    assert payload["failed_interface_count"] == 1
    assert payload["status_label"] == "需处理"
    assert "从未成功过的接口" in payload["status_reason"]
    assert payload["top_failed_interfaces"][0]["interface_name"] == "margin"
