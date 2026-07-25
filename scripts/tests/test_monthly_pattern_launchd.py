from __future__ import annotations

import plistlib
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_monthly_pattern_launchd_runs_once_on_day_two() -> None:
    plist_path = (
        _repo_root()
        / "deploy/launchd/com.alyx.tradesystem.monthly-pattern.plist"
    )
    with plist_path.open("rb") as stream:
        payload = plistlib.load(stream)

    assert payload["StartCalendarInterval"] == {
        "Day": 2,
        "Hour": 23,
        "Minute": 10,
    }
    assert payload["RunAtLoad"] is False


def test_monthly_pattern_runner_exports_sourced_credentials_to_python() -> None:
    runner = (
        _repo_root() / "deploy/launchd/monthly-pattern-runner.sh"
    ).read_text(encoding="utf-8")

    assert 'set -a; source "$REPO_ROOT/scripts/.env"; set +a' in runner
    assert 'set -a; source "$HOME/.config/tradeSystem.env"; set +a' in runner
    assert "monthly-pattern daily --input-by launchd" in runner
