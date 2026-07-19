from pathlib import Path


def test_trend_leader_runner_exports_expected_path():
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "deploy/launchd/trend-leader-runner.sh"
    path_exports = [
        line
        for line in runner.read_text(encoding="utf-8").splitlines()
        if line.startswith("export PATH=")
    ]

    assert path_exports == [
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"'
    ]
