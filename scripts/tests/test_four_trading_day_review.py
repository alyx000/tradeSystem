from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

from automations import four_trading_day_review as review


def _seed_thesis_review_db(tmp_path, reviews: list[dict]) -> None:
    """在 tmp_path/data/trade.db 建 thesis_review 表并插真行，走真实 SQL 读取路径。

    PROJECT_ROOT=tmp_path 时 _db_uri_readonly() 正好指向该库。
    """
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "trade.db"))
    conn.execute(
        """
        CREATE TABLE thesis_review (
            thesis_id INTEGER PRIMARY KEY NOT NULL,
            executed_as_planned INTEGER NOT NULL,
            exit_trigger TEXT,
            lessons TEXT,
            discipline_score INTEGER,
            realized_pnl_pct REAL,
            realized_pnl_amount REAL,
            holding_days INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            input_by TEXT NOT NULL
        )
        """
    )
    for r in reviews:
        conn.execute(
            "INSERT INTO thesis_review (thesis_id, executed_as_planned, exit_trigger, "
            "lessons, discipline_score, realized_pnl_amount, input_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                r["thesis_id"],
                r["executed_as_planned"],
                r.get("exit_trigger"),
                r.get("lessons"),
                r.get("discipline_score"),
                r.get("realized_pnl_amount"),
                r.get("input_by", "tester"),
            ),
        )
    conn.commit()
    conn.close()


def test_load_thesis_reviews_returns_empty_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    assert review._load_thesis_reviews() == {}


_EIGHT_DAYS = [
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
    "2026-05-23",
    "2026-05-26",
    "2026-05-27",
    "2026-05-28",
    "2026-05-29",
]


def test_open_thesis_appears_in_failure_self_check(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-27",
            "exec_time": "10:00:00",
            "stock_code": "300999",
            "stock_name": "测试买",
            "direction": "buy",
            "shares": 100,
            "amount": 1000.0,
            "net_amount": -1000.0,
            "total_fees": 0.0,
            "thesis_id": None,
        },
    ]
    theses = [
        {
            "id": 10,
            "account_id": "default",
            "status": "open",
            "stock_code": "600111",
            "stock_name": "北方稀土",
            "opened_at": "2026-05-15",
            "closed_at": None,
            "entry_reason": "稀土主线龙头",
            "failure_condition": "跌破20日线",
            "stop_loss": 10.5,
            "target_price": 15.0,
            "trade_mode": "swing",
            "sector": "稀土",
            "planned_position_pct": 20.0,
        },
    ]

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return theses
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=False)
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## 持仓失效自查清单（需人工核对）" in report_md
    assert "跌破20日线" in report_md
    assert "稀土主线龙头" in report_md
    assert "10.50" in report_md  # stop_loss
    assert "15.00" in report_md  # target_price
    assert "14" in report_md  # 持仓天数 = 2026-05-29 - 2026-05-15


def _closed_thesis(tid: int, code: str, name: str, failure: str, closed_at: str = "2026-05-27") -> dict:
    return {
        "id": tid,
        "account_id": "default",
        "status": "closed",
        "stock_code": code,
        "stock_name": name,
        "opened_at": "2026-05-20",
        "closed_at": closed_at,
        "entry_reason": "测试逻辑",
        "failure_condition": failure,
        "stop_loss": None,
        "target_price": None,
        "trade_mode": "swing",
        "sector": "测试",
        "planned_position_pct": 10.0,
    }


