"""FastAPI 依赖注入。"""
from __future__ import annotations

from db.connection import get_connection
from db.migrate import migrate


def get_db_conn():
    """请求级连接：每个请求独立连接，请求结束自动关闭。

    异常时显式 rollback，与 CLI 侧 get_db() 行为一致。
    """
    conn = get_connection()
    migrate(conn)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
