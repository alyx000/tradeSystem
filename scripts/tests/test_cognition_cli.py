"""L3: 认知层 CLI smoke 测试（方案 §七）。

覆盖两层：
- argparse 解析层：12 个 knowledge 子命令（含 validate 别名）参数签名不回归
- 子进程端到端：真实 main.py + tmp SQLite，校验 JSON 输出结构与错误处理

与 test_cli_smoke.py 风格一致：不依赖 mock，env TRADE_DB_PATH 切换真实 DB。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ──────────────────────────────────────────────────────────────
# argparse 签名层（不跑命令，只校验解析）
# ──────────────────────────────────────────────────────────────
def _build_parser():
    from main import build_parser
    return build_parser()


KNOWLEDGE_COGNITION_COMMANDS: list[list[str]] = [
    # cognition-add
    ["knowledge", "cognition-add",
     "--category", "signal", "--title", "T", "--description", "D",
     "--input-by", "pytest"],
    ["knowledge", "cognition-add",
     "--category", "signal", "--title", "T", "--description", "D",
     "--evidence-level", "hypothesis", "--status", "active",
     "--tags", '["a","b"]',
     "--conditions-json", '{"k":"v"}',
     "--input-by", "pytest", "--json"],
    # cognition-list
    ["knowledge", "cognition-list"],
    ["knowledge", "cognition-list",
     "--status", "active", "--category", "signal",
     "--evidence-level", "principle", "--keyword", "反包",
     "--limit", "5", "--offset", "10", "--json"],
    # cognition-show
    ["knowledge", "cognition-show", "--id", "cog_x", "--json"],
    # cognition-refine
    ["knowledge", "cognition-refine",
     "--id", "cog_x", "--description", "新描述",
     "--input-by", "pytest", "--json"],
    ["knowledge", "cognition-refine",
     "--id", "cog_x",
     "--pattern", "当{x}时，{y}→{z}",
     "--evidence-level", "principle",
     "--tags", '["a","b"]',
     "--status", "active",
     "--input-by", "pytest"],
    # cognition-deprecate
    ["knowledge", "cognition-deprecate",
     "--id", "cog_x", "--reason", "多次实例否定",
     "--input-by", "pytest", "--json"],
    # instance-add
    ["knowledge", "instance-add",
     "--cognition-id", "cog_x", "--observed-date", "2026-04-14",
     "--source-type", "teacher_note", "--input-by", "pytest"],
    ["knowledge", "instance-add",
     "--cognition-id", "cog_x", "--observed-date", "2026-04-14",
     "--source-type", "teacher_note",
     "--source-note-id", "42",
     "--teacher-id", "10", "--teacher-name-snapshot", "沈纯",
     "--position-cap", "0.3",
     "--parameters-json", '{"k":"v"}',
     "--input-by", "pytest", "--json"],
    # instance-batch-add
    ["knowledge", "instance-batch-add",
     "--file", "/tmp/batch.json", "--input-by", "pytest", "--json"],
    # instance-pending
    ["knowledge", "instance-pending"],
    ["knowledge", "instance-pending", "--check-ready", "--limit", "50", "--json"],
    # instance-validate + alias validate
    ["knowledge", "instance-validate",
     "--instance-id", "inst_x",
     "--outcome", "validated",
     "--outcome-fact-source", "daily_market:2026-04-15",
     "--input-by", "pytest"],
    ["knowledge", "validate",
     "--instance-id", "inst_x",
     "--outcome", "invalidated",
     "--outcome-fact-source", "daily_market:2026-04-15",
     "--outcome-date", "2026-04-16", "--lesson", "复盘教训",
     "--input-by", "pytest", "--json"],
    # instance-list
    ["knowledge", "instance-list"],
    ["knowledge", "instance-list",
     "--cognition-id", "cog_x", "--outcome", "pending",
     "--date-from", "2026-04-01", "--date-to", "2026-04-30",
     "--limit", "20", "--json"],
    # review-generate / show / confirm
    ["knowledge", "review-generate",
     "--period-type", "weekly",
     "--from", "2026-04-07", "--to", "2026-04-11",
     "--input-by", "pytest", "--json"],
    ["knowledge", "review-show", "--id", "rev_x", "--json"],
    ["knowledge", "review-confirm",
     "--id", "rev_x", "--user-reflection", "本周反思",
     "--action-items-json", '["a","b"]',
     "--input-by", "pytest", "--json"],
    # review-list
    ["knowledge", "review-list"],
    ["knowledge", "review-list",
     "--period-type", "weekly", "--status", "draft",
     "--from", "2026-04-01", "--to", "2026-04-30",
     "--limit", "10", "--offset", "0", "--json"],
]


@pytest.mark.parametrize(
    "cmd", KNOWLEDGE_COGNITION_COMMANDS,
    ids=[" ".join(c[:3]) for c in KNOWLEDGE_COGNITION_COMMANDS],
)
def test_cognition_cli_parseable(cmd: list[str]) -> None:
    parser = _build_parser()
    try:
        parser.parse_args(cmd)
    except SystemExit as e:
        pytest.fail(
            f"knowledge 子命令解析失败（退出码 {e.code}）: {' '.join(cmd)}\n"
            "请检查 main.py 中 knowledge 子命令注册"
        )


def test_cognition_cli_subcommands_registered() -> None:
    """12 个子命令（+ validate 别名 = 13）全部在 argparse 注册。"""
    parser = _build_parser()
    knowledge_sub = None
    for action in parser._subparsers._actions:  # type: ignore[union-attr]
        if hasattr(action, "_name_parser_map"):
            know = action._name_parser_map.get("knowledge")
            if know is None:
                continue
            for sub_action in know._subparsers._actions:  # type: ignore[union-attr]
                if hasattr(sub_action, "_name_parser_map"):
                    knowledge_sub = sub_action
                    break
            break
    assert knowledge_sub is not None, "knowledge 子解析器未找到"
    registered = set(knowledge_sub._name_parser_map.keys())
    expected = {
        "cognition-add", "cognition-list", "cognition-show",
        "cognition-refine", "cognition-deprecate",
        "instance-add", "instance-batch-add", "instance-pending",
        "instance-validate", "validate",
        "instance-list",
        "review-generate", "review-show", "review-confirm", "review-list",
    }
    missing = expected - registered
    assert not missing, f"knowledge 下未注册: {missing}"


# ──────────────────────────────────────────────────────────────
# 子进程端到端：真 DB + JSON 输出
# ──────────────────────────────────────────────────────────────
MAIN_PY = SCRIPTS_DIR / "main.py"


def _run_cli(db_path: Path, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["TRADE_DB_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(MAIN_PY), *args],
        cwd=str(SCRIPTS_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_cli_json(db_path: Path, *args: str) -> dict:
    rc, out, err = _run_cli(db_path, *args)
    assert rc == 0, f"CLI 非零退出: args={args!r} rc={rc}\nstdout:{out}\nstderr:{err}"
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        pytest.fail(f"CLI 未输出合法 JSON：{exc}\nstdout:{out}\nstderr:{err}")


@pytest.fixture
def cli_db(tmp_path):
    return tmp_path / "cli_cognition.db"


def _seed_daily_market(db_path: Path, dates: list[str]) -> None:
    """为 outcome_fact_source 查表校验预写入 daily_market.date 行。

    CLI 测试用子进程跑 main.py，此处用 db.connection.get_connection 以保证
    row_factory=sqlite3.Row（migrate() 部分 upgrade 分支需要）。
    """
    from db.connection import get_connection
    from db.migrate import migrate
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        migrate(conn)
        for d in dates:
            conn.execute(
                "INSERT OR IGNORE INTO daily_market (date) VALUES (?)", (d,)
            )
        conn.commit()
    finally:
        conn.close()


def test_cli_knowledge_help_shows_cognition_commands(cli_db):
    """`knowledge --help` 列出 cognition-add / review-generate 等。"""
    rc, out, _ = _run_cli(cli_db, "knowledge", "--help")
    assert rc == 0
    for cmd in ("cognition-add", "cognition-list", "instance-add",
                "instance-validate", "review-generate", "review-confirm"):
        assert cmd in out, f"--help 缺少命令：{cmd}"


def test_cli_cognition_list_empty_json(cli_db):
    """空库下 cognition-list --json 返回 cognitions=[]。"""
    payload = _run_cli_json(cli_db, "knowledge", "cognition-list", "--json")
    assert payload["status"] == "ok"
    assert payload["subcommand"] == "cognition-list"
    assert payload["cognitions"] == []


def test_cli_cognition_add_requires_input_by(cli_db):
    """缺 --input-by → argparse 层直接报错（exit 2）。"""
    rc, _, err = _run_cli(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal",
        "--title", "T",
        "--description", "D",
    )
    assert rc == 2
    assert "--input-by" in err


def test_cli_cognition_add_unknown_category_reports_validation_error(cli_db):
    """未知 category → status=validation_error，CLI 进程退出 0（走 _emit_cli_result）。"""
    payload = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "phantom",
        "--title", "T",
        "--description", "D",
        "--input-by", "pytest",
        "--json",
    )
    assert payload["status"] == "validation_error"
    assert "category" in payload["message"]


def test_cli_end_to_end_cognition_add_list_show(cli_db):
    """add → list 有一条 → show 含 instances_stats（空）。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal",
        "--title", "尾盘加速→次日冲高",
        "--description", "连续加速收盘，次日惯性冲高",
        "--evidence-level", "hypothesis",
        "--input-by", "pytest",
        "--json",
    )
    assert added["status"] == "ok"
    cog_id = added["cognition"]["cognition_id"]
    assert cog_id.startswith("cog_")

    listed = _run_cli_json(cli_db, "knowledge", "cognition-list", "--json")
    assert any(c["cognition_id"] == cog_id for c in listed["cognitions"])

    shown = _run_cli_json(
        cli_db, "knowledge", "cognition-show", "--id", cog_id, "--json"
    )
    assert shown["status"] == "ok"
    assert shown["cognition"]["cognition_id"] == cog_id
    assert shown["cognition"]["instances_stats"] == {"total": 0, "by_outcome": {}}


