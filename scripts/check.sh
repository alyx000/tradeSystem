#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/scripts"

echo "[scripts-check] compileall"
python3 -m compileall .

echo "[scripts-check] pytest"
python3 -m pytest -q
