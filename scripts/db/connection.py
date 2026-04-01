"""SQLite 连接管理：WAL 模式、busy_timeout、外键约束。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB_PATH = Path(
    os.environ.get("TRADE_DB_PATH")
    or Path(__file__).resolve().parent.parent.parent / "data" / "trade.db"
)


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """创建并配置 SQLite 连接。

    - WAL 模式：多进程可同时读取，写入不阻塞读取
    - busy_timeout 5 秒：写冲突时自动等待
    - 外键约束开启
    """
    path = str(db_path or _DEFAULT_DB_PATH)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db(db_path: str | Path | None = None):
    """上下文管理器：自动 commit / rollback / close。CLI 和脚本使用。"""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
