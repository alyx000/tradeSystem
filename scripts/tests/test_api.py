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
from services.content_identity import canonical_content_sha256


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


def test_api_get_db_dependency_does_not_activate_v40_on_v39_database(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy-v39-api.db"
    conn = get_connection(db_path)
    conn.executescript(
        """
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            platform TEXT,
            schedule TEXT
        );
        CREATE TABLE teacher_notes (
            id INTEGER PRIMARY KEY,
            teacher_id INTEGER REFERENCES teachers(id),
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            source_type TEXT DEFAULT 'text',
            input_by TEXT,
            core_view TEXT,
            position_advice TEXT,
            obsidian_path TEXT,
            tags TEXT,
            key_points TEXT,
            sectors TEXT,
            avoid TEXT,
            raw_content TEXT,
            mentioned_stocks TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO teachers (id, name) VALUES (1, '旧老师');
        PRAGMA user_version = 39;
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    from api.main import app

    response = TestClient(app).get("/api/teachers")

    assert response.status_code == 200
    conn = get_connection(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 39
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(teacher_notes)")
        }
    finally:
        conn.close()
    assert "source_url" not in columns


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


def _api_source_note(
    *,
    raw_content: str = "API 公众号原文\n",
    article_id: str = "api-article-1",
    source_url: str = "https://mp.weixin.qq.com/s/api-example",
) -> dict:
    return {
        "teacher_name": "安静拆主线",
        "date": "2026-07-13",
        "title": "API 盘后复盘",
        "raw_content": raw_content,
        "source_platform": "wechat_mp",
        "source_url": source_url,
        "source_article_id": article_id,
        "published_at": "2026-07-13T20:00:00+08:00",
        "fetched_at": "2026-07-13T22:15:00+08:00",
        "content_sha256": canonical_content_sha256(raw_content),
        "input_by": "codex_automation",
    }


# ──────────────────────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_regulatory_monitor_list(client, db_path):
    conn = get_connection(db_path)
    Q.upsert_regulatory_monitor(conn, {
        "ts_code": "600000.SH",
        "name": "浦发银行",
        "regulatory_type": 1,
        "risk_level": 1,
        "reason": "停牌核查",
        "publish_date": "2026-04-03",
        "source": "tushare:suspend_d",
        "risk_score": 1.0,
        "detail_json": {"k": "v"},
    })
    Q.replace_stk_alert_snapshot(conn, "2026-04-03", [{
        "ts_code": "300834.SZ",
        "name": "星辉环材",
        "monitor_start": "2026-04-07",
        "monitor_end": "2026-04-20",
        "alert_type": "交易所重点提示证券",
        "source": "test:stk_alert",
        "detail_json": {},
    }])
    conn.commit()
    conn.close()

    r = client.get("/api/regulatory-monitor", params={"date": "2026-04-03"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    codes = {row["ts_code"] for row in rows}
    assert codes == {"600000.SH", "300834.SZ"}
    by_code = {row["ts_code"]: row for row in rows}
    assert by_code["600000.SH"]["regulatory_type"] == 1
    assert by_code["300834.SZ"]["regulatory_type"] == 3
    assert by_code["300834.SZ"]["monitor_start_date"] == "2026-04-07"
    assert by_code["300834.SZ"]["monitor_end_date"] == "2026-04-20"

    r2 = client.get("/api/regulatory-monitor", params={"date": "2026-04-03", "type": "2"})
    assert r2.status_code == 200
    assert r2.json() == []

    r3 = client.get("/api/regulatory-monitor", params={"date": "2026-04-03", "type": "3"})
    assert r3.status_code == 200
    alert_rows = r3.json()
    assert len(alert_rows) == 1
    assert alert_rows[0]["ts_code"] == "300834.SZ"
    assert alert_rows[0]["regulatory_type"] == 3

    r_bad = client.get("/api/regulatory-monitor", params={"date": "not-a-date"})
    assert r_bad.status_code == 422


def test_meta_commands(seeded_client):
    r = seeded_client.get("/api/meta/commands")
    assert r.status_code == 200
    data = r.json()
    assert data["generated_by"] == "python3 scripts/generate_command_index.py"
    assert len(data["daily_quickstart"]) >= 1
    assert any(section["title"] == "开发与页面" for section in data["sections"])


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
        assert data["review_signals"]["sectors"]["data_status"] == "missing"

    def test_prefill_sector_source_failed_status(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-06-18",
            "sh_index_close": 3200.0,
            "raw_data": {
                "sector_moneyflow_ths": {
                    "status": "source_failed",
                    "error": "provider unavailable",
                    "data": [],
                },
            },
        })
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-06-18/prefill")
        assert r.status_code == 200
        data = r.json()
        assert data["review_signals"]["sectors"]["data_status"] == "source_failed"
        assert data["review_signals"]["sectors"]["projection_candidates"] == []

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

    def test_prefill_prev_market_scrubs_legacy_northbound(self, client, db_path):
        """北向净额下线（口径存疑）：复盘预填 prev_market 不得外泄前一日 raw_data.northbound。

        当日 market 经 enrich_daily_market_row 已弹掉 raw_data；prev_market 须同样处理，
        否则旧归档里的口径存疑北向净额/活跃股会经 /review/{date}/prefill 原样返回。
        """
        conn = get_connection(db_path)
        prev_env = {
            "date": "2026-06-16",
            "raw_data": {
                "indices": {"shanghai": {"close": 3100.0}},
                "northbound": {"net_buy_billion": 42.93, "top_active_stocks": [{"name": "中际旭创"}]},
            },
        }
        Q.upsert_daily_market(conn, {
            "date": "2026-06-16", "sh_index_close": 3100.0, "northbound_net": 42.93,
            "raw_data": json.dumps(prev_env, ensure_ascii=False),
        })
        Q.upsert_daily_market(conn, {"date": "2026-06-17", "sh_index_close": 3120.0})
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-06-17/prefill")
        assert r.status_code == 200
        prev = r.json()["prev_market"]
        assert prev is not None
        assert prev["date"] == "2026-06-16"
        # 顶层净额置空 + raw_data 已弹出（不携带嵌套 northbound 块）。
        # 用嵌套块特有键判定，避免与列名 northbound_net 子串混淆。
        assert prev.get("northbound_net") is None
        assert "raw_data" not in prev
        dumped = json.dumps(prev, ensure_ascii=False)
        assert "net_buy_billion" not in dumped
        assert "top_active_stocks" not in dumped

    def test_prefill_includes_holding_signals(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-03",
            "sh_index_close": 3210.0,
            "total_amount": 12000.0,
            "raw_data": json.dumps({
                "date": "2026-04-03",
                "holdings_data": [
                    {
                        "code": "300750.SZ",
                        "name": "宁德时代",
                        "close": 192.0,
                        "pnl_pct": 6.67,
                        "ma5": 188.0,
                        "ma10": 185.0,
                        "ma20": 180.0,
                        "volume_vs_ma5": "以上",
                        "turnover_rate": 6.2,
                        "sector_change_pct": 3.2,
                    },
                ],
                "limit_cpt_list": {
                    "data": [
                        {"name": "电池", "rank": 1, "up_nums": 5, "cons_nums": 2, "pct_chg": 4.2},
                    ],
                },
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "电池", "net_amount": 800000000, "pct_change": 3.2, "lead_stock": "宁德时代"},
                    ],
                },
                "sector_moneyflow_dc": {
                    "data": [
                        {"name": "电池", "content_type": "行业", "net_amount": 600000000, "pct_change": 2.8, "buy_sm_amount_stock": "宁德时代"},
                    ],
                },
            }, ensure_ascii=False),
        })
        Q.upsert_main_theme(conn, {"date": "2026-04-03", "theme_name": "电池", "status": "active"})
        Q.upsert_holding(
            conn,
            stock_code="300750.SZ",
            stock_name="宁德时代",
            entry_price=180.0,
            current_price=None,
            sector="电池",
            stop_loss=175.0,
            target_price=193.0,
            status="active",
        )
        Q.replace_holding_tasks(
            conn,
            trade_date="2026-04-02",
            tasks=[{
                "stock_code": "300750.SZ",
                "stock_name": "宁德时代",
                "action_plan": "若冲高回落则减仓",
                "status": "open",
            }],
        )
        # daily_basic 提供涨跌停价计算基准，落在前一交易日 04-02（限价取严格早于查询日的前收）；
        # stk_limit 保留以验证其已不再被读取。
        conn.execute(
            """
            INSERT INTO raw_interface_payloads
            (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
             payload_json, payload_hash, row_count, status, params_json, source_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "daily_basic", "tushare:daily_basic", "post_core", "2026-04-02", "2026-04-02", "raw_daily_basic",
                "daily_basic:2026-04-02:test",
                json.dumps({"rows": [{"ts_code": "300750.SZ", "close": 192.0}]}, ensure_ascii=False),
                "dbp", 1, "success", "{}", "{}",
            ),
        )
        for interface_name, raw_table, payload in [
            ("anns_d", "raw_anns_d", {"rows": [{"ts_code": "300750.SZ", "title": "回购公告", "ann_date": "20260402"}]}),
            ("disclosure_date", "raw_disclosure_date", {"rows": [{"ts_code": "300750.SZ", "ann_date": "20260404", "report_end": "20260331"}]}),
            ("stk_limit", "raw_stk_limit", {"rows": [{"ts_code": "300750.SZ", "pre_close": 190.0, "up_limit": 209.0, "down_limit": 171.0}]}),
            ("stock_st", "raw_stock_st", {"rows": [{"ts_code": "300750.SZ", "name": "宁德时代"}]}),
            ("share_float", "raw_share_float", {"rows": [{"ts_code": "300750.SZ", "ann_date": "20260401", "float_date": "20260410", "float_share": 123456789}]}),
        ]:
            conn.execute(
                """
                INSERT INTO raw_interface_payloads
                (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
                 payload_json, payload_hash, row_count, status, params_json, source_meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interface_name,
                    f"tushare:{interface_name}",
                    "post_extended",
                    "2026-04-03",
                    "2026-04-03",
                    raw_table,
                    f"{interface_name}:2026-04-03:test",
                    json.dumps(payload, ensure_ascii=False),
                    f"h:{interface_name}",
                    len(payload["rows"]),
                    "success",
                    "{}",
                    "{}",
                ),
            )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-04-03/prefill")
        assert r.status_code == 200
        data = r.json()
        signals = data["holding_signals"]
        assert signals["date"] == "2026-04-03"
        assert len(signals["items"]) == 1
        item = signals["items"][0]
        assert item["stock_code"] == "300750.SZ"
        assert item["price_snapshot"]["current_price"] == 192.0
        # 创业板 20% 实时算价，基准 192.0：up = 192 × 1.2 = 230.4；
        # stk_limit 快照（209.0，错按 10% 塞）已不再被读取。
        assert item["price_snapshot"]["up_limit"] == 230.4
        assert item["technical_signals"]["above_ma5"] is True
        assert item["technical_signals"]["volume_vs_ma5"] == "以上"
        assert item["technical_signals"]["turnover_rate"] == 6.2
        assert item["technical_signals"]["turnover_status"] == "活跃"
        assert item["theme_signals"]["is_main_theme"] is True
        assert item["theme_signals"]["is_strongest_sector"] is True
        assert item["theme_signals"]["sector_flow_source"] == "ths"
        assert item["event_signals"]["has_recent_announcement"] is True
        assert item["event_signals"]["has_disclosure_plan"] is True
        assert item["event_signals"]["is_st"] is True
        assert item["event_signals"]["share_float_upcoming"][0]["float_date"] == "20260410"
        assert item["latest_task"]["action_plan"] == "若冲高回落则减仓"
        labels = {flag["label"] for flag in item["risk_flags"]}
        assert "财报临近" in labels
        assert "ST" in labels
        assert "临近止盈" in labels
        assert "info_signals" in item
        assert item["info_signals"]["investor_qa"] == []
        assert item["info_signals"]["research_reports"] == []
        assert item["info_signals"]["news"] == []

    def test_prefill_holding_signals_includes_info_signals_from_envelope(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-05",
            "sh_index_close": 3210.0,
            "total_amount": 12000.0,
            "raw_data": json.dumps({
                "date": "2026-04-05",
                "holdings_data": [
                    {"code": "300750.SZ", "name": "宁德时代", "close": 195.0, "pnl_pct": 8.33},
                ],
                "holdings_info": {
                    "300750.SZ": {
                        "name": "宁德时代",
                        "investor_qa": [
                            {"question": "公司产能规划如何", "answer": "已规划新增 100GWh", "date": "2026-04-04"},
                            {"question": "海外市场进展", "answer": "欧洲工厂已投产", "date": "2026-04-03"},
                        ],
                        "research_reports": [
                            {"institution": "中金", "rating": "买入", "target_price": 220, "date": "2026-04-04"},
                        ],
                        "news": [
                            {"title": "宁德时代发布新一代电池", "time": "2026-04-05 10:30"},
                            {"title": "宁德与车企签署供货协议", "time": "2026-04-04 16:00"},
                        ],
                    },
                },
            }, ensure_ascii=False),
        })
        Q.upsert_holding(
            conn, stock_code="300750.SZ", stock_name="宁德时代",
            entry_price=180.0, sector="电池", status="active",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-04-05/prefill")
        assert r.status_code == 200
        item = r.json()["holding_signals"]["items"][0]
        info = item["info_signals"]
        assert len(info["investor_qa"]) == 2
        assert info["investor_qa"][0]["question"] == "公司产能规划如何"
        assert len(info["research_reports"]) == 1
        assert info["research_reports"][0]["institution"] == "中金"
        assert info["research_reports"][0]["target_price"] == 220
        assert len(info["news"]) == 2
        assert "新一代电池" in info["news"][0]["title"]

    def test_prefill_holding_signals_gracefully_degrades_when_sources_missing(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_holding(
            conn,
            stock_code="300750",
            stock_name="宁德时代",
            entry_price=180.0,
            current_price=181.0,
            sector="电池",
            status="active",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-04-03/prefill")
        assert r.status_code == 200
        data = r.json()
        item = data["holding_signals"]["items"][0]
        assert item["stock_code"] == "300750"
        assert item["price_snapshot"]["current_price"] == 181.0
        assert item["event_signals"]["recent_announcements"] == []
        assert item["event_signals"]["disclosure_dates"] == []
        assert item["event_signals"]["share_float_upcoming"] == []
        assert item["theme_signals"]["is_main_theme"] is False
        assert item["risk_flags"] == []
        assert "info_signals" in item
        assert item["info_signals"]["investor_qa"] == []
        assert item["info_signals"]["research_reports"] == []
        assert item["info_signals"]["news"] == []

    def test_prefill_holding_signals_include_stop_loss_and_target_alerts(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_holding(
            conn,
            stock_code="300750",
            stock_name="宁德时代",
            entry_price=180.0,
            current_price=100.0,
            stop_loss=101.0,
            target_price=100.0,
            status="active",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-04-03/prefill")
        assert r.status_code == 200
        item = r.json()["holding_signals"]["items"][0]
        labels = {flag["label"] for flag in item["risk_flags"]}
        assert "触及止损" in labels
        assert "触及止盈" in labels

    def test_holding_signals_falls_back_to_snapshot_when_envelope_missing_holdings_data(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-03",
            "sh_index_close": 3210.0,
            "total_amount": 12000.0,
            "raw_data": json.dumps({"date": "2026-04-03"}, ensure_ascii=False),
        })
        Q.upsert_holding(
            conn,
            stock_code="300750",
            stock_name="宁德时代",
            entry_price=180.0,
            sector="电池",
            status="active",
        )
        Q.upsert_main_theme(conn, {"date": "2026-04-03", "theme_name": "电池", "status": "active"})
        Q.upsert_holding_quote_snapshot(
            conn,
            trade_date="2026-04-03",
            stock_code="300750.SZ",
            stock_name="宁德时代",
            close=192.0,
            pnl_pct=6.67,
            turnover_rate=6.2,
            ma5=188.0,
            ma10=185.0,
            ma20=180.0,
            volume_vs_ma5="以上",
        )
        conn.execute(
            """
            INSERT INTO raw_interface_payloads
            (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
             payload_json, payload_hash, row_count, status, params_json, source_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "stk_limit", "tushare:stk_limit", "post_extended", "2026-04-03", "2026-04-03", "raw_stk_limit",
                "stk_limit:2026-04-03:test:snapshot",
                json.dumps({"rows": [{"ts_code": "300750.SZ", "pre_close": 190.0, "up_limit": 209.0, "down_limit": 171.0}]}, ensure_ascii=False),
                "h_snapshot", 1, "success", "{}", "{}",
            ),
        )
        conn.commit()
        conn.close()

        r = client.get("/api/holdings/signals", params={"date": "2026-04-03"})
        assert r.status_code == 200
        data = r.json()
        item = data["items"][0]
        assert item["price_snapshot"]["current_price"] == 192.0
        assert item["technical_signals"]["above_ma5"] is True
        assert item["technical_signals"]["turnover_rate"] == 6.2
        assert item["technical_signals"]["turnover_status"] == "活跃"
        assert item["technical_signals"]["volume_vs_ma5"] == "以上"

    def test_holding_signals_merges_snapshot_into_sparse_envelope_rows(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-03",
            "sh_index_close": 3210.0,
            "total_amount": 12000.0,
            "raw_data": json.dumps({
                "date": "2026-04-03",
                "holdings_data": [
                    {"code": "300750.SZ", "name": "宁德时代", "close": 192.0},
                ],
            }, ensure_ascii=False),
        })
        Q.upsert_holding(
            conn,
            stock_code="300750",
            stock_name="宁德时代",
            entry_price=180.0,
            sector="电池",
            status="active",
        )
        Q.upsert_holding_quote_snapshot(
            conn,
            trade_date="2026-04-03",
            stock_code="300750.SZ",
            stock_name="宁德时代",
            close=192.0,
            pnl_pct=6.67,
            turnover_rate=6.2,
            ma5=188.0,
            ma10=185.0,
            ma20=180.0,
            volume_vs_ma5="以上",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/holdings/signals", params={"date": "2026-04-03"})
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["price_snapshot"]["current_price"] == 192.0
        assert item["technical_signals"]["ma5"] == 188.0
        assert item["technical_signals"]["turnover_rate"] == 6.2
        assert item["technical_signals"]["volume_vs_ma5"] == "以上"
        assert item["technical_signals"]["above_ma5"] is True

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

    def test_save_review_normalizes_agent_summary_fields_for_display(self, client):
        body = {
            "step1_market": {
                "facts": ["上证小幅震荡收红", "成交额守住 2.5 万亿"],
                "judgement": "今天是弱修复，不是强共振。",
            }
        }
        r = client.put("/api/review/2026-04-01", json=body)
        assert r.status_code == 200

        r = client.get("/api/review/2026-04-01")
        assert r.status_code == 200
        data = r.json()
        step1 = json.loads(data["step1_market"])
        assert "上证小幅震荡收红" in step1["notes"]
        assert "成交额守住 2.5 万亿" in step1["notes"]
        assert "今天是弱修复，不是强共振。" in step1["notes"]

    def test_review_to_draft_generates_review_observation_and_trade_draft(self, client):
        body = {
            "step1_market": {
                "direction": {"trend": "主升"},
            },
            "step2_sectors": {
                "selection_summary": "AI算力仍是最值得跟踪的方向，明天重点看分歧后的回流确认。",
                "projections": [
                    {
                        "sector_name": "AI算力",
                        "big_cycle_stage": "主升",
                        "connection_bias": "加强",
                        "market_fit": "匹配大势节奏",
                        "return_flow_view": "预期回流",
                        "fully_priced_risk": "中",
                        "logic_aesthetic": "增量订单明确，容量足够大。",
                        "judgement_notes": "需要确认是否能承接分歧。",
                        "key_stocks": ["高标A", "中军B"],
                        "supporting_facts": ["活跃主线", "资金回流"],
                    },
                    {
                        "sector_name": "机器人",
                        "big_cycle_stage": "震荡",
                        "connection_bias": "不清楚",
                        "market_fit": "一般",
                        "return_flow_view": "仅跟踪",
                        "fully_priced_risk": "低",
                        "logic_aesthetic": "新催化待验证。",
                        "judgement_notes": "只跟踪支线弹性。",
                        "key_stocks": ["弹性C"],
                        "supporting_facts": ["板块有异动"],
                    }
                ],
                "next_day_focus": [
                    {
                        "sector_name": "AI算力",
                        "key_stocks": ["高标A", "中军B"],
                        "focus_reason": "分歧后的回流确认",
                    },
                    {
                        "sector_name": "机器人",
                        "key_stocks": ["弹性C"],
                        "focus_reason": "支线异动观察",
                    }
                ],
            },
        }
        r = client.put("/api/review/2026-04-03", json=body)
        assert r.status_code == 200

        r = client.post("/api/review/2026-04-03/to-draft", json={"input_by": "pytest"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["review_date"] == "2026-04-03"
        assert payload["trade_date"] == "2026-04-06"
        assert payload["observation"]["source_type"] == "review"
        assert payload["draft"]["trade_date"] == "2026-04-06"

        sector_view = json.loads(payload["draft"]["sector_view_json"])
        assert sector_view["main_themes"] == ["AI算力", "机器人"]
        stock_focus = json.loads(payload["draft"]["stock_focus_json"])
        assert any(item["subject_type"] == "sector" and item["subject_name"] == "AI算力" for item in stock_focus)
        assert any(item["subject_type"] == "sector" and item["subject_name"] == "机器人" for item in stock_focus)
        assert any(item["subject_type"] == "stock" and item["subject_name"] == "高标A" for item in stock_focus)
        assert any(item["subject_type"] == "stock" and item["subject_name"] == "弹性C" for item in stock_focus)

        fact_candidates = json.loads(payload["draft"]["fact_check_candidates_json"])
        assert any(item["check_type"] == "market_amount_gte_prev_day" for item in fact_candidates)
        assert any(item["check_type"] == "sector_change_positive" and item["subject_name"] == "AI算力" for item in fact_candidates)
        assert any(item["check_type"] == "sector_change_positive" and item["subject_name"] == "机器人" for item in fact_candidates)

        judgement_candidates = json.loads(payload["draft"]["judgement_check_candidates_json"])
        labels = {item["label"] for item in judgement_candidates}
        assert "AI算力连接点判断：加强" in labels
        assert "AI算力逻辑审美是否成立" in labels
        assert "机器人连接点判断：不清楚" in labels

        observation_judgements = json.loads(payload["observation"]["judgements_json"])
        ai_projection = next(
            item for item in observation_judgements
            if item.get("kind") == "sector_projection" and item.get("sector_name") == "AI算力"
        )
        assert ai_projection["big_cycle_stage"] == "主升"
        assert ai_projection["connection_bias"] == "加强"
        assert ai_projection["market_fit"] == "匹配大势节奏"
        assert ai_projection["supporting_facts"] == ["活跃主线", "资金回流"]

    def test_review_to_draft_skips_calendar_closure_dates(self, client, db_path):
        body = {
            "step1_market": {"direction": {"trend": "震荡"}},
            "step2_sectors": {"selection_summary": "节后再看主线。"},
        }
        r = client.put("/api/review/2026-04-03", json=body)
        assert r.status_code == 200

        conn = get_connection(db_path)
        Q.insert_calendar_event(conn, date="2026-04-06", event="清明休市", category="假期")
        conn.commit()
        conn.close()

        r = client.post("/api/review/2026-04-03/to-draft", json={"input_by": "pytest"})
        assert r.status_code == 200
        assert r.json()["trade_date"] == "2026-04-07"

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

    def test_style_factors_capacity_and_first_open_series(self, client, db_path):
        """容量票 / 一字首开溢价应可经 /api/style-factors/series 取序列（趋势图数据源）。"""
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-01", "premium_capacity": 1.79, "premium_first_open": -0.71,
        })
        Q.upsert_daily_market(conn, {
            "date": "2026-04-02", "premium_capacity": 2.18, "premium_first_open": 8.02,
        })
        conn.commit()
        conn.close()

        r = client.get("/api/style-factors/series", params={
            "metrics": "premium_capacity,premium_first_open",
            "from": "2026-04-01", "to": "2026-04-02",
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert data[0]["premium_capacity"] == 1.79
        assert data[1]["premium_first_open"] == 8.02

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

    def test_knowledge_asset_draft_requires_trade_date(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "manual_note",
                "title": "机器人资料",
                "content": "机器人回流，002594.SZ 关注趋势延续。",
                "source": "manual",
                "tags": ["机器人"],
            },
        )
        asset = r.json()

        r = client.post(
            f"/api/knowledge/assets/{asset['asset_id']}/draft",
            json={"input_by": "cursor"},
        )
        assert r.status_code == 422

    def test_post_knowledge_asset_rejects_teacher_note(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "teacher_note",
                "title": "不应写入",
                "content": "正文",
                "tags": [],
            },
        )
        assert r.status_code == 422

    def test_post_knowledge_asset_rejects_course_note(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "course_note",
                "title": "不应新建课程类型",
                "content": "正文",
                "tags": [],
            },
        )
        assert r.status_code == 422

    def test_post_knowledge_asset_rejects_unknown_asset_type(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "not_a_type",
                "title": "x",
                "content": "y",
                "tags": [],
            },
        )
        assert r.status_code == 422

    def test_post_knowledge_asset_strips_asset_type_whitespace(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": " manual_note ",
                "title": "空白规范化",
                "content": "z",
                "tags": [],
            },
        )
        assert r.status_code == 200
        assert r.json().get("asset_type") == "manual_note"

    def test_list_knowledge_assets_excludes_legacy_teacher_note_rows(self, client, db_path):
        conn = get_connection(db_path)
        conn.execute(
            """
            INSERT INTO knowledge_assets
            (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
            VALUES (?, 'teacher_note', ?, ?, NULL, '[]', '', '{}')
            """,
            ("asset_legacy_tn", "库内遗留 teacher_note", "仅列表过滤用"),
        )
        conn.commit()
        conn.close()

        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "manual_note",
                "title": "手动一条",
                "content": "正文",
                "tags": [],
            },
        )
        assert r.status_code == 200
        r = client.get("/api/knowledge/assets?limit=20")
        assert r.status_code == 200
        assets = r.json()
        types = {a.get("asset_type") for a in assets}
        assert "teacher_note" not in types
        assert any(a.get("title") == "手动一条" for a in assets)
        assert not any(a.get("asset_id") == "asset_legacy_tn" for a in assets)

    def test_list_knowledge_assets_keyword_and_asset_type(self, client):
        client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "manual_note",
                "title": "锂电主题",
                "content": "正文A",
                "tags": [],
            },
        )
        client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "news_note",
                "title": "宏观周报",
                "content": "锂电在正文里",
                "tags": [],
            },
        )
        r = client.get("/api/knowledge/assets?keyword=锂电&asset_type=news_note&limit=50")
        assert r.status_code == 200
        assets = r.json()
        assert all(a.get("asset_type") == "news_note" for a in assets)
        assert any(a.get("title") == "宏观周报" for a in assets)
        assert not any(a.get("title") == "锂电主题" for a in assets)

    def test_list_knowledge_assets_rejects_course_note_filter(self, client):
        r = client.get("/api/knowledge/assets?asset_type=course_note&limit=10")
        assert r.status_code == 422

    def test_list_knowledge_assets_rejects_typo_asset_type(self, client):
        r = client.get("/api/knowledge/assets?asset_type=manual_not&limit=10")
        assert r.status_code == 422

    def test_list_knowledge_assets_rejects_invalid_created_from(self, client):
        r = client.get("/api/knowledge/assets?created_from=2026-13-40&limit=10")
        assert r.status_code == 422

    def test_list_teacher_notes_limit_offset_and_filters(self, client, db_path):
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "分页测试老师")
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-01-01", title="最早", raw_content="x"
        )
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-01-03", title="最新", raw_content="x"
        )
        Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-01-02", title="中间", raw_content="unique_kw_xyz"
        )
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes?limit=1&offset=0")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "最新"

        r2 = client.get("/api/teacher-notes?limit=1&offset=1")
        assert r2.status_code == 200
        assert r2.json()[0]["title"] == "中间"

        r3 = client.get("/api/teacher-notes?keyword=unique_kw_xyz&from=2026-01-01&to=2026-01-31")
        assert r3.status_code == 200
        titles = {n["title"] for n in r3.json()}
        assert titles == {"中间"}

    def test_draft_from_asset_rejects_legacy_teacher_note_row(self, client, db_path):
        conn = get_connection(db_path)
        conn.execute(
            """
            INSERT INTO knowledge_assets
            (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
            VALUES (?, 'teacher_note', ?, ?, NULL, '[]', '', '{}')
            """,
            ("asset_legacy_tn2", "遗留", "x"),
        )
        conn.commit()
        conn.close()

        r = client.post(
            f"/api/knowledge/assets/asset_legacy_tn2/draft",
            json={"trade_date": "2026-04-11"},
        )
        assert r.status_code == 422

    def test_draft_from_asset_rejects_invalid_trade_clues_json(self, client, db_path):
        conn = get_connection(db_path)
        conn.execute(
            """
            INSERT INTO knowledge_assets
            (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
            VALUES (?, 'manual_note', ?, ?, NULL, '[]', '', ?)
            """,
            ("asset_bad_tc", "trade_clues 损坏", "x", "{not-json"),
        )
        conn.commit()
        conn.close()

        r = client.post(
            "/api/knowledge/assets/asset_bad_tc/draft",
            json={"trade_date": "2026-04-11"},
        )
        assert r.status_code == 422
        detail = str(r.json().get("detail", ""))
        assert "asset_bad_tc" in detail or "JSON" in detail or "json" in detail.lower()

    def test_delete_knowledge_asset(self, client):
        r = client.post(
            "/api/knowledge/assets",
            json={
                "asset_type": "manual_note",
                "title": "待删",
                "content": "x",
                "tags": [],
            },
        )
        aid = r.json()["asset_id"]
        r = client.delete(f"/api/knowledge/assets/{aid}")
        assert r.status_code == 200
        assert r.json().get("ok") is True
        r = client.delete(f"/api/knowledge/assets/{aid}")
        assert r.status_code == 404

    def test_draft_from_teacher_note(self, client):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "规划测试老师",
                "date": "2026-04-05",
                "title": "笔记生成草稿",
                "raw_content": "AI算力 688041.SH 仍有分歧",
                "tags": ["AI算力"],
                "input_by": "pytest",
            },
        )
        assert r.status_code == 200
        nid = r.json()["id"]
        r = client.post(
            f"/api/knowledge/teacher-notes/{nid}/draft",
            json={"trade_date": "2026-04-11", "input_by": "pytest"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["observation"]["source_type"] == "teacher_note"
        assert payload["draft"]["trade_date"] == "2026-04-11"
        assert "teacher_note" in payload
        refs = __import__("json").loads(payload["observation"]["source_refs_json"])
        assert any(r.get("teacher_note_id") == nid for r in refs)

    def test_draft_from_teacher_note_404(self, client):
        r = client.post(
            "/api/knowledge/teacher-notes/999999/draft",
            json={"trade_date": "2026-04-11"},
        )
        assert r.status_code == 404

    def test_draft_from_teacher_note_requires_trade_date(self, client):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "规划测试老师B",
                "date": "2026-04-05",
                "title": "缺 trade_date 草稿测试",
                "raw_content": "内容",
                "tags": [],
                "input_by": "pytest",
            },
        )
        assert r.status_code == 200
        nid = r.json()["id"]
        r = client.post(
            f"/api/knowledge/teacher-notes/{nid}/draft",
            json={"input_by": "pytest"},
        )
        assert r.status_code == 422

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
        assert any(item["interface_label"] == "融资融券数据" for item in interfaces)

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
        assert "interface_label" in inspect_payload["runs"][0]
        assert "status_label" in inspect_payload["runs"][0]

        r = client.get("/api/ingest/inspect", params={"date": "2026-04-03", "interface": "margin"})
        assert r.status_code == 200
        inspect_filtered_payload = r.json()
        assert inspect_filtered_payload["interface_name"] == "margin"

        r = client.get("/api/ingest/inspect", params={"date": "2026-04-03", "stage": "post_extended"})
        assert r.status_code == 200
        inspect_stage_payload = r.json()
        assert inspect_stage_payload["stage"] == "post_extended"

        r = client.get("/api/ingest/runs", params={"date": "2026-04-03"})
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) >= 1
        assert "provider_label" in runs[0]

        r = client.get("/api/ingest/errors", params={"date": "2026-04-03"})
        assert r.status_code == 200
        errors = r.json()
        assert isinstance(errors, list)
        if errors:
            assert "error_type_label" in errors[0]
            assert "retryable_label" in errors[0]
            assert "action_hint" in errors[0]
            assert "restriction_label" in errors[0]

        r = client.get("/api/ingest/retry")
        assert r.status_code == 200
        retry_payload = r.json()
        assert "retryable_count" in retry_payload
        assert "status_label" in retry_payload
        assert "status_reason" in retry_payload
        groups = retry_payload.get("groups") or []
        if groups:
            assert "interface_label" in groups[0]

        r = client.get("/api/ingest/retry", params={"interface": "margin"})
        assert r.status_code == 200
        retry_filtered_payload = r.json()
        assert retry_filtered_payload["interface_name"] == "margin"

        r = client.get("/api/ingest/retry", params={"stage": "post_extended"})
        assert r.status_code == 200
        retry_stage_payload = r.json()
        assert retry_stage_payload["stage"] == "post_extended"

        r = client.get("/api/ingest/health", params={"date": "2026-04-03", "days": 7})
        assert r.status_code == 200
        health_payload = r.json()
        assert "top_failed_interfaces" in health_payload
        assert "daily_failures" in health_payload
        assert "failed_interface_count" in health_payload
        assert "never_succeeded_count" in health_payload
        assert "failure_rate" in health_payload
        assert "status_label" in health_payload
        assert "status_reason" in health_payload
        if health_payload["top_failed_interfaces"]:
            assert "consecutive_failure_days" in health_payload["top_failed_interfaces"][0]
            assert "days_since_last_success" in health_payload["top_failed_interfaces"][0]

        r = client.get("/api/ingest/health", params={"date": "2026-04-03", "days": 7, "stage": "post_extended"})
        assert r.status_code == 200
        health_stage_payload = r.json()
        assert health_stage_payload["stage"] == "post_extended"

        r = client.get("/api/ingest/health/dashboard", params={"date": "2026-04-03", "days": 7})
        assert r.status_code == 200
        dashboard_health_payload = r.json()
        assert dashboard_health_payload["core"]["stage"] == "post_core"
        assert dashboard_health_payload["extended"]["stage"] == "post_extended"
        assert "status_label" in dashboard_health_payload["core"]
        assert "status_label" in dashboard_health_payload["extended"]

        r = client.get(
            "/api/ingest/health",
            params={"date": "2026-04-03", "days": 7, "stage": "post_extended", "interface": "margin"},
        )
        assert r.status_code == 200
        health_interface_payload = r.json()
        assert health_interface_payload["stage"] == "post_extended"
        assert health_interface_payload["interface_name"] == "margin"

        r = client.post("/api/ingest/reconcile", json={"stale_minutes": 5})
        assert r.status_code == 200
        reconcile_payload = r.json()
        assert reconcile_payload["stale_minutes"] == 5
        assert "reconciled_count" in reconcile_payload

        r = client.post("/api/ingest/retry-run", json={"limit": 2, "input_by": "web"})
        assert r.status_code == 200
        retry_run_payload = r.json()
        assert "requested_groups" in retry_run_payload
        assert "attempted_groups" in retry_run_payload

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
        create_payload = r.json()
        assert create_payload["created"] is True
        assert create_payload["deduplicated_by"] is None
        nid = create_payload["id"]

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

    def test_source_note_post_is_idempotent_and_reports_identity(self, client, db_path):
        body = _api_source_note()

        first = client.post("/api/teacher-notes", json=body)
        duplicate = client.post("/api/teacher-notes", json=body)

        assert first.status_code == 200
        assert duplicate.status_code == 200
        assert first.json()["created"] is True
        assert first.json()["deduplicated_by"] is None
        assert duplicate.json() == {
            "id": first.json()["id"],
            "created": False,
            "deduplicated_by": "source_article_id",
        }
        conn = get_connection(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM teacher_notes WHERE source_article_id='api-article-1'"
        ).fetchone()[0] == 1
        conn.close()

    def test_source_note_partial_bundle_returns_422(self, client):
        response = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "安静拆主线",
                "date": "2026-07-13",
                "title": "不完整来源",
                "source_platform": "wechat_mp",
                "input_by": "codex_automation",
            },
        )

        assert response.status_code == 422
        assert "complete" in response.json()["detail"]

    def test_same_source_changed_content_returns_409(self, client):
        first = client.post("/api/teacher-notes", json=_api_source_note())
        assert first.status_code == 200

        changed = client.post(
            "/api/teacher-notes",
            json=_api_source_note(raw_content="被篡改的 API 原文"),
        )

        assert changed.status_code == 409
        assert "source_content_changed" in changed.json()["detail"]

    def test_source_duplicate_does_not_repeat_watchlist_sync(self, client, db_path):
        body = _api_source_note(
            article_id="api-side-effect",
            source_url="https://mp.weixin.qq.com/s/api-side-effect",
        )
        body.update({
            "mentioned_stocks": [
                {"code": "688997", "name": "API幂等", "tier": "tier2_watch"},
            ],
            "sync_watchlist_from_mentions": True,
        })

        first = client.post("/api/teacher-notes", json=body)
        duplicate = client.post("/api/teacher-notes", json=body)

        assert first.status_code == 200
        assert first.json()["created"] is True
        assert len(first.json()["watchlist_sync"]["added"]) == 1
        assert duplicate.status_code == 200
        assert duplicate.json()["created"] is False
        assert "watchlist_sync" not in duplicate.json()
        conn = get_connection(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE stock_code='688997'"
        ).fetchone()[0] == 1
        conn.close()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("source_url", "https://mp.weixin.qq.com/s/changed"),
            ("source_article_id", "changed-id"),
            ("raw_content", "changed body"),
            ("date", "2026-07-14"),
        ],
    )
    def test_source_note_immutable_fields_return_422(self, client, field, value):
        body = _api_source_note(
            article_id=f"api-immutable-{field}",
            source_url=f"https://mp.weixin.qq.com/s/api-immutable-{field}",
        )
        created = client.post("/api/teacher-notes", json=body)
        assert created.status_code == 200

        response = client.put(
            f"/api/teacher-notes/{created.json()['id']}",
            json={field: value},
        )

        assert response.status_code == 422

    def test_create_note_mentioned_stocks_default_no_watchlist_sync(self, client, db_path):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "API池老师",
                "date": "2026-04-10",
                "title": "仅记笔记",
                "mentioned_stocks": [
                    {"code": "300750", "name": "宁德时代", "tier": "tier3_sector"},
                ],
            },
        )
        assert r.status_code == 200
        payload = r.json()
        assert "watchlist_sync" not in payload
        conn = get_connection(db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE stock_code='300750'"
        ).fetchone()[0]
        assert n == 0
        conn.close()

    def test_create_note_watchlist_sync_opt_in(self, client, db_path):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "API池老师2",
                "date": "2026-04-11",
                "title": "同步标题",
                "mentioned_stocks": [
                    {"code": "688999", "name": "测同步", "tier": "tier2_watch"},
                ],
                "sync_watchlist_from_mentions": True,
                "input_by": "codex_automation",
            },
        )
        assert r.status_code == 200
        payload = r.json()
        nid = payload["id"]
        assert "watchlist_sync" in payload
        assert len(payload["watchlist_sync"]["added"]) == 1
        assert payload["watchlist_sync"]["added"][0]["code"] == "688999"
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT source_note_id, input_by FROM watchlist WHERE stock_code='688999'"
        ).fetchone()
        assert row["source_note_id"] == nid
        assert row["input_by"] == "codex_automation"
        conn.close()

    def test_create_note_mentioned_stocks_non_object_rejected(self, client):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "坏格式老师",
                "date": "2026-04-12",
                "title": "T",
                "mentioned_stocks": ["300750", {"code": "688041", "name": "B"}],
            },
        )
        assert r.status_code == 422
        detail = r.json().get("detail", "")
        assert "mentioned_stocks[0]" in (detail if isinstance(detail, str) else str(detail))

    def test_create_note_sync_invalid_mentioned_element_422(self, client):
        r = client.post(
            "/api/teacher-notes",
            json={
                "teacher_name": "同步坏格式",
                "date": "2026-04-12",
                "title": "T",
                "mentioned_stocks": ["300750"],
                "sync_watchlist_from_mentions": True,
            },
        )
        assert r.status_code == 422

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

    def test_note_list_omits_raw_content(self, client, db_path):
        """列表端点剔除 raw_content（占 payload ~82%），仅在详情端点按需返回。"""
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "全文老师")
        nid = Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-05-02", title="有全文的笔记",
            core_view="结构化核心观点", raw_content="这是很长的原始全文" * 500,
        )
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes")
        assert r.status_code == 200
        target = next((n for n in r.json() if n["id"] == nid), None)
        assert target is not None
        # 轻字段照常返回，供卡片渲染
        assert target["title"] == "有全文的笔记"
        assert target["core_view"] == "结构化核心观点"
        # 重字段 raw_content 不进列表响应
        assert target.get("raw_content") in (None, "")
        # 但保留轻量布尔标记，供前端决定是否渲染「原始观点全文」入口
        assert target["has_raw_content"] is True
        # 以及截断到 200 字的轻量预览（供列表页摘要，远小于全文）
        assert len(target["raw_content_preview"]) == 200
        assert target["raw_content_preview"].startswith("这是很长的原始全文")

    def test_note_list_preview_for_raw_only_note(self, client, db_path):
        """仅录入 raw_content（无 core_view，如资料工作台所建）的笔记，
        列表仍提供 raw_content_preview 兜底预览，避免下游只剩标题。"""
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "仅全文老师")
        nid = Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-05-06", title="仅有全文",
            raw_content="只有原始正文没有结构化观点",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes")
        target = next((n for n in r.json() if n["id"] == nid), None)
        assert target is not None
        assert target.get("core_view") in (None, "")
        assert target["raw_content_preview"] == "只有原始正文没有结构化观点"

    def test_note_list_has_raw_content_false_when_empty(self, client, db_path):
        """无全文的笔记 has_raw_content 为 False，前端不展示空折叠入口。"""
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "无全文老师")
        nid = Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-05-05", title="无全文笔记",
            core_view="只有结构化观点",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes")
        target = next((n for n in r.json() if n["id"] == nid), None)
        assert target is not None
        assert target["has_raw_content"] is False
        assert target["raw_content_preview"] is None

    def test_note_detail_includes_raw_content(self, client, db_path):
        """详情端点仍完整返回 raw_content，供前端展开时按需加载。"""
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "全文老师2")
        nid = Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-05-03", title="详情笔记",
            raw_content="完整原始全文内容",
        )
        conn.commit()
        conn.close()

        r = client.get(f"/api/teacher-notes/{nid}")
        assert r.status_code == 200
        assert r.json()["raw_content"] == "完整原始全文内容"

    def test_note_list_keyword_search_still_matches_raw_content(self, client, db_path):
        """剔除 raw_content 字段后，关键词搜索仍按 raw_content 命中（SQL 不变）。"""
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "搜索老师")
        nid = Q.insert_teacher_note(
            conn, teacher_id=tid, date="2026-05-04", title="标题无关键词",
            raw_content="正文里藏着 唯一关键词ZZZ 用于搜索",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/teacher-notes?keyword=唯一关键词ZZZ")
        assert r.status_code == 200
        target = next((n for n in r.json() if n["id"] == nid), None)
        assert target is not None  # 搜索仍命中
        assert target.get("raw_content") in (None, "")  # 但响应里不带全文

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

    def test_holdings_signals_api(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-04-03",
            "raw_data": json.dumps({
                "holdings_data": [
                    {
                        "code": "300750.SZ",
                        "close": 192.0,
                        "pnl_pct": 6.67,
                        "ma5": 188.0,
                        "ma10": 185.0,
                        "ma20": 180.0,
                        "volume_vs_ma5": "以上",
                        "turnover_rate": 5.8,
                        "sector_change_pct": 3.2,
                    },
                ],
                "limit_cpt_list": {"data": [{"name": "电池", "rank": 1, "up_nums": 5}]},
                "sector_moneyflow_ths": {"data": [{"industry": "电池", "net_amount": 800000000, "pct_change": 3.2, "lead_stock": "宁德时代"}]},
            }, ensure_ascii=False),
        })
        Q.upsert_main_theme(conn, {"date": "2026-04-03", "theme_name": "电池", "status": "active"})
        Q.upsert_holding(conn, stock_code="300750", stock_name="宁德时代", entry_price=180.0, sector="电池", status="active")
        conn.execute(
            """
            INSERT INTO raw_interface_payloads
            (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
             payload_json, payload_hash, row_count, status, params_json, source_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "stk_limit", "tushare:stk_limit", "post_extended", "2026-04-03", "2026-04-03", "raw_stk_limit",
                "stk_limit:2026-04-03:test",
                json.dumps({"rows": [{"ts_code": "300750.SZ", "pre_close": 190.0, "up_limit": 209.0, "down_limit": 171.0}]}, ensure_ascii=False),
                "h1", 1, "success", "{}", "{}",
            ),
        )
        # daily_basic 提供涨跌停价计算基准（前一交易日 04-02 收盘）；stk_limit 已不再被读取。
        conn.execute(
            """
            INSERT INTO raw_interface_payloads
            (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
             payload_json, payload_hash, row_count, status, params_json, source_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "daily_basic", "tushare:daily_basic", "post_core", "2026-04-02", "2026-04-02", "raw_daily_basic",
                "daily_basic:2026-04-02:test",
                json.dumps({"rows": [{"ts_code": "300750.SZ", "close": 192.0}]}, ensure_ascii=False),
                "db1", 1, "success", "{}", "{}",
            ),
        )
        conn.commit()
        conn.close()

        r = client.get("/api/holdings/signals", params={"date": "2026-04-03"})
        assert r.status_code == 200
        data = r.json()
        assert data["date"] == "2026-04-03"
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["stock_code"] == "300750"
        assert item["price_snapshot"]["current_price"] == 192.0
        # 涨跌停价按板块比例实时算（创业板 300xxx → 20%），基准来自 daily_basic 收盘 192.0：
        # down = 192 × 0.8 = 153.6；上面那份 stk_limit 快照（171.0，错按 10% 塞）已不再被读取。
        assert item["price_snapshot"]["down_limit"] == 153.6
        assert item["theme_signals"]["is_main_theme"] is True
        assert item["technical_signals"]["above_ma10"] is True
        assert item["technical_signals"]["turnover_status"] == "活跃"
        assert "info_signals" in item
        assert item["info_signals"]["investor_qa"] == []

    def test_holding_tasks_api(self, client, db_path):
        conn = get_connection(db_path)
        Q.replace_holding_tasks(
            conn,
            trade_date="2026-04-03",
            tasks=[{
                "stock_code": "300750.SZ",
                "stock_name": "宁德时代",
                "action_plan": "若冲高回落则减仓",
                "status": "open",
            }],
        )
        conn.commit()
        conn.close()

        r = client.get("/api/holdings/tasks", params={"date": "2026-04-03", "status": "open"})
        assert r.status_code == 200
        tasks = r.json()
        assert len(tasks) == 1
        assert tasks[0]["action_plan"] == "若冲高回落则减仓"

        task_id = tasks[0]["id"]
        r = client.put(f"/api/holdings/tasks/{task_id}", json={"status": "done"})
        assert r.status_code == 200

        conn = get_connection(db_path)
        row = conn.execute("SELECT status FROM holding_tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        assert row["status"] == "done"

    def test_save_review_step7_writes_holding_tasks(self, client, db_path):
        conn = get_connection(db_path)
        Q.upsert_holding(conn, stock_code="300750.SZ", stock_name="宁德时代", entry_price=180.0, sector="电池", status="active")
        conn.commit()
        conn.close()

        r = client.put("/api/review/2026-04-03", json={
            "step7_positions": {
                "positions": [
                    {"stock": "宁德时代(300750.SZ)", "action_plan": "若冲高回落则减仓"},
                    {"stock": "浦发银行(600000.SH)", "action_plan": ""},
                ],
            },
        })
        assert r.status_code == 200

        conn = get_connection(db_path)
        task = conn.execute(
            "SELECT trade_date, stock_code, stock_name, action_plan, source, status FROM holding_tasks"
        ).fetchone()
        conn.close()
        assert task["trade_date"] == "2026-04-03"
        assert task["stock_code"] == "300750.SZ"
        assert task["stock_name"] == "宁德时代"
        assert task["action_plan"] == "若冲高回落则减仓"
        assert task["source"] == "review_step7"
        assert task["status"] == "open"

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

    def test_market_ma5w_flags_fallback_from_db_history(self, client, db_path):
        conn = get_connection(db_path)
        for idx in range(24):
            day = f"2026-04-{idx + 1:02d}"
            Q.upsert_daily_market(conn, {
                "date": day,
                "sh_index_close": float(124 - idx),
                "sz_index_close": float(224 - idx),
            })
        Q.upsert_daily_market(conn, {
            "date": "2026-04-25",
            "sh_index_close": 125.0,
            "sz_index_close": 225.0,
            "sh_above_ma5w": None,
            "sz_above_ma5w": None,
        })
        conn.commit()
        conn.close()

        r = client.get("/api/market/2026-04-25")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["sh_above_ma5w"] is True
        assert data["sz_above_ma5w"] is True

    def test_market_history(self, seeded_client):
        r = seeded_client.get("/api/market/history", params={"days": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 2
        assert "raw_data" not in data[0]

    def _seed_concentration(self, db_path, days):
        """days=[(date, total, [(行业,share)], [stocks])]。"""
        from services.volume_concentration import repo
        conn = get_connection(db_path)
        for d, total, sectors, stocks in days:
            repo.save_concentration(conn, {
                "date": d, "top_n": 20, "total_amount_billion": total,
                "market_total_billion": 40000.0, "stocks": stocks,
                "sector_summary": [{"industry": i, "count": 1, "amount_billion": total * sh,
                                    "share_in_top_n": sh, "codes": []} for i, sh in sectors],
                "source": {"industry_coverage": 1.0},
            })
        conn.commit()
        conn.close()

    def test_concentration_history_series_and_snapshot(self, client, db_path):
        self._seed_concentration(db_path, [
            ("2026-05-28", 4000.0, [("半导体", 0.32), ("通信", 0.25), ("电池", 0.17), ("未分类", 0.26)],
             [{"code": "A", "name": "甲", "industry": "半导体", "change_pct": 1.0}]),
            ("2026-05-29", 3680.0, [("半导体", 0.30), ("通信", 0.25), ("电池", 0.18), ("未分类", 0.27)],
             [{"code": "B", "name": "乙", "industry": "通信", "change_pct": -3.5}]),
        ])
        r = client.get("/api/market/concentration/history", params={"days": 30})
        assert r.status_code == 200
        p = r.json()
        assert [s["date"] for s in p["series"]] == ["2026-05-28", "2026-05-29"]
        assert p["series"][1]["cr3"] == 73.0
        assert p["series"][1]["market_share_pct"] == 9.2
        assert "未分类" not in p["sector_keys"]
        assert p["snapshot"]["date"] == "2026-05-29"
        assert p["snapshot"]["rotation"]["new"] == [{"name": "乙", "industry": "通信", "change_pct": -3.5}]
        assert p["snapshot"]["rotation"]["dropped"] == [{"name": "甲"}]

    def test_concentration_history_empty(self, client):
        r = client.get("/api/market/concentration/history")
        assert r.status_code == 200
        p = r.json()
        assert p["series"] == [] and p["sector_keys"] == [] and p["snapshot"] is None

    def test_concentration_history_caps_days(self, client):
        """days 超上限 → 钳到 120(requested_days 反映生效窗口)。"""
        r = client.get("/api/market/concentration/history", params={"days": 500})
        assert r.status_code == 200
        assert r.json()["requested_days"] == 120

    def test_concentration_history_negative_days_clamped(self, client, db_path):
        """days<=0 → 钳到 1(防 SQLite LIMIT 负数返全表),不 500。"""
        self._seed_concentration(db_path, [
            ("2026-05-28", 1000.0, [("电池", 1.0)], []),
            ("2026-05-29", 1100.0, [("电池", 1.0)], []),
        ])
        r = client.get("/api/market/concentration/history", params={"days": -5})
        assert r.status_code == 200
        p = r.json()
        assert p["requested_days"] == 1
        assert len(p["series"]) == 1   # 只取最新 1 日,非全表

    def test_concentration_history_sanitizes_non_finite_change_pct(self, client, db_path):
        """脏数据 change_pct=NaN → 端点清洗为 null,不 500(与其它 market 端点一致)。"""
        self._seed_concentration(db_path, [
            ("2026-05-28", 1000.0, [("电池", 1.0)],
             [{"code": "A", "name": "甲", "industry": "电池", "change_pct": 1.0}]),
            ("2026-05-29", 1000.0, [("通信", 1.0)],
             [{"code": "B", "name": "乙", "industry": "通信", "change_pct": float("nan")}]),
        ])
        r = client.get("/api/market/concentration/history", params={"days": 30})
        assert r.status_code == 200   # 不因 NaN 序列化 500
        new = r.json()["snapshot"]["rotation"]["new"]
        assert new == [{"name": "乙", "industry": "通信", "change_pct": None}]   # NaN→null

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

    def test_post_market_envelope_scrubs_legacy_northbound(self, client, db_path):
        """北向净额下线（口径存疑）：历史信封 raw_data.northbound 不得经 /post-market 外泄。

        旧归档里可能含口径存疑的北向净额/十大活跃股；信封面板会展示并复制整包 JSON，
        故返回前剔除 northbound 块（不改库内归档，仅服务时 scrub）。
        """
        env = {
            "date": "2026-05-12",
            "generated_at": "2026-05-12T20:00:00",
            "raw_data": {
                "indices": {"shanghai": {"close": 3010.0}},
                "northbound": {"net_buy_billion": 42.93, "top_active_stocks": [{"name": "中际旭创"}]},
            },
        }
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {"date": "2026-05-12", "sh_index_close": 3010.0, "raw_data": env})
        conn.commit()
        conn.close()
        r = client.get("/api/post-market/2026-05-12")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["raw_data"]["indices"]["shanghai"]["close"] == 3010.0
        assert "northbound" not in data["raw_data"]

    def test_post_market_envelope_sanitizes_non_finite_float(self, client, db_path):
        env = {
            "date": "2026-05-11",
            "generated_at": "2026-05-11T20:00:00",
            "raw_data": {
                "etf_flow": [
                    {
                        "name": "测试ETF",
                        "shares_change_billion": float("nan"),
                        "total_shares_billion": float("inf"),
                    }
                ]
            },
        }
        conn = get_connection(db_path)
        Q.upsert_daily_market(conn, {
            "date": "2026-05-11",
            "sh_index_close": 3001.0,
            "raw_data": env,
        })
        conn.commit()
        conn.close()

        r = client.get("/api/post-market/2026-05-11")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["raw_data"]["etf_flow"][0]["shares_change_billion"] is None
        assert data["raw_data"]["etf_flow"][0]["total_shares_billion"] is None

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
        "sector_moneyflow_ths": {
            "data": [
                {"name": "油服工程", "net_amount": 188000000.0, "pct_change": 4.68, "lead_stock": "准油股份", "pct_change_stock": 9.8},
                {"name": "电力", "net_amount": 92000000.0, "pct_change": 2.11, "lead_stock": "明星电力", "pct_change_stock": 7.5},
                {"name": "电池", "net_amount": 77000000.0, "pct_change": 3.86, "lead_stock": "力佳科技", "pct_change_stock": 28.18},
            ],
        },
        "sector_moneyflow_dc": {
            "data": [
                {
                    "name": "可控核聚变",
                    "content_type": "概念",
                    "net_amount": 256000000.0,
                    "pct_change": 3.25,
                    "buy_sm_amount_stock": "合锻智能",
                },
                {
                    "name": "海工装备",
                    "content_type": "概念",
                    "net_amount": 113000000.0,
                    "pct_change": 2.06,
                    "buy_sm_amount_stock": "巨力索具",
                },
            ],
        },
        "market_moneyflow_dc": {
            "data": [
                {
                    "net_amount": 650000000.0,
                    "net_amount_rate": 1.32,
                    "buy_elg_amount": 420000000.0,
                    "buy_lg_amount": 180000000.0,
                }
            ],
        },
        "daily_info": {
            "data": [
                {"ts_code": "SH_A", "ts_name": "上海A股", "amount": 6200, "vol": 41000000, "pe": 15.2, "tr": 1.3, "com_count": 1700},
                {"ts_code": "SZ_A", "ts_name": "深圳A股", "amount": 5100, "vol": 36000000, "pe": 28.5, "tr": 2.1, "com_count": 2800},
                {"ts_code": "SZ_GEM", "ts_name": "创业板", "amount": 2200, "vol": 18000000, "pe": 35.0, "tr": 3.2, "com_count": 1300},
                {"ts_code": "SH_B", "ts_name": "上海B股", "amount": 0.5, "vol": 100},
            ],
        },
        "limit_step": {
            "data": [
                {"name": "高标A", "nums": 6},
                {"name": "中位B", "nums": 4},
            ],
        },
        "limit_cpt_list": {
            "data": [
                {"rank": 1, "name": "可控核聚变", "up_nums": 12, "cons_nums": 3, "pct_chg": 4.5, "up_stat": "3板2家"},
                {"rank": 2, "name": "海工装备", "up_nums": 8, "cons_nums": 1, "pct_chg": 2.8, "up_stat": "2板1家"},
            ],
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
        "indices": {
            "shanghai": {"close": 3200.0, "change_pct": -1.5},
            "chinext": {"close": 2333.1, "change_pct": -2.15},
            "star50": {"close": 1663.69, "change_pct": -5.0},
        },
        "moving_averages": {
            "avg_price": {"ma5w": 32.01, "above_ma5w": False},
            "shanghai": {"ma5w": 4110.91, "above_ma5w": False},
        },
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

    def test_prefill_market_contains_moving_averages(self, client, db_path):
        """/api/review/{date}/prefill 中 market 应展开 moving_averages（平均股价 5 周线）。

        平均股价当日数值未落库（采集器算完丢弃），只持久化了 ma5w；
        大盘步骤用此值 + above_ma5w 展示「平均股价 32.01 · 线下」。
        """
        self._seed(db_path)
        r = client.get("/api/review/2026-05-20/prefill")
        m = r.json()["market"]
        assert "moving_averages" in m, "prefill.market 未展开 moving_averages"
        assert m["moving_averages"]["avg_price"]["ma5w"] == 32.01
        assert m["moving_averages"]["avg_price"]["above_ma5w"] is False

    def test_prefill_contains_review_signals(self, client, db_path):
        """/api/review/{date}/prefill 应返回结构化 review_signals，供前三步只读展示使用。"""
        self._seed(db_path)
        r = client.get("/api/review/2026-05-20/prefill")
        data = r.json()
        signals = data["review_signals"]
        assert signals["market"]["moneyflow_summary"]["net_amount_yi"] == 6.5
        assert signals["market"]["market_structure_rows"][0]["name"] == "上海A股"
        assert signals["sectors"]["strongest_rows"][0]["name"] == "可控核聚变"
        assert signals["sectors"]["industry_moneyflow_rows"][0]["name"] == "油服工程"
        assert signals["sectors"]["industry_moneyflow_rows"][0]["lead_stock"] == "准油股份"
        batt = [r for r in signals["sectors"]["industry_moneyflow_rows"] if r["name"] == "电池"]
        if batt:
            assert batt[0]["lead_stock"] is None, "涨幅>20%的领涨股应被过滤"
        assert signals["market"]["market_structure_rows"][0].get("pe") is not None
        assert len(signals["market"]["market_structure_rows"]) == 3, "B股应被过滤掉"
        assert signals["emotion"]["ladder_rows"][0]["name"] == "高标A"

    def test_prefill_contains_projection_candidates(self, client, db_path):
        self._seed(db_path)
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn,
            teacher_id=tid,
            date="2026-05-20",
            title="继续看 AI 算力",
            sectors="AI算力",
            key_points="主线没有切换，重点看回流。",
            raw_content="AI算力 分歧后仍有回流预期。",
        )
        Q.insert_industry_info(
            conn,
            date="2026-05-20",
            sector_name="AI算力",
            content="服务器链订单继续强化",
            info_type="analysis",
        )
        Q.upsert_main_theme(
            conn,
            {
                "date": "2026-05-20",
                "theme_name": "AI算力",
                "status": "active",
                "phase": "主升",
                "duration_days": 4,
                "key_stocks": ["高标A", "中军B"],
            },
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        candidates = data["review_signals"]["sectors"]["projection_candidates"]
        assert any(item["sector_name"] == "AI算力" for item in candidates)
        ai = next(item for item in candidates if item["sector_name"] == "AI算力")
        assert "main_theme" in ai["source_tags"]
        assert "teacher_note" in ai["source_tags"]
        assert ai["facts"]["phase_hint"] == "主升"
        assert ai["facts"]["emotion_leader"] == "高标A"
        assert ai["facts"]["capacity_leader"] == "高标A"
        assert ai["facts"]["lead_stock"] == "高标A"
        assert "服务器链订单继续强化" in ai["evidence_text"]

    def test_projection_candidates_split_emotion_and_capacity_leaders(self, client, db_path):
        self._seed(
            db_path,
            raw_data={
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "AI算力", "net_amount": 15.0, "pct_change": 4.8, "lead_stock": "弱源股"},
                    ],
                },
                "sector_rhythm_industry": [
                    {"name": "AI算力", "phase": "主升", "change_today": 4.8, "top_stock_today": "高标A"},
                ],
                "sector_industry": {
                    "data": [
                        {"name": "AI算力", "change_pct": 4.8, "top_stock": "高标A"},
                    ],
                },
                "top_volume_stocks": [
                    {"rank": 3, "name": "中军B", "code": "300002.SZ", "amount_billion": 188.0},
                ],
                "limit_up": {
                    "stocks": [
                        {"name": "高标A", "code": "300001.SZ", "limit_times": 3, "amount_billion": 62.0},
                        {"name": "弱源股", "code": "300003.SZ", "limit_times": 1, "amount_billion": 21.0},
                    ],
                },
            },
        )
        conn = get_connection(db_path)
        Q.upsert_main_theme(
            conn,
            {
                "date": "2026-05-20",
                "theme_name": "AI算力",
                "status": "active",
                "phase": "主升",
                "duration_days": 4,
                "key_stocks": ["高标A", "中军B"],
            },
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        ai = next(
            item
            for item in data["review_signals"]["sectors"]["projection_candidates"]
            if item["sector_name"] == "AI算力"
        )
        assert ai["facts"]["emotion_leader"] == "高标A"
        assert ai["facts"]["capacity_leader"] == "中军B"
        assert ai["facts"]["lead_stock"] == "中军B"
        assert "弱源股" not in f"{ai['facts']['emotion_leader']}{ai['facts']['capacity_leader']}"

    def test_projection_candidates_keep_moneyflow_source_stock_out_of_lead_stock(self, client, db_path):
        self._seed(
            db_path,
            raw_data={
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "电池", "net_amount": 77.0, "pct_change": 5.55, "lead_stock": "力佳科技"},
                    ],
                },
                "sector_rhythm_industry": [
                    {"name": "电池", "phase": "发酵", "change_today": 5.55, "top_stock_today": ""},
                ],
                "sector_industry": {
                    "data": [
                        {"name": "电池", "change_pct": 5.55, "top_stock": ""},
                    ],
                },
            },
        )

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        ind_row = data["review_signals"]["sectors"]["industry_moneyflow_rows"][0]
        assert ind_row["lead_stock"] == "力佳科技"
        assert ind_row["net_amount_yi"] == 77.0

        battery = next(
            item
            for item in data["review_signals"]["sectors"]["projection_candidates"]
            if item["sector_name"] == "电池"
        )
        assert battery["facts"]["emotion_leader"] is None
        assert battery["facts"]["capacity_leader"] is None
        assert battery["facts"]["lead_stock"] is None
        assert battery["facts"]["net_amount_yi"] == 77.0
        assert "力佳科技" not in battery["evidence_text"]

    def test_moneyflow_limit_up_leader_is_not_capacity_without_top_volume(self, client, db_path):
        self._seed(
            db_path,
            raw_data={
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "元件", "net_amount": 55.0, "pct_change": 0.2, "lead_stock": "贤丰控股"},
                    ],
                },
                "limit_up": {
                    "stocks": [
                        {"name": "贤丰控股", "code": "002141.SZ", "limit_times": 1, "amount_billion": 9.05},
                    ],
                },
            },
        )

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        yuanjian = next(
            item
            for item in r.json()["review_signals"]["sectors"]["projection_candidates"]
            if item["sector_name"] == "元件"
        )

        assert yuanjian["facts"]["emotion_leader"] == "贤丰控股"
        assert yuanjian["facts"]["capacity_leader"] is None
        assert yuanjian["facts"]["lead_stock"] == "贤丰控股"

    def test_projection_candidates_filter_out_st_and_bj_candidates(self, client, db_path):
        self._seed(
            db_path,
            raw_data={
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "机器人", "net_amount": 12.0, "pct_change": 3.1, "lead_stock": "*ST机器"},
                    ],
                },
                "concept_moneyflow_dc": {
                    "data": [
                        {"name": "机器人", "content_type": "概念", "net_amount": 6.0e8, "pct_change": 3.0},
                    ],
                },
                "limit_up": {
                    "stocks": [
                        {"name": "*ST机器", "code": "000004.SZ", "limit_times": 1, "amount_billion": 8.0},
                        {"name": "北交龙头", "code": "920001.BJ", "limit_times": 2, "amount_billion": 18.0},
                    ],
                },
            },
        )

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        robot = next(
            item
            for item in data["review_signals"]["sectors"]["projection_candidates"]
            if item["sector_name"] == "机器人"
        )
        assert robot["facts"]["emotion_leader"] is None
        assert robot["facts"]["capacity_leader"] is None
        assert robot["facts"]["lead_stock"] is None

    def test_prefill_uses_main_themes_as_of_target_date(self, client, db_path):
        self._seed(
            db_path,
            raw_data={
                "sector_rhythm_industry": [
                    {"name": "电池", "phase": "发酵", "confidence": "中", "change_today": 2.0},
                ],
            },
        )
        conn = get_connection(db_path)
        Q.upsert_main_theme(
            conn,
            {"date": "2026-05-18", "theme_name": "电池", "status": "active"},
        )
        Q.upsert_main_theme(
            conn,
            {"date": "2026-05-19", "theme_name": "AI", "status": "active"},
        )
        Q.upsert_main_theme(
            conn,
            {"date": "2026-05-20", "theme_name": "AI", "status": "fading"},
        )
        Q.upsert_main_theme(
            conn,
            {"date": "2026-05-21", "theme_name": "未来主线", "status": "active"},
        )
        conn.commit()
        conn.close()

        data = client.get("/api/review/2026-05-20/prefill").json()

        assert [(item["theme_name"], item["date"]) for item in data["main_themes"]] == [
            ("电池", "2026-05-18"),
        ]
        candidate_names = {
            item["sector_name"]
            for item in data["review_signals"]["sectors"]["projection_candidates"]
        }
        assert "AI" not in candidate_names
        assert "未来主线" not in candidate_names

    def test_projection_candidates_keep_sector_types_and_leaders_separate(self):
        from api.routes.review import _build_review_signals, _build_sector_projection_candidates

        market = {
            "sector_industry": {
                "data": [{"name": "同名板块", "change_pct": 2.1, "top_stock": "行业龙"}],
            },
            "sector_concept": {
                "data": [{"name": "同名板块", "change_pct": 3.2, "top_stock": "概念龙"}],
            },
            "sector_rhythm_industry": [
                {
                    "name": "同名板块",
                    "phase": "启动",
                    "confidence": "高",
                    "change_today": 2.1,
                    "top_stock_today": "行业龙",
                },
            ],
            "sector_rhythm_concept": [
                {
                    "name": "同名板块",
                    "phase": "发酵",
                    "confidence": "中",
                    "change_today": 3.2,
                    "top_stock_today": "概念龙",
                },
            ],
            "top_volume_stocks": [
                {"rank": 1, "name": "共享叙事龙", "code": "000003.SZ", "amount_billion": 200.0},
                {"rank": 4, "name": "行业龙", "code": "000001.SZ", "amount_billion": 80.0},
                {"rank": 5, "name": "概念龙", "code": "000002.SZ", "amount_billion": 70.0},
            ],
            "limit_up": {
                "stocks": [
                    {
                        "name": "共享叙事龙",
                        "code": "000003.SZ",
                        "limit_times": 3,
                        "amount_billion": 80.0,
                    },
                ],
            },
        }
        signals = _build_review_signals(market)

        rows = _build_sector_projection_candidates(
            trade_date="2026-05-20",
            market=market,
            post_market_env={"raw_data": {"limit_up": market["limit_up"]}},
            main_themes=[
                {
                    "date": "2026-05-20",
                    "theme_name": "同名板块",
                    "status": "active",
                    "key_stocks": ["共享叙事龙"],
                },
            ],
            teacher_notes=[],
            industry_info=[],
            sector_signals=signals.get("sectors"),
        )

        by_key = {item["sector_key"]: item for item in rows}
        assert set(by_key) == {"industry:同名板块", "concept:同名板块"}
        assert by_key["industry:同名板块"]["sector_type"] == "industry"
        assert by_key["concept:同名板块"]["sector_type"] == "concept"
        assert by_key["industry:同名板块"]["facts"]["lead_stock"] == "行业龙"
        assert by_key["concept:同名板块"]["facts"]["lead_stock"] == "概念龙"
        assert all(item["candidate_tier"] == "core" for item in by_key.values())
        assert all(item["data_status"] == "ok" for item in by_key.values())
        for item in by_key.values():
            assert {"source_tags", "facts", "key_stocks", "evidence_text"} <= item.keys()
            assert item["rank_reason"]
            assert item["evidence_items"]
            assert all(evidence["trade_date"] == "2026-05-20" for evidence in item["evidence_items"])
            assert all(
                evidence["evidence_id"].startswith("2026-05-20:")
                for evidence in item["evidence_items"]
            )

    def test_projection_candidate_hard_gates_and_negative_flow_counter_evidence(self):
        from api.routes.review import _build_review_signals, _build_sector_projection_candidates

        market = {
            "sector_industry": {
                "data": [
                    {"name": "主线确认", "change_pct": 1.2},
                    {"name": "双证据", "change_pct": 1.5},
                    {"name": "模糊主线", "change_pct": 1.1},
                ],
            },
            "sector_concept": {
                "data": [{"name": "模糊主线", "change_pct": 1.3}],
            },
            "sector_rhythm_industry": [
                {"name": "高节奏", "phase": "启动", "confidence": "高", "change_today": 1.0},
                {"name": "单证据", "phase": "观察", "confidence": "低", "change_today": 0.5},
            ],
            "sector_moneyflow_ths": {
                "data": [
                    {"name": "双证据", "net_amount": 2.0, "pct_change": 1.5},
                    {
                        "name": "负资金流",
                        "net_amount": -8.0,
                        "pct_change": -1.0,
                        "lead_stock": "负流股",
                    },
                ],
            },
            "limit_cpt_list": {
                "data": [
                    {"rank": 2, "name": "最强概念", "up_nums": 5, "pct_chg": 3.0},
                    {"rank": 0, "name": "零名次", "up_nums": 5, "pct_chg": 3.0},
                    {"rank": 2.5, "name": "小数名次", "up_nums": 5, "pct_chg": 3.0},
                ],
            },
            "top_volume_stocks": [
                {"rank": 1, "name": "负流股", "code": "000009.SZ", "amount_billion": 100.0},
            ],
        }
        signals = _build_review_signals(market)

        rows = _build_sector_projection_candidates(
            trade_date="2026-05-20",
            market=market,
            post_market_env={
                "raw_data": {
                    "limit_up": {
                        "stocks": [
                            {
                                "name": "负流股",
                                "code": "000009.SZ",
                                "limit_times": 2,
                                "amount_billion": 30.0,
                            },
                        ],
                    },
                },
            },
            main_themes=[
                {"date": "2026-05-20", "theme_name": "主线确认", "status": "active"},
                {"date": "2026-05-20", "theme_name": "模糊主线", "status": "active"},
            ],
            teacher_notes=[],
            industry_info=[
                {"date": "2026-05-20", "sector_name": "只有资料", "content": "只有逻辑叙事"},
            ],
            sector_signals=signals.get("sectors"),
        )

        by_key = {item["sector_key"]: item for item in rows}
        assert by_key["industry:高节奏"]["candidate_tier"] == "core"
        assert by_key["concept:最强概念"]["candidate_tier"] == "core"
        assert by_key["industry:主线确认"]["candidate_tier"] == "core"
        assert by_key["industry:双证据"]["candidate_tier"] == "core"
        assert by_key["industry:单证据"]["candidate_tier"] == "watch"
        assert by_key["industry:只有资料"]["candidate_tier"] == "context"
        assert by_key["industry:模糊主线"]["candidate_tier"] == "watch"
        assert by_key["concept:模糊主线"]["candidate_tier"] == "watch"
        assert by_key["concept:零名次"]["candidate_tier"] != "core"
        assert by_key["concept:小数名次"]["candidate_tier"] != "core"

        negative = by_key["industry:负资金流"]
        assert negative["candidate_tier"] != "core"
        assert not any(item["polarity"] == "support" for item in negative["evidence_items"])
        assert any(item["polarity"] == "counter" for item in negative["evidence_items"])
        assert negative["facts"]["emotion_leader"] is None
        assert negative["facts"]["capacity_leader"] is None
        assert negative["facts"]["lead_stock"] is None

    def test_projection_candidate_backfills_hidden_negative_industry_moneyflow(self):
        from api.routes.review import _build_review_signals, _build_sector_projection_candidates

        market = {
            "sector_industry": {
                "data": [{"name": "负流候选", "change_pct": 1.2}],
            },
            "sector_moneyflow_ths": {
                "data": [
                    {"name": f"正流{i}", "net_amount": 13.0 - i, "pct_change": 1.0}
                    for i in range(1, 7)
                ] + [
                    {"name": "负流候选", "net_amount": -8.0, "pct_change": -1.0},
                ],
            },
        }
        signals = _build_review_signals(market)

        displayed = signals["sectors"]["industry_moneyflow_rows"]
        assert len(displayed) == 5
        assert [row["name"] for row in displayed] == [f"正流{i}" for i in range(1, 6)]

        rows = _build_sector_projection_candidates(
            trade_date="2026-05-20",
            market=market,
            post_market_env=None,
            main_themes=[],
            teacher_notes=[],
            industry_info=[],
            sector_signals=signals["sectors"],
        )

        by_key = {item["sector_key"]: item for item in rows}
        negative = by_key["industry:负流候选"]
        assert negative["facts"]["net_amount_yi"] == -8.0
        assert any(
            item["source"] == "industry_moneyflow"
            and item["polarity"] == "counter"
            for item in negative["evidence_items"]
        )
        assert "industry:正流6" not in by_key

    def test_projection_candidates_split_legacy_dc_concepts_from_industries(self):
        from api.routes.review import _build_review_signals, _build_sector_projection_candidates

        market = {
            "sector_moneyflow_dc": {
                "data": [
                    {
                        "name": "可控核聚变",
                        "content_type": "概念",
                        "net_amount": 2.0e8,
                        "pct_change": 3.0,
                    },
                    {
                        "name": "电力",
                        "content_type": "行业",
                        "net_amount": 1.0e8,
                        "pct_change": 1.5,
                    },
                ],
            },
        }
        signals = _build_review_signals(market)

        rows = _build_sector_projection_candidates(
            trade_date="2026-05-20",
            market=market,
            post_market_env=None,
            main_themes=[],
            teacher_notes=[],
            industry_info=[],
            sector_signals=signals["sectors"],
        )

        assert {item["sector_key"] for item in rows} == {
            "concept:可控核聚变",
            "industry:电力",
        }

    @pytest.mark.parametrize(
        ("market", "expected_status"),
        [
            (None, "missing"),
            (
                {
                    "sector_industry": {"data": []},
                    "sector_concept": {"data": []},
                    "sector_rhythm_industry": [],
                    "sector_rhythm_concept": [],
                    "sector_moneyflow_ths": {"data": []},
                    "concept_moneyflow_ths": {"data": []},
                    "limit_cpt_list": {"data": []},
                },
                "source_ok_empty",
            ),
            (
                {
                    "sector_rhythm_concept": {
                        "status": "source_failed",
                        "error": "provider unavailable",
                    },
                },
                "source_failed",
            ),
            (
                {"sector_rhythm_concept": {"status": "missing"}},
                "missing",
            ),
            (
                {"sector_rhythm_concept": {"status": "rule_filtered_empty"}},
                "rule_filtered_empty",
            ),
            (
                {
                    "limit_cpt_list": {
                        "data": [{"rank": 4, "name": "叙事板块", "up_nums": 0, "pct_chg": 0}],
                    },
                },
                "rule_filtered_empty",
            ),
        ],
    )
    def test_projection_candidate_data_status_is_explicit(self, market, expected_status):
        from api.routes.review import _build_review_signals, _build_sector_projection_candidates

        rows = _build_sector_projection_candidates(
            trade_date="2026-05-20",
            market=market,
            post_market_env=None,
            main_themes=[
                {"date": "2026-05-20", "theme_name": "叙事板块", "status": "active"},
            ],
            teacher_notes=[],
            industry_info=[],
            sector_signals=_build_review_signals(market).get("sectors"),
        )

        target = next(item for item in rows if item["sector_name"] == "叙事板块")
        assert target["data_status"] == expected_status

    def test_projection_core_order_is_deterministic_and_directly_sliceable_to_six(self):
        from api.routes.review import (
            _build_review_signals,
            _build_sector_projection_candidates,
            _take_core_projection_candidates,
        )

        rhythm_rows = [
            {"name": f"板块{index}", "phase": "启动", "confidence": "高", "change_today": 1.0}
            for index in range(7)
        ]

        def build(rows):
            market = {"sector_rhythm_industry": rows}
            return _build_sector_projection_candidates(
                trade_date="2026-05-20",
                market=market,
                post_market_env=None,
                main_themes=[],
                teacher_notes=[],
                industry_info=[],
                sector_signals=_build_review_signals(market).get("sectors"),
            )

        forward = build(rhythm_rows)
        reverse = build(list(reversed(rhythm_rows)))
        expected = [f"industry:板块{index}" for index in range(6)]

        assert [item["sector_key"] for item in _take_core_projection_candidates(forward)] == expected
        assert [item["sector_key"] for item in _take_core_projection_candidates(reverse)] == expected

        mixed = build([
            {"name": "唯一核心", "phase": "启动", "confidence": "高", "change_today": 1.0},
            {"name": "观察项", "phase": "观察", "confidence": "低", "change_today": 0.5},
        ])
        assert [item["sector_key"] for item in _take_core_projection_candidates(mixed)] == [
            "industry:唯一核心",
        ]

    def test_teacher_note_freeform_sector_text_does_not_create_fake_candidate(self, client, db_path):
        self._seed(db_path)
        conn = get_connection(db_path)
        tid = Q.get_or_create_teacher(conn, "小鲍")
        Q.insert_teacher_note(
            conn,
            teacher_id=tid,
            date="2026-05-20",
            title="继续看 AI",
            sectors="继续看AI算力",
            key_points="重点观察机器人回流",
            raw_content="继续看AI算力，不追高。",
        )
        conn.commit()
        conn.close()

        r = client.get("/api/review/2026-05-20/prefill")
        assert r.status_code == 200
        data = r.json()
        candidates = data["review_signals"]["sectors"]["projection_candidates"]
        sector_names = {item["sector_name"] for item in candidates}
        assert "继续看AI算力" not in sector_names
        assert "重点观察机器人回流" not in sector_names

    def test_prefill_review_signals_degrade_gracefully_when_sections_missing(self, client, db_path):
        """/api/review/{date}/prefill 缺失新增接口时，review_signals 应返回空结构而不是报错。"""
        self._seed(db_path, raw_data={"date": "2026-05-20", "raw_data": {}})
        r = client.get("/api/review/2026-05-20/prefill")
        data = r.json()
        signals = data["review_signals"]
        assert signals["market"]["moneyflow_summary"] is None
        assert signals["market"]["market_structure_rows"] == []
        assert signals["sectors"]["strongest_rows"] == []
        assert signals["sectors"]["industry_moneyflow_rows"] == []
        assert signals["sectors"]["concept_moneyflow_rows"] == []
        assert signals["sectors"]["projection_candidates"] == []
        assert signals["emotion"]["ladder_rows"] == []

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