def test_discipline_review_buckets(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-27",
            "exec_time": "10:00:00",
            "stock_code": "300999",
            "stock_name": "测试买",
            "direction": "buy",
            "shares": 100,
            "amount": 1000.0,
            "net_amount": -1000.0,
            "total_fees": 0.0,
            "thesis_id": None,
        },
    ]
    theses = [
        _closed_thesis(11, "600222", "未复盘票", "量能萎缩"),
        _closed_thesis(12, "600333", "偏离票", "破位"),
        _closed_thesis(13, "600444", "合规票", "破位"),
        _closed_thesis(14, "600555", "低分票", "破位"),
    ]
    reviews = [
        {"thesis_id": 12, "executed_as_planned": 0, "exit_trigger": "discretionary",
         "discipline_score": 2, "realized_pnl_amount": -1200.0, "lessons": "没按计划止损扛单"},
        {"thesis_id": 13, "executed_as_planned": 1, "exit_trigger": "target_hit",
         "discipline_score": 5, "realized_pnl_amount": 3000.0, "lessons": "按计划止盈"},
        {"thesis_id": 14, "executed_as_planned": 1, "exit_trigger": "stop_hit",
         "discipline_score": 2, "realized_pnl_amount": -500.0, "lessons": "止损偏慢"},
    ]
    _seed_thesis_review_db(tmp_path, reviews)

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return theses
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=False)
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## 纪律执行回顾" in report_md
    # 未复盘桶
    assert "600222" in report_md
    assert "量能萎缩" in report_md
    # 偏离（executed_as_planned=0）+ 低分（discipline_score<=2）桶
    assert "600333" in report_md
    assert "没按计划止损扛单" in report_md
    assert "600555" in report_md
    assert "止损偏慢" in report_md
    # 合规（executed_as_planned=1 且高分）不出现在任何地方
    assert "600444" not in report_md
    assert "按计划止盈" not in report_md


def test_no_open_no_closed_renders_empty_fallbacks(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-27",
            "exec_time": "10:00:00",
            "stock_code": "300999",
            "stock_name": "测试买",
            "direction": "buy",
            "shares": 100,
            "amount": 1000.0,
            "net_amount": -1000.0,
            "total_fees": 0.0,
            "thesis_id": None,
        },
    ]

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return []
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=False)
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## 持仓失效自查清单（需人工核对）" in report_md
    assert "（当前无 open 状态 thesis，无需自查）" in report_md
    assert "## 纪律执行回顾" in report_md
    assert "（本期无已平仓 thesis 需复盘）" in report_md


def test_push_summary_includes_failure_discipline_segment(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1, "account_id": "default", "biz_date": "2026-05-26", "exec_time": "09:40:00",
            "stock_code": "600333", "stock_name": "偏离票", "direction": "buy",
            "shares": 200, "amount": 2000.0, "net_amount": -2000.0, "total_fees": 0.0, "thesis_id": 12,
        },
        {
            "id": 2, "account_id": "default", "biz_date": "2026-05-27", "exec_time": "10:10:00",
            "stock_code": "600333", "stock_name": "偏离票", "direction": "sell",
            "shares": 200, "amount": 1800.0, "net_amount": 1800.0, "total_fees": 0.0, "thesis_id": 12,
        },
    ]
    theses = [
        {
            "id": 10, "account_id": "default", "status": "open", "stock_code": "600111",
            "stock_name": "北方稀土", "opened_at": "2026-05-15", "closed_at": None,
            "entry_reason": "稀土主线", "failure_condition": "跌破20日线", "stop_loss": 10.5,
            "target_price": 15.0, "trade_mode": "swing", "sector": "稀土", "planned_position_pct": 20.0,
        },
        _closed_thesis(11, "600222", "未复盘票", "量能萎缩"),
        _closed_thesis(12, "600333", "偏离票", "破位"),
    ]
    reviews = [
        {"thesis_id": 12, "executed_as_planned": 0, "exit_trigger": "discretionary",
         "discipline_score": 2, "realized_pnl_amount": -200.0, "lessons": "扛单"},
    ]
    _seed_thesis_review_db(tmp_path, reviews)
    sent: dict[str, str] = {}

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return theses
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    class FakePusher:
        def __init__(self, config):
            pass

        def initialize(self):
            return True

        def send_markdown(self, title, markdown):
            sent["title"] = title
            sent["markdown"] = markdown
            return True

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)
    fake_dingtalk_module = ModuleType("scripts.pushers.dingtalk_pusher")
    fake_dingtalk_module.DingTalkPusher = FakePusher
    monkeypatch.setitem(sys.modules, "scripts.pushers.dingtalk_pusher", fake_dingtalk_module)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=True)

    assert result["push_ok"] is True
    markdown = sent["markdown"]
    assert "### 失效与纪律" in markdown
    assert "持仓自查 1 项" in markdown
    assert "未复盘 1" in markdown
    assert "偏离/低分 1" in markdown
    # 摘要仍不得泄漏英文字段名（与既有断言一致）
    assert "failure_condition" not in markdown
    assert "planned_position_pct" not in markdown
    assert "thesis_id" not in markdown


