"""盘中半小时扫描的滚动快照、outbox 与跨进程锁。"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_PATH = REPO_ROOT / "data" / "runs" / "intraday-summary" / "state.json"
DEFAULT_REPORT_ROOT = REPO_ROOT / "data" / "reports" / "intraday-summary"
STATE_VERSION = 1
HISTORY_LIMIT = 100


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "trade_date": None,
        "universe": None,
        "industry_map": None,
        "last_snapshot": None,
        "captured_slot_ids": [],
        "pending_reports": [],
        "sent_slot_ids": [],
        "suppressed_slot_ids": [],
        "expired_pending_ids": [],
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        raise ValueError("盘中半小时扫描状态文件版本或结构非法")
    defaults = empty_state()
    for key, value in defaults.items():
        payload.setdefault(key, value)
    if not isinstance(payload["pending_reports"], list):
        raise ValueError("盘中半小时扫描 pending_reports 字段非法")
    if not isinstance(payload["captured_slot_ids"], list):
        raise ValueError("盘中半小时扫描 captured_slot_ids 字段非法")
    if not isinstance(payload["sent_slot_ids"], list):
        raise ValueError("盘中半小时扫描 sent_slot_ids 字段非法")
    return payload


def save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(text, encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def reset_for_day(state: dict, day: str) -> None:
    if state.get("trade_date") == day:
        return
    pending = list(state.get("pending_reports") or [])
    expired = list(state.get("expired_pending_ids") or [])
    expired.extend(str(item.get("item_id") or "") for item in pending)
    state.update(empty_state())
    state["trade_date"] = day
    state["expired_pending_ids"] = [item for item in expired if item][-HISTORY_LIMIT:]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
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
