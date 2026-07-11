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


def test_factor_score_normalizes_stored_review_json_like_api(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = get_connection(tmp_path / "review-factor-cli.db")
    migrate(conn)
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