def test_cli_end_to_end_instance_add_and_pending(cli_db):
    """add cognition → add instance → instance-pending 至少含该 instance。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]

    inst = _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id,
        "--observed-date", "2026-04-10",
        "--source-type", "teacher_note",
        "--input-by", "pytest", "--json",
    )
    assert inst["status"] == "ok"
    inst_id = inst["instance"]["instance_id"]
    assert inst_id.startswith("inst_")

    pending = _run_cli_json(
        cli_db,
        "knowledge", "instance-pending", "--json",
    )
    ids = {row["instance_id"] for row in pending["instances"]}
    assert inst_id in ids


def test_cli_validate_requires_fact_source_format(cli_db):
    """validate 别名缺 YYYY-MM-DD → validation_error。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    inst = _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id, "--observed-date", "2026-04-10",
        "--source-type", "teacher_note", "--input-by", "pytest", "--json",
    )
    inst_id = inst["instance"]["instance_id"]

    # 走 validate 别名，outcome_fact_source 仅填 table（缺日期段）
    payload = _run_cli_json(
        cli_db,
        "knowledge", "validate",
        "--instance-id", inst_id,
        "--outcome", "validated",
        "--outcome-fact-source", "daily_market",  # invalid
        "--input-by", "pytest", "--json",
    )
    assert payload["status"] == "validation_error"
    assert "outcome_fact_source" in payload["message"]


