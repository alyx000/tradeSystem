"""TDD: 行业推荐 service 编排层单测（codex 中等 3）

锁定 LLM 触发条件：从「有 sectors」改为「有 market_views」后，
确保 comment() 只在有大盘观点时被调、skip_llm 时永不被调。
mock comment() 隔离 Antigravity subprocess。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import patch

import pytest

from db.schema import init_schema


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_recommend_service.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _note(conn, the_date, sectors_json, core_view):
    conn.execute(
        "INSERT INTO teacher_notes (title, date, sectors, core_view, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        ("观点", the_date, sectors_json, core_view, "tdd-test"),
    )
    conn.commit()


def _industry(conn, the_date, sector_name, content):
    conn.execute(
        "INSERT INTO industry_info (date, sector_name, content, confidence, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (the_date, sector_name, content, "高", "tdd-test"),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────
# S1: 有 market_views → 调用 comment()，LLM 文本进推送
# ─────────────────────────────────────────────────────────────
def test_s1_llm_called_when_market_views_present(conn):
    today = date.today().isoformat()
    _note(conn, today, '["半导体"]', "今日大盘观点")

    from services.recommend import service

    with patch.object(service, "comment", return_value="- 提炼后的大盘要点") as m:
        rec = service.run_recommend(conn, lookback_days=3, top_k=5, skip_llm=False)

    m.assert_called_once()
    assert "提炼后的大盘要点" in rec.markdown


# ─────────────────────────────────────────────────────────────
# S2: 无 market_views（只有 industry_info）→ 不调 comment()，催化段照常
# ─────────────────────────────────────────────────────────────
def test_s2_llm_not_called_when_no_market_views(conn):
    today = date.today().isoformat()
    _industry(conn, today, "六氟化钨", "日企停产催化")  # 仅 catalysts，无 market_views/sectors

    from services.recommend import service

    with patch.object(service, "comment") as m:
        rec = service.run_recommend(conn, lookback_days=3, top_k=5, skip_llm=False)

    m.assert_not_called()
    assert "日企停产催化" in rec.markdown


# ─────────────────────────────────────────────────────────────
# S3: skip_llm=True → 永不调 comment()，大盘判断降级展示原文
# ─────────────────────────────────────────────────────────────
def test_s3_llm_never_called_when_skip_llm(conn):
    today = date.today().isoformat()
    _note(conn, today, '["半导体"]', "今日大盘观点")

    from services.recommend import service

    with patch.object(service, "comment") as m:
        rec = service.run_recommend(conn, lookback_days=3, top_k=5, skip_llm=True)

    m.assert_not_called()
    assert "今日大盘观点" in rec.markdown  # 降级展示原始 core_view
