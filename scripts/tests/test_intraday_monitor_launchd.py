"""intraday-monitor launchd 部署契约。"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "deploy/launchd/intraday-monitor-runner.sh"
PLIST = REPO_ROOT / "deploy/launchd/com.alyx.tradesystem.intraday-monitor.plist"
TRANSITION_GUARD = REPO_ROOT / "deploy/launchd/intraday_monitor_transition_guard.py"


def _load_transition_guard():
    spec = importlib.util.spec_from_file_location("intraday_monitor_transition_guard", TRANSITION_GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_contract():
    text = RUNNER.read_text(encoding="utf-8")
    assert RUNNER.exists() and os.access(RUNNER, os.X_OK)
    assert 'export PATH="$HOME/.local/bin:' in text
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in text
    assert 'REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"' in text
    assert 'cd "$REPO_ROOT"' in text
    assert 'source "$REPO_ROOT/scripts/.env"' in text
    assert 'source "$HOME/.config/tradeSystem.env"' in text
    assert "TZ=Asia/Shanghai /bin/date '+%u'" in text
    assert "TZ=Asia/Shanghai /bin/date '+%H%M'" in text
    assert "-lt 930" in text and "-ge 1506" in text
    assert 'if [ "$SHANGHAI_WEEKDAY" -gt 5 ]; then' in text
    assert "DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set}" in text
    assert "DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}" in text
    assert "exec /usr/bin/python3 scripts/main.py intraday-monitor check --json" in text


def test_runner_uses_the_checkout_that_contains_it():
    """避免已加载任务误跑另一个含未提交代码的工作区。"""
    text = RUNNER.read_text(encoding="utf-8")
    assert 'REPO_ROOT="/Users/alyx/tradeSystem"' not in text


def test_runner_syntax():
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)


def test_plist_contract():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.alyx.tradesystem.intraday-monitor"
    assert data["ProgramArguments"] == [
        "/Users/alyx/tradeSystem/.worktrees/intraday-monitor-runtime/"
        "deploy/launchd/intraday-monitor-runner.sh"
    ]
    assert data["StartInterval"] == 180
    assert data["StartCalendarInterval"] == {"Hour": 9, "Minute": 59}
    assert data["RunAtLoad"] is False
    assert data["KeepAlive"] is False
    assert data["StandardOutPath"] == data["StandardErrorPath"]
    assert data["StandardOutPath"] == "/tmp/tradesystem-intraday-monitor.log"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 原生 plutil 校验；结构契约在所有平台执行")
def test_plist_parses_with_plutil():
    output = subprocess.run(
        ["plutil", "-lint", str(PLIST)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK" in output.stdout


def test_transition_guard_parses_default_rule_ids_without_executing_source():
    guard = _load_transition_guard()
    source = '''
A = MonitorRule(rule_id="rule-a", instrument_name="A", code="1", threshold=1)
B = MonitorRule("rule-b", "B", "2", 2)
UNUSED = MonitorRule(rule_id="unused", instrument_name="U", code="3", threshold=3)
DEFAULT_RULES: tuple[MonitorRule, ...] = (A, B)
'''
    assert guard.default_rule_ids(source) == {"rule-a", "rule-b"}


def test_transition_guard_parses_market_scan_rule_ids_without_executing_source():
    guard = _load_transition_guard()
    source = '''
A = MarketScanRule(rule_id="scan-a", display_name="A", start_time=None, end_time=None, min_amount_yi=1)
DEFAULT_MARKET_SCAN_RULES = (A,)
'''
    assert guard.default_rule_ids(
        source,
        rules_path="scripts/services/intraday_monitor/market_scan.py",
        constructor_name="MarketScanRule",
        collection_name="DEFAULT_MARKET_SCAN_RULES",
    ) == {"scan-a"}


@pytest.fixture
def target_repo(tmp_path):
    """一份自包含的目标提交；无需 CI fetch 历史，也不随生产规则增删漂移。"""
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    rules = repo / "scripts/services/intraday_monitor/rules.py"
    rules.parent.mkdir(parents=True)
    rules.write_text(
        'A = MonitorRule(rule_id="known-rule")\nDEFAULT_RULES = (A,)\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "commit", "-m", "fixture target"],
        check=True, capture_output=True,
    )
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    # 工作区故意与提交不同，确保 guard 读取指定提交，而不是磁盘当前文件。
    rules.write_text('DEFAULT_RULES = ()\n', encoding="utf-8")
    return repo, commit


def test_transition_guard_blocks_pending_unknown_to_target_commit(tmp_path, target_repo):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"pending_events": [{"rule_id": "future-rule"}]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(TRANSITION_GUARD),
            "--repo",
            str(target_repo[0]),
            "--target-commit",
            target_repo[1],
            "--state-path",
            str(state_path),
        ],
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["status"] == "blocked_pending_rules"
    assert payload["unknown_rule_ids"] == ["future-rule"]


def test_transition_guard_allows_pending_known_to_target_commit(tmp_path, target_repo):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"pending_events": [{"rule_id": "known-rule"}]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(TRANSITION_GUARD),
            "--repo",
            str(target_repo[0]),
            "--target-commit",
            target_repo[1],
            "--state-path",
            str(state_path),
        ],
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "safe"


def test_transition_guard_missing_commit_fails_closed(tmp_path, target_repo):
    result = subprocess.run(
        [sys.executable, str(TRANSITION_GUARD), "--repo", str(target_repo[0]),
         "--target-commit", "missing-target", "--state-path", str(tmp_path / "absent.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "guard_failed"
