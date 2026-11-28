import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("mode", "statuses", "expected"),
    [("pre", (0, 1, 1, 1, 1, 1, 1, 1), 0), ("post", (0,) * 8, 0)]
    + [("post", (0,) * index + tuple(range(index + 1, 9)), index + 1)
       for index in range(8)],
)
def test_today_runner_runs_requested_collectors(tmp_path, mode, statuses, expected):
    runner = tmp_path / "deploy/launchd/today-runner.sh"
    runner.parent.mkdir(parents=True)
    shutil.copy(ROOT / "deploy/launchd/today-runner.sh", runner)
    tools = tmp_path / "scripts/tools"
    tools.mkdir(parents=True)
    trace = tmp_path / "calls"
    paths = [tools.parent / "main.py", tools / "course_core_minutes.py",
             tools / "course_opening_auction.py", tools / "theme_intraday_breadth.py",
             tools / "course_cross_market.py", tools / "broad_etf_minutes.py",
             tools / "domestic_index_futures.py", tools / "bank_broker_intraday.py"]
    for path, status in zip(paths, statuses):
        path.write_text(
            "import json, os, sys\n"
            "with open(os.environ['TEST_INVOCATIONS'], 'a') as f:\n"
            "    f.write(json.dumps(sys.argv) + '\\n')\n"
            f"raise SystemExit({status})\n"
        )
    result = subprocess.run(
        ["/bin/bash", str(runner), mode], env={"TEST_INVOCATIONS": str(trace)},
        capture_output=True, text=True,
    )
    assert result.returncode == expected, result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    main_call = calls[0]
    assert main_call[0:3] == ["scripts/main.py", mode, "--date"]
    assert len(calls) == (len(paths) if mode == "post" else 1)
    if mode == "post":
        assert calls[1] == ["scripts/tools/course_core_minutes.py", "--date",
                            main_call[3], "--input-by", "launchd", "--retry-previous"]
        assert calls[2] == ["scripts/tools/course_opening_auction.py", "--date",
                            main_call[3], "--input-by", "launchd"]
        assert calls[3] == ["scripts/tools/theme_intraday_breadth.py", "--date",
                            main_call[3], "--input-by", "launchd", "--save"]
        assert calls[4] == ["scripts/tools/course_cross_market.py", "--replay-day", "--date",
                            main_call[3], "--input-by", "launchd"]
        assert calls[5] == ["scripts/tools/broad_etf_minutes.py", "--replay-day", "--date",
                            main_call[3], "--input-by", "launchd"]
        assert calls[6] == ["scripts/tools/domestic_index_futures.py", "--date",
                            main_call[3], "--input-by", "launchd"]
        assert calls[7] == ["scripts/tools/bank_broker_intraday.py", "--date",
                            main_call[3], "--input-by", "launchd", "--save"]
