"""L1: DB 查询 CRUD + FTS + JSON + 触发器测试。"""
from __future__ import annotations

import json

import pytest

from db.connection import get_connection
from db.schema import init_schema
from db import queries as Q


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def teacher_id(conn):
    return Q.get_or_create_teacher(conn, "小鲍", platform="微信", schedule="周一至周五")


# ──────────────────────────────────────────────────────────────
# Teachers / Teacher Notes
# ──────────────────────────────────────────────────────────────

class TestTeacherNotes:
    def test_insert_teacher_and_note(self, conn, teacher_id):
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="四维度训练营", core_view="关注锂电板块",
            tags=["短线", "锂电"], sectors=[{"name": "锂电", "view": "看好"}],
        )
        assert note_id > 0
        row = conn.execute("SELECT * FROM teacher_notes WHERE id = ?", (note_id,)).fetchone()
        assert row["title"] == "四维度训练营"
        assert json.loads(row["tags"]) == ["短线", "锂电"]

    def test_insert_duplicate_teacher_name(self, conn, teacher_id):
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO teachers (name) VALUES ('小鲍')")

    def test_get_or_create_existing(self, conn, teacher_id):
        same_id = Q.get_or_create_teacher(conn, "小鲍")
        assert same_id == teacher_id

    def test_update_note(self, conn, teacher_id):
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="test", core_view="original",
        )
        conn.execute(
            "UPDATE teacher_notes SET core_view = ? WHERE id = ?",
            ("updated", note_id),
        )
        row = conn.execute("SELECT core_view FROM teacher_notes WHERE id = ?", (note_id,)).fetchone()
        assert row["core_view"] == "updated"

    def test_delete_note_cascades_attachments(self, conn, teacher_id):
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01", title="with_att",
        )
        Q.insert_attachment(conn, note_id, "path/img.png", "image/png")
        att = conn.execute("SELECT * FROM note_attachments WHERE note_id = ?", (note_id,)).fetchall()
        assert len(att) == 1

        conn.execute("DELETE FROM teacher_notes WHERE id = ?", (note_id,))
        att = conn.execute("SELECT * FROM note_attachments WHERE note_id = ?", (note_id,)).fetchall()
        assert len(att) == 0

    def test_list_teachers(self, conn, teacher_id):
        Q.get_or_create_teacher(conn, "鞠磊")
        teachers = Q.list_teachers(conn)
        names = [t["name"] for t in teachers]
        assert "小鲍" in names
        assert "鞠磊" in names


# ──────────────────────────────────────────────────────────────
# FTS
# ──────────────────────────────────────────────────────────────

class TestFTS:
    def test_fts_search_chinese(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="锂电板块分析", core_view="锂电储能看好",
        )
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="AI分析", core_view="AI算力趋势",
        )
        results = Q.search_teacher_notes(conn, "锂电")
        assert len(results) >= 1
        assert any("锂电" in r.get("title", "") or "锂电" in (r.get("core_view") or "") for r in results)

    def test_fts_search_no_match(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="test", core_view="nothing here",
        )
        results = Q.search_teacher_notes(conn, "完全不存在的词汇xyz")
        assert len(results) == 0

    def test_fts_cross_field(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="test", core_view="nothing", avoid=["锂电过热"],
        )
        results = Q.search_teacher_notes(conn, "锂电")
        assert len(results) >= 1

    def test_fts_trigger_sync_on_update(self, conn, teacher_id):
        note_id = Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="before", core_view="储能分析",
        )
        conn.execute(
            "UPDATE teacher_notes SET title = ? WHERE id = ?", ("储能深度after", note_id)
        )
        results = Q.search_teacher_notes(conn, "储能深度after")
        assert len(results) >= 1
        results_old = Q.search_teacher_notes(conn, "before")
        assert len(results_old) == 0

    def test_fts_trigger_sync_on_delete(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="deleteme_unique", core_view="unique_content_xyz",
        )
        results = Q.search_teacher_notes(conn, "unique_content_xyz")
        assert len(results) >= 1
        conn.execute("DELETE FROM teacher_notes WHERE title = 'deleteme_unique'")
        results = Q.search_teacher_notes(conn, "unique_content_xyz")
        assert len(results) == 0

    def test_industry_info_fts_independent(self, conn):
        Q.insert_industry_info(
            conn, date="2026-04-01", sector_name="锂电", content="锂电板块持续走强",
            info_type="news",
        )
        results = Q.search_industry_info(conn, "锂电")
        assert len(results) >= 1
        assert results[0]["sector_name"] == "锂电"

    def test_macro_info_fts_independent(self, conn):
        Q.insert_macro_info(
            conn, date="2026-04-01", title="美联储利率决议",
            content="美联储维持利率不变", category="monetary",
        )
        results = Q.search_macro_info(conn, "美联储")
        assert len(results) >= 1