# ──────────────────────────────────────────────────────────────
# market-timing（大盘择时观察：盘面概览面板数据）
# ──────────────────────────────────────────────────────────────

def _seed_timing(conn, date, *, indices, resonance, amount_yi, pctile, advance=None,
                 decline=None, limit_down=None):
    """向 market_timing_signal 灌一日数据（市场级列冗余写各行）。"""
    from services.market_timing import repo as mt_repo
    for code, name, extra in indices:
        row = {
            "trade_date": date, "index_code": code, "index_name": name,
            "fractal_status": "none",  # NOT NULL（scanner 必填，测试默认补齐，extra 可覆盖）
            "resonance_count": resonance, "market_amount_yi": amount_yi,
            "amount_pctile_20d": pctile, "advance": advance, "decline": decline,
            "limit_down_count": limit_down, "data_source": "test",
        }
        row.update(extra)
        mt_repo.upsert_signal(conn, row)
    conn.commit()


def test_market_timing_by_date_returns_signals_and_context(client, db_path):
    conn = get_connection(db_path)
    _seed_timing(conn, "2026-06-12", indices=[
        ("000001.SH", "上证综指", {"swing_pivot_date": "2026-05-14", "swing_pivot_type": "high",
                                "swing_pivot_price": 4258.86, "fib_day_count": 21, "fib_hit": 21,
                                "fractal_status": "forming", "fractal_low_date": "2026-06-11",
                                "fractal_low_price": 3958.44}),
        ("932000.CSI", "中证2000", {"swing_pivot_date": "2026-05-14", "swing_pivot_type": "high",
                                  "fib_day_count": 21, "fib_hit": 21, "fractal_status": "forming"}),
    ], resonance=3, amount_yi=32150.0, pctile=0.8, advance=4500, decline=900, limit_down=12)
    conn.close()

    data = client.get("/api/market/timing/2026-06-12").json()
    assert data["available"] is True
    assert data["resonance_count"] == 3
    assert data["context"]["market_amount_yi"] == 32150.0
    assert data["context"]["amount_pctile_20d"] == 0.8
    assert data["context"]["limit_down_count"] == 12
    assert len(data["signals"]) == 2
    sh = next(s for s in data["signals"] if s["index_code"] == "000001.SH")
    assert sh["fib_hit"] == 21 and sh["swing_pivot_price"] == 4258.86
    assert sh["fractal_status"] == "forming"
    # 市场级列不混进逐指数行
    assert "resonance_count" not in sh


