from __future__ import annotations

import sqlite3

import pytest

from db.migrate import migrate
from db import queries as Q
from services.daily_leaders.service import _confirmed_step5_leaders
from services.review_leaders import build_review_with_step5, sync_leader_tracking_from_step5


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "trade.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    migrate(c)
    yield c
    c.close()


def test_sync_leader_tracking_accepts_step5_dict(conn):
    step5 = {
        "top_leaders": [
            {
                "stock": "688041 海光信息",
                "sector": "半导体",
                "attribute_type": "走势引领",
                "position": "主升初期",
            }
        ]
    }

    count = sync_leader_tracking_from_step5(conn, "2026-07-03", step5)

    rows = Q.get_active_leaders(conn)
    assert count == 1
    assert rows[0]["stock_code"] == "688041 海光信息"
    assert rows[0]["stock_name"] == "688041 海光信息"
    assert rows[0]["sector"] == "半导体"
    assert rows[0]["attribute_type"] == "走势引领"
    assert rows[0]["current_phase"] == "主升初期"


def test_confirmed_step5_preserves_canonical_stock_code():
    source = {
        "date": "2026-07-14",
        "top_leaders": [
            {
                "stock": "600519 贵州茅台",
                "stock_code": "600519.SH",
                "sector": "白酒",
                "leader_role": "趋势中军",
            }
        ],
    }

    leaders = _confirmed_step5_leaders(source, "2026-07-14")

    assert leaders[0]["stock_code"] == "600519"


def test_sync_leader_tracking_prefers_stock_code_over_display_name(conn):
    step5 = {
        "top_leaders": [
            {
                "stock": "600519 贵州茅台",
                "stock_code": "600519",
                "sector": "白酒",
                "attribute_type": "趋势中军",
            }
        ]
    }

    count = sync_leader_tracking_from_step5(conn, "2026-07-14", step5)

    rows = Q.get_active_leaders(conn)
    assert count == 1
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["stock_name"] == "600519 贵州茅台"


def test_sync_leader_tracking_reuses_code_when_display_name_changes_across_days(conn):
    first_day = {
        "top_leaders": [
            {
                "stock": "600519 贵州茅台",
                "stock_code": "600519",
                "sector": "白酒",
                "attribute_type": "趋势中军",
            }
        ]
    }
    second_day = {
        "top_leaders": [
            {
                "stock": "贵州茅台",
                "stock_code": "600519",
                "sector": "白酒",
                "attribute_type": "趋势中军",
            }
        ]
    }

    sync_leader_tracking_from_step5(conn, "2026-07-14", first_day)
    sync_leader_tracking_from_step5(conn, "2026-07-15", second_day)

    rows = Q.get_active_leaders(conn)
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["stock_name"] == "贵州茅台"
    assert rows[0]["first_seen_date"] == "2026-07-14"
    assert rows[0]["last_seen_date"] == "2026-07-15"
    assert rows[0]["consecutive_days"] == 2


def test_sync_leader_tracking_ignores_invalid_payload(conn):
    assert sync_leader_tracking_from_step5(conn, "2026-07-03", None) == 0
    assert sync_leader_tracking_from_step5(conn, "2026-07-03", {"top_leaders": "bad"}) == 0
    assert Q.get_active_leaders(conn) == []


def test_sync_leader_tracking_ignores_non_string_stock_or_sector(conn):
    step5 = {
        "top_leaders": [
            {"stock": 688041, "sector": "半导体"},
            {"stock": "海光信息", "sector": ["半导体"]},
            {"stock": "  ", "sector": "半导体"},
            {"stock": "海光信息", "sector": "  "},
        ]
    }

    count = sync_leader_tracking_from_step5(conn, "2026-07-03", step5)

    assert count == 0
    assert Q.get_active_leaders(conn) == []


def test_build_review_with_step5_preserves_existing_sections():
    existing = {
        "step1_market": {"notes": "大盘复盘"},
        "step5_leaders": {"top_leaders": [{"stock": "旧票", "sector": "旧板块"}]},
    }
    confirmed = {
        "top_leaders": [
            {"stock": "新票", "sector": "半导体", "attribute_type": "容量最大"}
        ],
        "notes": "系统候选，经用户确认",
    }

    merged = build_review_with_step5(existing, confirmed)

    assert merged["step1_market"] == {"notes": "大盘复盘"}
    assert merged["step5_leaders"] == confirmed
