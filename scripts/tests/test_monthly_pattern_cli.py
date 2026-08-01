from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

from cli import monthly_pattern
from db.connection import get_connection as real_get_connection
from db.connection import get_readonly_connection as real_get_readonly_connection
from db.migrate import migrate as real_migrate


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
    seed = real_get_connection(db_path)
    real_migrate(seed)
    seed.close()
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: real_get_connection(db_path),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: real_get_readonly_connection(db_path),
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
        "technical_candidate": [
            {"stock_code": "600003", "status": "technical_candidate"}
        ],
        "risk": [{"stock_code": "600004", "status": "risk"}],
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
            focus_rows["active"]
            + focus_rows["fundamental_verified"]
            + focus_rows["technical_candidate"]
            + focus_rows["risk"],
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


def _monitor_args(
    *,
    save_report: bool = False,
    as_json: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        date="2026-06-30",
        months=35,
        max_seeds=3,
        json=as_json,
        save_report=save_report,
    )


def _monitor_summary(status: str = "complete") -> dict:
    return {
        "requested_date": "2026-06-30",
        "target_date": "2026-06-30",
        "seed_month": "2026-05",
        "status": status,
        "source_status": {},
        "counts": {},
        "mainline_context": {},
        "candidates": [],
        "data_issues": [],
        "unresolved_rules": [],
        "error": "blocked fixture" if status == "blocked" else None,
    }


def test_monitor_parser_is_read_only_and_validates_bounds() -> None:
    parser = argparse.ArgumentParser()
    monthly_pattern.register_subparser(parser.add_subparsers(dest="command"))

    args = parser.parse_args(
        [
            "monthly-pattern",
            "monitor",
            "--date",
            "2026-06-30",
            "--months",
            "35",
            "--max-seeds",
            "3",
            "--json",
        ]
    )

    assert args.monthly_pattern_command == "monitor"
    assert args.input_by is None
    assert args.date == "2026-06-30"
    assert args.months == 35
    assert args.max_seeds == 3
    assert args.json is True
    assert args.save_report is False

    with pytest.raises(SystemExit):
        parser.parse_args(["monthly-pattern", "monitor", "--months", "34"])
    with pytest.raises(SystemExit):
        parser.parse_args(["monthly-pattern", "monitor", "--max-seeds", "0"])


def test_monitor_daily_parser_has_three_distinct_run_modes() -> None:
    parser = argparse.ArgumentParser()
    monthly_pattern.register_subparser(parser.add_subparsers(dest="command"))

    args = parser.parse_args(
        [
            "monthly-pattern",
            "monitor-daily",
            "--date",
            "2026-06-30",
            "--months",
            "35",
            "--no-push",
            "--json",
        ]
    )

    assert args.monthly_pattern_command == "monitor-daily"
    assert args.input_by is None
    assert args.date == "2026-06-30"
    assert args.months == 35
    assert args.no_push is True
    assert args.dry_run is False
    assert args.json is True
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "monthly-pattern",
                "monitor-daily",
                "--dry-run",
                "--no-push",
            ]
        )


def test_monitor_uses_readonly_connection_and_does_not_save_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readonly_conn = sqlite3.connect(":memory:")
    registry = object()
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: registry,
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: readonly_conn,
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: pytest.fail("monitor 不应打开可写连接"),
    )

    def fake_run(conn, got_registry, requested_date, **kwargs):
        assert conn is readonly_conn
        assert got_registry is registry
        assert requested_date == "2026-06-30"
        assert kwargs == {"months": 35, "max_seeds": 3}
        return _monitor_summary()

    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "run_monitor",
        fake_run,
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_renderer,
        "render_monitor",
        lambda _summary: "# monitor\n",
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_write_monitor_report",
        lambda *_args: pytest.fail("默认 monitor 不应落报告"),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "_push",
        lambda *_args: pytest.fail("monitor 不应推送"),
    )

    monthly_pattern._run_monitor({}, _monitor_args())

    assert capsys.readouterr().out == "# monitor\n\n"
    with pytest.raises(sqlite3.ProgrammingError):
        readonly_conn.execute("SELECT 1")


