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
        "main_sector_window": 3, "main_sector_required_hits": 2,
        "main_sector_snapshot_count": 3, "main_sector_source_date": "2026-06-12",
        "main_sector_status": "exact", "main_line": "l2", "main_concepts": [],
        "mainline_llm": {"enabled": False, "status": "not_applicable", "accepted_concepts": []},
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


def test_render_daily_candidate_label_covers_dual15(conn):
    """概览候选标签为「加速∩主线候选（涨停∪双创15%）」，不再误导为「涨停∩主线候选」。"""
    md = renderer.render_daily(conn, _summary(
        limit_up=0, candidates=1, entered=[], refreshed=[], in_pool_signals=[], exited=[]))
    assert "加速∩主线候选" in md
    assert "涨停∩主线候选" not in md


def test_render_daily_renders_funnel_counts(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "42" in md            # 涨停总数
    assert "3" in md             # candidates(涨停∩主线)
    assert "玻璃玻纤" in md       # 主线板块


def test_render_daily_shows_stable_window_and_preserves_sector_order(conn):
    md = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        main_sectors=["半导体", "通信设备", "元件", "光学光电子"],
    ))
    assert "申万二级近3个有效快照 Top-K 至少2次∪手工" in md
    assert "半导体、通信设备、元件、光学光电子" in md
    assert "目标日快照可用" in md


def test_render_daily_llm_failure_explains_closed_branch(conn):
    md = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={
            "enabled": True,
            "status": "fallback_l2",
            "reason": "startup_failed",
            "accepted_concepts": [],
        },
    ))
    assert "LLM调用失败，概念分支已关闭（原因：startup_failed）" in md


def test_render_daily_distinguishes_invalid_llm_output(conn):
    md = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={
            "enabled": True,
            "status": "fallback_l2",
            "reason": "invalid_output",
            "accepted_concepts": [],
        },
    ))
    assert "LLM输出非法，概念分支已关闭（原因：invalid_output）" in md
    assert "LLM调用失败" not in md


def test_render_daily_distinguishes_disabled_and_valid_empty_llm(conn):
    disabled = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={"enabled": False, "status": "disabled", "accepted_concepts": ["PCB概念"]},
    ))
    valid_empty = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={"enabled": True, "status": "ok", "accepted_concepts": []},
    ))
    assert "人工禁用 LLM，使用机械概念分支" in disabled
    assert "LLM未确认概念分支" in valid_empty


def test_render_daily_llm_skipped_and_non_hybrid_visibility(conn):
    skipped = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={
            "enabled": True,
            "status": "skipped_empty_concepts",
            "accepted_concepts": [],
        },
    ))
    l2 = renderer.render_daily(conn, _summary(
        main_line="l2",
        mainline_llm={"enabled": True, "status": "fallback_l2", "reason": "timeout"},
    ))
    mechanical = renderer.render_daily(conn, _summary(
        main_line="l2+concept",
        mainline_llm={"enabled": False, "status": "not_applicable"},
    ))

    assert "无可供 LLM 过滤的机械概念" in skipped
    assert "LLM主线过滤" not in l2
    assert "LLM主线过滤" not in mechanical


def test_render_daily_main_sector_source_states_are_truthful(conn):
    fallback = renderer.render_daily(conn, _summary(
        main_sector_status="fallback",
        main_sector_source_date="2026-06-11",
        main_sector_snapshot_count=1,
        main_sector_required_hits=1,
        degraded_main=True,
    ))
    missing = renderer.render_daily(conn, _summary(
        main_sectors=["IT服务Ⅱ"],
        main_sector_status="missing",
        main_sector_source_date=None,
        main_sector_snapshot_count=0,
        main_sector_required_hits=1,
        degraded_main=True,
    ))

    assert "目标日不可用，使用2026-06-11快照" in fallback
    assert "历史仅1条有效快照" in fallback
    assert "无可用集中度快照，仅保留手工板块" in missing
    assert "已回退最近一日" not in missing