# ──────────────────────────────────────────────────────────────
# JSON
# ──────────────────────────────────────────────────────────────

class TestJSON:
    def test_json_column_roundtrip(self, conn):
        Q.upsert_daily_market(conn, {
            "date": "2026-04-01",
            "continuous_board_counts": {"2板": 15, "3板": 5},
        })
        row = conn.execute(
            "SELECT json_extract(continuous_board_counts, '$.\"2板\"') as v FROM daily_market WHERE date = '2026-04-01'"
        ).fetchone()
        assert row["v"] == 15

    def test_json_extract_from_raw_data(self, conn):
        Q.upsert_daily_market(conn, {
            "date": "2026-04-01",
            "sh_index_close": 3285.89,
            "raw_data": {"indices": {"sh_close": 3285.89}},
        })
        row = conn.execute(
            "SELECT json_extract(raw_data, '$.indices.sh_close') as v FROM daily_market WHERE date = '2026-04-01'"
        ).fetchone()
        assert row["v"] == 3285.89


# ──────────────────────────────────────────────────────────────
# Daily Market
# ──────────────────────────────────────────────────────────────

class TestDailyMarket:
    def test_all_fields(self, conn):
        Q.upsert_daily_market(conn, {
            "date": "2026-04-01",
            "sh_index_close": 3285.89,
            "sh_index_change_pct": 0.52,
            "sz_index_close": 10123.45,
            "sz_index_change_pct": 0.78,
            "total_amount": 12345.0,
            "advance_count": 3200,
            "decline_count": 1800,
            "sh_above_ma5w": True,
            "sz_above_ma5w": True,
            "chinext_above_ma5w": False,
            "star50_above_ma5w": False,
            "avg_price_above_ma5w": True,
            "limit_up_count": 85,
            "limit_down_count": 5,
            "seal_rate": 78.5,
            "broken_rate": 21.5,
            "highest_board": 7,
            "northbound_net": 50.3,
            "margin_balance": 16000.0,
        })
        row = Q.get_daily_market(conn, "2026-04-01")
        assert row is not None
        assert row["sh_index_close"] == 3285.89
        assert row["sh_above_ma5w"] == 1
        assert row["advance_count"] == 3200

    def test_date_check(self, conn):
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO daily_market (date) VALUES ('bad-date')")

    def test_premium_backfill(self, conn):
        Q.upsert_daily_market(conn, {"date": "2026-04-01"})
        row = Q.get_daily_market(conn, "2026-04-01")
        assert row["premium_10cm"] is None
        assert row["premium_30cm"] is None

        Q.update_premium(conn, "2026-04-01", premium_10cm=2.5, premium_20cm=5.0, premium_30cm=0.8)
        row = Q.get_daily_market(conn, "2026-04-01")
        assert row["premium_10cm"] == 2.5
        assert row["premium_20cm"] == 5.0
        assert row["premium_30cm"] == 0.8

    def test_range_query(self, conn):
        for d in ["2026-03-29", "2026-03-30", "2026-03-31", "2026-04-01"]:
            Q.upsert_daily_market(conn, {"date": d, "total_amount": 10000.0})
        rows = Q.get_daily_market_range(conn, "2026-03-30", "2026-03-31")
        assert len(rows) == 2

    def test_style_factors_series(self, conn):
        Q.upsert_daily_market(conn, {"date": "2026-04-01", "seal_rate": 78.5, "broken_rate": 21.5})
        Q.upsert_daily_market(conn, {"date": "2026-04-02", "seal_rate": 80.0, "broken_rate": 20.0})
        series = Q.get_style_factors_series(conn, ["seal_rate", "broken_rate"], "2026-04-01", "2026-04-02")
        assert len(series) == 2
        assert series[0]["seal_rate"] == 78.5

    def test_style_factors_rejects_bad_columns(self, conn):
        series = Q.get_style_factors_series(conn, ["drop_table", "bad_col"], "2026-04-01", "2026-04-02")
        assert series == []

    def test_get_prev_daily_market(self, conn):
        Q.upsert_daily_market(conn, {"date": "2026-03-28", "total_amount": 10000.0})
        Q.upsert_daily_market(conn, {"date": "2026-03-31", "total_amount": 11000.0})
        Q.upsert_daily_market(conn, {"date": "2026-04-01", "total_amount": 12000.0})
        prev = Q.get_prev_daily_market(conn, "2026-04-01")
        assert prev is not None
        assert prev["date"] == "2026-03-31"
        assert prev["total_amount"] == 11000.0

    def test_get_prev_daily_market_none(self, conn):
        Q.upsert_daily_market(conn, {"date": "2026-04-01", "total_amount": 12000.0})
        prev = Q.get_prev_daily_market(conn, "2026-04-01")
        assert prev is None

    def test_get_avg_amount(self, conn):
        for i, d in enumerate(["2026-03-27", "2026-03-28", "2026-03-31", "2026-04-01", "2026-04-02"]):
            Q.upsert_daily_market(conn, {"date": d, "total_amount": 10000.0 + i * 1000})
        avg5 = Q.get_avg_amount(conn, "2026-04-02", days=5)
        assert avg5 is not None
        assert 10000 <= avg5 <= 14000

    def test_get_avg_amount_fewer_days(self, conn):
        Q.upsert_daily_market(conn, {"date": "2026-04-01", "total_amount": 12000.0})
        avg = Q.get_avg_amount(conn, "2026-04-02", days=5)
        assert avg == 12000.0

    def test_get_avg_amount_no_data(self, conn):
        avg = Q.get_avg_amount(conn, "2026-04-01", days=5)
        assert avg is None

    def test_get_daily_market_history(self, conn):
        for d in ["2026-03-28", "2026-03-31", "2026-04-01"]:
            Q.upsert_daily_market(conn, {"date": d, "total_amount": 10000.0, "raw_data": {"big": "data"}})
        history = Q.get_daily_market_history(conn, days=2)
        assert len(history) == 2
        assert history[0]["date"] == "2026-04-01"
        assert "raw_data" not in history[0]
        assert "premium_30cm" in history[0]


