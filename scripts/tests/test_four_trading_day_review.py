from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

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


def _holding_snapshot(day: str, *members: dict) -> dict[str, dict]:
    return {
        day: {
            "status": "success",
            "reason": None,
            "members": {
                str(member["stock_code"]).split(".", 1)[0]: {
                    "stock_code": str(member["stock_code"]).split(".", 1)[0],
                    "stock_name": member.get(
                        "stock_name",
                        f"票{str(member['stock_code']).split('.', 1)[0]}",
                    ),
                    "shares": member.get("shares", 100),
                    "change_pct": member.get("change_pct"),
                    "error": member.get("error", ""),
                }
                for member in members
            },
        }
    }


def _execution(
    *,
    row_id: int,
    trade_date: str,
    exec_time: str | None,
    code: str,
    direction: str,
    shares: int = 100,
    balance_after: int | None = None,
    broker_contract_no: str | None = None,
    broker_trade_no: str | None = None,
    price: float | None = None,
    amount: float | None = None,
    import_run_id: str | None = None,
    source_file: str | None = None,
    thesis_id: int | None = None,
) -> dict:
    return {
        "id": row_id,
        "account_id": "default",
        "biz_date": trade_date,
        "exec_time": exec_time,
        "stock_code": code,
        "stock_name": f"票{code}",
        "direction": direction,
        "shares": shares,
        "balance_after": balance_after,
        "broker_contract_no": broker_contract_no,
        "broker_trade_no": broker_trade_no,
        "price": price,
        "amount": amount,
        "import_run_id": import_run_id,
        "source_file": source_file,
        "thesis_id": thesis_id,
    }


def test_weak_sell_order_classifies_unique_tied_and_known_weaker():
    trade_date = "2026-05-27"
    prior = {trade_date: "2026-05-26"}
    sell = _execution(
        row_id=1,
        trade_date=trade_date,
        exec_time="10:00:00",
        code="600001.SH",
        direction="sell",
    )

    unique = review._analyze_weak_sell_order(
        rows=[sell],
        current_days=[trade_date],
        prior_day_by_day=prior,
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -2.0},
        ),
    )
    tied = review._analyze_weak_sell_order(
        rows=[sell],
        current_days=[trade_date],
        prior_day_by_day=prior,
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -5.0},
        ),
    )
    weaker_retained = review._analyze_weak_sell_order(
        rows=[sell],
        current_days=[trade_date],
        prior_day_by_day=prior,
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -2.0},
            {"stock_code": "600002", "change_pct": -5.0},
        ),
    )

    assert unique[0]["status"] == "sold_weakest"
    assert unique[0]["rank"] == 1
    assert tied[0]["status"] == "tied_weakest"
    assert weaker_retained[0]["status"] == "known_weaker_retained"
    assert weaker_retained[0]["weaker_retained"] == ["600002 票600002(-5.00%)"]


def test_weak_sell_order_replays_partial_sell_before_next_choice():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=50,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600002",
                direction="sell",
                shares=100,
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -3.0, "shares": 100},
        ),
    )

    assert [check["status"] for check in checks] == [
        "sold_weakest",
        "known_weaker_retained",
    ]
    assert checks[0]["exit_scope"] == "部分"
    assert checks[1]["weaker_retained"] == ["600001 票600001(-5.00%)"]


