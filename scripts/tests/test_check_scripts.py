"""统一检查入口必须隔离数据库和 Python 缓存，不依赖机器上的缓存目录。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def test_check_scripts_isolates_and_cleans_test_db_and_python_cache(tmp_path):
    root = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "python3"
    recorder.write_text(
        '#!/bin/sh\n'
        'printf "%s|%s|%s\\n" "$*" "$TRADE_DB_PATH" "$PYTHONPYCACHEPREFIX" >> "$CHECK_ENV_LOG"\n'
        'mkdir -p "$PYTHONPYCACHEPREFIX"\n'
        'touch "$TRADE_DB_PATH"\n', encoding="utf-8",
    )
    recorder.chmod(0o700)
    log = tmp_path / "commands.log"
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                   "CHECK_ENV_LOG": str(log), "TMPDIR": str(tmp_path),
                   "TRADE_DB_PATH": str(tmp_path / "must-not-use.db"),
                   "PYTHONPYCACHEPREFIX": "/nonexistent/caller-cache"}
    result = subprocess.run(["bash", str(root / "scripts/check.sh")], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    recorded = [line.split("|") for line in log.read_text().splitlines()]
    assert [row[0] for row in recorded] == ["-m compileall .", "-m pytest -q"]
    for _, database, cache in recorded:
        workspace = Path(database).parent
        assert workspace.parent == tmp_path
        assert workspace.name.startswith("tradesystem-check.")
        assert Path(cache) == workspace / "pycache"
        assert not workspace.exists()
    assert not (tmp_path / "must-not-use.db").exists()