# ──────────────────────────────────────────────────────────────
# Daily Reviews
# ──────────────────────────────────────────────────────────────

class TestDailyReviews:
    def test_save_and_load(self, conn):
        Q.upsert_daily_review(conn, "2026-04-01", {
            "step1_market": {"indices": {"sh": 3285}},
            "step2_sectors": {"main_theme": "AI"},
        })
        row = Q.get_daily_review(conn, "2026-04-01")
        assert row is not None
        assert json.loads(row["step1_market"])["indices"]["sh"] == 3285

    def test_partial_save(self, conn):
        Q.upsert_daily_review(conn, "2026-04-01", {"step1_market": {"sh": 3285}})
        row = Q.get_daily_review(conn, "2026-04-01")
        assert row["step2_sectors"] is None

    def test_updated_at_trigger(self, conn):
        Q.upsert_daily_review(conn, "2026-04-01", {"step1_market": {"sh": 3285}})
        row1 = Q.get_daily_review(conn, "2026-04-01")
        import time
        time.sleep(0.1)
        Q.upsert_daily_review(conn, "2026-04-01", {"step1_market": {"sh": 3300}})
        row2 = Q.get_daily_review(conn, "2026-04-01")
        assert row2["updated_at"] >= row1["updated_at"]


# ──────────────────────────────────────────────────────────────
# Holdings / Watchlist / Blacklist
# ──────────────────────────────────────────────────────────────

