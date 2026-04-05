"""L2: 资料层服务测试。"""
from __future__ import annotations

import json

from db.connection import get_connection
from db.migrate import migrate
from services.knowledge_service import KnowledgeService


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


def test_draft_from_asset_creates_observation_and_draft(tmp_path):
    db_path = tmp_path / "knowledge.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = KnowledgeService(str(db_path))
    asset = service.add_asset(
        asset_type="teacher_note",
        title="老师观点",
        content="机器人回流，002594.SZ 关注趋势延续，主线仍有分歧。",
        source="teacher",
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