def test_monitor_save_report_is_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    readonly_conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: readonly_conn,
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "run_monitor",
        lambda *_args, **_kwargs: _monitor_summary(),
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_renderer,
        "render_monitor",
        lambda _summary: "# monitor\n",
    )
    reports: list[tuple[str, str]] = []
    monkeypatch.setattr(
        monthly_pattern,
        "_write_monitor_report",
        lambda day, markdown: (
            reports.append((day, markdown))
            or tmp_path / "monitor.md"
        ),
    )

    monthly_pattern._run_monitor(
        {},
        _monitor_args(save_report=True),
    )

    assert reports == [("2026-06-30", "# monitor\n")]


def test_monitor_daily_explicit_date_is_readonly_and_push_gated(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readonly_conn = sqlite3.connect(":memory:")
    registry = object()
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: registry,
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: readonly_conn,
    )

    def fake_run(conn, got_registry, requested_date, **kwargs):
        assert conn is readonly_conn
        assert got_registry is registry
        assert requested_date == "2026-06-30"
        assert kwargs == {"months": 35, "max_seeds": None}
        return _monitor_summary()

    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "run_monitor",
        fake_run,
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_renderer,
        "render_monitor",
        lambda _summary, *, automated_snapshot: (
            "# automated\n" if automated_snapshot else "# manual\n"
        ),
    )

    def fake_process(summary, **kwargs):
        assert summary["status"] == "complete"
        assert kwargs["monitor_markdown"] == "# automated\n"
        assert kwargs["dry_run"] is False
        assert kwargs["push"] is True
        assert kwargs["push_allowed"] is False
        return {
            "target_date": "2026-06-30",
            "seed_month_end": "2026-05-29",
            "run_status": "complete",
            "events": [],
            "new_notification_count": 0,
            "pending_count": 0,
            "sent_count": 0,
            "push_status": "gate_blocked",
            "report_path": "/tmp/report.md",
        }

    monkeypatch.setattr(
        monthly_pattern.monitor_daily,
        "process_summary",
        fake_process,
    )
    args = argparse.Namespace(
        date="2026-06-30",
        months=35,
        dry_run=False,
        no_push=False,
        json=False,
    )

    monthly_pattern._run_monitor_daily({}, args)

    assert "push=gate_blocked" in capsys.readouterr().out
    with pytest.raises(sqlite3.ProgrammingError):
        readonly_conn.execute("SELECT 1")


def test_monitor_daily_partial_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readonly_conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: readonly_conn,
    )
    partial_summary = _monitor_summary()
    partial_summary["status"] = "partial"
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "run_monitor",
        lambda *_args, **_kwargs: partial_summary,
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_renderer,
        "render_monitor",
        lambda *_args, **_kwargs: "# partial\n",
    )
    monkeypatch.setattr(
        monthly_pattern.monitor_daily,
        "process_summary",
        lambda *_args, **_kwargs: {
            "target_date": "2026-06-30",
            "seed_month_end": "2026-05-29",
            "run_status": "partial",
            "events": [],
            "new_notification_count": 0,
            "pending_count": 0,
            "sent_count": 0,
            "push_status": "not_requested",
            "report_path": "/tmp/report.md",
        },
    )
    args = argparse.Namespace(
        date="2026-06-30",
        months=35,
        dry_run=False,
        no_push=True,
        json=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        monthly_pattern._run_monitor_daily({}, args)

    assert exc_info.value.code == 1
    with pytest.raises(sqlite3.ProgrammingError):
        readonly_conn.execute("SELECT 1")


def test_monitor_daily_default_non_trading_day_skips_before_scan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "resolve_target_date",
        lambda *_args, **_kwargs: ("2000-01-03", "2000-01-03"),
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "run_monitor",
        lambda *_args, **_kwargs: pytest.fail("休市默认运行不应扫描"),
    )
    args = argparse.Namespace(
        date=None,
        months=48,
        dry_run=False,
        no_push=False,
        json=False,
    )

    monthly_pattern._run_monitor_daily({}, args)

    assert "安全跳过" in capsys.readouterr().out


