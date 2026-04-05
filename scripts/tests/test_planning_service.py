"""L2: 交易计划服务测试。"""
from __future__ import annotations

import json

from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.planning_service import PlanningService


class _FakeProvider:
    name = "fake"

    def __init__(self, supported: set[str], results: dict[str, DataResult]):
        self._supported = supported
        self._results = results

    def supports(self, method_name: str) -> bool:
        return method_name in self._supported


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider):
        self.providers = [provider]
        self._provider = provider

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        return self._provider._results[method_name]


def test_create_observation_and_draft(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs1 = service.create_observation(
        trade_date="2026-04-07",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "分歧"},
        sector_facts={"main_themes": ["AI算力"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "容量核心"}],
        judgements=[{"text": "主线是否切换尚未确认", "ambiguous": True}],
        input_by="cursor",
    )
    obs2 = service.create_observation(
        trade_date="2026-04-07",
        source_type="agent_assisted",
        title="资料提炼",
        sector_facts={"main_themes": ["机器人"]},
        stock_facts=[{"subject_code": "002594.SZ", "subject_name": "比亚迪", "reason": "趋势核心"}],
        judgements=["情绪偏分歧"],
        input_by="openclaw",
    )

    draft = service.create_draft(
        trade_date="2026-04-07",
        source_observation_ids=[obs1["observation_id"], obs2["observation_id"]],
        input_by="cursor",
    )

    assert draft["status"] == "ready_for_confirm"
    market_view = json.loads(draft["market_view_json"])
    sector_view = json.loads(draft["sector_view_json"])
    stock_focus = json.loads(draft["stock_focus_json"])
    assert market_view["bias"] == "分歧"
    assert "AI算力" in sector_view["main_themes"]
    assert "机器人" in sector_view["main_themes"]
    assert len(stock_focus) == 2


def test_confirm_plan_and_review(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-08",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-08",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-08",
        input_by="cursor",
    )
    assert plan["status"] == "confirmed"
    assert plan["source_draft_id"] == draft["draft_id"]

    review = service.review_plan(
        plan_id=plan["plan_id"],
        trade_date="2026-04-08",
        outcome_summary="计划完成度一般",
        input_by="cursor",
    )
    assert review["plan_id"] == plan["plan_id"]

    updated_plan = service.get_plan(plan_id=plan["plan_id"])
    assert updated_plan["status"] == "reviewed"


def test_confirm_plan_preserves_edited_draft_watch_items(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-08",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-08",
        source_observation_ids=[obs["observation_id"]],
    )
    service.update_draft(
        draft["draft_id"],
        watch_items=[
            {
                "subject_type": "stock",
                "subject_code": "002594.SZ",
                "subject_name": "比亚迪",
                "reason": "手工改成趋势观察",
                "fact_checks": [],
                "judgement_checks": [],
                "trigger_conditions": ["站稳20日线"],
                "invalidations": ["跌破昨日低点"],
                "priority": 7,
            }
        ],
        input_by="cursor",
    )

    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-08",
        input_by="cursor",
    )
    watch_items = json.loads(plan["watch_items_json"])
    assert watch_items == [
        {
            "subject_type": "stock",
            "subject_code": "002594.SZ",
            "subject_name": "比亚迪",
            "reason": "手工改成趋势观察",
            "fact_checks": [],
            "judgement_checks": [],
            "trigger_conditions": ["站稳20日线"],
            "invalidations": ["跌破昨日低点"],
            "priority": 7,
        }
    ]


def test_update_observation_draft_and_plan(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-08",
        source_type="manual",
        title="初始观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    updated_obs = service.update_observation(
        obs["observation_id"],
        title="已修改观察",
        judgements=["情绪偏分歧"],
        input_by="cursor",
    )
    assert updated_obs["title"] == "已修改观察"
    assert json.loads(updated_obs["judgements_json"]) == ["情绪偏分歧"]

    draft = service.create_draft(
        trade_date="2026-04-08",
        source_observation_ids=[obs["observation_id"]],
    )
    updated_draft = service.update_draft(
        draft["draft_id"],
        summary="更新后的草稿摘要",
        watch_items=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "手工更新"}],
        input_by="cursor",
    )
    assert updated_draft["summary"] == "更新后的草稿摘要"
    assert json.loads(updated_draft["watch_items_json"])[0]["reason"] == "手工更新"

    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-08",
        input_by="cursor",
    )
    updated_plan = service.update_plan(
        plan["plan_id"],
        title="更新后的正式计划",
        market_bias="分歧",
        main_themes=["机器人"],
        input_by="cursor",
    )
    assert updated_plan["title"] == "更新后的正式计划"
    assert updated_plan["market_bias"] == "分歧"
    assert json.loads(updated_plan["main_themes_json"]) == ["机器人"]


