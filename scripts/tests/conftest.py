"""保证从仓库根目录或 scripts/ 运行 pytest 时均能 import collectors、generators 等包。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 手动推送联调脚本，非标准单测（会 return bool）；请直接 `python scripts/tests/test_pushers.py`
collect_ignore = ["test_pushers.py"]


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """共享 API 测试客户端 fixture：隔离库 + FastAPI TestClient。

    用法：
        def test_xxx(api_client):
            client, db_path = api_client
            ...
    与 `test_api.py` 的 `client` fixture 保持同一 monkeypatch 模式，使用
    `db.connection._DEFAULT_DB_PATH` 切换 service / 路由底层连接目标。
    """
    from fastapi.testclient import TestClient

    db_path = tmp_path / "test_api.db"
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)

    from db.connection import get_connection
    from db.migrate import migrate

    conn = get_connection(db_path)
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()

    from api.main import app
    return TestClient(app), str(db_path)
