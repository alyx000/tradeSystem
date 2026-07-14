"""string-yang launchd 运行环境回归测试。"""
from __future__ import annotations

from pathlib import Path


def test_runner_path_includes_user_local_bin_for_agy() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = (repo_root / "deploy/launchd/string-yang-runner.sh").read_text(encoding="utf-8")

    assert 'export PATH="$HOME/.local/bin:' in runner