def _generate_report(tmp_path, monkeypatch, *, theses, reviews=None, rows=None) -> str:
    if reviews:
        _seed_thesis_review_db(tmp_path, reviews)
    exec_rows = rows if rows is not None else [
        {
            "id": 1, "account_id": "default", "biz_date": "2026-05-27", "exec_time": "10:00:00",
            "stock_code": "300999", "stock_name": "占位买", "direction": "buy",
            "shares": 100, "amount": 1000.0, "net_amount": -1000.0, "total_fees": 0.0, "thesis_id": None,
        }
    ]

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return theses
        if cmd[2:4] == ["executions", "list"]:
            return exec_rows
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)
    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=False)
    return Path(result["report_path"]).read_text(encoding="utf-8")


def test_discipline_closed_at_on_window_boundary_counts_as_current(tmp_path, monkeypatch):
    # closed_at 等于 current_start（2026-05-26）应进本期纪律回顾，不进 backlog
    theses = [_closed_thesis(20, "600230", "边界票", "破位", closed_at="2026-05-26")]
    report_md = _generate_report(tmp_path, monkeypatch, theses=theses)
    assert "600230" in report_md
    assert "另有" not in report_md  # 未计入历史 backlog


def test_discipline_executed_as_planned_2_renders_in_deviation_bucket(tmp_path, monkeypatch):
    # executed_as_planned=2 应落「未执行」桶（即使纪律分不低）
    theses = [_closed_thesis(21, "600231", "未执行票", "破位")]
    reviews = [
        {"thesis_id": 21, "executed_as_planned": 2, "exit_trigger": "forced",
         "discipline_score": 3, "realized_pnl_amount": -800.0, "lessons": "没执行计划"},
    ]
    report_md = _generate_report(tmp_path, monkeypatch, theses=theses, reviews=reviews)
    assert "600231" in report_md
    assert "未执行" in report_md  # _EP_LABEL[2]
    assert "没执行计划" in report_md


def test_push_summary_excludes_cashflow_but_report_keeps_it(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1, "account_id": "default", "biz_date": "2026-05-26", "exec_time": "09:40:00",
            "stock_code": "600333", "stock_name": "测试", "direction": "buy",
            "shares": 200, "amount": 2000.0, "net_amount": -2000.0, "total_fees": 0.0, "thesis_id": None,
        },
        {
            "id": 2, "account_id": "default", "biz_date": "2026-05-27", "exec_time": "10:10:00",
            "stock_code": "600333", "stock_name": "测试", "direction": "sell",
            "shares": 200, "amount": 1800.0, "net_amount": 1800.0, "total_fees": 0.0, "thesis_id": None,
        },
    ]
    sent: dict[str, str] = {}

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return []
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    class FakePusher:
        def __init__(self, config):
            pass

        def initialize(self):
            return True

        def send_markdown(self, title, markdown):
            sent["markdown"] = markdown
            return True

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)
    fake_dingtalk_module = ModuleType("scripts.pushers.dingtalk_pusher")
    fake_dingtalk_module.DingTalkPusher = FakePusher
    monkeypatch.setitem(sys.modules, "scripts.pushers.dingtalk_pusher", fake_dingtalk_module)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=True)

    # 钉钉短摘要：现金流已撤掉，但盈亏/胜率仍在；闭环改名为「已实现盈亏」
    markdown = sent["markdown"]
    assert "现金流" not in markdown
    assert "已实现盈亏" in markdown
    assert "胜率" in markdown
    # 本地完整报告：现金流保留（仅短摘要精简）
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "现金流" in report_md