def test_weak_sell_order_merges_only_identified_adjacent_split_sells():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=40,
                broker_contract_no="ORDER-1",
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="10:01:00",
                code="600001",
                direction="sell",
                shares=60,
                broker_contract_no="ORDER-1",
            ),
            _execution(
                row_id=3,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600003",
                direction="buy",
                shares=100,
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert len(checks) == 1
    assert checks[0]["shares"] == 100
    assert checks[0]["status"] == "sold_weakest"


def test_weak_sell_order_keeps_distinct_consecutive_sells_separate():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=40,
                broker_contract_no="ORDER-1",
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="14:00:00",
                code="600001",
                direction="sell",
                shares=60,
                broker_contract_no="ORDER-2",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert len(checks) == 2
    assert [check["shares"] for check in checks] == [40, 60]
    assert [check["exit_scope"] for check in checks] == ["部分", "全部"]


def test_weak_sell_order_collapses_split_plus_summary_duplicate():
    trade_date = "2026-07-06"
    rows = [
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="13:15:55",
                code="688772",
                direction="sell",
                shares=88,
                broker_contract_no="ORDER-1",
                broker_trade_no="0000000057670108",
                price=18.17,
                amount=1598.96,
                import_run_id="DAILY-RUN",
                source_file="daily.tsv",
                thesis_id=24,
            ),
            _execution(
                row_id=3,
                trade_date=trade_date,
                exec_time="13:15:56",
                code="688772",
                direction="sell",
                shares=712,
                broker_contract_no="ORDER-1",
                broker_trade_no="0000000057672610",
                price=18.17,
                amount=12937.04,
                import_run_id="DAILY-RUN",
                source_file="daily.tsv",
                thesis_id=24,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="13:15:56",
                code="688772",
                direction="sell",
                shares=800,
                balance_after=0,
                broker_contract_no="SUMMARY-1",
                broker_trade_no="57670108",
                price=18.17,
                amount=14536.0,
                import_run_id="WEEKLY-RUN",
                source_file="weekly.tsv",
            ),
        ]
    rows[0].update(net_amount=1593.96, total_fees=5.0)
    rows[1].update(net_amount=12931.04, total_fees=6.0)
    canonical = review._collapse_split_plus_summary_rows(rows)
    conflicted = [dict(row) for row in rows]
    conflicted[2]["thesis_id"] = 25
    second_summary = dict(rows[2])
    second_summary.update(
        {
            "id": 4,
            "broker_contract_no": "SUMMARY-2",
            "broker_trade_no": "0000000057672610",
            "import_run_id": "MONTHLY-RUN",
            "source_file": "monthly.tsv",
            "net_amount": 14525.000000001,
            "total_fees": 11.000000001,
        }
    )
    multiple_summaries = review._collapse_split_plus_summary_rows(
        [*rows, second_summary]
    )
    financial_conflict = dict(second_summary)
    financial_conflict.update(
        {
            "id": 5,
            "broker_contract_no": "SUMMARY-3",
            "import_run_id": "FINANCIAL-RUN",
            "source_file": "financial.tsv",
            "net_amount": 14524.0,
            "total_fees": 12.0,
        }
    )
    checks = review._analyze_weak_sell_order(
        rows=rows,
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-07-03"},
        snapshots=_holding_snapshot(
            "2026-07-03",
            {"stock_code": "688772", "change_pct": -5.0, "shares": 800},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert canonical[0]["thesis_id"] == 24
    assert canonical[0]["net_amount"] == pytest.approx(14525.0)
    assert canonical[0]["total_fees"] == pytest.approx(11.0)
    assert len(multiple_summaries) == 1
    assert multiple_summaries[0]["shares"] == 800
    with pytest.raises(RuntimeError, match="conflict"):
        review._collapse_split_plus_summary_rows(
            [*rows, second_summary, financial_conflict]
        )
    with pytest.raises(RuntimeError, match="conflicting thesis_id"):
        review._collapse_split_plus_summary_rows(conflicted)
    assert len(checks) == 1
    assert checks[0]["exec_time"] == "13:15:55"
    assert checks[0]["shares"] == 800
    assert checks[0]["exit_scope"] == "全部"


def test_split_plus_summary_does_not_collapse_same_source_real_orders():
    trade_date = "2026-07-06"
    rows = [
        _execution(
            row_id=1,
            trade_date=trade_date,
            exec_time="13:15:55",
            code="688772",
            direction="sell",
            shares=100,
            broker_contract_no="ORDER-1",
            broker_trade_no="TRADE-1",
            price=18.17,
            amount=1817.0,
            import_run_id="SAME-RUN",
            source_file="same.tsv",
        ),
        _execution(
            row_id=2,
            trade_date=trade_date,
            exec_time="13:15:56",
            code="688772",
            direction="sell",
            shares=200,
            broker_contract_no="ORDER-1",
            broker_trade_no="TRADE-2",
            price=18.17,
            amount=3634.0,
            import_run_id="SAME-RUN",
            source_file="same.tsv",
        ),
        _execution(
            row_id=3,
            trade_date=trade_date,
            exec_time="13:15:57",
            code="688772",
            direction="sell",
            shares=300,
            balance_after=0,
            broker_contract_no="ORDER-2",
            broker_trade_no="TRADE-3",
            price=18.17,
            amount=5451.0,
            import_run_id="SAME-RUN",
            source_file="same.tsv",
        ),
    ]

    assert len(review._collapse_split_plus_summary_rows(rows)) == 3


def test_split_plus_summary_collapses_single_cross_source_duplicate():
    trade_date = "2026-07-06"
    rows = [
        _execution(
            row_id=1,
            trade_date=trade_date,
            exec_time="13:15:55",
            code="688772",
            direction="sell",
            shares=800,
            broker_contract_no="ORDER-1",
            broker_trade_no="0000000057670108",
            price=18.17,
            amount=14536.0,
            import_run_id="DAILY-RUN",
            source_file="daily.tsv",
            thesis_id=24,
        ),
        _execution(
            row_id=2,
            trade_date=trade_date,
            exec_time="13:15:56",
            code="688772",
            direction="sell",
            shares=800,
            balance_after=0,
            broker_contract_no="SUMMARY-1",
            broker_trade_no="57670108",
            price=18.17,
            amount=14536.0,
            import_run_id="WEEKLY-RUN",
            source_file="weekly.tsv",
        ),
    ]
    rows[0].update(net_amount=14525.0, total_fees=11.0)

    canonical = review._collapse_split_plus_summary_rows(rows)

    assert len(canonical) == 1
    assert canonical[0]["shares"] == 800
    assert canonical[0]["thesis_id"] == 24
    assert canonical[0]["net_amount"] == pytest.approx(14525.0)
    assert canonical[0]["total_fees"] == pytest.approx(11.0)

    conflicting_summary = dict(rows[1])
    conflicting_summary.update(net_amount=14524.0, total_fees=12.0)
    with pytest.raises(RuntimeError, match="cash fields conflict"):
        review._collapse_split_plus_summary_rows([rows[0], conflicting_summary])


def test_weak_sell_order_fail_closed_for_missing_quote_but_keeps_known_negative():
    trade_date = "2026-05-27"
    sell = _execution(
        row_id=1,
        trade_date=trade_date,
        exec_time="10:00:00",
        code="600001",
        direction="sell",
    )
    unknown = review._analyze_weak_sell_order(
        rows=[sell],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "588000", "change_pct": None, "error": "source_failed"},
        ),
    )
    known_negative = review._analyze_weak_sell_order(
        rows=[sell],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -6.0},
            {"stock_code": "588000", "change_pct": None, "error": "source_failed"},
        ),
    )

    assert unknown[0]["status"] == "unknown_missing_quote"
    assert unknown[0]["missing_quotes"] == ["588000 票588000(缺行情)"]
    assert known_negative[0]["status"] == "known_weaker_retained"
    rendered = review._render_weak_sell_checks(known_negative)
    assert "2/2（已知）" in rendered
    assert "更弱：600002 票600002(-6.00%)" in rendered
    assert "缺行情：588000 票588000(缺行情)" in rendered


