#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[check] verifying command index..."
cd "$repo_root"
make commands-check

run_web=false
run_scripts=false

if [[ $# -eq 0 ]]; then
  run_web=true
  run_scripts=true
else
  for arg in "$@"; do
    case "$arg" in
      --web)
        run_web=true
        ;;
      --scripts)
        run_scripts=true
        ;;
      *)
        echo "usage: bash check.sh [--web] [--scripts]" >&2
        exit 2
        ;;
    esac
  done
fi

if [[ "$run_web" == true ]]; then
  echo "[check] running web checks..."
  cd "$repo_root/web"
  npm run check
fi

if [[ "$run_scripts" == true ]]; then
  echo "[check] running scripts checks..."
  cd "$repo_root"
  bash scripts/check.sh
fi
