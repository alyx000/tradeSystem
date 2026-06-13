"""趋势主升观察清单 Markdown 渲染单测（Stage 3）。

渲染器读 summary（scanner.run_daily 产物）+ 回查 pool 富化名称/在池天数/退出原因，
产出**盘后只读观察清单**：全部标 [判断]、守红线（不出价位、不给买卖建议）。
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from db.schema import init_schema
from services.trend_leader import pool, renderer


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _seed(conn):
    """入池一只(entered)、一只在池信号、一只已退池。"""
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-12", date="2026-06-12")
    pool.record(conn, code="300750", name="宁德时代", sw_l2="电池",
                first_limit_date="2026-06-09", date="2026-06-09")
    pool.touch(conn, "300750", date="2026-06-12")  # days_in_pool→推进
    pool.record(conn, code="000001", name="平安银行", sw_l2="股份制银行Ⅱ",
                first_limit_date="2026-06-08", date="2026-06-08")
    pool.mark_exited(conn, "000001", date="2026-06-12", reason="收盘跌破MA10")


def _summary(**over):
    base = {
        "date": "2026-06-12", "limit_up": 42,
        "main_sectors": ["半导体", "玻璃玻纤", "电池"], "degraded_main": False,
        "candidates": 3, "entered": ["600552"], "refreshed": [], "exited": ["000001"],
        "in_pool_signals": [{"code": "300750", "shrink_pullback_buy": True,
                             "near_ma5": True, "overheat": False}],
        "data_errors": [], "source_errors": [],
    }
    base.update(over)
    return base


def test_render_daily_has_judgment_marker_and_redline(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "[判断]" in md
    assert "不构成买卖建议" in md  # 红线声明


def test_render_daily_renders_funnel_counts(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "42" in md            # 涨停总数
    assert "3" in md             # candidates(涨停∩主线)
    assert "玻璃玻纤" in md       # 主线板块


def test_render_daily_entered_section_lists_name_sector(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "凯盛科技" in md       # entered 跨表富化名称
    assert "600552" in md


def test_render_daily_in_pool_signal_marked(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "宁德时代" in md       # in_pool 信号股富化名称
    assert "缩量阴线" in md       # 命中 shrink_pullback_buy=True 的信号标签出现


def test_render_daily_exited_section_shows_reason(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "平安银行" in md
    assert "收盘跌破MA10" in md   # 退出原因


def test_render_daily_empty_states(conn):
    md = renderer.render_daily(conn, _summary(
        entered=[], exited=[], in_pool_signals=[]))
    assert "今日无新入池" in md
    assert "今日无退池" in md


def test_render_daily_no_price_or_buy_advice(conn):
    """红线：不出价位、不给目标价/买入价。"""
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "目标价" not in md
    assert "买入价" not in md
    assert not re.search(r"\d+(\.\d+)?\s*元", md)  # 无「N 元」价位


def test_render_daily_degraded_main_annotated(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary(degraded_main=True))
    assert "回退" in md           # 主线当日缺失回退最近一日


def test_render_daily_ops_errors_surfaced(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary(
        source_errors=["limit_up"], data_errors=["600552"]))
    assert "limit_up" in md
    assert "600552" in md


def test_render_pool_lists_rows(conn):
    _seed(conn)
    rows = pool.list_pool(conn, status="active")
    out = renderer.render_pool(rows)
    assert "凯盛科技" in out and "宁德时代" in out
    assert "平安银行" not in out  # 只列 active


def test_render_pool_empty(conn):
    out = renderer.render_pool([])
    assert "池为空" in out or "无在池" in out
