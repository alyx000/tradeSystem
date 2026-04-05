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
from providers.base import DataResult


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

    def test_prefill_holdings_from_post_envelope_raw_data(self, client, db_path):
        """当日 daily_market.raw_data 信封含 holdings_data 时，补全预填现价与盈亏（不依赖 DB current_price）。"""
        conn = get_connection(db_path)
        envelope = {
            "date": "2026-06-15",
            "holdings_data": [
                {"code": "300750.SZ", "close": 180.5, "pnl_pct": 3.25, "name": "宁德时代"},
            ],
            "raw_data": {"indices": {"shanghai": {"close": 3000.0}}},
        }
        Q.upsert_daily_market(conn, {
            "date": "2026-06-15",
            "sh_index_close": 3000.0,
            "total_amount": 8000.0,
            "raw_data": json.dumps(envelope, ensure_ascii=False),
        })
        Q.upsert_holding(
            conn,
            stock_code="300750",
            stock_name="宁德时代",
            entry_price=170.0,
            status="active",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-06-15/prefill")
        assert r.status_code == 200
        h = r.json()["holdings"]
        assert len(h) == 1
        assert h[0]["current_price"] == 180.5
        assert h[0]["prefill_pnl_pct"] == 3.25

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


class TestPlanningAndKnowledgeAPI:
    def test_knowledge_asset_flow(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "manual_note",
                "title": "机器人资料",
                "content": "机器人回流，002594.SZ 关注趋势延续，主线仍有分歧。",
                "source": "manual",
                "tags": ["机器人"],
            },
        )
        assert r.status_code == 200
        asset = r.json()
        assert asset["title"] == "机器人资料"

        r = client.get("/api/knowledge/assets")
        assert r.status_code == 200
        assets = r.json()
        assert len(assets) == 1

        r = client.post(
            f"/api/knowledge/assets/{asset['asset_id']}/draft",
            json={"trade_date": "2026-04-10", "input_by": "cursor"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["observation"]["source_type"] == "knowledge_asset"
        assert payload["draft"]["trade_date"] == "2026-04-10"

    def test_plan_flow_and_diagnostics(self, client, db_path):
        conn = get_connection(db_path)
        conn.execute(
            """
            INSERT INTO market_fact_snapshots
            (snapshot_id, biz_date, fact_type, subject_type, subject_code, subject_name, facts_json, source_interfaces_json, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-04-11:margin_stats:market:CN",
                "2026-04-11",
                "margin_stats",
                "market",
                "CN",
                "A股市场",
                json.dumps({"total_rzrqye_yi": 12345.6}, ensure_ascii=False),
                json.dumps(["margin"], ensure_ascii=False),
                "high",
            ),
        )
        conn.commit()
        conn.close()

        r = client.post(
            "/api/plans/drafts",
            json={
                "trade_date": "2026-04-11",
                "title": "次日草稿",
                "market_facts": {"bias": "震荡"},
                "sector_facts": {"main_themes": ["AI"]},
                "stock_facts": [{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
                "judgements": [],
                "input_by": "cursor",
            },
        )
        assert r.status_code == 200
        draft = r.json()

        r = client.post(
            f"/api/plans/{draft['draft_id']}/confirm",
            json={"trade_date": "2026-04-11", "input_by": "cursor"},
        )
        assert r.status_code == 200
        plan = r.json()

        conn = get_connection(db_path)
        watch_items = json.loads(
            conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
        )
        watch_items[0]["fact_checks"] = [
            {"check_type": "margin_balance_change_positive", "label": "融资余额变化为正", "params": {}}
        ]
        conn.execute(
            "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
            (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
        )
        conn.commit()
        conn.close()

        r = client.get(f"/api/plans/{plan['plan_id']}/diagnostics")
        assert r.status_code == 200
        diagnostics = r.json()
        assert diagnostics["fact_check_count"] == 1

        r = client.post(
            f"/api/plans/{plan['plan_id']}/review",
            json={"trade_date": "2026-04-11", "outcome_summary": "计划完成度一般", "input_by": "cursor"},
        )
        assert r.status_code == 200
        review = r.json()
        assert review["plan_id"] == plan["plan_id"]

        r = client.get("/api/plans/drafts", params={"date": "2026-04-11"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

        r = client.get("/api/plans", params={"date": "2026-04-11"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

        r = client.get("/api/plans/observations", params={"date": "2026-04-11"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_plan_diagnostics_uses_provider_fallback(self, client, db_path):
        class _FakeProvider:
            def supports(self, method_name: str) -> bool:
                return method_name in {"get_stock_daily", "get_stock_ma", "get_stock_announcements"}

        class _FakeRegistry:
            providers = [_FakeProvider()]

            def call(self, method_name: str, *args, **kwargs):
                if method_name == "get_stock_daily":
                    return DataResult(data={"close": 102.0, "change_pct": 3.5}, source="fake:daily")
                if method_name == "get_stock_ma":
                    return DataResult(data={"ma20": 100.0}, source="fake:ma")
                if method_name == "get_stock_announcements":
                    return DataResult(data=[{"title": "测试公告"}], source="fake:ann")
                return DataResult(data=None, source="fake", error="unsupported")

        from api.main import app
        from api.deps import get_provider_registry

        app.dependency_overrides[get_provider_registry] = lambda: _FakeRegistry()

        try:
            r = client.post(
                "/api/plans/drafts",
                json={
                    "trade_date": "2026-04-12",
                    "title": "次日草稿",
                    "market_facts": {"bias": "震荡"},
                    "sector_facts": {"main_themes": ["AI"]},
                    "stock_facts": [{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
                    "judgements": [],
                    "input_by": "cursor",
                },
            )
            draft = r.json()
            r = client.post(
                f"/api/plans/{draft['draft_id']}/confirm",
                json={"trade_date": "2026-04-12", "input_by": "cursor"},
            )
            plan = r.json()

            conn = get_connection(db_path)
            watch_items = json.loads(
                conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
            )
            watch_items[0]["fact_checks"] = [
                {"check_type": "price_above_ma20", "label": "站稳20日线", "params": {"ts_code": "300750.SZ"}},
                {"check_type": "ret_1d_gte", "label": "单日涨幅不低于2%", "params": {"ts_code": "300750.SZ", "value": 2}},
                {"check_type": "announcement_exists", "label": "存在公告", "params": {"ts_code": "300750.SZ"}},
            ]
            conn.execute(
                "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
                (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
            )
            conn.commit()
            conn.close()

            r = client.get(f"/api/plans/{plan['plan_id']}/diagnostics")
            assert r.status_code == 200
            results = r.json()["items_json"][0]["fact_check_results"]
            assert [item["result"] for item in results] == ["pass", "pass", "pass"]
        finally:
            app.dependency_overrides.pop(get_provider_registry, None)

    def test_update_observation_draft_and_plan_endpoints(self, client):
        r = client.post(
            "/api/plans/drafts",
            json={
                "trade_date": "2026-04-13",
                "title": "次日草稿",
                "market_facts": {"bias": "震荡"},
                "sector_facts": {"main_themes": ["AI"]},
                "stock_facts": [{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
                "judgements": [],
                "input_by": "cursor",
            },
        )
        assert r.status_code == 200
        draft = r.json()

        observations = client.get("/api/plans/observations", params={"date": "2026-04-13"}).json()
        assert len(observations) == 1
        observation_id = observations[0]["observation_id"]

        r = client.put(
            f"/api/plans/observations/{observation_id}",
            json={"title": "已修改观察", "judgements": ["情绪偏分歧"], "input_by": "cursor"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "已修改观察"

        r = client.put(
            f"/api/plans/drafts/{draft['draft_id']}",
            json={"summary": "更新后的草稿摘要", "input_by": "cursor"},
        )
        assert r.status_code == 200
        assert r.json()["summary"] == "更新后的草稿摘要"

        r = client.post(
            f"/api/plans/{draft['draft_id']}/confirm",
            json={"trade_date": "2026-04-13", "input_by": "cursor"},
        )
        assert r.status_code == 200
        plan = r.json()

        r = client.put(
            f"/api/plans/{plan['plan_id']}",
            json={"title": "更新后的正式计划", "market_bias": "分歧", "main_themes": ["机器人"], "input_by": "cursor"},
        )
        assert r.status_code == 200
        updated_plan = r.json()
        assert updated_plan["title"] == "更新后的正式计划"

    def test_update_plan_endpoint_rejects_direct_status_change(self, client):
        r = client.post(
            "/api/plans/drafts",
            json={
                "trade_date": "2026-04-14",
                "title": "次日草稿",
                "market_facts": {"bias": "震荡"},
                "sector_facts": {"main_themes": ["AI"]},
                "stock_facts": [{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
                "judgements": [],
                "input_by": "cursor",
            },
        )
        draft = r.json()
        r = client.post(
            f"/api/plans/{draft['draft_id']}/confirm",
            json={"trade_date": "2026-04-14", "input_by": "cursor"},
        )
        plan = r.json()

        r = client.put(
            f"/api/plans/{plan['plan_id']}",
            json={"status": "reviewed", "input_by": "cursor"},
        )
        assert r.status_code == 422

    def test_create_plan_draft_endpoint_rejects_missing_observations(self, client):
        r = client.post(
            "/api/plans/drafts",
            json={
                "trade_date": "2026-04-14",
                "source_observation_ids": ["obs_missing_1", "obs_missing_2"],
                "input_by": "cursor",
            },
        )
        assert r.status_code == 404

    def test_review_plan_endpoint_returns_404_for_missing_plan(self, client):
        r = client.post(
            "/api/plans/plan_missing/review",
            json={"trade_date": "2026-04-14", "outcome_summary": "不存在", "input_by": "cursor"},
        )
        assert r.status_code == 404

    def test_review_plan_endpoint_rejects_mismatched_trade_date(self, client):
        r = client.post(
            "/api/plans/drafts",
            json={
                "trade_date": "2026-04-14",
                "title": "次日草稿",
                "market_facts": {"bias": "震荡"},
                "sector_facts": {"main_themes": ["AI"]},
                "stock_facts": [{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
                "judgements": [],
                "input_by": "cursor",
            },
        )
        draft = r.json()
        r = client.post(
            f"/api/plans/{draft['draft_id']}/confirm",
            json={"trade_date": "2026-04-14", "input_by": "cursor"},
        )
        plan = r.json()

        r = client.post(
            f"/api/plans/{plan['plan_id']}/review",
            json={"trade_date": "2026-04-15", "outcome_summary": "日期不一致", "input_by": "cursor"},
        )
        assert r.status_code == 422

    def test_ingest_api_flow(self, client):
        r = client.get("/api/ingest/interfaces")
        assert r.status_code == 200
        interfaces = r.json()
        assert any(item["interface_name"] == "margin" for item in interfaces)

        r = client.post(
            "/api/ingest/run-interface",
            json={"name": "margin", "date": "2026-04-03", "input_by": "cursor"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["run"]["interface_name"] == "margin"

        r = client.get("/api/ingest/inspect", params={"date": "2026-04-03"})
        assert r.status_code == 200
        inspect_payload = r.json()
        assert inspect_payload["run_count"] >= 1

        r = client.get("/api/ingest/runs", params={"date": "2026-04-03"})
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) >= 1

        r = client.get("/api/ingest/errors", params={"date": "2026-04-03"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        r = client.get("/api/ingest/retry")
        assert r.status_code == 200
        assert "retryable_count" in r.json()

    def test_ingest_api_not_found(self, client):
        r = client.post(
            "/api/ingest/run-interface",
            json={"name": "not_registered", "date": "2026-04-03", "input_by": "cursor"},
        )
        assert r.status_code == 404

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

    def test_industry_list_keyword_respects_limit(self, client, db_path):
        conn = get_connection(db_path)
        for i in range(4):
            Q.insert_industry_info(
                conn,
                date=f"2026-06-{10 + i:02d}",
                sector_name="关键词限流",
                content=f"note{i}",
                info_type="news",
            )
        conn.commit()
        conn.close()
        r = client.get("/api/industry", params={"keyword": "关键词限流", "limit": 2})
        assert r.status_code == 200
        assert len(r.json()) == 2

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


# ──────────────────────────────────────────────────────────────
# enrich_daily_market_row：/market 与 /prefill 扩展字段回归
# ──────────────────────────────────────────────────────────────

_RICH_RAW_DATA = {
    "date": "2026-05-20",
    "generated_at": "2026-05-20T20:00:00",
    "raw_data": {
        "sector_industry": {
            "data": [{"name": "油服工程", "change_pct": 4.68, "volume_billion": 110.0}],
            "bottom": [{"name": "IT服务", "change_pct": -3.59, "volume_billion": 454.19}],
        },
        "sector_concept": {
            "data": [{"name": "低空经济", "change_pct": 2.3}],
        },
        "style_factors": {
            "cap_preference": {"relative": "偏大盘", "spread": -0.77, "csi300_chg": -1.04, "csi1000_chg": -1.81},
            "board_preference": {"dominant_type": "10cm", "pct_10cm": 90.9},
            "premium_snapshot": {
                "first_board_10cm": {"count": 45, "premium_median": 0.95},
                "second_board": {"count": 4, "premium_median": 7.18},
            },
            "premium_trend": {"direction": "震荡", "first_board_median_5d": [0.95, 0.92]},
            "switch_signals": ["大盘股跑赢小盘股，审美偏向容量票"],
        },
        "sector_rhythm_industry": [
            {
                "name": "油服工程", "phase": "启动", "rank_today": 1,
                "change_today": 4.68, "confidence": "中",
            }
        ],
        "sector_rhythm_concept": [
            {"name": "低空经济", "phase": "发酵", "rank_today": 2, "change_today": 2.3, "confidence": "高"},
        ],
        "indices": {"shanghai": {"close": 3200.0, "change_pct": -1.5}},
    },
}


class TestEnrichMarketRow:
    """验证 enrich_daily_market_row 在 /market 与 /prefill 两个入口的一致性。"""

    @staticmethod
    def _seed(db_path, raw_data=None):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-05-20",
            "sh_index_close": 3200.0,
            "total_amount": 18000.0,
            "premium_10cm": 0.95,
            "premium_second_board": 7.18,
            "raw_data": raw_data or _RICH_RAW_DATA,
        })
        conn.commit()
        conn.close()

    def test_market_returns_style_factors(self, client, db_path):
        """/api/market/{date} 应展开 style_factors。"""
        self._seed(db_path)
        r = client.get("/api/market/2026-05-20")
        data = r.json()
        assert data["available"] is True
        assert "style_factors" in data, "style_factors 未展开到顶层"
        assert data["style_factors"]["cap_preference"]["relative"] == "偏大盘"
        assert "raw_data" not in data, "raw_data 应已从响应中移除"

    def test_market_returns_rhythm(self, client, db_path):
        """/api/market/{date} 应展开 sector_rhythm_industry 和 sector_rhythm_concept。"""
        self._seed(db_path)
        r = client.get("/api/market/2026-05-20")
        data = r.json()
        assert "sector_rhythm_industry" in data, "sector_rhythm_industry 未展开"
        assert data["sector_rhythm_industry"][0]["name"] == "油服工程"
        assert "sector_rhythm_concept" in data, "sector_rhythm_concept 未展开"

    def test_market_existing_keys_not_regressed(self, client, db_path):
        """/api/market 原有 sector_industry / indices 展开行为不应回退。"""
        self._seed(db_path)
        r = client.get("/api/market/2026-05-20")
        data = r.json()
        assert "sector_industry" in data
        assert data["sector_industry"]["data"][0]["name"] == "油服工程"
        assert "indices" in data
        assert data["indices"]["shanghai"]["close"] == 3200.0

    def test_prefill_market_contains_style_factors(self, client, db_path):
        """/api/review/{date}/prefill 中 market 字段应含 style_factors。"""
        self._seed(db_path)
        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        m = data.get("market")
        assert m is not None, "prefill.market 不应为 None"
        assert "style_factors" in m, "prefill.market 未展开 style_factors"
        assert m["style_factors"]["board_preference"]["dominant_type"] == "10cm"
        assert "raw_data" not in m, "prefill.market 中 raw_data 应已移除"

    def test_prefill_market_contains_sector_rhythm(self, client, db_path):
        """/api/review/{date}/prefill 中 market 应含 sector_rhythm_industry。"""
        self._seed(db_path)
        r = client.get("/api/review/2026-05-20/prefill")
        data = r.json()
        m = data["market"]
        assert "sector_rhythm_industry" in m
        assert m["sector_rhythm_industry"][0]["phase"] == "启动"
        assert "sector_industry" in m
        assert m["sector_industry"]["data"][0]["name"] == "油服工程"

    def test_prefill_contains_industry_info(self, client, db_path):
        """/api/review/{date}/prefill 应返回 industry_info 顶层列表。"""
        self._seed(db_path)
        conn = get_connection(db_path)
        Q.insert_industry_info(conn, date="2026-05-20", sector_name="油服",
                               content="油服板块资金流入", info_type="news")
        Q.insert_industry_info(conn, date="2026-05-18", sector_name="储能",
                               content="储能政策利好", info_type="analysis")
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-05-20/prefill")
        data = r.json()
        assert "industry_info" in data, "prefill 应含 industry_info"
        assert isinstance(data["industry_info"], list)
        names = [i["sector_name"] for i in data["industry_info"]]
        assert "油服" in names
        assert "储能" in names

    def test_prefill_industry_info_empty_when_none(self, client):
        """/api/review/{date}/prefill 无行业信息时 industry_info 应为空列表。"""
        r = client.get("/api/review/2099-01-01/prefill")
        data = r.json()
        assert data["industry_info"] == []

    def test_market_no_raw_data_still_available(self, client, db_path):
        """raw_data 为空时 /api/market/{date} 应仍返回 available=True。"""
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {"date": "2026-05-21", "sh_index_close": 3100.0})
        conn.commit()
        conn.close()
        r = client.get("/api/market/2026-05-21")
        data = r.json()
        assert data["available"] is True
        assert "style_factors" not in data