def test_cli_end_to_end_validate_updates_parent_counts(cli_db):
    """instance-validate 后 cognition-show 显示 validated=1、invalidated=0。"""
    _seed_daily_market(cli_db, ["2026-04-11"])
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    inst = _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id, "--observed-date", "2026-04-10",
        "--source-type", "teacher_note", "--input-by", "pytest", "--json",
    )
    inst_id = inst["instance"]["instance_id"]

    validated = _run_cli_json(
        cli_db,
        "knowledge", "instance-validate",
        "--instance-id", inst_id,
        "--outcome", "validated",
        "--outcome-fact-source", "daily_market:2026-04-11",
        "--input-by", "pytest", "--json",
    )
    assert validated["status"] == "ok"
    assert validated["cognition"]["validated_count"] == 1
    assert validated["cognition"]["invalidated_count"] == 0

    shown = _run_cli_json(
        cli_db, "knowledge", "cognition-show", "--id", cog_id, "--json"
    )
    assert shown["cognition"]["validated_count"] == 1
    assert shown["cognition"]["instances_stats"]["by_outcome"].get("validated") == 1


def test_cli_review_generate_and_confirm(cli_db):
    """review-generate → review-show(draft) → review-confirm → status=confirmed。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id, "--observed-date", "2026-04-08",
        "--source-type", "teacher_note", "--input-by", "pytest", "--json",
    )

    rev = _run_cli_json(
        cli_db,
        "knowledge", "review-generate",
        "--period-type", "weekly",
        "--from", "2026-04-07", "--to", "2026-04-11",
        "--input-by", "pytest", "--json",
    )
    assert rev["status"] == "ok"
    rev_id = rev["review"]["review_id"]
    assert rev_id.startswith("rev_")
    assert rev["review"]["status"] == "draft"
    active = json.loads(rev["review"]["active_cognitions_json"])
    assert cog_id in active

    shown = _run_cli_json(
        cli_db, "knowledge", "review-show", "--id", rev_id, "--json"
    )
    assert shown["review"]["status"] == "draft"

    confirmed = _run_cli_json(
        cli_db,
        "knowledge", "review-confirm",
        "--id", rev_id,
        "--user-reflection", "本周反思",
        "--action-items-json", '["下周跟踪M1"]',
        "--input-by", "pytest", "--json",
    )
    assert confirmed["status"] == "ok"
    assert confirmed["review"]["status"] == "confirmed"
    assert confirmed["review"]["user_reflection"] == "本周反思"


def test_cli_cognition_refine_and_deprecate(cli_db):
    """refine version+=1 且 deprecate 后 status=deprecated。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    assert added["cognition"]["version"] == 1

    refined = _run_cli_json(
        cli_db,
        "knowledge", "cognition-refine",
        "--id", cog_id,
        "--description", "精炼后的描述",
        "--input-by", "pytest", "--json",
    )
    assert refined["cognition"]["version"] == 2
    assert refined["cognition"]["description"] == "精炼后的描述"

    dep = _run_cli_json(
        cli_db,
        "knowledge", "cognition-deprecate",
        "--id", cog_id, "--reason", "证伪占优",
        "--input-by", "pytest", "--json",
    )
    assert dep["cognition"]["status"] == "deprecated"
    tags = json.loads(dep["cognition"]["tags"])
    assert any("deprecated_reason" in t and "证伪占优" in t for t in tags)


