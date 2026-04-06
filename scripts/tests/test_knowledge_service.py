"""L2: 资料层服务测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from db.connection import get_connection
from db.migrate import migrate
from services.knowledge_service import KnowledgeService

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_MAIN_PY = _SCRIPTS_DIR / "main.py"


def test_knowledge_cli_draft_teacher_note_missing_note(tmp_path):
    """CLI 与 API 一致：无效 note_id 输出 validation_error，不抛未捕获异常。"""
    db_path = tmp_path / "cli_knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    env = {**os.environ, "TRADE_DB_PATH": str(db_path)}
    r = subprocess.run(
        [
            sys.executable,
            str(_MAIN_PY),
            "knowledge",
            "draft-from-teacher-note",
            "--note-id",
            "999999",
            "--date",
            "2026-04-10",
            "--json",
        ],
        cwd=str(_SCRIPTS_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout.strip())
    assert out.get("status") == "validation_error"
    assert "笔记" in out.get("message", "") or "teacher" in out.get("message", "").lower()


def test_knowledge_cli_draft_asset_missing_id(tmp_path):
    db_path = tmp_path / "cli_knowledge2.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    env = {**os.environ, "TRADE_DB_PATH": str(db_path)}
    r = subprocess.run(
        [
            sys.executable,
            str(_MAIN_PY),
            "knowledge",
            "draft-from-asset",
            "--asset-id",
            "asset_nope",
            "--date",
            "2026-04-10",
            "--json",
        ],
        cwd=str(_SCRIPTS_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout.strip())
    assert out.get("status") == "validation_error"
    assert "资料" in out.get("message", "") or "asset" in out.get("message", "").lower()


def test_list_assets_filters_keyword_and_date(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = KnowledgeService(str(db_path))
    service.add_asset(
        asset_type="manual_note",
        title="alpha",
        content="正文含关键词beta",
        tags=[],
    )
    service.add_asset(
        asset_type="news_note",
        title="新闻",
        content="无关",
        tags=[],
    )
    rows = service.list_assets(limit=50, keyword="beta", asset_type="manual_note")
    assert len(rows) == 1
    assert rows[0]["title"] == "alpha"


def test_add_and_list_assets(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = KnowledgeService(str(db_path))
    asset = service.add_asset(
        asset_type="manual_note",
        title="AI算力复盘",
        content="AI算力回流，关注 300750.SZ，注意监管风险。",
        source="manual",
        tags=["AI算力"],
    )
    assert asset["title"] == "AI算力复盘"

    assets = service.list_assets()
    assert len(assets) == 1
    clues = json.loads(assets[0]["trade_clues"])
    assert "AI算力" in clues["themes"]
    assert clues["stocks"][0]["subject_code"] == "300750.SZ"


def test_add_asset_rejects_teacher_note(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = KnowledgeService(str(db_path))
    with pytest.raises(ValueError, match="teacher_notes"):
        service.add_asset(
            asset_type="teacher_note",
            title="x",
            content="y",
        )


def test_draft_from_asset_rejects_invalid_trade_clues(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO knowledge_assets
        (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
        VALUES (?, 'manual_note', ?, ?, NULL, '[]', '', ?)
        """,
        ("asset_bad_tc", "x", "y", "{oops"),
    )
    conn.commit()
    conn.close()

    service = KnowledgeService(str(db_path))
    with pytest.raises(ValueError, match="trade_clues|JSON"):
        service.draft_from_asset(asset_id="asset_bad_tc", trade_date="2026-04-10")


def test_draft_from_asset_rejects_legacy_teacher_note_row(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO knowledge_assets
        (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
        VALUES (?, 'teacher_note', ?, ?, NULL, '[]', '', '{}')
        """,
        ("asset_legacy_tn", "遗留老师资产", "正文"),
    )
    conn.commit()
    conn.close()

    service = KnowledgeService(str(db_path))
    with pytest.raises(ValueError, match="teacher_notes"):
        service.draft_from_asset(asset_id="asset_legacy_tn", trade_date="2026-04-10")


def test_draft_from_asset_creates_observation_and_draft(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = KnowledgeService(str(db_path))
    asset = service.add_asset(
        asset_type="manual_note",
        title="资料摘录",
        content="机器人回流，002594.SZ 关注趋势延续，主线仍有分歧。",
        source="manual",
        tags=["机器人"],
    )

    result = service.draft_from_asset(
        asset_id=asset["asset_id"],
        trade_date="2026-04-10",
        input_by="cursor",
    )

    observation = result["observation"]
    draft = result["draft"]
    assert observation["source_type"] == "knowledge_asset"
    sector_facts = json.loads(observation["sector_facts_json"])
    assert "机器人" in sector_facts["main_themes"]
    stock_focus = json.loads(draft["stock_focus_json"])
    assert stock_focus[0]["subject_code"] == "002594.SZ"