def test_weak_sell_order_not_applicable_and_ambiguous_order():
    trade_date = "2026-05-27"
    only_one = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
            )
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
        ),
    )
    ambiguous = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600002",
                direction="sell",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -2.0},
        ),
    )

    assert only_one[0]["status"] == "not_applicable"
    assert {check["status"] for check in ambiguous} == {"unknown_ambiguous_order"}


def test_weak_sell_order_only_taints_actions_at_and_after_ambiguous_time():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="09:30:00",
                code="600001",
                direction="sell",
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600002",
                direction="sell",
            ),
            _execution(
                row_id=3,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600003",
                direction="sell",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -3.0},
            {"stock_code": "600003", "change_pct": -2.0},
        ),
    )

    assert checks[0]["status"] == "sold_weakest"
    assert [check["status"] for check in checks[1:]] == [
        "unknown_ambiguous_order",
        "unknown_ambiguous_order",
    ]


def test_weak_sell_order_missing_time_taints_multiple_same_stock_actions():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time=None,
                code="600001",
                direction="sell",
                shares=50,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=50,
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert {check["status"] for check in checks} == {"unknown_ambiguous_order"}


def test_weak_sell_order_respects_sell_before_buy_and_buy_before_sell():
    trade_date = "2026-05-27"
    snapshot = _holding_snapshot(
        "2026-05-26",
        {"stock_code": "600001", "change_pct": -5.0},
    )
    sell = _execution(
        row_id=1,
        trade_date=trade_date,
        exec_time="10:00:00",
        code="600001",
        direction="sell",
    )
    buy = _execution(
        row_id=2,
        trade_date=trade_date,
        exec_time="11:00:00",
        code="600002",
        direction="buy",
    )
    sell_before_buy = review._analyze_weak_sell_order(
        rows=[buy, sell],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=snapshot,
    )
    buy["id"] = 1
    buy["exec_time"] = "09:30:00"
    sell["id"] = 2
    buy_before_sell = review._analyze_weak_sell_order(
        rows=[sell, buy],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=snapshot,
    )

    assert sell_before_buy[0]["status"] == "not_applicable"
    assert sell_before_buy[0]["position_count"] == 1
    assert buy_before_sell[0]["status"] == "unknown_missing_quote"
    assert buy_before_sell[0]["missing_quotes"] == ["600002 票600002(缺行情)"]


