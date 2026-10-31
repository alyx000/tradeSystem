"""七天横盘不创新低 launchd 调度与凭据边界。"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "deploy/launchd/no-new-low-runner.sh"
PLIST = REPO_ROOT / "deploy/launchd/com.alyx.tradesystem.no-new-low.plist"


def test_runner_contract_and_redacted_env():
    assert RUNNER.exists() and os.access(RUNNER, os.X_OK)
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)
    text = RUNNER.read_text(encoding="utf-8")
    assert "exec /usr/bin/python3 scripts/main.py no-new-low daily" in text
    assert text.index("set -a") < text.index('source "$REPO_ROOT/scripts/.env"') < text.index("set +a")
    for variable in ("DINGTALK_WEBHOOK_TOKEN", "DINGTALK_WEBHOOK_SECRET", "TUSHARE_TOKEN"):
        assert f"${{{variable}:+set}}" in text
        assert f"${variable}" not in text


def test_plist_weekdays_at_2320_and_no_run_at_load():
    subprocess.run(["plutil", "-lint", str(PLIST)], check=True, capture_output=True, text=True)
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.alyx.tradesystem.no-new-low"
    assert data["ProgramArguments"] == ["/Users/alyx/tradeSystem/deploy/launchd/no-new-low-runner.sh"]
    intervals = data["StartCalendarInterval"]
    assert sorted(item["Weekday"] for item in intervals) == [1, 2, 3, 4, 5]
    assert {(item["Hour"], item["Minute"]) for item in intervals} == {(23, 20)}
    assert data["RunAtLoad"] is False
    assert data["StandardOutPath"] == data["StandardErrorPath"] == "/tmp/tradesystem-no-new-low.log"
