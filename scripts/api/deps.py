"""FastAPI 依赖注入。"""
from __future__ import annotations

from db.connection import get_connection, get_readonly_connection
from db.migrate import migrate


def get_provider_registry():
    """按需初始化 provider registry，失败时降级为 None。"""
    try:
        from main import load_config, setup_providers
        from utils.network_env import without_standard_http_proxy

        config = load_config()
        with without_standard_http_proxy():
            return setup_providers(config)
    except Exception:
        return None


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


def get_readonly_db_conn():
    """请求级强只读连接：SQLite mode=ro，不执行 migrate，不做隐式写入。"""
    conn = get_readonly_connection()
    try:
        yield conn
    finally:
        conn.close()