def test_render_daily_legacy_summary_keeps_legacy_mainline_label(conn):
    legacy = _summary(degraded_main=True)
    for key in (
        "main_sector_window",
        "main_sector_required_hits",
        "main_sector_snapshot_count",
        "main_sector_source_date",
        "main_sector_status",
    ):
        legacy.pop(key)

    md = renderer.render_daily(conn, legacy)

    assert "申万二级 Top-K∪手工" in md
    assert "近3个有效快照" not in md
    assert "已回退最近一日" in md


def test_render_daily_entered_section_lists_name_sector(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "凯盛科技" in md       # entered 跨表富化名称
    assert "600552" in md


def test_render_daily_entered_annotates_concept_branch(conn):
    """概念分支入池（二级不在主线）→ 申万二级列标注「·分支:概念名」说明为何算主线。"""
    pool.record(conn, code="301628", name="强达电路", sw_l2="其他电子Ⅱ",
                first_limit_date="2026-06-12", date="2026-06-12",
                signal_json={"entry_trigger": "涨停", "branch_concepts": ["PCB概念"]})
    md = renderer.render_daily(conn, _summary(
        main_sectors=["半导体"], entered=["301628"], refreshed=[],
        in_pool_signals=[], exited=[]))
    assert "其他电子Ⅱ·分支:PCB概念" in md   # 标注分支来源


def test_render_daily_entered_shows_accel_date_and_trigger(conn):
    """GAP A：入池表头为「首次加速日」(board-aware)，非旧「首次涨停日」；触发列区分涨停/双创15%。"""
    pool.record(conn, code="688512", name="慧智微", sw_l2="半导体",
                first_limit_date="2026-06-12", date="2026-06-12",
                signal_json={"entry_trigger": "双创15%加速"})
    md = renderer.render_daily(conn, _summary(
        entered=["688512"], refreshed=[], in_pool_signals=[], exited=[]))
    assert "首次加速日" in md
    assert "首次涨停日" not in md       # 旧标签已替换
    assert "首次涨停加速" not in md     # 区块标题也 board-aware（双创15%票不被标涨停）
    assert "双创15%加速" in md          # 触发类型展示


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
        entered=[], refreshed=[], exited=[], in_pool_signals=[]))
    assert "今日无新入池" in md
    assert "今日无退池" in md


def test_render_daily_refreshed_shown_as_entry(conn):
    """同日重跑/推送失败重试：入池票变 refreshed 仍须出现在「今日新入池」，不能消失。"""
    _seed(conn)
    md = renderer.render_daily(conn, _summary(
        entered=[], refreshed=["300750"], in_pool_signals=[]))
    assert "宁德时代" in md          # refreshed 命中仍渲染
    assert "今日无新入池" not in md   # 不能误报无新增
    assert "今日新入池：1" in md      # 概览计数与表格一致（不能显示 0 却列出该票）


def test_render_daily_exit_section_marked_judgment(conn):
    """红线：退池区块（趋势破坏=派生判断）必须带 [判断] 标记。"""
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "今日退池（趋势破坏）[判断]" in md


def test_render_daily_no_price_or_buy_advice(conn):
    """红线：不出价位、不给目标价/买入价。"""
    _seed(conn)
    md = renderer.render_daily(conn, _summary())
    assert "目标价" not in md
    assert "买入价" not in md
    assert not re.search(r"\d+(\.\d+)?\s*元", md)  # 无「N 元」价位


def test_render_daily_no_buy_sell_action_terms(conn):
    """红线：用户可见 MD 只描述客观状态，不出现可操作买卖动作词。

    «不构成买卖建议» 是免责声明（含『买卖』）属允许；这里挡的是暗示动作的具体词。
    """
    _seed(conn)
    md = renderer.render_daily(conn, _summary(degraded_main=True,
                                              source_errors=["limit_up"], data_errors=["600552"]))
    for term in ["低吸", "买点", "买入", "卖出", "了结", "止盈", "止损", "目标价", "见顶"]:
        assert term not in md, f"红线违规：渲染出现买卖动作词「{term}」"


def test_render_daily_degraded_main_annotated(conn):
    _seed(conn)
    md = renderer.render_daily(conn, _summary(
        degraded_main=True,
        main_sector_status="fallback",
        main_sector_source_date="2026-06-11",
    ))
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
