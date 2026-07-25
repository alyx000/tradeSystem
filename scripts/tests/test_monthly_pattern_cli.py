from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from cli import monthly_pattern
from db.connection import get_connection as real_get_connection


def _args(*, dry_run: bool, no_push: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        date="2026-06-30",
        months=48,
        input_by="pytest",
        no_financial=False,
        dry_run=dry_run,
        no_push=no_push,
    )


def _summary(status: str = "complete") -> dict:
    return {
        "scan_date": "2026-06-30",
        "signal_month": "2026-06",
        "status": status,
        "source_status": {},
        "counts": {},
        "candidates": [],
        "transitions": [],
        "error": "boom" if status == "failed" else None,
    }


def test_daily_dry_run_uses_memory_copy_and_has_no_report_or_push(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "trade.db"
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: real_get_connection(db_path),
    )
    monkeypatch.setattr(monthly_pattern, "_initialized_registry", lambda _config: object())

    def fake_run(conn, _registry, _date, **_kwargs):
        conn.execute("CREATE TABLE dry_run_marker (id INTEGER)")
        conn.execute("INSERT INTO dry_run_marker VALUES (1)")
        conn.commit()
        return _summary()

    monkeypatch.setattr(monthly_pattern.service, "run_daily", fake_run)
    monkeypatch.setattr(monthly_pattern.renderer, "render_daily", lambda _summary: "# report")
    monkeypatch.setattr(
        monthly_pattern,
        "_write_report",
        lambda *_args: pytest.fail("dry-run 不应落报告"),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_push",
        lambda *_args: pytest.fail("dry-run 不应推送"),
    )

    monthly_pattern._run_daily({}, _args(dry_run=True))

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='dry_run_marker'"
    ).fetchone() is None


def test_daily_no_push_persists_and_writes_report_without_push(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "trade.db"
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: real_get_connection(db_path),
    )
    monkeypatch.setattr(monthly_pattern, "_initialized_registry", lambda _config: object())

    def fake_run(conn, _registry, _date, **_kwargs):
        conn.execute("CREATE TABLE persisted_marker (id INTEGER)")
        conn.commit()
        return _summary()

    reports = []
    monkeypatch.setattr(monthly_pattern.service, "run_daily", fake_run)
    monkeypatch.setattr(monthly_pattern.renderer, "render_daily", lambda _summary: "# report")
    monkeypatch.setattr(
        monthly_pattern,
        "_write_report",
        lambda day, markdown: reports.append((day, markdown)) or tmp_path / "report.md",
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_push",
        lambda *_args: pytest.fail("--no-push 不应推送"),
    )

    monthly_pattern._run_daily({}, _args(dry_run=False, no_push=True))

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='persisted_marker'"
    ).fetchone()
    assert reports == [("2026-06-30", "# report")]


def test_daily_push_uses_bounded_summary_and_keeps_full_report(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "trade.db"
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: real_get_connection(db_path),
    )
    monkeypatch.setattr(monthly_pattern, "_initialized_registry", lambda _config: object())
    summary = _summary()
    monkeypatch.setattr(
        monthly_pattern.service,
        "run_daily",
        lambda *_args, **_kwargs: summary,
    )
    monkeypatch.setattr(
        monthly_pattern.renderer,
        "render_daily",
        lambda _summary: "# full report",
    )
    monkeypatch.setattr(monthly_pattern, "REPORT_DIR", tmp_path)
    focus_rows = {
        "active": [{"stock_code": "600001", "status": "active"}],
        "fundamental_verified": [
            {"stock_code": "600002", "status": "fundamental_verified"}
        ],
    }
    monkeypatch.setattr(
        monthly_pattern.pool,
        "list_pool",
        lambda _conn, *, status: focus_rows[status],
    )
    render_calls = []

    def fake_push_summary(
        actual_summary,
        *,
        full_markdown,
        report_path,
        focus_candidates,
    ):
        assert Path(report_path).read_text(encoding="utf-8") == full_markdown
        render_calls.append(
            (actual_summary, full_markdown, report_path, focus_candidates)
        )
        return "# bounded push"

    monkeypatch.setattr(
        monthly_pattern.renderer,
        "render_push_summary",
        fake_push_summary,
    )
    pushes = []
    monkeypatch.setattr(
        monthly_pattern,
        "_push",
        lambda title, markdown: pushes.append((title, markdown)),
    )

    monthly_pattern._run_daily({}, _args(dry_run=False, no_push=False))

    report_path = tmp_path / "2026-06-30.md"
    assert report_path.read_text(encoding="utf-8") == "# full report"
    assert render_calls == [
        (
            summary,
            "# full report",
            str(report_path),
            focus_rows["active"] + focus_rows["fundamental_verified"],
        )
    ]
    assert pushes == [("月线模式观察池 · 2026-06-30", "# bounded push")]


