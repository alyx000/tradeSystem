"""L2: YAML 迁移测试。"""
from __future__ import annotations

import json
import sqlite3

import pytest
import yaml

from db.connection import get_connection
from db.migrate import (
    get_schema_version,
    import_calendar,
    import_daily_market,
    import_teacher_notes,
    migrate,
)
from db import queries as Q


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


# ──────────────────────────────────────────────────────────────
# 老师笔记迁移
# ──────────────────────────────────────────────────────────────

def _make_teacher_yaml(path):
    data = {
        "teachers": [
            {
                "name": "小鲍",
                "platform": "微信",
                "schedule": "周一至周五",
                "notes": [
                    {
                        "date": "2026-03-30",
                        "title": "四维度训练营",
                        "core_view": "关注锂电板块，储能方向看好",
                        "tags": ["短线", "锂电"],
                        "sectors": [{"name": "锂电", "view": "看好"}],
                        "avoid": ["追高"],
                    },
                    {
                        "date": "2026-03-31",
                        "title": "盘后复盘",
                        "core_view": "AI算力趋势持续",
                        "tags": ["AI"],
                    },
                ],
            },
            {
                "name": "鞠磊",
                "platform": "直播",
                "notes": [
                    {
                        "date": "2026-03-30",
                        "title": "周末复盘",
                        "core_view": "大盘震荡，注意节奏",
                    },
                ],
            },
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
    return path


class TestTeacherNotesMigration:
    def test_import_basic(self, conn, tmp_path):
        path = _make_teacher_yaml(tmp_path / "teacher-notes.yaml")
        count = import_teacher_notes(conn, yaml_path=path)
        assert count == 3

        teachers = Q.list_teachers(conn)
        assert len(teachers) == 2
        names = {t["name"] for t in teachers}
        assert names == {"小鲍", "鞠磊"}

        notes = conn.execute("SELECT * FROM teacher_notes ORDER BY date").fetchall()
        assert len(notes) == 3

        note = dict(notes[0])
        assert json.loads(note["tags"]) == ["短线", "锂电"]
        assert json.loads(note["sectors"]) == [{"name": "锂电", "view": "看好"}]

    def test_import_idempotent(self, conn, tmp_path):
        path = _make_teacher_yaml(tmp_path / "teacher-notes.yaml")
        count1 = import_teacher_notes(conn, yaml_path=path)
        count2 = import_teacher_notes(conn, yaml_path=path)
        assert count1 == 3
        assert count2 == 0

        notes = conn.execute("SELECT * FROM teacher_notes").fetchall()
        assert len(notes) == 3

    def test_import_partial_yaml(self, conn, tmp_path):
        data = {
            "teachers": [
                {
                    "name": "test_teacher",
                    "notes": [
                        {"date": "2026-04-01", "title": "没有core_view的笔记"},
                    ],
                },
            ],
        }
        path = tmp_path / "partial.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        count = import_teacher_notes(conn, yaml_path=path)
        assert count == 1
        note = conn.execute("SELECT * FROM teacher_notes").fetchone()
        assert note["core_view"] is None

    def test_search_after_import(self, conn, tmp_path):
        path = _make_teacher_yaml(tmp_path / "teacher-notes.yaml")
        import_teacher_notes(conn, yaml_path=path)
        results = Q.search_teacher_notes(conn, "锂电")
        assert len(results) >= 1

    def test_nonexistent_yaml(self, conn, tmp_path):
        count = import_teacher_notes(conn, yaml_path=tmp_path / "nonexistent.yaml")
        assert count == 0


# ──────────────────────────────────────────────────────────────
# 日历迁移
# ──────────────────────────────────────────────────────────────

def _make_calendar_yaml(manual_path, auto_path):
    manual = {
        "events": [
            {"date": "2026-04-01", "event": "CPI数据发布", "impact": "high", "category": "macro"},
            {"date": "2026-04-15", "event": "GDP数据", "impact": "high"},
        ],
    }
    auto = {
        "events": [
            {"date": "2026-04-01", "event": "美联储讲话", "impact": "medium", "source": "akshare"},
            {"date": "2026-04-01", "event": "CPI数据发布", "impact": "high"},
        ],
    }
    with open(manual_path, "w", encoding="utf-8") as f:
        yaml.dump(manual, f, allow_unicode=True)
    with open(auto_path, "w", encoding="utf-8") as f:
        yaml.dump(auto, f, allow_unicode=True)


class TestCalendarMigration:
    def test_import_manual(self, conn, tmp_path):
        manual = tmp_path / "calendar.yaml"
        _make_calendar_yaml(manual, tmp_path / "auto.yaml")
        count = import_calendar(conn, manual_path=manual, auto_path=tmp_path / "nonexistent.yaml")
        assert count == 2
        rows = Q.get_calendar_range(conn, "2026-04-01", "2026-04-30")
        assert len(rows) == 2

    def test_import_auto(self, conn, tmp_path):
        manual = tmp_path / "calendar.yaml"
        auto = tmp_path / "auto.yaml"
        _make_calendar_yaml(manual, auto)
        count = import_calendar(conn, manual_path=tmp_path / "nonexistent.yaml", auto_path=auto)
        assert count == 2

    def test_dedup(self, conn, tmp_path):
        manual = tmp_path / "calendar.yaml"
        auto = tmp_path / "auto.yaml"
        _make_calendar_yaml(manual, auto)
        count = import_calendar(conn, manual_path=manual, auto_path=auto)
        assert count == 3
        rows = conn.execute("SELECT * FROM calendar_events").fetchall()
        assert len(rows) == 3


# ──────────────────────────────────────────────────────────────
# 每日行情迁移
# ──────────────────────────────────────────────────────────────

def _make_daily_yaml(daily_dir, date_str):
    day_dir = daily_dir / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "indices": {
            "sh_close": 3285.89,
            "sh_change_pct": 0.52,
            "sz_close": 10123.45,
            "sz_change_pct": 0.78,
        },
        "total_amount": 12345.0,
        "emotion": {
            "limit_up_count": 85,
            "limit_down_count": 5,
            "seal_rate": 78.5,
            "broken_rate": 21.5,
            "highest_board": 7,
        },
        "market_breadth": {
            "advance_count": 3200,
            "decline_count": 1800,
        },
        "capital_flow": {
            "northbound_net": 50.3,
        },
    }
    with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
    return data


class TestDailyMarketMigration:
    def test_import_basic(self, conn, tmp_path):
        daily_dir = tmp_path / "daily"
        _make_daily_yaml(daily_dir, "2026-03-31")
        count = import_daily_market(conn, daily_dir=daily_dir)
        assert count == 1

        row = Q.get_daily_market(conn, "2026-03-31")
        assert row is not None
        assert row["sh_index_close"] == 3285.89
        assert row["total_amount"] == 12345.0
        assert row["limit_up_count"] == 85
        assert row["advance_count"] == 3200

    def test_import_preserves_raw_data(self, conn, tmp_path):
        daily_dir = tmp_path / "daily"
        _make_daily_yaml(daily_dir, "2026-03-31")
        import_daily_market(conn, daily_dir=daily_dir)
        row = Q.get_daily_market(conn, "2026-03-31")
        raw = json.loads(row["raw_data"])
        assert raw["indices"]["sh_close"] == 3285.89

    def test_import_nested_envelope(self, conn, tmp_path):
        """真实 post-market.yaml：列从内层 raw_data 抽取，raw_data 列存完整信封。"""
        daily_dir = tmp_path / "daily"
        day_dir = daily_dir / "2026-06-01"
        day_dir.mkdir(parents=True)
        envelope = {
            "date": "2026-06-01",
            "generated_at": "2026-06-01T20:00:00",
            "raw_data": {
                "indices": {"shanghai": {"close": 3200.0, "change_pct": 0.6}},
                "total_volume": {"total_billion": 7777.0},
                "breadth": {"advance": 2000, "decline": 1000},
                "limit_up": {"count": 60},
                "limit_down": {"count": 4},
                "northbound": {"net_buy_billion": 9.9},
            },
            "holdings_data": [],
        }
        with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
            yaml.dump(envelope, f, allow_unicode=True)

        count = import_daily_market(conn, daily_dir=daily_dir)
        assert count == 1
        row = Q.get_daily_market(conn, "2026-06-01")
        assert row["sh_index_close"] == 3200.0
        assert row["total_amount"] == 7777.0
        assert row["advance_count"] == 2000
        assert row["limit_up_count"] == 60
        assert row["northbound_net"] == 9.9
        raw = json.loads(row["raw_data"])
        assert raw["raw_data"]["indices"]["shanghai"]["close"] == 3200.0

    def test_import_multiple_days(self, conn, tmp_path):
        daily_dir = tmp_path / "daily"
        _make_daily_yaml(daily_dir, "2026-03-29")
        _make_daily_yaml(daily_dir, "2026-03-30")
        _make_daily_yaml(daily_dir, "2026-03-31")
        count = import_daily_market(conn, daily_dir=daily_dir)
        assert count == 3

    def test_import_idempotent(self, conn, tmp_path):
        daily_dir = tmp_path / "daily"
        _make_daily_yaml(daily_dir, "2026-03-31")
        count1 = import_daily_market(conn, daily_dir=daily_dir)
        count2 = import_daily_market(conn, daily_dir=daily_dir)
        assert count1 == 1
        assert count2 == 0


class TestHoldingsMigration:
    def test_v4_dedupes_duplicate_active_holdings(self, tmp_path):
        db_path = tmp_path / "holdings_v3.db"
        conn = get_connection(db_path)
        conn.executescript(
            """
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY,
                stock_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                market TEXT DEFAULT 'A股',
                sector TEXT,
                shares INTEGER,
                entry_date TEXT,
                entry_price REAL,
                current_price REAL,
                stop_loss REAL,
                target_price REAL,
                position_ratio REAL,
                status TEXT DEFAULT 'active',
                note TEXT,
                updated_at TEXT
            );
            CREATE TRIGGER IF NOT EXISTS holdings_updated
            AFTER UPDATE ON holdings BEGIN
                UPDATE holdings SET updated_at = datetime('now') WHERE id = new.id;
            END;
            """
        )
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            "INSERT INTO holdings (stock_code, stock_name, status) VALUES (?, ?, 'active')",
            ("300750", "宁德时代-旧"),
        )
        conn.execute(
            "INSERT INTO holdings (stock_code, stock_name, status) VALUES (?, ?, 'active')",
            ("300750.SZ", "宁德时代-新"),
        )
        conn.commit()

        migrate(conn)

        active = conn.execute(
            "SELECT stock_code FROM holdings WHERE status = 'active' ORDER BY id"
        ).fetchall()
        closed = conn.execute(
            "SELECT stock_code FROM holdings WHERE status = 'closed' ORDER BY id"
        ).fetchall()
        assert get_schema_version(conn) == 4
        assert [row["stock_code"] for row in active] == ["300750.SZ"]
        assert [row["stock_code"] for row in closed] == ["300750"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO holdings (stock_code, stock_name, status) VALUES (?, ?, 'active')",
                ("300750.SH", "重复持仓"),
            )
        conn.close()
