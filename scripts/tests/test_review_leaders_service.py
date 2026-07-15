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


def test_sync_leader_tracking_migrates_legacy_display_identity_to_canonical_row(conn):
    for seen_date in ("2026-07-12", "2026-07-13"):
        Q.upsert_leader_tracking(
            conn,
            stock_code="600519 贵州茅台",
            stock_name="600519 贵州茅台",
            sector="白酒",
            attribute_type="趋势中军",
            seen_date=seen_date,
        )

    count = sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "贵州茅台",
                    "stock_code": "600519",
                    "sector": "白酒",
                    "attribute_type": "趋势中军",
                }
            ]
        },
    )

    rows = conn.execute(
        "SELECT * FROM leader_tracking ORDER BY id"
    ).fetchall()
    assert count == 1
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["stock_name"] == "贵州茅台"
    assert rows[0]["first_seen_date"] == "2026-07-12"
    assert rows[0]["last_seen_date"] == "2026-07-14"
    assert rows[0]["consecutive_days"] == 3


def test_sync_leader_tracking_coalesces_existing_legacy_and_canonical_rows(conn):
    for day in range(5, 10):
        Q.upsert_leader_tracking(
            conn,
            stock_code="600519 贵州茅台",
            stock_name="600519 贵州茅台",
            sector="白酒",
            attribute_type="趋势中军",
            seen_date=f"2026-07-{day:02d}",
        )
    for seen_date in ("2026-07-12", "2026-07-13"):
        Q.upsert_leader_tracking(
            conn,
            stock_code="600519",
            stock_name="贵州茅台",
            sector="白酒",
            attribute_type="趋势中军",
            seen_date=seen_date,
        )

    count = sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "贵州茅台",
                    "stock_code": "600519",
                    "sector": "白酒",
                    "attribute_type": "趋势中军",
                }
            ]
        },
    )

    rows = conn.execute(
        "SELECT * FROM leader_tracking ORDER BY id"
    ).fetchall()
    assert count == 1
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["stock_name"] == "贵州茅台"
    assert rows[0]["first_seen_date"] == "2026-07-05"
    assert rows[0]["last_seen_date"] == "2026-07-14"
    assert rows[0]["consecutive_days"] == 6


def test_sync_leader_tracking_keeps_distinct_codes_with_the_same_name(conn):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600001",
        stock_name="同名股票",
        sector="软件开发",
        attribute_type="前排活跃",
        seen_date="2026-07-13",
    )

    sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "同名股票",
                    "stock_code": "600002",
                    "sector": "软件开发",
                    "attribute_type": "前排活跃",
                }
            ]
        },
    )

    rows = conn.execute(
        "SELECT * FROM leader_tracking ORDER BY stock_code"
    ).fetchall()
    assert [row["stock_code"] for row in rows] == ["600001", "600002"]
    assert [row["first_seen_date"] for row in rows] == ["2026-07-13", "2026-07-14"]


def test_sync_leader_tracking_does_not_assign_ambiguous_legacy_name_to_new_code(conn):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600001",
        stock_name="同名股票",
        sector="软件开发",
        attribute_type="前排活跃",
        seen_date="2026-07-12",
    )
    Q.upsert_leader_tracking(
        conn,
        stock_code="同名股票",
        stock_name="同名股票",
        sector="软件开发",
        attribute_type="前排活跃",
        seen_date="2026-07-13",
    )

    sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "同名股票",
                    "stock_code": "600002",
                    "sector": "软件开发",
                    "attribute_type": "前排活跃",
                }
            ]
        },
    )

    rows = conn.execute(
        "SELECT * FROM leader_tracking ORDER BY stock_code"
    ).fetchall()
    assert [row["stock_code"] for row in rows] == ["600001", "600002", "同名股票"]
    canonical_new = next(row for row in rows if row["stock_code"] == "600002")
    assert canonical_new["first_seen_date"] == "2026-07-14"
    assert canonical_new["consecutive_days"] == 1


def test_sync_leader_tracking_merge_keeps_latest_nonempty_phase_and_notes(conn):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519 贵州茅台",
        stock_name="600519 贵州茅台",
        sector="白酒",
        attribute_type="趋势中军",
        seen_date="2026-07-12",
        current_phase="主升",
        notes="旧说明",
    )
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519",
        stock_name="贵州茅台",
        sector="白酒",
        attribute_type="趋势中军",
        seen_date="2026-07-13",
    )

    sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "贵州茅台",
                    "stock_code": "600519",
                    "sector": "白酒",
                    "attribute_type": "趋势中军",
                }
            ]
        },
    )

    rows = conn.execute("SELECT * FROM leader_tracking").fetchall()
    assert len(rows) == 1
    assert rows[0]["current_phase"] == "主升"
    assert rows[0]["notes"] == "旧说明"


def test_sync_leader_tracking_migrates_legacy_identity_with_sector_whitespace(conn):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519 贵州茅台",
        stock_name="600519 贵州茅台",
        sector="软件  开发",
        attribute_type="趋势中军",
        seen_date="2026-07-13",
    )

    sync_leader_tracking_from_step5(
        conn,
        "2026-07-14",
        {
            "top_leaders": [
                {
                    "stock": "贵州茅台",
                    "stock_code": "600519",
                    "sector": "软件 开发",
                    "attribute_type": "趋势中军",
                }
            ]
        },
    )

    rows = conn.execute("SELECT * FROM leader_tracking ORDER BY id").fetchall()
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["sector"] == "软件 开发"
    assert rows[0]["first_seen_date"] == "2026-07-13"
    assert rows[0]["last_seen_date"] == "2026-07-14"


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