def test_daily_archives_full_report_before_focus_pool_query_failure(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "trade.db"
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: real_get_connection(db_path),
    )
    monkeypatch.setattr(monthly_pattern, "_initialized_registry", lambda _config: object())
    monkeypatch.setattr(
        monthly_pattern.service,
        "run_daily",
        lambda *_args, **_kwargs: _summary(),
    )
    monkeypatch.setattr(
        monthly_pattern.renderer,
        "render_daily",
        lambda _summary: "# full report before focus failure",
    )
    monkeypatch.setattr(monthly_pattern, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(
        monthly_pattern.pool,
        "list_pool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("focus decode failed")
        ),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_push",
        lambda *_args: pytest.fail("focus 查询失败时不应推送"),
    )

    with pytest.raises(RuntimeError, match="focus decode failed"):
        monthly_pattern._run_daily({}, _args(dry_run=False, no_push=False))

    assert (tmp_path / "2026-06-30.md").read_text(
        encoding="utf-8"
    ) == "# full report before focus failure"


def test_month_range_is_inclusive_and_rejects_reverse_order() -> None:
    assert monthly_pattern._month_range("2025-11", "2026-02") == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    with pytest.raises(ValueError, match="不能晚于"):
        monthly_pattern._month_range("2026-02", "2025-11")


def test_writing_commands_require_input_by_but_pool_does_not() -> None:
    parser = argparse.ArgumentParser()
    monthly_pattern.register_subparser(parser.add_subparsers(dest="command"))

    with pytest.raises(SystemExit):
        parser.parse_args(["monthly-pattern", "daily"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "monthly-pattern",
                "backfill",
                "--start-month",
                "2026-01",
                "--end-month",
                "2026-06",
            ]
        )

    assert parser.parse_args(
        ["monthly-pattern", "daily", "--input-by", "codex"]
    ).input_by == "codex"
    assert parser.parse_args(["monthly-pattern", "pool"]).input_by is None
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "monthly-pattern",
                "daily",
                "--input-by",
                "codex",
                "--date",
                "2026-06",
            ]
        )


@pytest.mark.parametrize(
    ("start_month", "end_month"),
    [
        ("2026-07", "2026-07"),
        ("2026-08", "2026-08"),
        ("2026-06", "2026-08"),
    ],
)
def test_backfill_rejects_current_incomplete_or_future_month_before_registry_init(
    monkeypatch: pytest.MonkeyPatch,
    start_month: str,
    end_month: str,
) -> None:
    monkeypatch.setattr(monthly_pattern, "_last_completed_month", lambda: "2026-06")
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: pytest.fail("非法回放范围不应初始化 provider"),
    )
    args = argparse.Namespace(
        start_month=start_month,
        end_month=end_month,
        input_by="pytest",
        months=48,
        no_financial=False,
        dry_run=True,
    )

    with pytest.raises(SystemExit) as exc:
        monthly_pattern._run_backfill({}, args)

    assert exc.value.code == 2


def test_backfill_surfaces_live_pool_temporal_order_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(monthly_pattern, "_last_completed_month", lambda: "2026-06")
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_working_connection",
        lambda *, dry_run: (conn, None),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_close_working",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        monthly_pattern.service,
        "run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            monthly_pattern.service.MonthlyPatternTemporalOrderError(
                "scan_date=2026-06-30 早于月线状态水位 2026-07-02"
            )
        ),
    )
    args = argparse.Namespace(
        start_month="2026-06",
        end_month="2026-06",
        input_by="pytest",
        months=48,
        no_financial=False,
        dry_run=True,
    )

    with pytest.raises(SystemExit) as exc:
        monthly_pattern._run_backfill({}, args)

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "早于月线状态水位" in captured.err
