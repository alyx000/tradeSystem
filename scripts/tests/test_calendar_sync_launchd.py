"""宏观日历 06:30 launchd 模板契约。"""
from __future__ import annotations

import os
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy/launchd/calendar-sync-runner.sh"
PLIST = ROOT / "deploy/launchd/com.alyx.tradesystem.calendar-sync.plist"


def test_calendar_sync_runner_contract():
    text = RUNNER.read_text(encoding="utf-8")
    assert os.access(RUNNER, os.X_OK)
    assert 'cd "$REPO_ROOT"' in text
    assert 'source "$REPO_ROOT/scripts/.env"' in text
    assert "TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}" in text
    assert "scripts/main.py prefetch-calendar" in text
    assert "--days 14" in text
    assert "--input-by launchd_calendar_sync" in text
    assert "--json" in text


def test_calendar_sync_plist_contract():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.alyx.tradesystem.calendar-sync"
    assert data["ProgramArguments"] == ["/bin/bash", str(RUNNER)]
    assert data["StartCalendarInterval"] == {"Hour": 6, "Minute": 30}
    assert data["RunAtLoad"] is False
    assert data["StandardOutPath"] == data["StandardErrorPath"]
