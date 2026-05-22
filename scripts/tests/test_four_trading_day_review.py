from __future__ import annotations

from datetime import date
from pathlib import Path

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
