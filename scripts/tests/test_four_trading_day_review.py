from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import ModuleType

from automations import four_trading_day_review as review


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