def test_pl_ratio_shows_infinity_when_no_losses(tmp_path, monkeypatch):
    # 本期全胜无亏损：盈亏比应显示 ∞（与 PF 口径一致），而非 -
    rows = [
        {
            "id": 1, "account_id": "default", "biz_date": "2026-05-26", "exec_time": "09:40:00",
            "stock_code": "600333", "stock_name": "测试", "direction": "buy",
            "shares": 200, "amount": 2000.0, "net_amount": -2000.0, "total_fees": 0.0, "thesis_id": None,
        },
        {
            "id": 2, "account_id": "default", "biz_date": "2026-05-27", "exec_time": "10:10:00",
            "stock_code": "600333", "stock_name": "测试", "direction": "sell",
            "shares": 200, "amount": 2400.0, "net_amount": 2400.0, "total_fees": 0.0, "thesis_id": None,
        },
    ]
    sent: dict[str, str] = {}

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return []
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    class FakePusher:
        def __init__(self, config):
            pass

        def initialize(self):
            return True

        def send_markdown(self, title, markdown):
            sent["markdown"] = markdown
            return True

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(review, "_try_get_last_n_trade_days", lambda n, as_of: _EIGHT_DAYS)
    monkeypatch.setattr(review, "_run_json", fake_run_json)
    fake_dingtalk_module = ModuleType("scripts.pushers.dingtalk_pusher")
    fake_dingtalk_module.DingTalkPusher = FakePusher
    monkeypatch.setitem(sys.modules, "scripts.pushers.dingtalk_pusher", fake_dingtalk_module)

    result = review.generate(run_date=date(2026, 5, 29), account="default", limit=10000, push=True)

    assert "盈亏比 ∞" in sent["markdown"]  # 摘要
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "| 盈亏比 | ∞ |" in report_md  # 本地已闭环表（inf 渲染为 ∞ 而非 "inf"）


def test_discipline_out_of_window_unreviewed_counts_as_backlog(tmp_path, monkeypatch):
    # 窗口外已平仓且无 review → 只增 backlog 计数，不进本期明细表
    theses = [
        _closed_thesis(22, "600240", "本期未复盘票", "破位"),  # 窗口内，触发 section 渲染
        _closed_thesis(23, "600241", "窗口外票", "破位", closed_at="2026-05-10"),
    ]
    report_md = _generate_report(tmp_path, monkeypatch, theses=theses)
    assert "另有 1 笔历史已平仓 thesis 仍未复盘" in report_md
    assert "600241" not in report_md  # 窗口外票不进任何明细
    assert "600240" in report_md  # 窗口内未复盘票进未复盘表


def test_four_day_review_counts_same_stock_same_direction_as_one_action(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-20",
            "exec_time": "10:00:00",
            "stock_code": "688143",
            "stock_name": "长盈通",
            "direction": "sell",
            "shares": 54,
            "amount": 540.0,
            "total_fees": 0.0,
        },
        {
            "id": 2,
            "account_id": "default",
            "biz_date": "2026-05-20",
            "exec_time": "10:00:00",
            "stock_code": "688143",
            "stock_name": "长盈通",
            "direction": "sell",
            "shares": 146,
            "amount": 1460.0,
            "total_fees": 0.0,
        },
        {
            "id": 3,
            "account_id": "default",
            "biz_date": "2026-05-20",
            "exec_time": "14:00:00",
            "stock_code": "002008",
            "stock_name": "大族激光",
            "direction": "buy",
            "shares": 100,
            "amount": 1000.0,
            "total_fees": 0.0,
        },
    ]

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return []
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        review,
        "_try_get_last_n_trade_days",
        lambda n, as_of: [
            "2026-05-11",
            "2026-05-12",
            "2026-05-13",
            "2026-05-14",
            "2026-05-15",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
        ],
    )
    monkeypatch.setattr(review, "_run_json", fake_run_json)

    result = review.generate(
        run_date=date(2026, 5, 20),
        account="default",
        limit=10000,
        push=False,
    )

    report_md = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "| 本期 2026-05-15~2026-05-20 | 2 | 1 | 1 |" in report_md
    assert "| 2026-05-20 | 1 | 1 | 1,000.00 | 2,000.00 |" in report_md
    assert "sell 688143 长盈通 200@10.00" in report_md