def test_market_timing_empty_returns_available_false(client):
    data = client.get("/api/market/timing/2026-06-12").json()
    assert data["available"] is False
    assert data["signals"] == []


def test_market_timing_history_series_ascending_and_deduped(client, db_path):
    conn = get_connection(db_path)
    for d, res, pct in [("2026-06-10", 1, 0.3), ("2026-06-11", 0, 0.1), ("2026-06-12", 3, 0.8)]:
        _seed_timing(conn, d, indices=[
            ("000001.SH", "上证综指", {}), ("932000.CSI", "中证2000", {}),
        ], resonance=res, amount_yi=30000.0, pctile=pct)
    conn.close()

    data = client.get("/api/market/timing/history?days=30").json()
    series = data["series"]
    assert len(series) == 3  # 6 行/3日 去重为 3 个日期点
    assert [p["date"] for p in series] == ["2026-06-10", "2026-06-11", "2026-06-12"]  # 升序
    assert series[-1]["resonance_count"] == 3 and series[-1]["amount_pctile_20d"] == 0.8
    assert series[0]["date_short"] == "06-10"


def test_market_timing_history_empty(client):
    data = client.get("/api/market/timing/history?days=30").json()
    assert data["series"] == []


def test_market_timing_history_to_date_excludes_future(client, db_path):
    """复盘历史日期：to_date 给定时不带出该日之后的未来数据（前瞻偏差）。"""
    conn = get_connection(db_path)
    for d, res in [("2026-06-10", 1), ("2026-06-11", 0), ("2026-06-12", 3)]:
        _seed_timing(conn, d, indices=[("000001.SH", "上证综指", {})],
                     resonance=res, amount_yi=30000.0, pctile=0.3)
    conn.close()
    data = client.get("/api/market/timing/history?days=30&to_date=2026-06-11").json()
    assert [p["date"] for p in data["series"]] == ["2026-06-10", "2026-06-11"]  # 不含 06-12