class TestHoldings:
    def test_crud(self, conn):
        hid = Q.upsert_holding(conn, stock_code="300750", stock_name="宁德时代",
                               entry_price=200.0, shares=100, status="active")
        assert hid > 0
        rows = Q.get_holdings(conn, status="active")
        assert len(rows) == 1
        Q.update_holding(conn, hid, current_price=210.0)
        row = conn.execute("SELECT current_price FROM holdings WHERE id = ?", (hid,)).fetchone()
        assert row["current_price"] == 210.0
        Q.delete_holding(conn, hid)
        assert len(Q.get_holdings(conn)) == 0

    def test_updated_at_trigger(self, conn):
        hid = Q.upsert_holding(conn, stock_code="300750", stock_name="宁德时代")
        conn.commit()
        Q.update_holding(conn, hid, current_price=210.0)
        conn.commit()
        row = conn.execute("SELECT updated_at FROM holdings WHERE id = ?", (hid,)).fetchone()
        assert row["updated_at"] is not None


class TestWatchlist:
    def test_crud(self, conn):
        wid = Q.insert_watchlist(conn, stock_code="300750", stock_name="宁德时代",
                                 tier="tier1_core", add_reason="龙头")
        assert wid > 0
        rows = Q.get_watchlist(conn, tier="tier1_core")
        assert len(rows) == 1
        Q.update_watchlist_item(conn, wid, status="triggered")
        Q.delete_watchlist_item(conn, wid)
        rows = Q.get_watchlist(conn)
        assert len(rows) == 0

    def test_tier_specific_fields(self, conn):
        Q.insert_watchlist(conn, stock_code="300750", stock_name="宁德时代",
                           tier="tier1_core", entry_mode="打板", position_plan="半仓")
        Q.insert_watchlist(conn, stock_code="600519", stock_name="贵州茅台",
                           tier="tier3_sector", leader_type="中军", successor="五粮液")
        t1 = Q.get_watchlist(conn, tier="tier1_core")
        assert t1[0]["entry_mode"] == "打板"
        t3 = Q.get_watchlist(conn, tier="tier3_sector")
        assert t3[0]["leader_type"] == "中军"


class TestBlacklist:
    def test_crud(self, conn):
        bid = Q.insert_blacklist(conn, "000001", "平安银行", reason="观望")
        assert bid > 0
        rows = Q.get_blacklist(conn)
        assert len(rows) == 1
        Q.delete_blacklist(conn, bid)
        assert len(Q.get_blacklist(conn)) == 0


# ──────────────────────────────────────────────────────────────
# Calendar
# ──────────────────────────────────────────────────────────────

class TestCalendar:
    def test_insert_and_range(self, conn):
        Q.insert_calendar_event(conn, date="2026-04-01", event="CPI数据", impact="high", category="macro")
        Q.insert_calendar_event(conn, date="2026-04-15", event="GDP数据", impact="high", category="macro")
        Q.insert_calendar_event(conn, date="2026-05-01", event="劳动节", impact="low")
        rows = Q.get_calendar_range(conn, "2026-04-01", "2026-04-30")
        assert len(rows) == 2

    def test_impact_filter(self, conn):
        Q.insert_calendar_event(conn, date="2026-04-01", event="CPI数据", impact="high")
        Q.insert_calendar_event(conn, date="2026-04-01", event="小事件", impact="low")
        rows = Q.get_calendar_range(conn, "2026-04-01", "2026-04-01", impact="high")
        assert len(rows) == 1
        assert rows[0]["event"] == "CPI数据"


# ──────────────────────────────────────────────────────────────
# Emotion Cycle / Main Themes
# ──────────────────────────────────────────────────────────────

class TestEmotionCycle:
    def test_unique_date(self, conn):
        import sqlite3
        Q.upsert_emotion_cycle(conn, {"date": "2026-04-01", "phase": "启动"})
        Q.upsert_emotion_cycle(conn, {"date": "2026-04-01", "phase": "发酵"})
        row = Q.get_latest_emotion(conn)
        assert row["phase"] == "发酵"

    def test_latest(self, conn):
        Q.upsert_emotion_cycle(conn, {"date": "2026-03-31", "phase": "启动"})
        Q.upsert_emotion_cycle(conn, {"date": "2026-04-01", "phase": "发酵"})
        row = Q.get_latest_emotion(conn)
        assert row["date"] == "2026-04-01"


