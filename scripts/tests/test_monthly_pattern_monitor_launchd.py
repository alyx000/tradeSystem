"""monthly-pattern monitor-daily launchd 部署契约。"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "deploy/launchd/monthly-pattern-monitor-runner.sh"
PLIST = (
    REPO_ROOT
    / "deploy/launchd/com.alyx.tradesystem.monthly-pattern-monitor.plist"
)


class TestRunner:
    def test_exists_and_executable(self):
        assert RUNNER.exists()
        assert os.access(RUNNER, os.X_OK)

    def test_sets_path_and_repo_root(self):
        text = RUNNER.read_text(encoding="utf-8")
        assert 'export PATH="$HOME/.local/bin:' in text
        assert 'REPO_ROOT="/Users/alyx/tradeSystem"' in text
        assert 'cd "$REPO_ROOT"' in text

    def test_sources_and_exports_both_env_files(self):
        text = RUNNER.read_text(encoding="utf-8")
        start, end = text.index("set -a"), text.index("set +a")
        assert start < text.index('source "$REPO_ROOT/scripts/.env"') < end
        assert start < text.index(
            'source "$HOME/.config/tradeSystem.env"'
        ) < end

    def test_timestamp_and_safe_credential_diagnostics(self):
        text = RUNNER.read_text(encoding="utf-8")
        assert (
            "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z') "
            "monthly-pattern monitor-daily start"
        ) in text
        for variable in (
            "DINGTALK_WEBHOOK_TOKEN",
            "DINGTALK_WEBHOOK_SECRET",
            "TUSHARE_TOKEN",
        ):
            assert f"{variable}=${{{variable}:+set}}" in text
            assert f'echo "{variable}=${variable}"' not in text
            assert f"${{{variable}:-" not in text

    def test_uses_absolute_python_and_dedicated_command(self):
        assert (
            "exec /usr/bin/python3 scripts/main.py "
            "monthly-pattern monitor-daily"
        ) in RUNNER.read_text(encoding="utf-8")

    def test_timezone_independent_tick_guard(self):
        text = RUNNER.read_text(encoding="utf-8")
        assert "TZ=Asia/Shanghai /bin/date '+%u'" in text
        assert "TZ=Asia/Shanghai /bin/date '+%H%M'" in text
        assert "10#$SHANGHAI_HHMM" in text
        assert "-lt 1910" in text
        assert "-ge 1925" in text


class TestPlist:
    def _load(self) -> dict:
        return plistlib.loads(PLIST.read_bytes())

    def test_identity_and_runner(self):
        data = self._load()
        assert (
            data["Label"]
            == "com.alyx.tradesystem.monthly-pattern-monitor"
        )
        assert data["ProgramArguments"] == [str(RUNNER)]

    def test_timezone_independent_tick(self):
        data = self._load()
        assert data["StartInterval"] == 900
        assert "StartCalendarInterval" not in data

    def test_no_run_at_load_or_keep_alive(self):
        data = self._load()
        assert data["RunAtLoad"] is False
        assert data["KeepAlive"] is False

    def test_logs_are_merged(self):
        data = self._load()
        assert data["StandardOutPath"] == data["StandardErrorPath"]
        assert (
            data["StandardOutPath"]
            == "/tmp/tradesystem-monthly-pattern-monitor.log"
        )


def test_plist_parses_with_plutil():
    output = subprocess.run(
        ["plutil", "-lint", str(PLIST)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK" in output.stdout