def test_four_day_review_push_summary_includes_medium_context(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-19",
            "exec_time": "09:40:00",
            "stock_code": "688143",
            "stock_name": "长盈通",
            "direction": "buy",
            "shares": 200,
            "amount": 25700.0,
            "net_amount": -25700.0,
            "total_fees": 0.0,
            "thesis_id": 2,
        },
        {
            "id": 2,
            "account_id": "default",
            "biz_date": "2026-05-20",
            "exec_time": "10:10:00",
            "stock_code": "688143",
            "stock_name": "长盈通",
            "direction": "sell",
            "shares": 200,
            "amount": 28000.0,
            "net_amount": 28000.0,
            "total_fees": 0.0,
            "thesis_id": 2,
        },
        {
            "id": 3,
            "account_id": "default",
            "biz_date": "2026-05-21",
            "exec_time": "13:20:00",
            "stock_code": "601991",
            "stock_name": "大唐发电",
            "direction": "buy",
            "shares": 2600,
            "amount": 21450.0,
            "net_amount": -21450.0,
            "total_fees": 0.0,
            "thesis_id": 3,
        },
        {
            "id": 4,
            "account_id": "default",
            "biz_date": "2026-05-22",
            "exec_time": "10:30:00",
            "stock_code": "601991",
            "stock_name": "大唐发电",
            "direction": "sell",
            "shares": 2600,
            "amount": 19100.0,
            "net_amount": 19100.0,
            "total_fees": 0.0,
            "thesis_id": 3,
        },
        {
            "id": 5,
            "account_id": "default",
            "biz_date": "2026-05-22",
            "exec_time": "14:30:00",
            "stock_code": "159516",
            "stock_name": "半导设备",
            "direction": "buy",
            "shares": 10000,
            "amount": 12400.0,
            "net_amount": -12400.0,
            "total_fees": 0.0,
            "thesis_id": None,
        },
    ]
    theses = [
        {"id": 2, "status": "closed", "trade_mode": "dip", "sector": "光通信上游"},
        {"id": 3, "status": "closed", "trade_mode": "sentiment_relay", "sector": "电力"},
    ]
    sent: dict[str, str] = {}

    def fake_run_json(cmd: list[str]):
        if cmd[2:4] == ["db", "thesis-list"]:
            return theses
        if cmd[2:4] == ["executions", "list"]:
            return rows
        raise AssertionError(f"unexpected command: {cmd}")

    class FakePusher:
        def __init__(self, config):
            pass

        def initialize(self):
            return True

        def send_markdown(self, title, markdown):
            sent["title"] = title
            sent["markdown"] = markdown
            return True

    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        review,
        "_try_get_last_n_trade_days",
        lambda n, as_of: [
            "2026-05-13",
            "2026-05-14",
            "2026-05-15",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
            "2026-05-21",
            "2026-05-22",
        ],
    )
    monkeypatch.setattr(review, "_run_json", fake_run_json)
    fake_dingtalk_module = ModuleType("scripts.pushers.dingtalk_pusher")
    fake_dingtalk_module.DingTalkPusher = FakePusher
    monkeypatch.setitem(sys.modules, "scripts.pushers.dingtalk_pusher", fake_dingtalk_module)

    result = review.generate(
        run_date=date(2026, 5, 22),
        account="default",
        limit=10000,
        push=True,
    )

    assert result["push_ok"] is True
    assert sent["title"] == "最近4个交易日交易复盘"
    markdown = sent["markdown"]
    assert "### 逐日节奏" in markdown
    assert "- 2026-05-20：买0/卖1" in markdown
    assert "### Top 盈亏" in markdown
    assert "688143 长盈通" in markdown
    assert "601991 大唐发电" in markdown
    assert "### 需核查" in markdown
    assert "思路核查：3项/5行" in markdown
    assert "159516" in markdown
    assert "### 复盘问题" in markdown
    assert "核对止盈/止损与仓位执行" in markdown
    assert "failure_condition" not in markdown
    assert "planned_position_pct" not in markdown
    assert "thesis_id" not in markdown
    assert "本地完整报告：" in markdown