# ──────────────────────────────────────────────────────────────
# 三位一体因子评分 / 人工确认 / 严格 T+1
# ──────────────────────────────────────────────────────────────

def _seed_factor_trade_date(db_path, trade_date="2026-07-10"):
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": trade_date, "is_open": 1}])
    conn.commit()
    conn.close()


def _seed_factor_score_run(
    db_path,
    *,
    run_id="api-run-1",
    trade_date="2026-07-10",
    status="rule_only",
    retry_of_run_id=None,
):
    from api.routes.review import build_review_prefill
    from services.trinity_factor.review_input import normalize_review_steps
    from services.trinity_factor.repository import insert_score_run
    from services.trinity_factor.service import build_score_input_digest

    _seed_factor_trade_date(db_path, trade_date)
    conn = get_connection(db_path)
    current_review = Q.get_daily_review(conn, trade_date) or {}
    input_digest = build_score_input_digest(
        trade_date=trade_date,
        prefill=build_review_prefill(conn, trade_date),
        review_steps=normalize_review_steps(current_review),
    )
    insert_score_run(conn, {
        "score_run_id": run_id,
        "trade_date": trade_date,
        "retry_of_run_id": retry_of_run_id,
        "cache_key": f"cache-{run_id}",
        "input_digest": input_digest,
        "is_cacheable": status in {"success", "sector_failed", "rule_only"},
        "provider": "rules",
        "requested_model": "disabled",
        "actual_model": None,
        "cli_version": None,
        "runtime_version": "python-test",
        "prompt_versions_json": {"factor": "factor-v1", "sector": "sector-v1"},
        "prompt_sha256_json": {"factor": "p1", "sector": "p2"},
        "schema_version": "score-v1",
        "ruleset_version": "rules-v1",
        "evidence_snapshot_json": {},
        "rule_gate_json": {"rule_fallback_code": "market_node"},
        "factor_scores_json": None,
        "sector_scores_json": None,
        "system_recommendation_json": {
            "primary": {"factor_code": "market_node"},
            "supporting": [{"factor_code": "sector_rhythm"}],
            "recommendation_source": "rule_fallback",
        },
        "valid_raw_json": (
            {"factor": {"schema_version": "factor-v1"}}
            if status in {"success", "sector_failed"}
            else None
        ),
        "raw_output_sha256_json": None,
        "diagnostics_json": {},
        "status": status,
        "attempt_count": 0,
        "duration_ms": 0,
    })
    conn.commit()
    conn.close()


