"""非交易日拦截逻辑的单元测试。"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from providers.base import DataResult


class _ProxyTracker:
    """可调用的代理屏蔽上下文桩：记录 enter/exit，供断言某段代码是否在屏蔽内执行。

    cmd_post 会多次 `without_standard_http_proxy()`（主采集块、内嵌 ingest 块各一次），
    各块顺序进入/退出，因此用单一 active 标志即可。
    """

    def __init__(self):
        self.active = False
        self.enter_count = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.active = True
        self.enter_count += 1
        return self

    def __exit__(self, *exc):
        self.active = False
        return False


def _make_registry(is_trade_day_value=None, error=None, cal_data=None):
    """构造 mock registry，支持 is_trade_day 返回值配置。"""
    registry = MagicMock()

    def call_side(method: str, *args, **kwargs):
        if method == "is_trade_day":
            if error:
                return DataResult(data=None, source="mock", error=error)
            return DataResult(data=is_trade_day_value, source="mock")
        if method == "get_trade_calendar":
            return DataResult(data=cal_data or [], source="mock")
        return DataResult(data=None, source="mock", error="unexpected")

    registry.call.side_effect = call_side
    return registry


class TestWeekendCheck:
    """周末快速判断（不调 API）。"""

    @patch("main.setup_providers")
    @patch("main.load_config", return_value={})
    def test_cmd_pre_skips_on_saturday(self, _lc, mock_sp, caplog):
        mock_sp.return_value = _make_registry(is_trade_day_value=True)
        from main import cmd_pre
        with caplog.at_level(logging.WARNING):
            cmd_pre({}, "2026-04-11")  # 周六
        assert "周末" in caplog.text
        mock_sp.assert_not_called()

    @patch("main.setup_providers")
    @patch("main.load_config", return_value={})
    def test_cmd_post_skips_on_sunday(self, _lc, mock_sp, caplog):
        mock_sp.return_value = _make_registry(is_trade_day_value=True)
        from main import cmd_post
        with caplog.at_level(logging.WARNING):
            cmd_post({}, "2026-04-12")  # 周日
        assert "周末" in caplog.text
        mock_sp.assert_not_called()

    @patch("main.setup_providers")
    @patch("main.load_config", return_value={})
    def test_cmd_evening_skips_on_weekend(self, _lc, mock_sp, caplog):
        mock_sp.return_value = _make_registry(is_trade_day_value=True)
        from main import cmd_evening
        with caplog.at_level(logging.WARNING):
            cmd_evening({}, "2026-04-11")  # 周六
        assert "周末" in caplog.text
        mock_sp.assert_not_called()


class TestHolidayCheck:
    """工作日但法定假日，通过 is_trade_day API + DB 交易日历判断。"""

    @patch("main.setup_pushers")
    @patch("main.setup_providers")
    @patch("db.connection.get_connection")
    def test_cmd_pre_skips_on_holiday(self, mock_conn_fn, mock_sp, _push, caplog, tmp_path):
        conn = _make_test_db(tmp_path, holiday="2026-04-06")
        mock_conn_fn.return_value = conn
        reg = _make_registry(is_trade_day_value=False)
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from main import cmd_pre
        with caplog.at_level(logging.WARNING):
            cmd_pre({}, "2026-04-06")  # 周一，法定假日
        assert "非交易日" in caplog.text

    @patch("main.setup_pushers")
    @patch("main.setup_providers")
    @patch("db.connection.get_connection")
    def test_cmd_evening_skips_on_holiday(self, mock_conn_fn, mock_sp, _push, caplog, tmp_path):
        conn = _make_test_db(tmp_path, holiday="2026-04-06")
        mock_conn_fn.return_value = conn
        reg = _make_registry(is_trade_day_value=False)
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from main import cmd_evening
        with caplog.at_level(logging.WARNING):
            cmd_evening({}, "2026-04-06")
        assert "非交易日" in caplog.text

    @patch("main.setup_pushers")
    @patch("main.setup_providers")
    @patch("db.connection.get_connection")
    def test_api_failure_does_not_block(self, mock_conn_fn, mock_sp, _push, caplog, tmp_path):
        """is_trade_day 调用失败时不应阻塞后续流程（DB 也无缓存）。"""
        conn = _make_test_db(tmp_path)  # 空 DB，无日历缓存
        mock_conn_fn.return_value = conn
        reg = _make_registry(error="api down")
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg

        from main import cmd_evening
        with caplog.at_level(logging.WARNING):
            try:
                cmd_evening({}, "2026-04-06")
            except Exception:
                pass
        assert "非交易日" not in caplog.text


class TestPostCommandIngestAudit:
    """盘后主流程触发的 IngestService 审计应归类为 system。"""

    @patch("services.ingest_service.IngestService")
    @patch("generators.obsidian_export.ObsidianExporter")
    @patch("main.setup_pushers")
    @patch("db.dual_write.sync_holdings_quotes_from_post_market", return_value=0)
    @patch("db.dual_write.sync_daily_market_to_db", return_value=True)
    @patch("generators.ReportGenerator")
    @patch("collectors.WatchlistCollector")
    @patch("collectors.HoldingsCollector")
    @patch("collectors.MarketCollector")
    @patch("utils.trade_date.is_trade_day", return_value=True)
    @patch("utils.trade_date.ensure_trade_calendar", return_value=0)
    @patch("db.connection.get_connection")
    @patch("main.setup_providers")
    @patch("main.cmd_evening")
    def test_cmd_post_uses_system_triggered_by_for_ingest_service(
        self,
        mock_evening,
        mock_setup_providers,
        mock_get_connection,
        _mock_ensure_trade_calendar,
        _mock_is_trade_day,
        mock_market_collector_cls,
        mock_holdings_collector_cls,
        mock_watchlist_collector_cls,
        mock_report_generator_cls,
        _mock_sync_daily_market,
        _mock_sync_holdings_quotes,
        mock_setup_pushers,
        mock_obsidian_exporter_cls,
        mock_ingest_service_cls,
        caplog,
        tmp_path,
    ):
        from main import cmd_post

        mock_get_connection.return_value = MagicMock()

        registry = MagicMock()
        registry.initialize_all.return_value = {}
        mock_setup_providers.return_value = registry

        market_collector = MagicMock()
        market_collector.collect_post_market.return_value = {"indices": {}}
        mock_market_collector_cls.return_value = market_collector

        holdings_collector = MagicMock()
        holdings_collector.collect_holdings_data.return_value = []
        holdings_collector.enrich_with_ma.return_value = []
        holdings_collector.collect_holdings_announcements.return_value = {}
        holdings_collector.collect_stock_info.return_value = {}
        mock_holdings_collector_cls.return_value = holdings_collector

        watchlist_collector = MagicMock()
        watchlist_collector.get_watchlist_summary.return_value = {}
        mock_watchlist_collector_cls.return_value = watchlist_collector

        yaml_path = Path(tmp_path) / "post-market.yaml"
        yaml_path.write_text("{}", encoding="utf-8")
        report_generator = MagicMock()
        report_generator.generate_post_market.return_value = ("# report", str(yaml_path))
        mock_report_generator_cls.return_value = report_generator

        multi = MagicMock()
        multi._pushers = []
        mock_setup_pushers.return_value = multi

        exporter = MagicMock()
        exporter.export_post_market.return_value = None
        mock_obsidian_exporter_cls.return_value = exporter

        proxy_tracker = _ProxyTracker()
        observed_active: list[bool] = []

        ingest_service = MagicMock()

        def _execute_stage_side_effect(stage, *args, **kwargs):
            # 守 Bug #2：记录每次 ingest 调用时是否处于代理屏蔽上下文。
            # 不抛断言——cmd_post 的 ingest 块裹在 try/except Exception 里，断言会被吞掉；
            # 改为记录 + 末尾统一断言，让回归（块在上下文外 → [False, False]）直接、可靠地变红。
            observed_active.append(proxy_tracker.active)
            # 守 Bug #1：返回真实契约（runs / recorded_runs），而非曾被误读的 interfaces。
            return {
                "recorded_runs": 2,
                "runs": [{"status": "success"}, {"status": "failed"}],
            }

        ingest_service.execute_stage.side_effect = _execute_stage_side_effect
        mock_ingest_service_cls.return_value = ingest_service

        with patch("main.without_standard_http_proxy", new=proxy_tracker), \
             patch("main._schedule_task_enabled", return_value=False), \
             caplog.at_level(logging.INFO):
            cmd_post({}, "2026-04-17")

        assert ingest_service.execute_stage.call_count == 2
        stages = [call.args[0] for call in ingest_service.execute_stage.call_args_list]
        triggered_values = [call.kwargs["triggered_by"] for call in ingest_service.execute_stage.call_args_list]
        assert stages == ["post_core", "post_extended"]
        assert triggered_values == ["system", "system"]
        # 守 Bug #2：两次 ingest 调用都必须在代理屏蔽上下文内（旧代码块在外 → [False, False]）。
        assert observed_active == [True, True]
        # 区分"真失败"与"正常 empty"：真在屏蔽内执行就不该落到 except 兜底分支。
        assert "IngestService 快照采集失败" not in caplog.text
        # 守 Bug #1：日志反映真实成功/空/失败数（旧代码恒打 0/0）。
        assert "成功 1 / 空结果 0 / 失败 1" in caplog.text
        # 主采集块 + 内嵌 ingest 块各进入一次代理屏蔽上下文。
        assert proxy_tracker.enter_count >= 2


# ──────────────────────────────────────────────────────────────
# DB 交易日历核心逻辑
# ──────────────────────────────────────────────────────────────

def _make_test_db(tmp_path, holiday=None):
    """创建带 trade_calendar 表的内存 DB。"""
    from db.schema import init_schema
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    if holiday:
        conn.execute(
            "INSERT INTO trade_calendar (date, is_open) VALUES (?, 0)", (holiday,)
        )
        conn.commit()
    return conn


class TestIsTradeDay:
    """utils.trade_date.is_trade_day 统一判断函数。"""

    def test_db_cached_holiday(self, tmp_path):
        """DB 中标记为非交易日时直接返回 False，不调 provider。"""
        from utils.trade_date import is_trade_day
        conn = _make_test_db(tmp_path, holiday="2026-05-01")
        assert is_trade_day("2026-05-01", conn=conn) is False

    def test_db_cached_trading_day(self, tmp_path):
        from utils.trade_date import is_trade_day
        conn = _make_test_db(tmp_path)
        conn.execute("INSERT INTO trade_calendar (date, is_open) VALUES ('2026-04-10', 1)")
        conn.commit()
        assert is_trade_day("2026-04-10", conn=conn) is True

    def test_db_miss_fallback_to_provider(self, tmp_path):
        """DB 无记录时走 provider，并回写 DB 缓存。"""
        from utils.trade_date import is_trade_day
        conn = _make_test_db(tmp_path)
        reg = _make_registry(is_trade_day_value=False)
        result = is_trade_day("2026-10-01", conn=conn, registry=reg)
        assert result is False
        row = conn.execute("SELECT is_open FROM trade_calendar WHERE date='2026-10-01'").fetchone()
        assert row is not None
        assert row[0] == 0

    def test_no_db_no_provider_fallback_weekend(self):
        from utils.trade_date import is_trade_day
        assert is_trade_day("2026-04-11") is False  # 周六

    def test_no_db_no_provider_fallback_weekday(self):
        from utils.trade_date import is_trade_day
        assert is_trade_day("2026-04-10") is True  # 周五


class TestEnsureTradeCalendar:
    """ensure_trade_calendar 导入年度日历。"""

    def test_imports_when_empty(self, tmp_path):
        from utils.trade_date import ensure_trade_calendar
        from db import queries as Q
        conn = _make_test_db(tmp_path)
        cal_data = [
            {"cal_date": f"2026{m:02d}{d:02d}", "is_open": 1}
            for m in range(1, 13) for d in range(1, 22)
        ]
        reg = _make_registry(cal_data=cal_data)
        count = ensure_trade_calendar(conn, reg, year=2026)
        assert count > 200
        assert Q.trade_calendar_year_covered(conn, 2026) is True

    def test_skips_when_already_covered(self, tmp_path):
        from utils.trade_date import ensure_trade_calendar
        conn = _make_test_db(tmp_path)
        for m in range(1, 13):
            for d in range(1, 22):
                conn.execute(
                    "INSERT OR IGNORE INTO trade_calendar (date, is_open) VALUES (?, 1)",
                    (f"2026-{m:02d}-{d:02d}",),
                )
        conn.commit()
        reg = _make_registry()
        count = ensure_trade_calendar(conn, reg, year=2026)
        assert count == 0
        reg.call.assert_not_called()


class TestPrefillIsTradingDay:
    """API prefill 返回 is_trading_day 字段。"""

    def _mock_prefill(self, date, conn, market=None):
        with patch("api.routes.review.Q") as mock_q, \
             patch("api.routes.review.build_holding_signals", return_value={"date": date, "items": []}), \
             patch("api.routes.review.parse_post_market_envelope", return_value=None), \
             patch("api.routes.review.holdings_quote_details_from_envelope", return_value={}), \
             patch("api.routes.review.enrich_daily_market_row"):
            mock_q.get_daily_market.return_value = market
            mock_q.get_prev_daily_market.return_value = None
            mock_q.get_avg_amount.return_value = None
            mock_q.get_latest_emotion.return_value = None
            mock_q.get_active_themes.return_value = []
            mock_q.get_holdings.return_value = []
            mock_q.get_calendar_range.return_value = []
            mock_q.get_prev_daily_review.return_value = None
            mock_q.get_recent_industry_info.return_value = []
            mock_q.compute_ma5w_flags_from_history.return_value = {}
            mock_q.is_trade_day_from_db.return_value = None
            mock_q.get_next_trade_date.return_value = None
            mock_q.get_prev_trade_date_from_db.return_value = None

            from api.routes.review import get_prefill
            return get_prefill(date, conn=conn)

    def test_weekend_returns_false(self):
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.return_value.fetchall.return_value = []
        conn.execute.return_value.fetchone.return_value = None
        result = self._mock_prefill("2026-04-11", conn)  # 周六
        assert result["is_trading_day"] is False

    def test_weekday_with_market_data_returns_true(self):
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.return_value.fetchall.return_value = []
        conn.execute.return_value.fetchone.return_value = None
        result = self._mock_prefill("2026-04-10", conn, market={"date": "2026-04-10", "raw_data": None})
        assert result["is_trading_day"] is True

    def test_holiday_with_db_calendar_returns_false(self, tmp_path):
        """法定假日（工作日）在 DB 有交易日历时应返回 False。"""
        conn = _make_test_db(tmp_path, holiday="2026-05-01")
        with patch("api.routes.review.Q") as mock_q, \
             patch("api.routes.review.build_holding_signals", return_value={"date": "2026-05-01", "items": []}), \
             patch("api.routes.review.parse_post_market_envelope", return_value=None), \
             patch("api.routes.review.holdings_quote_details_from_envelope", return_value={}), \
             patch("api.routes.review.enrich_daily_market_row"):
            mock_q.get_daily_market.return_value = None
            mock_q.get_prev_daily_market.return_value = None
            mock_q.get_avg_amount.return_value = None
            mock_q.get_latest_emotion.return_value = None
            mock_q.get_active_themes.return_value = []
            mock_q.get_holdings.return_value = []
            mock_q.get_calendar_range.return_value = []
            mock_q.get_prev_daily_review.return_value = None
            mock_q.get_recent_industry_info.return_value = []
            mock_q.compute_ma5w_flags_from_history.return_value = {}
            mock_q.get_next_trade_date.return_value = None
            mock_q.get_prev_trade_date_from_db.return_value = "2026-04-30"

            from api.routes.review import get_prefill
            result = get_prefill("2026-05-01", conn=conn)  # 劳动节（周五）
            assert result["is_trading_day"] is False
            assert result["prev_trade_date"] == "2026-04-30"


# ──────────────────────────────────────────────────────────────
# 交易日历导航：get_next_trade_date / get_prev_trade_date_from_db
# ──────────────────────────────────────────────────────────────

def _make_calendar_db(tmp_path, entries: list[tuple[str, int]]):
    """创建带 trade_calendar 数据的测试 DB。entries: [(date, is_open), ...]"""
    from db.schema import init_schema
    conn = sqlite3.connect(str(tmp_path / "cal_test.db"))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for dt, is_open in entries:
        conn.execute(
            "INSERT INTO trade_calendar (date, is_open) VALUES (?, ?)", (dt, is_open)
        )
    conn.commit()
    return conn


class TestTradeCalendarNavigation:
    """get_next_trade_date / get_prev_trade_date_from_db 数据层测试。"""

    CAL = [
        ("2026-04-08", 1),  # 周三
        ("2026-04-09", 1),  # 周四
        ("2026-04-10", 1),  # 周五
        ("2026-04-11", 0),  # 周六
        ("2026-04-12", 0),  # 周日
        ("2026-04-13", 1),  # 周一
        ("2026-04-14", 1),  # 周二
    ]

    def test_next_trade_date_normal(self, tmp_path):
        from db.queries import get_next_trade_date
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_next_trade_date(conn, "2026-04-10") == "2026-04-13"

    def test_next_trade_date_skips_weekend(self, tmp_path):
        from db.queries import get_next_trade_date
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_next_trade_date(conn, "2026-04-11") == "2026-04-13"

    def test_next_trade_date_none_at_boundary(self, tmp_path):
        from db.queries import get_next_trade_date
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_next_trade_date(conn, "2026-04-14") is None

    def test_prev_trade_date_normal(self, tmp_path):
        from db.queries import get_prev_trade_date_from_db
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_prev_trade_date_from_db(conn, "2026-04-13") == "2026-04-10"

    def test_prev_trade_date_skips_weekend(self, tmp_path):
        from db.queries import get_prev_trade_date_from_db
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_prev_trade_date_from_db(conn, "2026-04-12") == "2026-04-10"

    def test_prev_trade_date_none_at_boundary(self, tmp_path):
        from db.queries import get_prev_trade_date_from_db
        conn = _make_calendar_db(tmp_path, self.CAL)
        assert get_prev_trade_date_from_db(conn, "2026-04-08") is None

    def test_consecutive_holidays(self, tmp_path):
        """连续假日（如五一长假）跳过所有非交易日。"""
        from db.queries import get_next_trade_date, get_prev_trade_date_from_db
        cal = [
            ("2026-04-30", 1),
            ("2026-05-01", 0),
            ("2026-05-02", 0),
            ("2026-05-03", 0),
            ("2026-05-04", 0),
            ("2026-05-05", 0),
            ("2026-05-06", 1),
        ]
        conn = _make_calendar_db(tmp_path, cal)
        assert get_next_trade_date(conn, "2026-04-30") == "2026-05-06"
        assert get_prev_trade_date_from_db(conn, "2026-05-06") == "2026-04-30"
        assert get_prev_trade_date_from_db(conn, "2026-05-03") == "2026-04-30"


class TestPrefillNoteDateRange:
    """prefill 老师笔记范围查询 + prev_trade_date 返回。"""

    def _setup_db(self, tmp_path):
        """创建带 trade_calendar + teachers + teacher_notes 的测试 DB。"""
        from db.schema import init_schema
        conn = sqlite3.connect(str(tmp_path / "range_test.db"))
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        cal = [
            ("2026-04-10", 1),  # 周五 — 交易日
            ("2026-04-11", 0),  # 周六
            ("2026-04-12", 0),  # 周日
            ("2026-04-13", 1),  # 周一 — 下一个交易日
        ]
        for dt, is_open in cal:
            conn.execute(
                "INSERT INTO trade_calendar (date, is_open) VALUES (?, ?)", (dt, is_open)
            )
        conn.execute("INSERT INTO teachers (id, name) VALUES (1, '测试老师')")
        conn.execute(
            "INSERT INTO teacher_notes (teacher_id, date, title, raw_content) "
            "VALUES (1, '2026-04-10', '周五观点', '周五内容')"
        )
        conn.execute(
            "INSERT INTO teacher_notes (teacher_id, date, title, raw_content) "
            "VALUES (1, '2026-04-12', '周日观点', '周日内容')"
        )
        conn.commit()
        return conn

    def _call_prefill(self, date, conn):
        with patch("api.routes.review.build_holding_signals", return_value={"date": date, "items": []}), \
             patch("api.routes.review.parse_post_market_envelope", return_value=None), \
             patch("api.routes.review.holdings_quote_details_from_envelope", return_value={}), \
             patch("api.routes.review.enrich_daily_market_row"):
            from api.routes.review import get_prefill
            return get_prefill(date, conn=conn)

    def test_friday_prefill_includes_weekend_notes(self, tmp_path):
        """周五复盘应包含周五和周末的老师笔记。"""
        conn = self._setup_db(tmp_path)
        result = self._call_prefill("2026-04-10", conn)
        titles = [n["title"] for n in result["teacher_notes"]]
        assert "周五观点" in titles
        assert "周日观点" in titles

    def test_trading_day_returns_no_prev_trade_date(self, tmp_path):
        """交易日 prefill 不返回 prev_trade_date。"""
        conn = self._setup_db(tmp_path)
        result = self._call_prefill("2026-04-10", conn)
        assert result["prev_trade_date"] is None

    def test_non_trading_day_returns_prev_trade_date(self, tmp_path):
        """非交易日（周日）prefill 返回 prev_trade_date = 周五。"""
        conn = self._setup_db(tmp_path)
        result = self._call_prefill("2026-04-12", conn)
        assert result["is_trading_day"] is False
        assert result["prev_trade_date"] == "2026-04-10"


class TestTrendLeaderNonTradingDay:
    """trend-leader daily 非交易日守卫：周末/法定假日跳过扫描，不落池、不推送。

    根因：非交易日触发时 tushare 涨停榜返回「无数据」→ 降级 akshare 返回上一交易日榜单
    且不带日期校验，导致用当日（非交易日）日期落池 + 误推送。守卫对齐 main.py pre/post。
    """

    def _daily_args(self, date, *, dry_run=False, no_push=False):
        import argparse
        return argparse.Namespace(
            trend_leader_command="daily", date=date, sectors=None,
            top_k=5, main_line="l2", top_concepts=8,
            dry_run=dry_run, no_push=no_push,
        )

    @patch("cli.trend_leader._push_to_dingtalk")
    @patch("cli.trend_leader.scanner")
    @patch("cli.trend_leader.get_connection")
    @patch("main.setup_providers")
    def test_daily_skips_on_holiday(self, mock_sp, mock_conn_fn, mock_scanner, mock_push, caplog, tmp_path):
        """工作日法定假日（交易日历 is_open=0）→ 跳过，不调 scanner、不推送。"""
        conn = _make_test_db(tmp_path, holiday="2026-04-06")
        mock_conn_fn.return_value = conn
        reg = _make_registry(is_trade_day_value=False)
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from cli.trend_leader import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._daily_args("2026-04-06"))
        assert "非交易日" in caplog.text
        mock_scanner.run_daily.assert_not_called()
        mock_push.assert_not_called()

    @patch("cli.trend_leader.renderer")
    @patch("cli.trend_leader._push_to_dingtalk")
    @patch("cli.trend_leader.scanner")
    @patch("cli.trend_leader.get_connection")
    @patch("main.setup_providers")
    def test_dry_run_not_guarded_and_no_real_db_write(
        self, mock_sp, mock_conn_fn, mock_scanner, mock_push, mock_renderer, caplog, tmp_path
    ):
        """dry-run 刻意不套守卫，且不写真实库（codex 门2 finding 回归）。

        dry-run 走内存副本、不落池/不推送 → 非交易日也无污染面，照常跑（历史校准）。
        关键：守卫的日历预取若误作用于真实库会破坏「真无副作用」契约，断言真实 trade_calendar 未新增。
        """
        db_file = tmp_path / "test.db"
        conn = _make_test_db(tmp_path)  # 空日历，未缓存任何年份
        mock_conn_fn.return_value = conn
        # cal_data 备好：若 dry-run 误调守卫并写真实库，2024-05-01 就会被写入 trade_calendar。
        reg = _make_registry(is_trade_day_value=False, cal_data=[{"cal_date": "20240501", "is_open": 0}])
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_scanner.run_daily.return_value = {"date": "2024-05-01", "entered": []}
        mock_renderer.render_daily.return_value = "# 报告"
        from cli.trend_leader import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._daily_args("2024-05-01", dry_run=True))
        # dry-run 不守卫：scanner 照常跑（在内存副本上），无「非交易日」跳过日志。
        mock_scanner.run_daily.assert_called_once()
        assert "非交易日" not in caplog.text
        # 真无副作用：真实库 trade_calendar 未被守卫的日历预取写入（_run_daily 已关闭 conn，重开校验）。
        check = sqlite3.connect(str(db_file))
        try:
            n = check.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0]
        finally:
            check.close()
        assert n == 0, f"dry-run 不应写真实 trade_calendar，实际新增 {n} 条"

    @patch("cli.trend_leader.renderer")
    @patch("cli.trend_leader._push_to_dingtalk")
    @patch("cli.trend_leader.scanner")
    @patch("cli.trend_leader.get_connection")
    @patch("main.setup_providers")
    def test_daily_runs_on_trading_day(self, mock_sp, mock_conn_fn, mock_scanner, mock_push, mock_renderer, tmp_path):
        """交易日（is_trade_day=True）→ 正常调 scanner。"""
        conn = _make_test_db(tmp_path)
        mock_conn_fn.return_value = conn
        reg = _make_registry(is_trade_day_value=True)
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_scanner.run_daily.return_value = {"date": "2026-04-07", "entered": []}
        mock_renderer.render_daily.return_value = "# 报告"
        from cli.trend_leader import _run_daily
        _run_daily({}, self._daily_args("2026-04-07", no_push=True))
        mock_scanner.run_daily.assert_called_once()

    @patch("cli.trend_leader.renderer")
    @patch("cli.trend_leader._push_to_dingtalk")
    @patch("cli.trend_leader.scanner")
    @patch("cli.trend_leader.get_connection")
    @patch("main.setup_providers")
    def test_api_failure_does_not_block(self, mock_sp, mock_conn_fn, mock_scanner, mock_push, mock_renderer, caplog, tmp_path):
        """is_trade_day 判定失败且无日历缓存 → 不阻塞（工作日按 weekday 兜底放行）。"""
        conn = _make_test_db(tmp_path)  # 空日历
        mock_conn_fn.return_value = conn
        reg = _make_registry(error="api down")
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_scanner.run_daily.return_value = {"date": "2026-04-07", "entered": []}
        mock_renderer.render_daily.return_value = "# 报告"
        from cli.trend_leader import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._daily_args("2026-04-07", no_push=True))  # 周二
        assert "非交易日" not in caplog.text
        mock_scanner.run_daily.assert_called_once()

    @patch("cli.trend_leader._push_to_dingtalk")
    @patch("cli.trend_leader.scanner")
    @patch("cli.trend_leader.get_connection")
    @patch("main.setup_providers")
    def test_cross_year_date_prefetches_target_year_calendar(
        self, mock_sp, mock_conn_fn, mock_scanner, mock_push, caplog, tmp_path
    ):
        """--date 跨年（非当前年）历史校准：按目标年份预取日历，命中目标年法定假日 → 跳过。

        codex 门2 finding 2 回归：ensure_trade_calendar 默认当前年，跨年 --date 会漏目标年缓存。
        断言 get_trade_calendar 用目标年（2024）date_str 预取，且目标年假日被正确拦截。
        """
        conn = _make_test_db(tmp_path)  # 空日历，强制走 ensure_trade_calendar 预取
        mock_conn_fn.return_value = conn
        # get_trade_calendar 返回目标年(2024)日历且把 2024-05-01 标 is_open=0；is_trade_day 单点失败。
        reg = _make_registry(error="api down", cal_data=[{"cal_date": "20240501", "is_open": 0}])
        reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from cli.trend_leader import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._daily_args("2024-05-01"))
        # 关键断言：日历预取用的是目标年 2024（默认当前年则会是当前年 date_str），证明 year 被正确传入。
        cal_calls = [c for c in reg.call.call_args_list if c.args and c.args[0] == "get_trade_calendar"]
        assert cal_calls, "应调用 get_trade_calendar 预取日历"
        assert any("2024-" in str(c.args[1]) for c in cal_calls), \
            f"应按目标年 2024 预取，实际: {[c.args[1] for c in cal_calls]}"
        assert "非交易日" in caplog.text
        mock_scanner.run_daily.assert_not_called()


class TestSharedIsNonTradingDay:
    """utils.trade_date.is_non_trading_day 共享守卫单测（4 个 CLI 复用的真源）。"""

    def test_holiday_returns_true(self, tmp_path):
        conn = _make_test_db(tmp_path, holiday="2026-04-06")
        from utils.trade_date import is_non_trading_day
        reg = _make_registry(is_trade_day_value=False)
        assert is_non_trading_day(conn, reg, "2026-04-06") is True

    def test_trading_day_returns_false(self, tmp_path):
        conn = _make_test_db(tmp_path)
        from utils.trade_date import is_non_trading_day
        reg = _make_registry(is_trade_day_value=True)
        assert is_non_trading_day(conn, reg, "2026-04-07") is False  # 周二

    def test_failure_fails_open_false(self, tmp_path):
        """判定失败 + 无缓存 → fail-open 返回 False（工作日按 weekday 兜底）。"""
        conn = _make_test_db(tmp_path)
        from utils.trade_date import is_non_trading_day
        reg = _make_registry(error="api down")
        assert is_non_trading_day(conn, reg, "2026-04-07") is False  # 周二兜底=交易日

    def test_failure_rolls_back_partial_calendar(self, tmp_path, monkeypatch):
        """日历导入中途抛错 → fail-open 前回滚半截写入,不污染交易日缓存(codex 门2 finding)。"""
        conn = _make_test_db(tmp_path)
        import utils.trade_date as td

        def _boom(c, registry, year=None):
            c.execute("INSERT INTO trade_calendar (date, is_open) VALUES ('2026-09-30', 0)")  # 未提交
            raise RuntimeError("dirty calendar mid-write")

        monkeypatch.setattr(td, "ensure_trade_calendar", _boom)
        result = td.is_non_trading_day(conn, _make_registry(), "2026-09-30")
        assert result is False  # fail-open
        # 半截写入已回滚:同一连接 SELECT 不应看到未提交行(未回滚则可见)
        n = conn.execute("SELECT COUNT(*) FROM trade_calendar WHERE date='2026-09-30'").fetchone()[0]
        assert n == 0, "守卫异常路径未回滚,半截日历泄漏"


class TestVolumeWatchNonTradingDay:
    def _args(self, date, *, dry_run=False):
        import argparse
        return argparse.Namespace(volume_watch_command="daily", date=date, dry_run=dry_run, refetch=False)

    @patch("cli.volume_watch._push_to_dingtalk")
    @patch("cli.volume_watch.migrate")
    @patch("cli.volume_watch.service")
    @patch("cli.volume_watch.get_connection")
    @patch("main.setup_providers")
    def test_skips_on_holiday(self, mock_sp, mock_conn_fn, mock_service, _mig, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from cli.volume_watch import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06"))
        assert "非交易日" in caplog.text
        mock_service.run_daily.assert_not_called()
        mock_push.assert_not_called()

    @patch("cli.volume_watch._push_to_dingtalk")
    @patch("cli.volume_watch.migrate")
    @patch("cli.volume_watch.service")
    @patch("cli.volume_watch.get_connection")
    @patch("main.setup_providers")
    def test_dry_run_not_guarded(self, mock_sp, mock_conn_fn, mock_service, _mig, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_service.run_daily.return_value = "# md"
        from cli.volume_watch import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06", dry_run=True))
        assert "非交易日" not in caplog.text
        mock_service.run_daily.assert_called_once()

    @patch("cli.volume_watch._push_to_dingtalk")
    @patch("cli.volume_watch.migrate")
    @patch("cli.volume_watch.service")
    @patch("cli.volume_watch.get_connection")
    @patch("main.setup_providers")
    def test_runs_on_trading_day(self, mock_sp, mock_conn_fn, mock_service, _mig, mock_push, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path)
        reg = _make_registry(is_trade_day_value=True); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_service.run_daily.return_value = None  # 无数据,提前 return,免去 push 真调
        from cli.volume_watch import _run_daily
        _run_daily({}, self._args("2026-04-07"))
        mock_service.run_daily.assert_called_once()


class TestMarketTimingNonTradingDay:
    def _args(self, date, *, dry_run=False, no_push=False):
        import argparse
        return argparse.Namespace(market_timing_command="daily", date=date, dry_run=dry_run,
                                  no_push=no_push, pivot_index=None, pivot_date=None)

    @patch("cli.market_timing._push_to_dingtalk")
    @patch("cli.market_timing.formatter")
    @patch("cli.market_timing.scanner")
    @patch("cli.market_timing.get_connection")
    @patch("main.setup_providers")
    def test_skips_on_holiday(self, mock_sp, mock_conn_fn, mock_scanner, _fmt, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from cli.market_timing import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06"))
        assert "非交易日" in caplog.text
        mock_scanner.run_daily.assert_not_called()
        mock_push.assert_not_called()

    @patch("cli.market_timing._push_to_dingtalk")
    @patch("cli.market_timing.formatter")
    @patch("cli.market_timing.scanner")
    @patch("cli.market_timing.get_connection")
    @patch("main.setup_providers")
    def test_no_push_still_guarded_on_holiday(self, mock_sp, mock_conn_fn, mock_scanner, _fmt, mock_push, caplog, tmp_path):
        """--no-push 仍会落库 → 非交易日必须守卫（与 dry-run 区分）。"""
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        from cli.market_timing import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06", no_push=True))
        assert "非交易日" in caplog.text
        mock_scanner.run_daily.assert_not_called()

    @patch("cli.market_timing._push_to_dingtalk")
    @patch("cli.market_timing.formatter")
    @patch("cli.market_timing.scanner")
    @patch("cli.market_timing.get_connection")
    @patch("main.setup_providers")
    def test_dry_run_not_guarded(self, mock_sp, mock_conn_fn, mock_scanner, mock_fmt, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False); reg.initialize_all.return_value = {}
        mock_sp.return_value = reg
        mock_scanner.run_daily.return_value = {"signals": []}
        mock_fmt.render_daily.return_value = "# md"
        from cli.market_timing import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06", dry_run=True))
        assert "非交易日" not in caplog.text
        mock_scanner.run_daily.assert_called_once()


class TestSectorCorrelationNonTradingDay:
    def _args(self, date, *, dry_run=False):
        import argparse
        return argparse.Namespace(sector_correlation_command="daily", date=date, dry_run=dry_run,
                                  windows=None, top_industries=5, top_concepts=5, activity_days=5,
                                  indices=None, no_concept=False)

    @patch("cli.sector_correlation._push_to_dingtalk")
    @patch("cli.sector_correlation.service")
    @patch("cli.sector_correlation._setup_tushare")
    @patch("cli.sector_correlation.get_connection")
    def test_skips_on_holiday(self, mock_conn_fn, mock_setup, mock_service, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False)
        mock_setup.return_value = (reg, MagicMock())  # (registry, provider)
        from cli.sector_correlation import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06"))
        assert "非交易日" in caplog.text
        mock_service.run_daily.assert_not_called()
        mock_push.assert_not_called()

    @patch("cli.sector_correlation._push_to_dingtalk")
    @patch("cli.sector_correlation.service")
    @patch("cli.sector_correlation._setup_tushare")
    @patch("cli.sector_correlation.get_connection")
    def test_dry_run_not_guarded(self, mock_conn_fn, mock_setup, mock_service, mock_push, caplog, tmp_path):
        mock_conn_fn.return_value = _make_test_db(tmp_path, holiday="2026-04-06")
        reg = _make_registry(is_trade_day_value=False)
        mock_setup.return_value = (reg, MagicMock())
        mock_service.run_daily.return_value = None  # 无数据提前 return
        from cli.sector_correlation import _run_daily
        with caplog.at_level(logging.WARNING):
            _run_daily({}, self._args("2026-04-06", dry_run=True))
        assert "非交易日" not in caplog.text
        mock_service.run_daily.assert_called_once()