def test_monitor_daily_calendar_failure_persists_blocked_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_service,
        "resolve_target_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            monthly_pattern.indicator_watch_service.IndicatorWatchSourceError(
                "calendar unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: pytest.fail("日历失败不应继续打开业务库"),
    )
    monkeypatch.setattr(
        monthly_pattern.indicator_watch_renderer,
        "render_monitor",
        lambda summary, **_kwargs: (
            "# blocked\n"
            if summary["status"] == "blocked"
            else pytest.fail("应生成 blocked summary")
        ),
    )
    captured: dict = {}

    def fake_process(summary, **kwargs):
        captured["summary"] = summary
        captured["kwargs"] = kwargs
        return {
            "target_date": summary["target_date"],
            "seed_month_end": None,
            "run_status": "blocked",
            "events": [{"event_type": "run_blocked"}],
            "new_notification_count": 1,
            "pending_count": 1,
            "sent_count": 0,
            "push_status": "credentials_missing",
            "report_path": "/tmp/blocked.md",
        }

    monkeypatch.setattr(
        monthly_pattern.monitor_daily,
        "process_summary",
        fake_process,
    )
    args = argparse.Namespace(
        date=None,
        months=48,
        dry_run=False,
        no_push=False,
        json=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        monthly_pattern._run_monitor_daily({}, args)

    assert exc_info.value.code == 1
    assert captured["summary"]["status"] == "blocked"
    assert captured["summary"]["target_date"]
    assert captured["kwargs"]["push"] is True
    assert captured["kwargs"]["push_allowed"] is False
    assert captured["kwargs"]["persist_gate_blocked"] is True
    assert captured["kwargs"]["dry_run"] is False


def test_facts_backfill_parser_requires_audited_scope() -> None:
    parser = argparse.ArgumentParser()
    monthly_pattern.register_subparser(parser.add_subparsers(dest="command"))

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["monthly-pattern", "facts-backfill", "--input-by", "pytest"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "monthly-pattern",
                "facts-backfill",
                "--date",
                "2026-06-30",
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "monthly-pattern",
                "facts-backfill",
                "--date",
                "2026-06-30",
                "--input-by",
                "pytest",
                "--months",
                "34",
            ]
        )

    args = parser.parse_args(
        [
            "monthly-pattern",
            "facts-backfill",
            "--date",
            "2026-06-30",
            "--input-by",
            "pytest",
            "--dry-run",
            "--max-stocks",
            "3",
        ]
    )
    assert args.monthly_pattern_command == "facts-backfill"
    assert args.date == "2026-06-30"
    assert args.months == 48
    assert args.input_by == "pytest"
    assert args.dry_run is True
    assert args.max_stocks == 3
    assert args.expect_receipt_hash is None


@pytest.mark.parametrize(
    "args",
    [
        argparse.Namespace(
            date="2026-06-30",
            months=48,
            input_by="pytest",
            dry_run=False,
            expect_receipt_hash=None,
            max_stocks=None,
        ),
        argparse.Namespace(
            date="2026-06-30",
            months=48,
            input_by="pytest",
            dry_run=False,
            expect_receipt_hash="a" * 64,
            max_stocks=1,
        ),
    ],
)
def test_facts_backfill_rejects_unsafe_actual_args_before_provider_init(
    monkeypatch: pytest.MonkeyPatch,
    args: argparse.Namespace,
) -> None:
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: pytest.fail("非法参数不应初始化 provider"),
    )
    with pytest.raises(SystemExit) as exc:
        monthly_pattern._run_facts_backfill({}, args)
    assert exc.value.code == 2