def _factor_persistence_counts(db_path):
    conn = get_connection(db_path)
    counts = tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "daily_review_factor_evaluations",
            "daily_review_factor_score_runs",
            "daily_review_factor_score_requests",
        )
    )
    conn.close()
    return counts


def test_review_factor_score_no_llm_and_cache(client, db_path):
    _seed_factor_trade_date(db_path)
    body = {
        "no_llm": True,
        "input_by": "web",
        "steps": {
            "step1_market": {"notes": "大盘结构待确认"},
            "step6_nodes": {"systemic_risk": True},
        },
    }

    first = client.post("/api/review-factors/2026-07-10/score", json=body)
    second = client.post("/api/review-factors/2026-07-10/score", json=body)

    assert first.status_code == 200
    assert first.json()["status"] == "rule_only"
    assert first.json()["factor_scores"] is None
    assert second.status_code == 200
    assert second.json()["score_run_id"] == first.json()["score_run_id"]
    assert second.json()["cache_hit"] is True
    assert client.post(
        "/api/review-factors/2026-07-10/score",
        json={"no_llm": "yes", "input_by": "web"},
    ).status_code == 422


def test_review_factor_score_persists_production_market_fields_from_db(
    client, db_path
):
    from services.trinity_factor.repository import list_score_runs

    _seed_factor_trade_date(db_path, trade_date="2026-07-02")
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-01", "is_open": 1}])
    Q.upsert_daily_market(conn, {
        "date": "2026-07-01",
        "highest_board": 4,
        "continuous_board_counts": {
            "4": ["四板甲"],
            "3": ["三板甲"],
            "2": ["二板甲"],
        },
    })
    Q.upsert_daily_market(conn, {
        "date": "2026-07-02",
        "total_amount": 13542.7,
        "sh_index_change_pct": -2.0314,
        "sz_index_change_pct": -3.8486,
        "advance_count": 2219,
        "decline_count": 3162,
        "limit_up_count": 42,
        "limit_down_count": 11,
        "highest_board": 4,
        "continuous_board_counts": {
            "4": ["四板甲"],
            "3": ["三板甲"],
            "2": ["二板乙", "二板甲"],
        },
        "raw_data": {
            "raw_data": {
                "style_factors": {
                    "cap_preference": {
                        "csi300_chg": -0.4,
                        "csi1000_chg": 1.1,
                        "spread": 1.5,
                        "relative": "偏小盘",
                    },
                    "board_preference": {
                        "dominant_type": "20cm",
                        "pct_10cm": 20.0,
                        "pct_20cm": 75.0,
                        "pct_30cm": 5.0,
                    },
                    "premium_snapshot": {
                        "first_board": {
                            "count": 40,
                            "premium_median": 1.2,
                            "open_up_rate": 0.6,
                        },
                    },
                    "premium_trend": {"direction": "走强"},
                    "popularity": [{
                        "code": "000001.SZ",
                        "name": "四板甲",
                        "source": ["consecutive"],
                        "prev_close": 10.0,
                        "t_open_premium_pct": 2.0,
                        "t_close_change_pct": 5.0,
                        "t_is_limit_up": True,
                        "t_is_limit_down": False,
                    }],
                    "popularity_provenance": {
                        "source_trade_date": "2026-07-01",
                        "outcome_trade_date": "2026-07-02",
                    },
                    "promotion": {
                        "trade_date": "2026-07-02",
                        "prev_date": "2026-07-01",
                        "first_to_second": {
                            "base": 12,
                            "promoted": 4,
                            "rate": 0.333,
                            "promoted_names": ["乙", "甲"],
                        },
                    },
                },
            },
        },
    })
    conn.commit()
    conn.close()

    response = client.post(
        "/api/review-factors/2026-07-02/score",
        json={"no_llm": True, "input_by": "web"},
    )

    assert response.status_code == 200
    conn = get_connection(db_path)
    stored_run = list_score_runs(conn, trade_date="2026-07-02")[0]
    conn.close()
    factors = {
        row["factor_code"]: row
        for row in stored_run["evidence_snapshot_json"]["factor_candidates"]
    }
    market_factor = factors["market_node"]
    daily_market = next(
        item
        for item in market_factor["evidence_items"]
        if item["source"] == "daily_market"
    )
    assert daily_market["content"] == {
        "date": "2026-07-02",
        "total_amount": 13542.7,
        "sh_index_change_pct": -2.0314,
        "sz_index_change_pct": -3.8486,
        "advance_count": 2219,
        "decline_count": 3162,
        "limit_up_count": 42,
        "limit_down_count": 11,
    }
    assert factors["style_regime"]["evidence_quality"] == 4
    assert factors["leader_signal"]["evidence_quality"] == 3
    assert {
        item["source"]
        for item in factors["style_regime"]["evidence_items"]
        if item.get("kind") == "fact" and item.get("source_status") == "ok"
    } == {"cap_relative_strength", "board_preference", "premium_regime"}
    leader_facts = {
        item["source"]
        for item in factors["leader_signal"]["evidence_items"]
        if item.get("kind") == "fact" and item.get("source_status") == "ok"
    }
    assert leader_facts == {
        "ladder_structure", "promotion_realization", "prior_core_feedback",
    }
    prior_core_feedback = next(
        item
        for item in factors["leader_signal"]["evidence_items"]
        if item.get("source") == "prior_core_feedback"
    )
    assert prior_core_feedback["content"] == {
        "source_trade_date": "2026-07-01",
        "cohort_basis": "previous_highest_tier",
        "cohort_count": 1,
        "names": ["四板甲"],
        "codes": ["000001.SZ"],
        "median_open_premium_pct": 2.0,
        "median_close_change_pct": 5.0,
        "positive_close_count": 1,
        "limit_up_count": 1,
        "limit_down_count": 0,
    }
    assert stored_run["ruleset_version"] == "trinity_ruleset_v2"


