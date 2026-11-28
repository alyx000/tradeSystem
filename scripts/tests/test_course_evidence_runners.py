"""生产 shell 入口使用临时行情和快讯程序，避免联网与发送。"""
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
STEPS = ("intraday_events", "theme_catalysts", "course_cross_market", "broad_etf_minutes")
STUB = """
import json, os, sys, time
from pathlib import Path
folder = Path(os.environ["TEST_INVOCATIONS"])
name = Path(sys.argv[0]).stem
(folder / (name + ".json")).write_text(json.dumps(sys.argv[1:]))
if os.environ.get("CHECK_CONCURRENT") == "1":
    deadline = time.monotonic() + 5
    while len(list(folder.glob("*.json"))) < int(os.environ["TEST_EXPECTED_CALLS"]):
        if time.monotonic() > deadline:
            sys.exit(80)
        time.sleep(0.01)
sys.exit(json.loads(os.environ["TEST_EXITS"]).get(name, 0))
"""


def run_entry(tmp_path, entry, clock, exits, *, args=(), concurrent=False):
    runner = tmp_path / "deploy/launchd" / entry
    runner.parent.mkdir(parents=True)
    clock_program = tmp_path / "test-date"
    clock_program.write_text("#!/bin/bash\necho " + clock + "\n")
    clock_program.chmod(0o755)
    script = (ROOT / "deploy/launchd" / entry).read_text()
    script = script.replace("/bin/date", str(clock_program))
    script = script.replace("$HOME/.config/tradeSystem.env", str(tmp_path / "unused.env"))
    runner.write_text(script)
    tools = tmp_path / "scripts/tools"
    tools.mkdir(parents=True)
    for name in (*STEPS, "course_opening_auction"):
        (tools / (name + ".py")).write_text(STUB)
    (tools.parent / "main.py").write_text(STUB)
    invocations = tmp_path / "invocations"
    invocations.mkdir()
    env = {
        "HOME": os.environ["HOME"], "TEST_INVOCATIONS": str(invocations),
        "TEST_EXITS": json.dumps(exits), "CHECK_CONCURRENT": str(int(concurrent)),
        "TEST_EXPECTED_CALLS": str(len(STEPS) + int(clock.endswith("09 34"))),
    }
    run = subprocess.run(["/bin/bash", str(runner), *args], env=env, capture_output=True,
                         text=True, timeout=12)
    return run.returncode, {p.stem: json.loads(p.read_text()) for p in invocations.glob("*.json")}


@pytest.mark.parametrize("exits,expected", [({}, 0), ({"intraday_events": 4, "theme_catalysts": 3}, 4),
                                          ({"course_cross_market": 2}, 2), ({"broad_etf_minutes": 2}, 2),
                                          ({"course_opening_auction": 2}, 0)])
def test_same_slot_collectors_start_independently(tmp_path, exits, expected):
    result, calls = run_entry(tmp_path, "intraday-events-runner.sh", "2026-11-27 5 09 34",
                              exits, concurrent=True)
    assert result == expected
    stamp = "2026-11-27T09:32:00+08:00"
    assert calls["intraday_events"] == ["--interval-minutes", "10", "--push", "--input-by",
                                        "launchd", "--scheduled-at", stamp]
    assert calls["course_opening_auction"] == ["--date", "2026-11-27", "--scheduled-at", stamp, "--input-by", "launchd"]
    for name in STEPS[1:]:
        assert calls[name] == ["--scheduled-at", stamp, "--input-by", "launchd"]


@pytest.mark.parametrize("clock", ["2026-11-27 5 09 35", "2026-11-27 5 12 02", "2026-11-28 6 10 02"])
def test_outside_market_slot_skips_all_collectors(tmp_path, clock):
    assert run_entry(tmp_path, "intraday-events-runner.sh", clock, {}) == (0, {})


@pytest.mark.parametrize("clock,args,exits,expected,follow,lookback", [
    ("2026-11-27 5", (), {}, 0, True, "24"),
    ("2026-11-27 5", (), {"main": 5}, 5, False, "24"),
    ("2026-11-27 5", (), {"theme_catalysts": 3}, 3, True, "24"),
    ("2026-11-29 7", (), {}, 0, False, "54"),
    ("2026-11-27 5", ("--dry-run",), {}, 0, False, "24"),
])
def test_macro_replay_uses_completed_weekday_archive(tmp_path, clock, args, exits, expected, follow, lookback):
    status, calls = run_entry(tmp_path, "macro-flash-runner.sh", clock, exits, args=args)
    assert status == expected
    assert calls["main"] == ["macro-flash", "run", "--no-push", "--lookback-hours", lookback, *args]
    assert ("theme_catalysts" in calls) == follow
    if follow:
        assert calls["theme_catalysts"] == ["--archive", "--date", "2026-11-27", "--input-by", "launchd"]



def test_opening_auction_only_runs_in_first_intraday_slot(tmp_path):
    result, calls = run_entry(tmp_path, "intraday-events-runner.sh", "2026-11-27 5 09 42", {})
    assert result == 0 and set(calls) == set(STEPS)