def test_weak_sell_order_propagates_missing_partial_position():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600003",
                direction="sell",
                shares=50,
                balance_after=50,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600001",
                direction="sell",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -2.0},
        ),
    )

    assert checks[0]["status"] == "partial_history"
    assert checks[1]["status"] == "partial_history"
    assert all(check["day_balance_conflict"] for check in checks)
    assert checks[1]["missing_quotes"] == ["600003 票600003(缺行情)"]


def test_weak_sell_order_taints_unknown_remaining_shares_after_sell():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600002",
                direction="sell",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": None},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert checks[0]["status"] == "sold_weakest"
    assert checks[1]["status"] == "partial_history"
    assert checks[1]["uncertain_positions"] == ["600001 票600001(-5.00%)"]


def test_weak_sell_order_fail_closed_on_balance_after_conflict():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=50,
                balance_after=0,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600002",
                direction="sell",
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert [check["status"] for check in checks] == [
        "partial_history",
        "partial_history",
    ]
    assert checks[0]["balance_conflict"] is True
    assert checks[0]["exit_scope"] == "未知"
    assert checks[1]["uncertain_positions"] == ["600001 票600001(-5.00%)"]


def test_later_balance_conflict_invalidates_earlier_aligned_sell():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
                shares=100,
                balance_after=0,
            ),
            _execution(
                row_id=2,
                trade_date=trade_date,
                exec_time="11:00:00",
                code="600003",
                direction="sell",
                shares=100,
            ),
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0, "shares": 100},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    assert len(checks) == 2
    assert [check["status"] for check in checks] == [
        "partial_history",
        "partial_history",
    ]
    assert checks[0]["balance_conflict"] is False
    assert checks[0]["day_balance_conflict"] is True
    assert checks[1]["balance_conflict"] is True
    assert "整日回放不可靠" in review._render_weak_sell_checks(checks)


def test_weak_sell_order_treats_non_finite_change_as_missing_quote():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
            )
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": float("nan")},
            {"stock_code": "600002", "change_pct": -2.0},
        ),
    )

    assert checks[0]["status"] == "unknown_missing_quote"
    assert checks[0]["sold_change_pct"] is None