def test_facts_backfill_dry_run_uses_memory_connection_and_prints_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = sqlite3.connect(":memory:")
    registry = object()
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: registry,
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
    summary = {
        "status": "ready_to_confirm",
        "receipt_hash": "a" * 64,
        "write_boundary": {"database": False},
    }

    def fake_run(got_conn, got_registry, **kwargs):
        assert got_conn is conn
        assert got_registry is registry
        assert kwargs == {
            "target_date": "2026-06-30",
            "months": 48,
            "input_by": "pytest",
            "dry_run": True,
            "expected_receipt_hash": None,
            "max_stocks": 3,
            "ensure_schema_before_persist": None,
        }
        return summary

    monkeypatch.setattr(
        monthly_pattern.fact_backfill_service,
        "run_backfill",
        fake_run,
    )
    args = argparse.Namespace(
        date="2026-06-30",
        months=48,
        input_by="pytest",
        dry_run=True,
        expect_receipt_hash=None,
        max_stocks=3,
    )

    monthly_pattern._run_facts_backfill({}, args)

    output = capsys.readouterr().out
    assert '"status": "ready_to_confirm"' in output
    assert '"receipt_hash": "' in output


def test_facts_backfill_actual_passes_only_dedicated_schema_ensure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = sqlite3.connect(":memory:")
    registry = object()
    ensure_calls = []
    monkeypatch.setattr(
        monthly_pattern,
        "_initialized_registry",
        lambda _config: registry,
    )
    monkeypatch.setattr(monthly_pattern, "get_connection", lambda: conn)
    monkeypatch.setattr(
        monthly_pattern,
        "_close_working",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        monthly_pattern,
        "migrate",
        lambda _conn: pytest.fail("actual facts-backfill 不得调用全库 migrate"),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "ensure_monthly_pattern_derived_fact_schema",
        lambda got_conn: ensure_calls.append(got_conn),
    )

    def fake_run(got_conn, got_registry, **kwargs):
        assert got_conn is conn
        assert got_registry is registry
        ensure = kwargs.pop("ensure_schema_before_persist")
        assert callable(ensure)
        ensure()
        assert kwargs == {
            "target_date": "2026-06-30",
            "months": 48,
            "input_by": "pytest",
            "dry_run": False,
            "expected_receipt_hash": "a" * 64,
            "max_stocks": None,
        }
        return {
            "status": "complete",
            "receipt_hash": "a" * 64,
            "write_boundary": {"database": True},
        }

    monkeypatch.setattr(
        monthly_pattern.fact_backfill_service,
        "run_backfill",
        fake_run,
    )
    args = argparse.Namespace(
        date="2026-06-30",
        months=48,
        input_by="pytest",
        dry_run=False,
        expect_receipt_hash="a" * 64,
        max_stocks=None,
    )

    monthly_pattern._run_facts_backfill({}, args)

    assert ensure_calls == [conn]
    assert '"status": "complete"' in capsys.readouterr().out


def test_working_connection_dry_run_uses_readonly_backup_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = sqlite3.connect(":memory:")
    source.execute("CREATE TABLE source_marker (value TEXT)")
    source.execute("INSERT INTO source_marker VALUES ('preserved')")
    source.commit()
    calls = []
    monkeypatch.setattr(
        monthly_pattern,
        "get_readonly_connection",
        lambda: calls.append("readonly") or source,
    )
    monkeypatch.setattr(
        monthly_pattern,
        "get_connection",
        lambda: pytest.fail("dry-run 不得打开可写真实库连接"),
    )
    monkeypatch.setattr(
        monthly_pattern,
        "migrate",
        lambda _conn: calls.append("migrate_memory"),
    )

    working, real = monthly_pattern._working_connection(dry_run=True)
    try:
        assert real is source
        assert working.execute(
            "SELECT value FROM source_marker"
        ).fetchone()[0] == "preserved"
        assert calls == ["readonly", "migrate_memory"]
    finally:
        working.close()
        source.close()
