"""L5: FastAPI 后端测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fastapi.testclient import TestClient

from db.connection import get_connection
from db.migrate import migrate
from db import queries as Q


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_api.db"
    conn = get_connection(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    from api.main import app
    return TestClient(app)


@pytest.fixture
def seeded_client(client, db_path):
    """预置基础数据的客户端（老师笔记由需用例自行插入）。"""
    conn = get_connection(db_path)
    Q.get_or_create_teacher(conn, "小鲍")
    Q.upsert_daily_market(conn, {
        "date": "2026-04-01", "sh_index_close": 3285.89,
        "total_amount": 12345.0, "limit_up_count": 85,
        "seal_rate": 78.5, "broken_rate": 21.5,
    })
    Q.upsert_daily_market(conn, {
        "date": "2026-04-02", "sh_index_close": 3300.0,
        "total_amount": 13000.0, "seal_rate": 80.0, "broken_rate": 20.0,
    })
    Q.upsert_emotion_cycle(conn, {"date": "2026-04-01", "phase": "发酵"})
    Q.upsert_main_theme(conn, {"date": "2026-04-01", "theme_name": "AI", "status": "active"})
    Q.upsert_holding(conn, stock_code="300750", stock_name="宁德时代", status="active")
    Q.insert_calendar_event(conn, date="2026-04-01", event="CPI数据", impact="high")
    Q.insert_industry_info(conn, date="2026-04-01", sector_name="锂电",
                           content="锂电板块资金流入", info_type="news")
    conn.commit()
    conn.close()
    return client


# ──────────────────────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200


# ──────────────────────────────────────────────────────────────
# Review (八步复盘)
# ──────────────────────────────────────────────────────────────

class TestReview:
    def test_prefill_returns_market(self, seeded_client):
        r = seeded_client.get("/api/review/2026-04-02/prefill")
        assert r.status_code == 200
        data = r.json()
        assert data["market"]["sh_index_close"] == 3300.0
        assert data["prev_market"] is not None
        assert data["prev_market"]["sh_index_close"] == 3285.89
        assert data["avg_5d_amount"] is not None
        assert len(data["teacher_notes"]) >= 0
        assert data["emotion_cycle"]["phase"] == "发酵"

    def test_prefill_empty_date(self, client):
        r = client.get("/api/review/2026-12-31/prefill")
        assert r.status_code == 200
        data = r.json()
        assert data["market"] is None
        assert data["prev_market"] is None
        assert data["avg_5d_amount"] is None
        assert data["avg_20d_amount"] is None

    def test_save_and_load(self, client):
        body = {"step1_market": {"sh": 3285}, "step2_sectors": {"main": "AI"}}
        r = client.put("/api/review/2026-04-01", json=body)
        assert r.status_code == 200

        r = client.get("/api/review/2026-04-01")
        assert r.status_code == 200
        data = r.json()
        assert data["exists"] is True
        assert json.loads(data["step1_market"])["sh"] == 3285

    def test_partial_save(self, client):
        body = {"step1_market": {"sh": 3285}}
        r = client.put("/api/review/2026-04-01", json=body)
        assert r.status_code == 200

        r = client.get("/api/review/2026-04-01")
        data = r.json()
        assert data["step2_sectors"] is None

    def test_invalid_date(self, client):
        r = client.get("/api/review/not-a-date")
        assert r.status_code == 422


# ──────────────────────────────────────────────────────────────
# Search (查询中心)
# ──────────────────────────────────────────────────────────────

class TestSearch:
    def test_unified_cross_entity(self, seeded_client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-04-01",
            title="锂电板块分析", core_view="锂电看好",
            tags=["锂电", "短线"],
        )
        conn.commit()
        conn.close()
        r = seeded_client.get("/api/search/unified", params={"q": "锂电"})
        assert r.status_code == 200
        data = r.json()
        assert len(data.get("teacher_notes", [])) >= 1
        assert len(data.get("industry_info", [])) >= 1

    def test_unified_type_filter(self, seeded_client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-04-01",
            title="锂电笔记", core_view="锂电内容",
        )
        conn.commit()
        conn.close()
        r = seeded_client.get("/api/search/unified", params={"q": "锂电", "types": "teacher_notes"})
        data = r.json()
        assert "teacher_notes" in data
        assert "industry_info" not in data

    def test_teacher_timeline(self, seeded_client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-04-01",
            title="时间线笔记", core_view="内容",
        )
        conn.commit()
        conn.close()
        teachers = seeded_client.get("/api/teachers").json()
        tid = teachers[0]["id"]
        r = seeded_client.get(f"/api/teachers/{tid}/timeline")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_stock_mentions(self, seeded_client):
        r = seeded_client.get("/api/stock/300750/mentions")
        assert r.status_code == 200
        data = r.json()
        assert len(data["holdings"]) >= 1

    def test_style_factors(self, seeded_client):
        r = seeded_client.get("/api/style-factors/series", params={
            "metrics": "seal_rate,broken_rate", "from": "2026-04-01", "to": "2026-04-02"
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2

    def test_export_markdown(self, seeded_client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-04-01",
            title="导出测试锂电", core_view="锂电",
        )
        conn.commit()
        conn.close()
        r = seeded_client.get("/api/search/export", params={"q": "锂电"})
        assert r.status_code == 200
        assert "锂电" in r.text


# ──────────────────────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────────────────────

class TestCRUD:
    def test_teachers_list(self, seeded_client):
        r = seeded_client.get("/api/teachers")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_note_crud(self, client):
        r = client.post("/api/teacher-notes", json={
            "teacher_name": "CRUD测试老师", "date": "2026-04-01",
            "title": "CRUD测试", "core_view": "测试内容",
        })
        assert r.status_code == 200
        nid = r.json()["id"]

        r = client.get(f"/api/teacher-notes/{nid}")
        data = r.json()
        assert data["title"] == "CRUD测试"
        assert "attachments" in data
        assert isinstance(data["attachments"], list)

        r = client.put(f"/api/teacher-notes/{nid}", json={"core_view": "更新后"})
        assert r.status_code == 200

        r = client.delete(f"/api/teacher-notes/{nid}")
        assert r.status_code == 200

        r = client.get(f"/api/teacher-notes/{nid}")
        assert r.status_code == 404

    def test_note_list_has_attachments_field(self, client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "附件测试老师")
        nid = Q.insert_teacher_note(conn, teacher_id=tid, date="2026-05-01",
                                    title="附件笔记", key_points=["要点A", "要点B"])
        Q.insert_attachment(conn, nid, "data/attachments/2026-05-01/test.jpg", "image/jpeg", "测试图")
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes")
        assert r.status_code == 200
        notes = r.json()
        target = next((n for n in notes if n["id"] == nid), None)
        assert target is not None
        assert len(target["attachments"]) == 1
        att = target["attachments"][0]
        assert att["file_path"] == "data/attachments/2026-05-01/test.jpg"
        assert "/attachments/" in att["url"]

    def test_holdings_crud(self, client):
        r = client.post("/api/holdings", json={
            "stock_code": "300750", "stock_name": "宁德时代",
            "entry_price": 200.0, "shares": 100,
        })
        hid = r.json()["id"]

        r = client.get(f"/api/holdings/{hid}")
        assert r.json()["stock_code"] == "300750"

        r = client.put(f"/api/holdings/{hid}", json={"current_price": 210.0})
        assert r.status_code == 200

        r = client.delete(f"/api/holdings/{hid}")
        assert r.status_code == 200

    def test_watchlist_crud(self, client):
        r = client.post("/api/watchlist", json={
            "stock_code": "600519", "stock_name": "贵州茅台", "tier": "tier1_core",
        })
        wid = r.json()["id"]
        r = client.get(f"/api/watchlist/{wid}")
        assert r.json()["tier"] == "tier1_core"
        client.delete(f"/api/watchlist/{wid}")

    def test_blacklist_crud(self, client):
        r = client.post("/api/blacklist", json={
            "stock_code": "000001", "stock_name": "平安银行", "reason": "观望",
        })
        bid = r.json()["id"]
        r = client.get("/api/blacklist")
        assert len(r.json()) >= 1
        client.delete(f"/api/blacklist/{bid}")

    def test_calendar_range(self, seeded_client):
        r = seeded_client.get("/api/calendar/range", params={
            "from": "2026-04-01", "to": "2026-04-30", "impact": "high",
        })
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_trades_crud(self, client):
        r = client.post("/api/trades", json={
            "date": "2026-04-01", "stock_code": "300750",
            "stock_name": "宁德时代", "direction": "买入", "price": 200.0,
        })
        tid = r.json()["id"]
        r = client.get(f"/api/trades/{tid}")
        assert r.json()["direction"] == "买入"
        client.delete(f"/api/trades/{tid}")

    def test_industry_crud(self, client):
        r = client.post("/api/industry", json={
            "date": "2026-04-01", "sector_name": "AI",
            "content": "算力需求增长", "info_type": "news",
        })
        assert r.status_code == 200

    def test_macro_crud(self, client):
        r = client.post("/api/macro", json={
            "date": "2026-04-01", "title": "CPI数据",
            "content": "CPI同比增长2.1%", "category": "monetary",
        })
        assert r.status_code == 200

    def test_market_get(self, seeded_client):
        r = seeded_client.get("/api/market/2026-04-01")
        assert r.status_code == 200
        data = r.json()
        assert data["sh_index_close"] == 3285.89
        assert data["available"] is True

    def test_market_no_data(self, client):
        r = client.get("/api/market/2099-01-01")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False

    def test_market_raw_data_parsed(self, client, db_path):
        """raw_data JSON 中的板块数据应被自动展开。"""
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-05-01", "sh_index_close": 3300.0,
            "raw_data": {
                "sector_industry": {"data": [{"name": "电力", "pct_change": 2.5}]},
                "sector_concept": {"data": [{"name": "AI算力", "pct_change": 3.1}]},
            },
        })
        conn.commit()
        conn.close()

        r = client.get("/api/market/2026-05-01")
        data = r.json()
        assert data["available"] is True
        assert data["sector_industry"]["data"][0]["name"] == "电力"
        assert data["sector_concept"]["data"][0]["name"] == "AI算力"

    def test_market_nested_envelope_raw_data(self, client, db_path):
        """post-market 信封：indices/板块仅在内层 raw_data 时也应展开到 API。"""
        envelope = {
            "date": "2026-05-02",
            "generated_at": "2026-05-02T20:00:00",
            "raw_data": {
                "indices": {
                    "chinext": {"close": 2333.1, "change_pct": 1.23},
                },
                "sector_industry": {"data": [{"name": "测试板块", "pct_change": 1.0}]},
            },
        }
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-05-02",
            "sh_index_close": 3000.0,
            "raw_data": envelope,
        })
        conn.commit()
        conn.close()

        r = client.get("/api/market/2026-05-02")
        data = r.json()
        assert data["available"] is True
        assert data["indices"]["chinext"]["close"] == 2333.1
        assert data["sector_industry"]["data"][0]["name"] == "测试板块"

    def test_market_history(self, seeded_client):
        r = seeded_client.get("/api/market/history", params={"days": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 2
        assert "raw_data" not in data[0]

    def test_post_market_envelope_from_db(self, client, db_path):
        env = {
            "date": "2026-05-10",
            "generated_at": "2026-05-10T20:00:00",
            "raw_data": {"indices": {"shanghai": {"close": 3000.0}}},
        }
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-05-10",
            "sh_index_close": 3000.0,
            "raw_data": env,
        })
        conn.commit()
        conn.close()
        r = client.get("/api/post-market/2026-05-10")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["raw_data"]["indices"]["shanghai"]["close"] == 3000.0

    def test_post_market_unavailable(self, client):
        r = client.get("/api/post-market/2099-01-01")
        assert r.status_code == 200
        assert r.json()["available"] is False


# ──────────────────────────────────────────────────────────────
# Review Prefill — prev_review 字段
# ──────────────────────────────────────────────────────────────

class TestPrefillPrevReview:
    def test_prev_review_included_in_prefill(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {"date": "2026-04-01", "sh_index_close": 3285.89, "total_amount": 10000.0})
        Q.upsert_daily_market(conn, {"date": "2026-04-02", "sh_index_close": 3300.0, "total_amount": 12000.0})
        Q.upsert_daily_review(conn, "2026-04-01", {
            "step4_style": json.dumps({"preference": {"cap_size": "小盘股"}, "effects": {}})
        })
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-04-02/prefill")
        assert r.status_code == 200
        data = r.json()
        assert "prev_review" in data
        pr = data["prev_review"]
        assert pr is not None
        assert pr["date"] == "2026-04-01"
        parsed = json.loads(pr["step4_style"])
        assert parsed["preference"]["cap_size"] == "小盘股"

    def test_prev_review_none_when_no_history(self, client):
        r = client.get("/api/review/2020-01-01/prefill")
        assert r.status_code == 200
        data = r.json()
        assert "prev_review" in data
        assert data["prev_review"] is None


# ──────────────────────────────────────────────────────────────
# Main Themes 端点
# ──────────────────────────────────────────────────────────────

class TestMainThemes:
    def test_list_main_themes_empty(self, client):
        r = client.get("/api/main-themes")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_main_themes_active(self, seeded_client):
        r = seeded_client.get("/api/main-themes")
        assert r.status_code == 200
        data = r.json()
        assert any(t["theme_name"] == "AI" for t in data)


# ──────────────────────────────────────────────────────────────
# Error Handling
# ──────────────────────────────────────────────────────────────

class TestErrors:
    def test_note_not_found(self, client):
        r = client.get("/api/teacher-notes/99999")
        assert r.status_code == 404

    def test_holding_not_found(self, client):
        r = client.get("/api/holdings/99999")
        assert r.status_code == 404

    def test_trade_not_found(self, client):
        r = client.get("/api/trades/99999")
        assert r.status_code == 404

    def test_create_note_missing_teacher(self, client):
        r = client.post("/api/teacher-notes", json={
            "date": "2026-04-01", "title": "test",
        })
        assert r.status_code == 422
