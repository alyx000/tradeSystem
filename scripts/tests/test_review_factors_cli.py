from __future__ import annotations

import argparse

import pytest

from cli import review_factors
from db import queries as Q
from db.connection import get_connection
from db.migrate import migrate


def _score_args(**overrides):
    values = {
        "review_factor_command": "factor-score",
        "date": "2026-07-10",
        "steps_file": None,
        "no_llm": True,
        "retry_of_run_id": None,
        "input_by": "cli-test",
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _seed_open_trade_date(conn) -> None:
    Q.upsert_trade_calendar(conn, [{"date": "2026-07-10", "is_open": 1}])
    conn.commit()


def test_factor_score_normalizes_stored_review_json_like_api(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = get_connection(tmp_path / "review-factor-cli.db")
    migrate(conn)
    _seed_open_trade_date(conn)
    Q.upsert_daily_review(
        conn,
        "2026-07-10",
        {"step6_nodes": {"systemic_risk": True}},
    )
    conn.commit()
    monkeypatch.setattr(review_factors, "_connection", lambda: conn)
    monkeypatch.setattr(
        review_factors,
        "build_review_prefill",
        lambda _conn, _date: {"date": _date, "review_signals": {"sectors": {}}},
    )

    result = review_factors._factor_score(_score_args())

    assert result["rule_gate"]["primary_category_lock"] == "market_node"
    assert result["diagnostics"]["request"]["input_by"] == "cli-test"


def test_invalid_calendar_date_exits_before_opening_database(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    opened = False

    def fail_if_opened():
        nonlocal opened
        opened = True
        raise AssertionError("database must not be opened for an invalid date")

    monkeypatch.setattr(review_factors, "_connection", fail_if_opened)

    with pytest.raises(SystemExit) as exc:
        review_factors.handle_command({}, _score_args(date="2026-99-99"))

    assert exc.value.code == 2
    assert opened is False
    assert "invalid calendar date" in capsys.readouterr().err.lower()


def test_factor_confirm_rejects_run_after_saved_score_input_changes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "review-factor-cli-stale.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_open_trade_date(conn)
    conn.close()
    monkeypatch.setattr(review_factors, "_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        review_factors,
        "build_review_prefill",
        lambda _conn, _date: {"date": _date, "review_signals": {"sectors": {}}},
    )

    scored = review_factors._factor_score(_score_args())
    conn = get_connection(db_path)
    Q.upsert_daily_review(
        conn,
        "2026-07-10",
        {"step1_market": {"notes": "评分后修改了大盘判断"}},
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        review_factors,
        "_read_json_file",
        lambda _path: {"status": "accepted"},
    )
    args = argparse.Namespace(
        date="2026-07-10",
        run_id=scored["score_run_id"],
        decision_file="unused.json",
        input_by="cli-test",
        json=True,
    )

    with pytest.raises(ValueError, match="score input has changed"):
        review_factors._factor_confirm(args)


def test_factor_score_and_confirm_share_summary_input_normalization(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "review-factor-cli-normalized.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_open_trade_date(conn)
    conn.close()
    monkeypatch.setattr(review_factors, "_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        review_factors,
        "build_review_prefill",
        lambda _conn, _date: {"date": _date, "review_signals": {"sectors": {}}},
    )
    monkeypatch.setattr(
        review_factors,
        "_read_json_file",
        lambda path: (
            {"steps": {"step1_market": {"judgement": "结构偏弱"}}}
            if path == "steps.json"
            else {
                "status": "overridden",
                "primary_factor": "market_node",
                "supporting_factors": [],
                "override_reason": "人工确认大盘节点约束更强",
            }
        ),
    )

    scored = review_factors._factor_score(_score_args(steps_file="steps.json"))
    conn = get_connection(db_path)
    Q.upsert_daily_review(
        conn,
        "2026-07-10",
        {
            "step1_market": {
                "judgement": "结构偏弱",
                "notes": "判断：结构偏弱",
            },
        },
    )
    conn.commit()
    conn.close()
    original_prefill = review_factors.build_review_prefill

    def checked_prefill(conn, date):
        assert conn.in_transaction is True
        return original_prefill(conn, date)

    monkeypatch.setattr(review_factors, "build_review_prefill", checked_prefill)
    args = argparse.Namespace(
        date="2026-07-10",
        run_id=scored["score_run_id"],
        decision_file="decision.json",
        input_by="cli-test",
        json=True,
    )

    confirmed = review_factors._factor_confirm(args)

    assert confirmed["status"] == "overridden"
    assert confirmed["primary_factor"] == "market_node"
