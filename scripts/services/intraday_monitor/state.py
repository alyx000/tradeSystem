"""盘中监控本地状态与跨进程锁。"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_PATH = REPO_ROOT / "data" / "runs" / "intraday-monitor" / "state.json"
STATE_VERSION = 1


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "last_fetch_at": None,
        "rules": {},
        "pending_events": [],
        "sent_event_ids": [],
        "expired_event_ids": [],
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        raise ValueError("盘中监控状态文件版本或结构非法")
    payload.setdefault("last_fetch_at", None)
    payload.setdefault("rules", {})
    payload.setdefault("pending_events", [])
    payload.setdefault("sent_event_ids", [])
    payload.setdefault("expired_event_ids", [])
    if not isinstance(payload["rules"], dict) or not isinstance(payload["pending_events"], list):
        raise ValueError("盘中监控状态字段类型非法")
    return payload


def save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(data, encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def locked_state(path: Path) -> Iterator[dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield load_state(path)