def test_update_plan_rejects_direct_status_change(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-08",
        source_type="manual",
        title="初始观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-08",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-08",
        input_by="cursor",
    )

    try:
        service.update_plan(plan["plan_id"], status="reviewed", input_by="cursor")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "status" in str(exc)


def test_diagnose_plan_uses_fact_snapshots(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO market_fact_snapshots
        (snapshot_id, biz_date, fact_type, subject_type, subject_code, subject_name, facts_json, source_interfaces_json, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-04-09:margin_stats:market:CN",
            "2026-04-09",
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

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-09",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-09",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-09",
        input_by="cursor",
    )

    conn = get_connection(db_path)
    watch_items = json.loads(
        conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
    )
    watch_items[0]["fact_checks"].append(
        {
            "check_type": "margin_balance_change_positive",
            "label": "融资余额变化为正",
            "params": {},
        }
    )
    conn.execute(
        "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
        (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
    )
    conn.commit()
    conn.close()

    diagnostics = service.diagnose_plan(plan_id=plan["plan_id"])
    assert diagnostics is not None
    assert diagnostics["fact_check_count"] == 1
    margin_result = diagnostics["items_json"][0]["fact_check_results"][-1]
    assert margin_result["check_type"] == "margin_balance_change_positive"
    assert margin_result["result"] == "pass"


def test_diagnose_plan_uses_provider_fallback_for_price_and_announcements(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    registry = _FakeRegistry(
        _FakeProvider(
            {"get_stock_daily", "get_stock_ma", "get_stock_announcements"},
            {
                "get_stock_daily": DataResult(
                    data={"close": 102.0, "change_pct": 3.5},
                    source="fake:daily",
                ),
                "get_stock_ma": DataResult(
                    data={"ma20": 100.0},
                    source="fake:ma",
                ),
                "get_stock_announcements": DataResult(
                    data=[{"title": "测试公告", "ann_date": "20260410"}],
                    source="fake:ann",
                ),
            },
        )
    )
    service = PlanningService(str(db_path), registry=registry)
    obs = service.create_observation(
        trade_date="2026-04-10",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-10",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-10",
        input_by="cursor",
    )

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

    diagnostics = service.diagnose_plan(plan_id=plan["plan_id"])
    assert diagnostics is not None
    results = diagnostics["items_json"][0]["fact_check_results"]
    assert [item["result"] for item in results] == ["pass", "pass", "pass"]


def test_diagnose_plan_uses_daily_market_for_amount_and_sector_checks(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.execute(
        """
        INSERT INTO daily_market (date, total_amount, raw_data)
        VALUES (?, ?, ?)
        """,
        (
            "2026-04-10",
            10000.0,
            json.dumps({"sector_concept": [{"sector_name": "机器人", "change_pct": -0.5, "limit_up_count": 1}]}, ensure_ascii=False),
        ),
    )
    conn.execute(
        """
        INSERT INTO daily_market (date, total_amount, raw_data)
        VALUES (?, ?, ?)
        """,
        (
            "2026-04-11",
            12000.0,
            json.dumps({"sector_concept": [{"sector_name": "机器人", "change_pct": 2.6, "limit_up_count": 4}]}, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-11",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["机器人"]},
        stock_facts=[{"subject_code": "002594.SZ", "subject_name": "比亚迪", "reason": "观察板块回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-11",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-11",
        input_by="cursor",
    )

    conn = get_connection(db_path)
    watch_items = json.loads(
        conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
    )
    watch_items[0]["fact_checks"] = [
        {"check_type": "market_amount_gte_prev_day", "label": "成交额不低于前一日", "params": {}},
        {"check_type": "sector_change_positive", "label": "板块涨幅为正", "params": {"sector_name": "机器人"}},
        {"check_type": "sector_limit_up_count_gte", "label": "板块涨停家数不少于3", "params": {"sector_name": "机器人", "value": 3}},
    ]
    conn.execute(
        "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
        (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
    )
    conn.commit()
    conn.close()

    diagnostics = service.diagnose_plan(plan_id=plan["plan_id"])
    assert diagnostics is not None
    results = diagnostics["items_json"][0]["fact_check_results"]
    assert [item["result"] for item in results] == ["pass", "pass", "pass"]
    assert results[0]["evidence_json"]["current_total_amount"] == 12000.0
    assert results[1]["evidence_json"]["sector_name"] == "机器人"
    assert results[2]["evidence_json"]["limit_up_count"] == 4.0


def test_diagnose_plan_counts_missing_and_unsupported_by_watch_item(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    service = PlanningService(str(db_path))
    obs = service.create_observation(
        trade_date="2026-04-11",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["机器人"]},
        stock_facts=[{"subject_code": "002594.SZ", "subject_name": "比亚迪", "reason": "观察板块回流"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-11",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-11",
        input_by="cursor",
    )

    conn = get_connection(db_path)
    watch_items = json.loads(
        conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
    )
    watch_items[0]["fact_checks"] = [
        {"check_type": "price_above_ma20", "label": "站稳20日线", "params": {"ts_code": "002594.SZ"}},
        {"check_type": "unknown_check", "label": "未知检查", "params": {}},
    ]
    conn.execute(
        "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
        (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
    )
    conn.commit()
    conn.close()

    diagnostics = service.diagnose_plan(plan_id=plan["plan_id"])
    assert diagnostics is not None
    assert diagnostics["watch_item_count"] == 1
    assert diagnostics["missing_data_count"] == 1
    assert diagnostics["unsupported_check_count"] == 1
    assert diagnostics["summary_json"]["missing_data_items"] == 1
    assert diagnostics["summary_json"]["unsupported_checks"] == 1


def test_diagnose_plan_uses_recent_trade_dates_for_ret_5d(tmp_path):
    db_path = tmp_path / "planning.db"
    conn = get_connection(db_path)
    migrate(conn)
    for date_str in ["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"]:
        conn.execute("INSERT INTO daily_market (date, total_amount) VALUES (?, ?)", (date_str, 10000.0))
    conn.commit()
    conn.close()

    registry = _FakeRegistry(
        _FakeProvider(
            {"get_stock_daily"},
            {
                "get_stock_daily": DataResult(
                    data={"close": 105.0, "change_pct": 2.0},
                    source="fake:daily",
                ),
            },
        )
    )

    calls: list[str] = []

    def _call(method_name: str, *args, **kwargs):
        calls.append(args[1])
        if args[1] == "2026-04-06":
            return DataResult(data={"close": 100.0, "change_pct": 0.0}, source="fake:daily")
        return DataResult(data={"close": 105.0, "change_pct": 2.0}, source="fake:daily")

    registry.call = _call  # type: ignore[assignment]

    service = PlanningService(str(db_path), registry=registry)
    obs = service.create_observation(
        trade_date="2026-04-10",
        source_type="manual",
        title="盘后观察",
        market_facts={"bias": "震荡"},
        sector_facts={"main_themes": ["AI"]},
        stock_facts=[{"subject_code": "300750.SZ", "subject_name": "宁德时代", "reason": "观察五日强度"}],
        judgements=[],
    )
    draft = service.create_draft(
        trade_date="2026-04-10",
        source_observation_ids=[obs["observation_id"]],
    )
    plan = service.confirm_plan(
        draft_id=draft["draft_id"],
        trade_date="2026-04-10",
        input_by="cursor",
    )

    conn = get_connection(db_path)
    watch_items = json.loads(
        conn.execute("SELECT watch_items_json FROM trade_plans WHERE plan_id = ?", (plan["plan_id"],)).fetchone()[0]
    )
    watch_items[0]["fact_checks"] = [
        {"check_type": "ret_5d_gte", "label": "五日涨幅不低于4%", "params": {"ts_code": "300750.SZ", "value": 4}},
    ]
    conn.execute(
        "UPDATE trade_plans SET watch_items_json = ? WHERE plan_id = ?",
        (json.dumps(watch_items, ensure_ascii=False), plan["plan_id"]),
    )
    conn.commit()
    conn.close()

    diagnostics = service.diagnose_plan(plan_id=plan["plan_id"])
    assert diagnostics is not None
    result = diagnostics["items_json"][0]["fact_check_results"][0]
    assert result["check_type"] == "ret_5d_gte"
    assert result["result"] == "pass"
    assert result["evidence_json"]["ret_5d"] == 5.0
    assert calls == ["2026-04-10", "2026-04-06"]