def test_review_factor_score_falls_back_to_promotion_for_legacy_feedback_provenance(
    client, db_path
):
    from services.trinity_factor.repository import list_score_runs

    _seed_factor_trade_date(db_path, trade_date="2026-07-02")
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-01", "is_open": 1}])
    Q.upsert_daily_market(conn, {
        "date": "2026-07-01",
        "highest_board": 3,
        "continuous_board_counts": {"3": ["三板甲"], "2": ["二板甲"]},
    })
    Q.upsert_daily_market(conn, {
        "date": "2026-07-02",
        "highest_board": 3,
        "continuous_board_counts": {"3": ["三板乙"], "2": ["二板乙"]},
        "raw_data": {"raw_data": {"style_factors": {
            "popularity": [{
                "code": "000002.SZ",
                "name": "三板甲",
                "source": ["consecutive"],
                "t_open_premium_pct": 1.0,
                "t_close_change_pct": 3.0,
                "t_is_limit_up": False,
                "t_is_limit_down": False,
            }],
            "promotion": {
                "trade_date": "2026-07-02",
                "prev_date": "2026-07-01",
            },
        }}},
    })
    conn.commit()
    conn.close()

    response = client.post(
        "/api/review-factors/2026-07-02/score",
        json={"no_llm": True, "input_by": "web"},
    )

    assert response.status_code == 200
    conn = get_connection(db_path)
    stored_run = list_score_runs(conn, trade_date="2026-07-02")[0]
    conn.close()
    leader = next(
        row
        for row in stored_run["evidence_snapshot_json"]["factor_candidates"]
        if row["factor_code"] == "leader_signal"
    )
    feedback = next(
        item
        for item in leader["evidence_items"]
        if item.get("source") == "prior_core_feedback"
    )
    assert leader["evidence_quality"] == 3
    assert feedback["content"]["source_trade_date"] == "2026-07-01"
    assert feedback["content"]["cohort_basis"] == "previous_highest_tier"