def test_cli_instance_unknown_cognition_reports_validation_error(cli_db):
    """未知 cognition_id → validation_error 且进程退出 0。"""
    payload = _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", "cog_does_not_exist",
        "--observed-date", "2026-04-10",
        "--source-type", "teacher_note",
        "--input-by", "pytest", "--json",
    )
    assert payload["status"] == "validation_error"
    assert "cognition not found" in payload["message"]


def test_cli_cognition_refine_status_merged_rejected(cli_db):
    """cognition-refine --status merged 被 argparse choices 挡下，返回非 0 退出码。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]

    rc, _out, err = _run_cli(
        cli_db,
        "knowledge", "cognition-refine",
        "--id", cog_id,
        "--status", "merged",
        "--input-by", "pytest",
        "--json",
    )
    assert rc != 0, f"期望非 0 退出码（argparse choices 拒绝 merged），实际: {rc}"
    assert "--status" in err or "merged" in err or "invalid choice" in err


def test_cli_validate_fact_source_not_in_whitelist(cli_db):
    """validate 时 outcome_fact_source 表不在白名单 → validation_error。"""
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    inst = _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id, "--observed-date", "2026-04-10",
        "--source-type", "teacher_note", "--input-by", "pytest", "--json",
    )
    inst_id = inst["instance"]["instance_id"]

    payload = _run_cli_json(
        cli_db,
        "knowledge", "instance-validate",
        "--instance-id", inst_id,
        "--outcome", "validated",
        "--outcome-fact-source", "fake_table:2026-04-15",
        "--input-by", "pytest", "--json",
    )
    assert payload["status"] == "validation_error"
    assert "白名单" in payload["message"]


# ──────────────────────────────────────────────────────────────
# review-list 端到端
# ──────────────────────────────────────────────────────────────
def test_cli_review_list_empty_json(cli_db):
    """空库下 review-list --json 返回 reviews=[]。"""
    payload = _run_cli_json(cli_db, "knowledge", "review-list", "--json")
    assert payload["status"] == "ok"
    assert payload["subcommand"] == "review-list"
    assert payload["reviews"] == []


def test_cli_review_list_filter_period_type(cli_db):
    """生成两条复盘后，--period-type weekly 只返回一条。"""
    # 先建一条 cognition + instance 以便走 generate_review 的聚合
    added = _run_cli_json(
        cli_db,
        "knowledge", "cognition-add",
        "--category", "signal", "--title", "T", "--description", "D",
        "--input-by", "pytest", "--json",
    )
    cog_id = added["cognition"]["cognition_id"]
    _run_cli_json(
        cli_db,
        "knowledge", "instance-add",
        "--cognition-id", cog_id, "--observed-date", "2026-04-08",
        "--source-type", "teacher_note", "--input-by", "pytest", "--json",
    )

    _run_cli_json(
        cli_db,
        "knowledge", "review-generate",
        "--period-type", "weekly",
        "--from", "2026-04-07", "--to", "2026-04-11",
        "--input-by", "pytest", "--json",
    )
    _run_cli_json(
        cli_db,
        "knowledge", "review-generate",
        "--period-type", "monthly",
        "--from", "2026-04-01", "--to", "2026-04-30",
        "--input-by", "pytest", "--json",
    )

    listed = _run_cli_json(
        cli_db, "knowledge", "review-list",
        "--period-type", "weekly", "--json",
    )
    assert listed["status"] == "ok"
    assert len(listed["reviews"]) == 1
    assert listed["reviews"][0]["period_type"] == "weekly"


def test_cli_review_list_invalid_status_rejected(cli_db):
    """review-list --status invalid 由 argparse choices 拒绝（非 0 退出码）。"""
    rc, _out, err = _run_cli(
        cli_db,
        "knowledge", "review-list",
        "--status", "invalid",
        "--json",
    )
    assert rc != 0, f"期望非 0 退出码，实际 rc={rc}"
    assert "--status" in err or "invalid choice" in err or "invalid" in err