def test_weak_sell_order_fail_closed_when_execution_window_is_truncated():
    trade_date = "2026-05-27"
    checks = review._analyze_weak_sell_order(
        rows=[
            _execution(
                row_id=1,
                trade_date=trade_date,
                exec_time="10:00:00",
                code="600001",
                direction="sell",
            )
        ],
        current_days=[trade_date],
        prior_day_by_day={trade_date: "2026-05-26"},
        snapshots=_holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "change_pct": -5.0},
            {"stock_code": "600002", "change_pct": -2.0},
        ),
        history_complete=False,
    )

    assert checks[0]["status"] == "partial_history"
    summary = review._summarize_weak_sell_checks(checks, history_complete=False)
    assert summary["history_complete"] is False
    assert "无法确认是否存在卖出动作" in review._render_weak_sell_checks(
        [],
        history_complete=False,
    )


def test_load_holding_snapshots_keeps_error_members(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "trade.db")
    conn.execute("CREATE TABLE daily_market (date TEXT PRIMARY KEY, raw_data TEXT)")
    conn.execute(
        "CREATE TABLE broker_executions ("
        "id INTEGER PRIMARY KEY, account_id TEXT, biz_date TEXT, exec_time TEXT, "
        "stock_code TEXT, direction TEXT, shares REAL, price REAL, amount REAL, "
        "balance_after REAL, broker_contract_no TEXT, broker_trade_no TEXT, "
        "import_run_id TEXT, source_file TEXT, imported_at TEXT, is_void INTEGER, "
        "void_reason TEXT, total_fees REAL, net_amount REAL, currency TEXT)"
    )
    conn.execute(
        "INSERT INTO daily_market(date, raw_data) VALUES (?, ?)",
        (
            "2026-05-26",
            json.dumps(
                {
                    "date": "2026-05-26",
                    "generated_at": "2026-05-26T20:00:00",
                    "holdings_data": [
                        {
                            "code": "600001.SH",
                            "name": "正常票",
                            "shares": 100,
                            "change_pct": -2.5,
                        },
                        {
                            "code": "588000",
                            "name": "科创50ETF",
                            "error": "source_failed",
                            "change_pct": 9.9,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)

    snapshots = review._load_holding_snapshots({"2026-05-26"})

    assert snapshots["2026-05-26"]["status"] == "success"
    assert snapshots["2026-05-26"]["members"]["600001"]["change_pct"] == -2.5
    assert snapshots["2026-05-26"]["members"]["588000"]["change_pct"] is None
    assert snapshots["2026-05-26"]["members"]["588000"]["error"] == "source_failed"


def test_snapshot_stale_uses_first_seen_time_for_exact_replacement(
    tmp_path, monkeypatch
):
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "trade.db")
    conn.execute("CREATE TABLE daily_market (date TEXT PRIMARY KEY, raw_data TEXT)")
    conn.execute(
        "CREATE TABLE broker_executions ("
        "id INTEGER PRIMARY KEY, account_id TEXT, biz_date TEXT, exec_time TEXT, "
        "stock_code TEXT, stock_code_raw TEXT, direction TEXT, shares REAL, "
        "price REAL, amount REAL, balance_after REAL, broker_contract_no TEXT, "
        "broker_trade_no TEXT, import_run_id TEXT, source_file TEXT, "
        "imported_at TEXT, is_void INTEGER, void_reason TEXT, total_fees REAL, "
        "net_amount REAL, currency TEXT)"
    )
    conn.execute(
        "INSERT INTO daily_market(date, raw_data) VALUES (?, ?)",
        (
            "2026-05-27",
            json.dumps(
                {
                    "date": "2026-05-27",
                    "generated_at": "2026-05-27T20:00:00",
                    "holdings_data": [
                        {
                            "code": "002409",
                            "name": "替换票",
                            "shares": 100,
                            "change_pct": 1.0,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ),
    )
    common = (
        "default",
        "2026-05-27",
        "09:32:29",
        "002409",
        "buy",
        100,
        200.66,
        20066.0,
        "0102000005015520",
    )
    conn.execute(
        """
        INSERT INTO broker_executions(
            id, account_id, biz_date, exec_time, stock_code, direction,
            shares, price, amount, broker_contract_no, broker_trade_no,
            import_run_id, source_file, imported_at, is_void, void_reason
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 'OLD', ?, 'DAY', 'day.tsv',
                  '2026-05-27 11:00:00', 1, 'semantic_duplicate_exact')
        """,
        common,
    )
    conn.execute(
        """
        INSERT INTO broker_executions(
            id, account_id, biz_date, exec_time, stock_code, direction,
            shares, price, amount, balance_after, broker_contract_no,
            broker_trade_no, import_run_id, source_file, imported_at,
            is_void, void_reason
        ) VALUES (2, ?, ?, ?, ?, ?, ?, ?, ?, 100, 'NEW', ?, 'WEEK', 'week.tsv',
                  '2026-05-28 01:00:00', 0, NULL)
        """,
        common,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)

    snapshots = review._load_holding_snapshots({"2026-05-27"})

    assert snapshots["2026-05-27"]["status"] == "success"
    assert snapshots["2026-05-27"]["members"]["002409"]["shares"] == 100


def test_load_holding_snapshots_rejects_future_generation_and_invalid_member(
    tmp_path, monkeypatch
):
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "trade.db")
    conn.execute("CREATE TABLE daily_market (date TEXT PRIMARY KEY, raw_data TEXT)")
    conn.execute(
        "CREATE TABLE broker_executions ("
        "id INTEGER PRIMARY KEY, account_id TEXT, biz_date TEXT, exec_time TEXT, "
        "stock_code TEXT, direction TEXT, shares REAL, price REAL, amount REAL, "
        "balance_after REAL, broker_contract_no TEXT, broker_trade_no TEXT, "
        "import_run_id TEXT, source_file TEXT, imported_at TEXT, is_void INTEGER, "
        "void_reason TEXT, total_fees REAL, net_amount REAL, currency TEXT)"
    )
    conn.executemany(
        "INSERT INTO daily_market(date, raw_data) VALUES (?, ?)",
        [
            (
                "2026-05-22",
                json.dumps(
                    {
                        "date": "2026-05-22",
                        "generated_at": "2026-05-22T20:00:00",
                        "holdings_data": [],
                    }
                ),
            ),
            (
                "2026-05-23",
                json.dumps(
                    {
                        "date": "2026-05-23",
                        "generated_at": "2026-05-23T10:00:00",
                        "holdings_data": [],
                    }
                ),
            ),
            (
                "2026-05-24",
                json.dumps(
                    {
                        "date": "2026-05-24",
                        "generated_at": "2026-05-24T16:00:00Z",
                        "holdings_data": [],
                    }
                ),
            ),
            (
                "2026-05-25",
                json.dumps(
                    {
                        "date": "2026-05-25",
                        "generated_at": "2026-05-27T20:00:00",
                        "holdings_data": [
                            {"code": "600001", "shares": 100, "change_pct": -5.0}
                        ],
                    }
                ),
            ),
            (
                "2026-05-26",
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "generated_at": "2026-05-26T20:00:00",
                        "holdings_data": [
                            {"code": "600001", "shares": 100, "change_pct": -5.0},
                            {"name": "缺代码持仓", "error": "source_failed"},
                            {
                                "code": "600002",
                                "shares": "NaN",
                                "change_pct": -2.0,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
        ],
    )
    conn.execute(
        "INSERT INTO broker_executions(account_id, biz_date, imported_at, is_void) "
        "VALUES ('default', '2026-05-22', '2026-05-22 13:00:00', 0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(review, "PROJECT_ROOT", tmp_path)

    snapshots = review._load_holding_snapshots(
        {
            "2026-05-22",
            "2026-05-23",
            "2026-05-24",
            "2026-05-25",
            "2026-05-26",
        }
    )

    assert snapshots["2026-05-22"]["status"] == "stale"
    assert snapshots["2026-05-22"]["reason"].startswith("late_execution_import:")
    assert snapshots["2026-05-23"]["reason"] == "generated_before_market_close:10:00:00"
    assert snapshots["2026-05-24"]["reason"] == "generated_date_mismatch:2026-05-25"
    assert snapshots["2026-05-25"]["status"] == "malformed"
    assert snapshots["2026-05-25"]["reason"] == "generated_date_mismatch:2026-05-27"
    assert snapshots["2026-05-26"]["status"] == "partial"
    assert snapshots["2026-05-26"]["reason"] == "invalid_members:2"


def test_report_renders_weak_sell_section_and_summary(tmp_path, monkeypatch):
    rows = [
        {
            "id": 1,
            "account_id": "default",
            "biz_date": "2026-05-27",
            "exec_time": "10:00:00",
            "stock_code": "600001",
            "stock_name": "弱票",
            "direction": "sell",
            "shares": 100,
            "amount": 900.0,
            "net_amount": 900.0,
            "total_fees": 0.0,
            "thesis_id": None,
        }
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
    monkeypatch.setattr(
        review,
        "_load_holding_snapshots",
        lambda days: _holding_snapshot(
            "2026-05-26",
            {"stock_code": "600001", "stock_name": "弱票", "change_pct": -5.0},
            {"stock_code": "600002", "stock_name": "强票", "change_pct": 2.0},
        ),
    )

    result = review.generate(
        run_date=date(2026, 5, 29),
        account="default",
        limit=10000,
        push=False,
    )
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## 是否优先卖出最弱个股" in report_md
    assert "前一收盘日弱势代理" in report_md
    assert "| 2026-05-27 10:00:00 | 600001 弱票 |" in report_md
    assert "符合：唯一最弱" in report_md
    assert result["weak_sell_summary"] == {
        "actions": 1,
        "comparable": 1,
        "aligned": 1,
        "weaker_retained": 0,
        "not_applicable": 0,
        "unknown": 0,
        "history_complete": True,
    }


def test_report_uses_same_canonical_split_summary_rows_for_all_metrics(
    tmp_path, monkeypatch
):
    trade_date = "2026-05-27"
    rows = [
        _execution(
            row_id=1,
            trade_date=trade_date,
            exec_time="13:15:55",
            code="688772",
            direction="sell",
            shares=88,
            broker_contract_no="ORDER-1",
            broker_trade_no="0000000057670108",
            price=18.17,
            amount=1598.96,
            import_run_id="DAILY-RUN",
            source_file="daily.tsv",
            thesis_id=24,
        ),
        _execution(
            row_id=3,
            trade_date=trade_date,
            exec_time="13:15:56",
            code="688772",
            direction="sell",
            shares=712,
            broker_contract_no="ORDER-1",
            broker_trade_no="0000000057672610",
            price=18.17,
            amount=12937.04,
            import_run_id="DAILY-RUN",
            source_file="daily.tsv",
            thesis_id=24,
        ),
        _execution(
            row_id=2,
            trade_date=trade_date,
            exec_time="13:15:56",
            code="688772",
            direction="sell",
            shares=800,
            balance_after=0,
            broker_contract_no="SUMMARY-1",
            broker_trade_no="57670108",
            price=18.17,
            amount=14536.0,
            import_run_id="WEEKLY-RUN",
            source_file="weekly.tsv",
        ),
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
    monkeypatch.setattr(
        review,
        "_load_holding_snapshots",
        lambda days: _holding_snapshot(
            "2026-05-26",
            {"stock_code": "688772", "change_pct": -5.0, "shares": 800},
            {"stock_code": "600002", "change_pct": -2.0, "shares": 100},
        ),
    )

    result = review.generate(
        run_date=date(2026, 5, 29),
        account="default",
        limit=10000,
        push=False,
    )
    report_md = Path(result["report_path"]).read_text(encoding="utf-8")

    assert result["weak_sell_summary"]["actions"] == 1
    assert "sell 688772 票688772 800@18.17" in report_md
    assert "14,536.00" in report_md
    assert "29,072.00" not in report_md
    assert "缺少 thesis_id" not in report_md


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
    assert "卖出选择 1" in markdown
    assert "可判 0" in markdown
    assert "缺数 1" in markdown
    assert "流水完整" in markdown
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
