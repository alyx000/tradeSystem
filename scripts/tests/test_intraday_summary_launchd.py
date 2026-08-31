"""intraday-summary launchd 部署契约。"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "deploy/launchd/intraday-summary-runner.sh"
PLIST = REPO_ROOT / "deploy/launchd/com.alyx.tradesystem.intraday-summary.plist"


def test_runner_contract():
    text = RUNNER.read_text(encoding="utf-8")
    assert RUNNER.exists() and os.access(RUNNER, os.X_OK)
    assert 'REPO_ROOT="/Users/alyx/tradeSystem"' in text
    assert 'source "$REPO_ROOT/scripts/.env"' in text
    assert 'source "$HOME/.config/tradeSystem.env"' in text
    assert "TZ=Asia/Shanghai /bin/date '+%u'" in text
    assert "TZ=Asia/Shanghai /bin/date '+%H%M'" in text
    assert "093[0-5]" in text and "150[0-5]" in text
    assert "DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set}" in text
    assert "DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}" in text
    assert "exec /usr/bin/python3 scripts/main.py intraday-summary run --json" in text


def test_runner_syntax():
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)


def test_plist_contract():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.alyx.tradesystem.intraday-summary"
    assert data["ProgramArguments"] == [
        "/Users/alyx/tradeSystem/deploy/launchd/intraday-summary-runner.sh"
    ]
    assert data["StartInterval"] == 60
    assert data["RunAtLoad"] is False
    assert data["KeepAlive"] is False
    assert data["StandardOutPath"] == data["StandardErrorPath"]


def test_plist_parses_with_plutil():
    output = subprocess.run(
        ["plutil", "-lint", str(PLIST)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK" in output.stdout
