"""盘后归档写入方按日期共用同一把文件锁。"""
from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def archive_lock(path: Path):
    # 锁文件不能随归档原子替换，也不能在解锁后删除。
    with path.with_name(".post-market.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_archive(path: Path) -> dict:
    with path.open("rb") as handle:
        content = yaml.load(handle, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(content, dict):
        raise ValueError(f"盘后归档格式错误：{path}")
    return content


def atomic_write_archive(path: Path, envelope: dict) -> None:
    body = yaml.dump(envelope, Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
                     allow_unicode=True, sort_keys=False)
    atomic_text(path, body)