class TestMainThemes:
    def test_unique_date_name(self, conn):
        import sqlite3
        Q.upsert_main_theme(conn, {"date": "2026-04-01", "theme_name": "AI", "status": "active"})
        Q.upsert_main_theme(conn, {"date": "2026-04-01", "theme_name": "AI", "status": "fading"})
        rows = conn.execute(
            "SELECT * FROM main_themes WHERE date = '2026-04-01' AND theme_name = 'AI'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "fading"

    def test_active_filter(self, conn):
        Q.upsert_main_theme(conn, {"date": "2026-04-01", "theme_name": "AI", "status": "active"})
        Q.upsert_main_theme(conn, {"date": "2026-04-01", "theme_name": "锂电", "status": "fading"})
        active = Q.get_active_themes(conn)
        assert len(active) == 1
        assert active[0]["theme_name"] == "AI"


# ──────────────────────────────────────────────────────────────
# Trades
# ──────────────────────────────────────────────────────────────

class TestTrades:
    def test_insert_and_query(self, conn):
        Q.insert_trade(
            conn, date="2026-04-01", stock_code="300750", stock_name="宁德时代",
            direction="买入", price=200.0, shares=100,
        )
        Q.insert_trade(
            conn, date="2026-04-01", stock_code="600519", stock_name="贵州茅台",
            direction="卖出", price=1800.0, pnl_pct=5.0,
        )
        all_trades = Q.get_trades(conn, date_from="2026-04-01", date_to="2026-04-01")
        assert len(all_trades) == 2

        by_code = Q.get_trades(conn, stock_code="300750")
        assert len(by_code) == 1
        assert by_code[0]["direction"] == "买入"


# ──────────────────────────────────────────────────────────────
# Review conclusion snippet（盘前简报）
# ──────────────────────────────────────────────────────────────

class TestReviewConclusionSnippet:
    def test_extract_from_step8_json(self):
        row = {
            "step8_plan": json.dumps({
                "summary": {"one_sentence": "一句话", "trinity": "三位一体长文"},
            }),
        }
        lines = Q.extract_review_conclusion_lines(row)
        assert lines == ["一句话", "三位一体长文"]

    def test_extract_plain_summary_string(self):
        row = {"summary": "纯文本结论"}
        assert Q.extract_review_conclusion_lines(row) == ["纯文本结论"]


# ──────────────────────────────────────────────────────────────
# Unified Search / Stock Mentions
# ──────────────────────────────────────────────────────────────

class TestUnifiedSearch:
    def test_cross_entity(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="锂电观点", core_view="锂电看好",
        )
        Q.insert_industry_info(
            conn, date="2026-04-01", sector_name="锂电",
            content="锂电板块资金流入", info_type="news",
        )
        results = Q.unified_search(conn, "锂电")
        assert "teacher_notes" in results
        assert "industry_info" in results
        assert len(results["teacher_notes"]) >= 1
        assert len(results["industry_info"]) >= 1

    def test_type_filter(self, conn, teacher_id):
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="锂电观点", core_view="锂电看好",
        )
        results = Q.unified_search(conn, "锂电", types=["teacher_notes"])
        assert "teacher_notes" in results
        assert "industry_info" not in results

    def test_stock_mentions(self, conn, teacher_id):
        Q.upsert_holding(conn, stock_code="300750", stock_name="宁德时代")
        Q.insert_watchlist(conn, stock_code="300750", stock_name="宁德时代", tier="tier1_core")
        Q.insert_teacher_note(
            conn, teacher_id=teacher_id, date="2026-04-01",
            title="test", raw_content="关注 300750 宁德时代",
        )
        mentions = Q.stock_mentions(conn, "300750")
        assert len(mentions["holdings"]) == 1
        assert len(mentions["watchlist"]) == 1
        assert len(mentions["notes"]) == 1


# ──────────────────────────────────────────────────────────────
# Schema Version
# ──────────────────────────────────────────────────────────────

class TestSchemaVersion:
    def test_version_management(self, tmp_path):
        from db.migrate import migrate, get_schema_version
        c = get_connection(tmp_path / "test.db")
        assert get_schema_version(c) == 0
        migrate(c)
        assert get_schema_version(c) == 3
        migrate(c)
        assert get_schema_version(c) == 3
        c.close()
