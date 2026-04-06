#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

api_pid=""
web_pid=""

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n "${web_pid}" ]] && kill -0 "${web_pid}" 2>/dev/null; then
    kill "${web_pid}" 2>/dev/null || true
  fi
  if [[ -n "${api_pid}" ]] && kill -0 "${api_pid}" 2>/dev/null; then
    kill "${api_pid}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit "${code}"
}

trap cleanup EXIT INT TERM

echo "[dev] starting api on http://localhost:8000"
(
  cd "$repo_root/scripts"
  python3 -m uvicorn api.main:app --reload --port 8000
) &
api_pid=$!

echo "[dev] starting web on http://localhost:5173"
(
  cd "$repo_root/web"
  npm run dev
) &
web_pid=$!

wait -n "${api_pid}" "${web_pid}"
