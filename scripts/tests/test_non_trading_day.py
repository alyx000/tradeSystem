"""非交易日拦截逻辑的单元测试。"""
from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from providers.base import DataResult


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