@pytest.mark.parametrize(
    "provenance_mode",
    ["explicit_misaligned", "legacy_misaligned"],
)
def test_review_factor_score_rejects_feedback_not_from_strict_previous_open_day(
    client, db_path, provenance_mode
):
    from services.trinity_factor.repository import list_score_runs

    _seed_factor_trade_date(db_path, trade_date="2026-07-13")
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-13", "is_open": 1},
    ])
    Q.upsert_daily_market(conn, {
        "date": "2026-07-09",
        "highest_board": 5,
        "continuous_board_counts": {"5": ["五板旧标"]},
    })
    Q.upsert_daily_market(conn, {
        "date": "2026-07-10",
        "highest_board": 4,
        "continuous_board_counts": {"4": ["四板甲"]},
    })
    style_factors = {
        "popularity": [{
            "code": "000001.SZ",
            "name": "四板甲",
            "source": ["consecutive"],
            "t_open_premium_pct": 1.0,
            "t_close_change_pct": 3.0,
            "t_is_limit_up": False,
            "t_is_limit_down": False,
        }],
        # No valid promotion group: it must not independently add leader_outcome.
        "promotion": {
            "trade_date": "2026-07-13",
            "prev_date": (
                "2026-07-09"
                if provenance_mode == "legacy_misaligned"
                else "2026-07-10"
            ),
        },
    }
    if provenance_mode == "explicit_misaligned":
        style_factors["popularity_provenance"] = {
            "source_trade_date": "2026-07-09",
            "outcome_trade_date": "2026-07-13",
        }
    Q.upsert_daily_market(conn, {
        "date": "2026-07-13",
        "highest_board": 4,
        "continuous_board_counts": {"4": ["四板乙"]},
        "raw_data": {"raw_data": {"style_factors": style_factors}},
    })
    conn.commit()
    conn.close()

    response = client.post(
        "/api/review-factors/2026-07-13/score",
        json={"no_llm": True, "input_by": "web"},
    )

    assert response.status_code == 200
    conn = get_connection(db_path)
    stored_run = list_score_runs(conn, trade_date="2026-07-13")[0]
    conn.close()
    assert stored_run["evidence_snapshot_json"]["strict_prev_trade_date"] == (
        "2026-07-10"
    )
    leader = next(
        row
        for row in stored_run["evidence_snapshot_json"]["factor_candidates"]
        if row["factor_code"] == "leader_signal"
    )
    facts = [
        item
        for item in leader["evidence_items"]
        if item.get("kind") == "fact" and item.get("source_status") == "ok"
    ]
    assert [item["source"] for item in facts] == ["ladder_structure"]
    assert leader["objective_source_count"] == 1
    assert leader["evidence_quality"] == 2


@pytest.mark.parametrize("input_by", [None, "", "  \t"])
def test_review_factor_score_requires_explicit_input_by(client, db_path, input_by):
    _seed_factor_trade_date(db_path)
    body = {"no_llm": True}
    if input_by is not None:
        body["input_by"] = input_by
    response = client.post("/api/review-factors/2026-07-10/score", json=body)

    assert response.status_code == 422
    assert "input_by" in response.text


@pytest.mark.parametrize(
    "invalid_field",
    [
        {"steps": []},
        {"retry_of_run_id": 123},
    ],
)
def test_review_factor_score_rejects_non_schema_types(client, db_path, invalid_field):
    _seed_factor_trade_date(db_path)
    response = client.post(
        "/api/review-factors/2026-07-10/score",
        json={"no_llm": True, "input_by": "web", **invalid_field},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("retry_of_run_id", ["", "  \t"])
def test_review_factor_score_rejects_blank_retry_id_before_any_write(
    client, db_path, retry_of_run_id
):
    _seed_factor_trade_date(db_path)
    before = _factor_persistence_counts(db_path)

    response = client.post(
        "/api/review-factors/2026-07-10/score",
        json={
            "no_llm": True,
            "input_by": "web",
            "retry_of_run_id": retry_of_run_id,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(error["loc"][-1] == "retry_of_run_id" for error in detail)
    assert _factor_persistence_counts(db_path) == before


def test_review_factor_score_strips_retry_id_whitespace(client, db_path):
    _seed_factor_trade_date(db_path)
    body = {"no_llm": True, "input_by": "web"}
    first = client.post("/api/review-factors/2026-07-10/score", json=body)
    run_id = first.json()["score_run_id"]

    retry = client.post(
        "/api/review-factors/2026-07-10/score",
        json={**body, "retry_of_run_id": f"  {run_id}\t"},
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["retry_of_run_id"] == run_id


def test_review_factor_score_rejects_extra_field_before_service_or_audit_write(
    client, db_path, monkeypatch
):
    import api.routes.review_factors as review_factors_route

    _seed_factor_trade_date(db_path)
    service_calls = 0
    original_score = review_factors_route.TrinityFactorService.score

    def counted_score(self, *args, **kwargs):
        nonlocal service_calls
        service_calls += 1
        return original_score(self, *args, **kwargs)

    monkeypatch.setattr(
        review_factors_route.TrinityFactorService,
        "score",
        counted_score,
    )

    response = client.post(
        "/api/review-factors/2026-07-10/score",
        json={
            "no_llm": True,
            "no_lllm": True,
            "input_by": "web",
        },
    )

    conn = get_connection(db_path)
    run_count = conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_runs"
    ).fetchone()[0]
    request_count = conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_requests"
    ).fetchone()[0]
    conn.close()

    assert response.status_code == 422
    assert service_calls == 0
    assert run_count == 0
    assert request_count == 0


def test_review_factor_score_rejects_oversized_snapshot_before_runner_or_audit(
    client, db_path, monkeypatch
):
    import api.routes.review_factors as review_factors_route
    from services.trinity_factor.runner import StructuredRunResult
    from services.trinity_factor.service import TrinityFactorService

    _seed_factor_trade_date(db_path)
    calls = []

    class CountingRunner:
        def run(self, **kwargs):
            calls.append(kwargs)
            return StructuredRunResult(
                status="schema_invalid",
                provider="antigravity",
                requested_model="model-a",
                actual_model=None,
                cli_version="agy-1",
                runtime_version="python-3.9",
                prompt_version="test-prompt",
                prompt_sha256="p" * 64,
                input_digest="i" * 64,
                schema_version="test-schema",
                ruleset_version="test-rules",
                attempt_count=2,
                duration_ms=1,
                is_cacheable=False,
                diagnostics={"reason": "schema_invalid"},
            )

    service = TrinityFactorService(runner=CountingRunner())
    monkeypatch.setattr(
        review_factors_route,
        "TrinityFactorService",
        lambda: service,
    )
    steps = {
        step_key: {
            f"field_{index:03d}": "界" * 500
            for index in range(100)
        }
        for step_key in (
            "step1_market",
            "step2_sectors",
            "step3_emotion",
            "step4_style",
            "step5_leaders",
            "step6_nodes",
        )
    }

    response = client.post(
        "/api/review-factors/2026-07-10/score",
        json={"no_llm": False, "input_by": "web", "steps": steps},
    )

    conn = get_connection(db_path)
    run_count = conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_runs"
    ).fetchone()[0]
    request_count = conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_score_requests"
    ).fetchone()[0]
    conn.close()

    assert response.status_code == 422
    assert "evidence snapshot exceeds" in response.text
    assert calls == []
    assert run_count == 0
    assert request_count == 0


def test_review_factor_score_openapi_requires_body_and_input_by(client):
    openapi = client.app.openapi()
    operation = openapi["paths"]["/api/review-factors/{date}/score"]["post"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True

    schema_ref = request_body["content"]["application/json"]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    request_schema = openapi["components"]["schemas"][schema_name]
    assert "input_by" in request_schema["required"]
    assert request_schema["additionalProperties"] is False


def test_review_factor_score_rejects_invalid_calendar_date(client):
    response = client.post(
        "/api/review-factors/2026-99-99/score",
        json={"no_llm": True, "input_by": "web"},
    )

    assert response.status_code == 422
    assert "invalid calendar date" in response.text.lower()


def test_review_put_rejects_conflicting_step5_stock_codes_and_rolls_back(
    client, db_path
):
    seeded = client.put(
        "/api/review/2026-07-10",
        json={"step1_market": {"notes": "原始复盘"}},
    )
    assert seeded.status_code == 200

    rejected = client.put(
        "/api/review/2026-07-10",
        json={
            "step1_market": {"notes": "不应写入"},
            "step5_leaders": {
                "top_leaders": [
                    {
                        "stock": "600001 同名股票",
                        "stock_code": "600002",
                        "sector": "软件开发",
                        "attribute_type": "趋势中军",
                    }
                ]
            },
        },
    )

    assert rejected.status_code == 422
    stored = client.get("/api/review/2026-07-10").json()
    assert json.loads(stored["step1_market"])["notes"] == "原始复盘"
    conn = get_connection(db_path)
    try:
        assert Q.get_active_leaders(conn) == []
    finally:
        conn.close()


def test_review_put_validates_factor_decision_and_syncs_legacy(client, db_path):
    _seed_factor_score_run(db_path)

    response = client.put("/api/review/2026-07-10", json={
        "step8_plan": {
            "summary": {"one_sentence": "保留字段"},
            "factor_decision": {
                "score_run_id": "api-run-1",
                "status": "accepted",
                "input_by": "web",
            },
        }
    })

    assert response.status_code == 200
    stored = client.get("/api/review/2026-07-10").json()
    step8 = json.loads(stored["step8_plan"])
    assert step8["factor_decision"]["primary_factor"] == "market_node"
    assert step8["key_factor"] == "market_node"
    assert step8["secondary_factors"] == ["sector_rhythm"]
    assert step8["summary"] == {"one_sentence": "保留字段"}

    unrelated = client.put("/api/review/2026-07-10", json={
        "step7_positions": {"notes": "只更新持仓复盘"},
    })
    assert unrelated.status_code == 200
    preserved_step8 = json.loads(client.get("/api/review/2026-07-10").json()["step8_plan"])
    assert preserved_step8["factor_decision"]["score_run_id"] == "api-run-1"

    changed = client.put("/api/review/2026-07-10", json={
        "step1_market": {"notes": "确认后修改评分输入"},
    })
    assert changed.status_code == 200
    changed_step8 = json.loads(client.get("/api/review/2026-07-10").json()["step8_plan"])
    assert changed_step8["factor_decision"] is None
    assert changed_step8["key_factor"] == ""
    assert changed_step8["secondary_factors"] == []

    cleared = client.put("/api/review/2026-07-10", json={
        "step8_plan": {
            "summary": {"one_sentence": "证据变化后保留其他字段"},
            "factor_decision": None,
            "key_factor": "",
            "secondary_factors": [],
        }
    })
    assert cleared.status_code == 200
    cleared_step8 = json.loads(client.get("/api/review/2026-07-10").json()["step8_plan"])
    assert cleared_step8["factor_decision"] is None
    assert cleared_step8["key_factor"] == ""
    assert cleared_step8["secondary_factors"] == []
    assert cleared_step8["summary"] == {"one_sentence": "证据变化后保留其他字段"}

    _seed_factor_score_run(db_path, run_id="api-run-2")
    invalid = client.put("/api/review/2026-07-10", json={
        "step8_plan": {
            "factor_decision": {
                "score_run_id": "api-run-2",
                "status": "overridden",
                "primary_factor": "style_regime",
                "input_by": "web",
            }
        }
    })
    assert invalid.status_code == 422
    assert "override_reason" in invalid.text


def test_review_put_rejects_factor_decision_when_score_input_changed(client, db_path):
    _seed_factor_score_run(db_path, run_id="stale-run")

    response = client.put("/api/review/2026-07-10", json={
        "step1_market": {"notes": "评分后修改了大盘事实判断"},
        "step8_plan": {
            "factor_decision": {
                "score_run_id": "stale-run",
                "status": "accepted",
                "input_by": "web",
            },
        },
    })

    assert response.status_code == 422
    assert "score input has changed" in response.text


def test_review_factor_score_and_confirm_share_summary_input_normalization(client, db_path):
    _seed_factor_trade_date(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-09", "is_open": 1}])
    conn.commit()
    conn.close()
    steps = {"step1_market": {"judgement": "结构偏弱"}}
    scored = client.post("/api/review-factors/2026-07-10/score", json={
        "steps": steps,
        "no_llm": True,
        "input_by": "web",
    })
    assert scored.status_code == 200

    saved = client.put("/api/review/2026-07-10", json={
        **steps,
        "step8_plan": {
            "factor_decision": {
                "score_run_id": scored.json()["score_run_id"],
                "status": "overridden",
                "primary_factor": "market_node",
                "supporting_factors": [],
                "override_reason": "人工确认大盘节点约束更强",
                "input_by": "web",
            },
        },
    })

    assert saved.status_code == 200


def test_review_factor_partial_confirm_canonicalizes_historical_text_steps(
    client, db_path
):
    _seed_factor_trade_date(db_path)
    conn = get_connection(db_path)
    Q.upsert_daily_review(conn, "2026-07-10", {
        "step1_market": {"judgement": "结构偏弱"},
    })
    conn.commit()
    assert isinstance(Q.get_daily_review(conn, "2026-07-10")["step1_market"], str)
    conn.close()

    scored = client.post("/api/review-factors/2026-07-10/score", json={
        "steps": {"step1_market": {"judgement": "结构偏弱"}},
        "no_llm": True,
        "input_by": "web",
    })
    assert scored.status_code == 200

    confirmed = client.put("/api/review/2026-07-10", json={
        "step8_plan": {
            "factor_decision": {
                "score_run_id": scored.json()["score_run_id"],
                "status": "overridden",
                "primary_factor": "market_node",
                "supporting_factors": [],
                "override_reason": "人工确认大盘节点约束更强",
                "input_by": "web",
            },
        },
    })
    assert confirmed.status_code == 200

    unrelated = client.put("/api/review/2026-07-10", json={
        "step7_positions": {"notes": "仅更新持仓复盘"},
    })
    assert unrelated.status_code == 200
    step8 = json.loads(client.get("/api/review/2026-07-10").json()["step8_plan"])
    assert step8["factor_decision"]["score_run_id"] == scored.json()["score_run_id"]


def test_review_factor_confirmation_checks_digest_inside_write_transaction(
    client, db_path, monkeypatch
):
    import api.routes.review as review_route

    _seed_factor_score_run(db_path, run_id="atomic-run")
    original = review_route.build_review_prefill
    observed = {"in_transaction": False}

    def checked_prefill(conn, date):
        observed["in_transaction"] = conn.in_transaction
        return original(conn, date)

    monkeypatch.setattr(review_route, "build_review_prefill", checked_prefill)
    response = client.put("/api/review/2026-07-10", json={
        "step8_plan": {
            "factor_decision": {
                "score_run_id": "atomic-run",
                "status": "accepted",
                "input_by": "web",
            },
        },
    })

    assert response.status_code == 200
    assert observed["in_transaction"] is True


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("unexpected", "value", "extra_forbidden"),
        ("input_by", 123, "string_type"),
        ("confirmed_outcome", "almost_hit", "literal_error"),
        ("source_date", 20260710, "string_type"),
        ("score_run_id", ["api-run-1"], "string_type"),
        ("evaluation_note", False, "string_type"),
    ],
)
def test_review_factor_evaluation_rejects_non_schema_payloads_before_write(
    client, db_path, field, value, error_type
):
    _seed_factor_score_run(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-13", "is_open": 1}])
    conn.commit()
    conn.close()
    body = {
        "source_date": "2026-07-10",
        "score_run_id": "api-run-1",
        "confirmed_outcome": "not_applicable",
        "evaluation_note": "当日未完成复盘",
        "input_by": "web",
        field: value,
    }

    response = client.put(
        "/api/review-factors/2026-07-13/evaluation",
        json=body,
    )

    conn = get_connection(db_path)
    evaluation_count = conn.execute(
        "SELECT COUNT(*) FROM daily_review_factor_evaluations"
    ).fetchone()[0]
    conn.close()
    detail = response.json()["detail"]

    assert response.status_code == 422
    assert isinstance(detail, list)
    assert any(
        error["loc"][-1] == field and error["type"] == error_type
        for error in detail
    )
    assert evaluation_count == 0


@pytest.mark.parametrize("field", ["source_date", "score_run_id"])
@pytest.mark.parametrize("blank", ["", "  \t"])
def test_review_factor_evaluation_put_rejects_explicit_blank_lookup_fields(
    client, db_path, field, blank
):
    _seed_factor_score_run(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-13", "is_open": 1}])
    conn.commit()
    conn.close()
    before = _factor_persistence_counts(db_path)
    body = {
        "source_date": "2026-07-10",
        "score_run_id": "api-run-1",
        "confirmed_outcome": "not_applicable",
        "input_by": "web",
        field: blank,
    }

    response = client.put(
        "/api/review-factors/2026-07-13/evaluation",
        json=body,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(error["loc"][-1] == field for error in detail)
    assert _factor_persistence_counts(db_path) == before


@pytest.mark.parametrize("field", ["source_date", "score_run_id"])
@pytest.mark.parametrize("blank", ["", "  \t"])
def test_review_factor_evaluation_get_rejects_explicit_blank_lookup_fields(
    client, db_path, field, blank
):
    _seed_factor_score_run(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-13", "is_open": 1}])
    conn.commit()
    conn.close()
    before = _factor_persistence_counts(db_path)
    params = {
        "source_date": "2026-07-10",
        "score_run_id": "api-run-1",
        field: blank,
    }

    response = client.get(
        "/api/review-factors/2026-07-13/evaluation",
        params=params,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(error["loc"][-1] == field for error in detail)
    assert _factor_persistence_counts(db_path) == before


def test_review_factor_evaluation_lookup_fields_strip_surrounding_whitespace(
    client, db_path
):
    _seed_factor_score_run(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-13", "is_open": 1}])
    conn.commit()
    conn.close()

    suggestion = client.get(
        "/api/review-factors/2026-07-13/evaluation",
        params={
            "source_date": "  2026-07-10\t",
            "score_run_id": "  api-run-1\t",
        },
    )
    confirmed = client.put(
        "/api/review-factors/2026-07-13/evaluation",
        json={
            "source_date": "  2026-07-10\t",
            "score_run_id": "  api-run-1\t",
            "confirmed_outcome": "not_applicable",
            "input_by": "web",
        },
    )

    assert suggestion.status_code == 200
    assert suggestion.json()["source_review_date"] == "2026-07-10"
    assert suggestion.json()["score_run_id"] == "api-run-1"
    assert confirmed.status_code == 200
    assert confirmed.json()["source_review_date"] == "2026-07-10"
    assert confirmed.json()["score_run_id"] == "api-run-1"


def test_review_factor_evaluation_openapi_keeps_required_body_fields(client):
    openapi = client.app.openapi()
    operation = openapi["paths"]["/api/review-factors/{date}/evaluation"]["put"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True

    schema_ref = request_body["content"]["application/json"]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    request_schema = openapi["components"]["schemas"][schema_name]
    assert set(request_schema["required"]) == {"confirmed_outcome", "input_by"}
    assert request_schema["additionalProperties"] is False


def test_review_factor_evaluation_is_strict_and_confirmable(client, db_path):
    _seed_factor_score_run(db_path)
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-11", "is_open": 0},
        {"date": "2026-07-13", "is_open": 1},
    ])
    conn.commit()
    conn.close()

    suggestion = client.get(
        "/api/review-factors/2026-07-13/evaluation",
        params={"source_date": "2026-07-10", "score_run_id": "api-run-1"},
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["system_outcome"] == "not_applicable"
    assert suggestion.json()["source_review_date"] == "2026-07-10"

    confirmed = client.put("/api/review-factors/2026-07-13/evaluation", json={
        "source_date": "2026-07-10",
        "score_run_id": "api-run-1",
        "confirmed_outcome": "not_applicable",
        "evaluation_note": "当日未完成复盘",
        "input_by": "web",
    })
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_outcome"] == "not_applicable"

    wrong_day = client.get(
        "/api/review-factors/2026-07-11/evaluation",
        params={"source_date": "2026-07-10", "score_run_id": "api-run-1"},
    )
    assert wrong_day.status_code == 422
    assert "evaluation_trade_date must be an open trade date" in wrong_day.text


def test_review_factor_evaluation_defaults_to_success_parent_before_failed_retry(
    client, db_path
):
    _seed_factor_score_run(db_path, run_id="api-parent", status="success")
    _seed_factor_score_run(
        db_path,
        run_id="api-failed-retry",
        status="factor_failed",
        retry_of_run_id="api-parent",
    )
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-13", "is_open": 1},
    ])
    conn.commit()
    conn.close()

    response = client.get(
        "/api/review-factors/2026-07-13/evaluation",
        params={"source_date": "2026-07-10"},
    )

    assert response.status_code == 200
    assert response.json()["score_run_id"] == "api-parent"


def test_review_factor_metrics_endpoint(client, db_path):
    _seed_factor_score_run(db_path)

    response = client.get("/api/review-factors/metrics", params={"days": 20})

    assert response.status_code == 200
    assert response.json()["runs"] == 1
    assert response.json()["days"] == 20
    assert client.get("/api/review-factors/metrics", params={"days": 0}).status_code == 422


def test_factor_score_confirm_and_evaluate_never_write_trade_drafts_or_plans(
    client, db_path
):
    conn = get_connection(db_path)
    Q.upsert_trade_calendar(conn, [
        {"date": "2026-07-10", "is_open": 1},
        {"date": "2026-07-13", "is_open": 1},
    ])
    before = {
        "drafts": conn.execute("SELECT COUNT(*) FROM trade_drafts").fetchone()[0],
        "plans": conn.execute("SELECT COUNT(*) FROM trade_plans").fetchone()[0],
    }
    conn.commit()
    conn.close()

    steps = {"step6_nodes": {"systemic_risk": True}}
    scored = client.post("/api/review-factors/2026-07-10/score", json={
        "steps": steps,
        "no_llm": True,
        "input_by": "web",
    })
    assert scored.status_code == 200
    confirmed = client.put("/api/review/2026-07-10", json={
        **steps,
        "step8_plan": {
            "factor_decision": {
                "score_run_id": scored.json()["score_run_id"],
                "status": "undetermined",
                "input_by": "web",
            },
        },
    })
    assert confirmed.status_code == 200
    assert client.put("/api/review/2026-07-13", json={
        "step1_market": {"notes": "完成 T+1 复盘"},
    }).status_code == 200
    evaluated = client.put("/api/review-factors/2026-07-13/evaluation", json={
        "source_date": "2026-07-10",
        "score_run_id": scored.json()["score_run_id"],
        "confirmed_outcome": "missing_data",
        "input_by": "web",
    })
    assert evaluated.status_code == 200

    conn = get_connection(db_path)
    after = {
        "drafts": conn.execute("SELECT COUNT(*) FROM trade_drafts").fetchone()[0],
        "plans": conn.execute("SELECT COUNT(*) FROM trade_plans").fetchone()[0],
    }
    conn.close()
    assert after == before
