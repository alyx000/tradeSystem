import argparse
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from cli import new_high


TARGET_DATE = "2026-07-14"


class _ProxyTracker:
    def __init__(self):
        self.active = False
        self.enter_count = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.active = True
        self.enter_count += 1
        return self

    def __exit__(self, *exc):
        self.active = False
        return False


def _record(date=TARGET_DATE):
    return {
        "status": "ok",
        "date": date,
        "market_count": 2,
        "new_high_count": 1,
        "sector_summary": [],
        "stocks": [],
        "source": {"quote_source": "fake"},
    }


def _summary(
    status,
    *,
    processed_dates=None,
    failed_date=None,
    target_record=None,
    failure_detail=None,
):
    return {
        "status": status,
        "processed_dates": list(processed_dates or []),
        "failed_date": failed_date,
        "target_record": target_record,
        "failure_detail": failure_detail,
    }


def _wire_run_for_post(monkeypatch, tmp_path, summary):
    import utils.trade_date as trade_date

    conn = MagicMock()
    registry = MagicMock(name="initialized_registry")
    run_due_dates = Mock(return_value=summary)
    ensure_trade_calendar = Mock(
        side_effect=AssertionError("post path must not write trade calendar")
    )
    monkeypatch.setattr(new_high, "get_connection", Mock(return_value=conn))
    monkeypatch.setattr(new_high, "migrate", Mock(side_effect=AssertionError("post path must not migrate")))
    monkeypatch.setattr(trade_date, "ensure_trade_calendar", ensure_trade_calendar)
    monkeypatch.setattr(
        new_high,
        "ensure_trade_calendar",
        ensure_trade_calendar,
        raising=False,
    )
    monkeypatch.setattr(new_high.service, "run_due_dates", run_due_dates, raising=False)
    monkeypatch.setattr(new_high.renderer, "render_daily", lambda record, top_n=10: f"# canonical {record['date']}\n")
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    return conn, registry, run_due_dates, ensure_trade_calendar


def test_daily_default_does_not_push():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    new_high.register_subparser(subparsers)

    args = parser.parse_args(["new-high", "daily"])

    assert args.command == "new-high"
    assert args.new_high_command == "daily"
    assert args.push is False
    assert args.top_n == 10


def test_persisted_manual_daily_uses_contiguous_due_date_coordinator(
    monkeypatch,
    tmp_path,
    capsys,
):
    import main

    registry = MagicMock()
    conn = MagicMock()
    target_record = _record()
    run_due_dates = Mock(
        return_value=_summary(
            "ok",
            processed_dates=["2026-07-13", TARGET_DATE],
            target_record=target_record,
        )
    )
    monkeypatch.setattr(main, "setup_providers", Mock(return_value=registry))
    monkeypatch.setattr(new_high, "get_connection", Mock(return_value=conn))
    monkeypatch.setattr(new_high, "migrate", Mock())
    monkeypatch.setattr(new_high.service, "run_due_dates", run_due_dates)
    monkeypatch.setattr(
        new_high.service,
        "run_daily",
        Mock(side_effect=AssertionError("persisted daily must use due-date coordinator")),
    )
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        new_high.renderer,
        "render_daily",
        lambda record, top_n=10: f"# canonical {record['date']}\n",
    )
    args = argparse.Namespace(
        date=TARGET_DATE,
        top_n=10,
        dry_run=False,
        push=False,
        json=False,
    )

    new_high._run_daily({}, args)

    run_due_dates.assert_called_once_with(
        conn,
        registry,
        TARGET_DATE,
        top_n=10,
    )
    assert f"# canonical {TARGET_DATE}" in capsys.readouterr().out
    conn.close.assert_called_once_with()


def test_backfill_ensures_each_calendar_year_before_processing(
    monkeypatch,
    capsys,
):
    import main

    registry = MagicMock()
    conn = MagicMock()
    ensure_calendar = Mock(return_value=0)
    run_backfill = Mock(
        return_value={
            "status": "ok",
            "processed_count": 0,
            "already_complete_count": 0,
            "closed_count": 0,
            "skipped_count": 0,
            "processed_dates": [],
            "already_complete_dates": [],
            "closed_dates": [],
            "skipped": [],
        }
    )
    monkeypatch.setattr(main, "setup_providers", Mock(return_value=registry))
    monkeypatch.setattr(new_high, "get_connection", Mock(return_value=conn))
    monkeypatch.setattr(new_high, "migrate", Mock())
    monkeypatch.setattr(new_high, "ensure_trade_calendar", ensure_calendar)
    monkeypatch.setattr(new_high.service, "run_backfill", run_backfill)
    args = argparse.Namespace(
        start_date="2025-12-31",
        end_date="2026-01-02",
        top_n=10,
        dry_run=False,
    )

    new_high._run_backfill({}, args)

    assert ensure_calendar.call_args_list == [
        ((conn, registry), {"year": 2025, "force": True}),
        ((conn, registry), {"year": 2026, "force": True}),
    ]
    run_backfill.assert_called_once()
    assert '"status": "ok"' in capsys.readouterr().out
    conn.close.assert_called_once_with()


def test_run_for_post_calls_run_due_dates_with_initialized_registry(monkeypatch, tmp_path):
    import utils.network_env as network_env

    summary = _summary("non_trading_day", failed_date=TARGET_DATE)
    conn, registry, run_due_dates, ensure_trade_calendar = _wire_run_for_post(
        monkeypatch,
        tmp_path,
        summary,
    )
    proxy_tracker = _ProxyTracker()
    active_checks = []

    def _run_due_dates(*args, **kwargs):
        active_checks.append(proxy_tracker.active)
        return summary

    run_due_dates.side_effect = _run_due_dates
    monkeypatch.setattr(network_env, "without_standard_http_proxy", proxy_tracker)
    monkeypatch.setattr(
        new_high,
        "without_standard_http_proxy",
        proxy_tracker,
        raising=False,
    )

    result = new_high.run_for_post(
        {"provider": "config"},
        TARGET_DATE,
        registry=registry,
    )

    assert result == summary
    run_due_dates.assert_called_once_with(
        conn,
        registry,
        TARGET_DATE,
        top_n=10,
    )
    conn.close.assert_called_once_with()
    registry.initialize_all.assert_not_called()
    assert proxy_tracker.enter_count == 1
    assert active_checks == [True]
    new_high.migrate.assert_not_called()
    ensure_trade_calendar.assert_not_called()


def test_run_for_post_closes_connection_when_service_raises(monkeypatch, tmp_path):
    summary = _summary("schema_missing", failed_date=TARGET_DATE)
    conn, registry, run_due_dates, ensure_trade_calendar = _wire_run_for_post(
        monkeypatch,
        tmp_path,
        summary,
    )
    run_due_dates.side_effect = RuntimeError("service exploded")

    with pytest.raises(RuntimeError, match="service exploded"):
        new_high.run_for_post({}, TARGET_DATE, registry=registry)

    conn.close.assert_called_once_with()
    registry.initialize_all.assert_not_called()
    ensure_trade_calendar.assert_not_called()


def test_run_for_post_without_registry_initializes_providers_inside_no_proxy_context(
    monkeypatch,
    tmp_path,
):
    import main
    import utils.network_env as network_env

    summary = _summary("non_trading_day", failed_date=TARGET_DATE)
    conn, _unused, run_due_dates, ensure_trade_calendar = _wire_run_for_post(
        monkeypatch,
        tmp_path,
        summary,
    )
    registry = MagicMock(name="new_registry")
    setup_providers = Mock(return_value=registry)
    proxy_tracker = _ProxyTracker()
    active_checks = []
    registry.initialize_all.side_effect = lambda: active_checks.append(proxy_tracker.active)

    def _run_due_dates(*args, **kwargs):
        active_checks.append(proxy_tracker.active)
        return summary

    run_due_dates.side_effect = _run_due_dates
    monkeypatch.setattr(main, "setup_providers", setup_providers)
    monkeypatch.setattr(new_high, "setup_providers", setup_providers, raising=False)
    monkeypatch.setattr(network_env, "without_standard_http_proxy", proxy_tracker)
    monkeypatch.setattr(
        new_high,
        "without_standard_http_proxy",
        proxy_tracker,
        raising=False,
    )

    result = new_high.run_for_post({"provider": "config"}, TARGET_DATE)

    assert result == summary
    setup_providers.assert_called_once_with({"provider": "config"})
    registry.initialize_all.assert_called_once_with()
    run_due_dates.assert_called_once_with(
        conn,
        registry,
        TARGET_DATE,
        top_n=10,
    )
    assert proxy_tracker.enter_count == 1
    assert active_checks == [True, True]
    ensure_trade_calendar.assert_not_called()


def test_run_for_post_ok_overwrites_only_target_report_files(monkeypatch, tmp_path):
    target_record = _record()
    summary = _summary(
        "ok",
        processed_dates=["2026-07-13", TARGET_DATE],
        target_record=target_record,
    )
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    target_md = tmp_path / f"{TARGET_DATE}.md"
    target_json = tmp_path / f"{TARGET_DATE}.json"
    target_md.write_text("stale md", encoding="utf-8")
    target_json.write_text("{\"stale\": true}\n", encoding="utf-8")

    new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert target_md.read_text(encoding="utf-8") == f"# canonical {TARGET_DATE}\n"
    assert json.loads(target_json.read_text(encoding="utf-8")) == target_record
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"{TARGET_DATE}.json",
        f"{TARGET_DATE}.md",
    ]


@pytest.mark.parametrize(
    ("existing_suffix", "missing_suffix"),
    [(".md", ".json"), (".json", ".md")],
)
def test_run_for_post_already_complete_only_fills_missing_report_file(
    monkeypatch,
    tmp_path,
    existing_suffix,
    missing_suffix,
):
    target_record = _record()
    summary = _summary("already_complete", target_record=target_record)
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    existing = tmp_path / f"{TARGET_DATE}{existing_suffix}"
    missing = tmp_path / f"{TARGET_DATE}{missing_suffix}"
    if existing_suffix == ".md":
        existing_content = f"# canonical {TARGET_DATE}\n"
    else:
        existing_content = json.dumps(target_record, ensure_ascii=False, indent=2) + "\n"
    existing.write_text(existing_content, encoding="utf-8")

    result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert existing.read_text(encoding="utf-8") == existing_content
    assert missing.exists()
    if missing_suffix == ".md":
        assert missing.read_text(encoding="utf-8") == f"# canonical {TARGET_DATE}\n"
    else:
        assert json.loads(missing.read_text(encoding="utf-8")) == target_record
    assert result["report_actions"][existing_suffix.removeprefix(".")] == "preserved"
    assert result["report_actions"][missing_suffix.removeprefix(".")] == "written"


def test_run_for_post_already_complete_repairs_empty_markdown_and_preserves_json(
    monkeypatch,
    tmp_path,
):
    target_record = _record()
    summary = _summary("already_complete", target_record=target_record)
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    md_path = tmp_path / f"{TARGET_DATE}.md"
    json_path = tmp_path / f"{TARGET_DATE}.json"
    md_path.write_text("", encoding="utf-8")
    canonical_json = json.dumps(target_record, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(canonical_json, encoding="utf-8")

    result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert md_path.read_text(encoding="utf-8") == f"# canonical {TARGET_DATE}\n"
    assert json_path.read_text(encoding="utf-8") == canonical_json
    assert result["report_actions"] == {"md": "repaired", "json": "preserved"}


def test_run_for_post_already_complete_repairs_invalid_utf8_markdown(
    monkeypatch,
    tmp_path,
):
    target_record = _record()
    summary = _summary("already_complete", target_record=target_record)
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    md_path = tmp_path / f"{TARGET_DATE}.md"
    json_path = tmp_path / f"{TARGET_DATE}.json"
    md_path.write_bytes(b"\xff\xfe\x00")
    json_path.write_text(
        json.dumps(target_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert md_path.read_text(encoding="utf-8") == f"# canonical {TARGET_DATE}\n"
    assert result["report_actions"] == {"md": "repaired", "json": "preserved"}


@pytest.mark.parametrize(
    "broken_json",
    [
        "{not-json\n",
        json.dumps({**_record(), "date": "2026-07-13"}, ensure_ascii=False),
        json.dumps({**_record(), "market_count": 999}, ensure_ascii=False),
    ],
    ids=["invalid-json", "wrong-date", "wrong-count"],
)
def test_run_for_post_already_complete_repairs_invalid_json_and_preserves_markdown(
    monkeypatch,
    tmp_path,
    broken_json,
):
    target_record = _record()
    summary = _summary("already_complete", target_record=target_record)
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    md_path = tmp_path / f"{TARGET_DATE}.md"
    json_path = tmp_path / f"{TARGET_DATE}.json"
    canonical_md = f"# canonical {TARGET_DATE}\n"
    md_path.write_text(canonical_md, encoding="utf-8")
    json_path.write_text(broken_json, encoding="utf-8")

    result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert md_path.read_text(encoding="utf-8") == canonical_md
    assert json.loads(json_path.read_text(encoding="utf-8")) == target_record
    assert result["report_actions"] == {"md": "preserved", "json": "repaired"}


def test_write_reports_serializes_both_artifacts_before_touching_existing_files(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    md_path = tmp_path / f"{TARGET_DATE}.md"
    json_path = tmp_path / f"{TARGET_DATE}.json"
    md_path.write_text("old md\n", encoding="utf-8")
    json_path.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setattr(
        new_high.json,
        "dumps",
        Mock(side_effect=TypeError("cannot serialize")),
    )

    with pytest.raises(TypeError, match="cannot serialize"):
        new_high._write_reports(TARGET_DATE, "new md\n", _record())

    assert md_path.read_text(encoding="utf-8") == "old md\n"
    assert json_path.read_text(encoding="utf-8") == '{"old": true}\n'


def test_atomic_report_replace_failure_preserves_complete_old_file_and_cleans_temp(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    target = tmp_path / f"{TARGET_DATE}.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    original_replace = new_high.os.replace

    def _fail_json_replace(source, destination):
        if Path(destination) == target:
            raise OSError("replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(new_high.os, "replace", _fail_json_replace)

    with pytest.raises(OSError, match="replace failed"):
        new_high._write_reports(TARGET_DATE, "new md\n", _record())

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not list(tmp_path.glob(".*.tmp"))


def test_partial_report_replace_is_repaired_on_next_canonical_run(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    md_path = tmp_path / f"{TARGET_DATE}.md"
    json_path = tmp_path / f"{TARGET_DATE}.json"
    md_path.write_text("old md\n", encoding="utf-8")
    json_path.write_text('{"old": true}\n', encoding="utf-8")
    original_replace = new_high.os.replace
    failed_once = False

    def _fail_markdown_once(source, destination):
        nonlocal failed_once
        if Path(destination) == md_path and not failed_once:
            failed_once = True
            raise OSError("markdown replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(new_high.os, "replace", _fail_markdown_once)
    with pytest.raises(OSError, match="markdown replace failed"):
        new_high._write_reports(
            TARGET_DATE,
            f"# canonical {TARGET_DATE}\n",
            _record(),
        )

    assert json.loads(json_path.read_text(encoding="utf-8")) == _record()
    assert md_path.read_text(encoding="utf-8") == "old md\n"

    monkeypatch.setattr(new_high.os, "replace", original_replace)
    result = new_high._write_reports(
        TARGET_DATE,
        f"# canonical {TARGET_DATE}\n",
        _record(),
        preserve_matching=True,
    )

    assert result["actions"] == {"md": "repaired", "json": "preserved"}
    assert md_path.read_text(encoding="utf-8") == f"# canonical {TARGET_DATE}\n"
    assert json.loads(json_path.read_text(encoding="utf-8")) == _record()


def test_preserve_matching_reports_does_not_replace_valid_files(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(new_high.C, "REPORT_DIR", str(tmp_path))
    markdown = f"# canonical {TARGET_DATE}\n"
    new_high._write_reports(TARGET_DATE, markdown, _record())
    replace = Mock(side_effect=AssertionError("valid reports must be preserved"))
    monkeypatch.setattr(new_high.os, "replace", replace)

    result = new_high._write_reports(
        TARGET_DATE,
        markdown,
        _record(),
        preserve_matching=True,
    )

    assert result["actions"] == {"md": "preserved", "json": "preserved"}
    replace.assert_not_called()


@pytest.mark.parametrize(
    "summary",
    [
        _summary("ok", processed_dates=[TARGET_DATE], target_record=_record()),
        _summary("already_complete", target_record=_record()),
        _summary("baseline_missing", failed_date=TARGET_DATE),
        _summary("baseline_mismatch", failed_date=TARGET_DATE),
        _summary("historical_gap", failed_date=TARGET_DATE),
        _summary("schema_missing", failed_date=TARGET_DATE),
        _summary("calendar_missing", failed_date=TARGET_DATE),
        _summary("non_trading_day", failed_date=TARGET_DATE),
        _summary(
            "source_failed",
            processed_dates=["2026-07-13"],
            failed_date=TARGET_DATE,
            target_record=None,
        ),
    ],
    ids=[
        "ok",
        "already-complete",
        "baseline-missing",
        "baseline-mismatch",
        "historical-gap",
        "schema-missing",
        "calendar-missing",
        "non-trading-day",
        "source-failed-after-prefix",
    ],
)
def test_run_for_post_never_pushes_and_only_completed_target_writes_reports(
    monkeypatch,
    tmp_path,
    summary,
):
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)
    push = Mock(side_effect=AssertionError("post path must not push"))
    monkeypatch.setattr(new_high, "_push_to_dingtalk", push)

    new_high.run_for_post({}, TARGET_DATE, registry=registry)

    push.assert_not_called()
    target_md = tmp_path / f"{TARGET_DATE}.md"
    target_json = tmp_path / f"{TARGET_DATE}.json"
    if summary["status"] in {"ok", "already_complete"}:
        assert target_md.is_file()
        assert target_json.is_file()
    else:
        assert not target_md.exists()
        assert not target_json.exists()


@pytest.mark.parametrize(
    "status",
    [
        "baseline_missing",
        "baseline_mismatch",
        "historical_gap",
        "schema_missing",
        "calendar_missing",
        "source_failed",
    ],
)
def test_run_for_post_failure_log_contains_status_and_failed_date(
    monkeypatch,
    tmp_path,
    caplog,
    status,
):
    summary = _summary(
        status,
        processed_dates=["2026-07-13"],
        failed_date=TARGET_DATE,
    )
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)

    with caplog.at_level(logging.WARNING):
        new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert status in caplog.text
    assert TARGET_DATE in caplog.text


def test_run_for_post_source_failure_log_keeps_prefix_source_and_error(
    monkeypatch,
    tmp_path,
    caplog,
):
    summary = _summary(
        "source_failed",
        processed_dates=["2026-07-13"],
        failed_date=TARGET_DATE,
        failure_detail={
            "failed_source": "get_adj_factor",
            "error": "provider unavailable",
        },
    )
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)

    with caplog.at_level(logging.WARNING):
        result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert result == summary
    assert "2026-07-13" in caplog.text
    assert "get_adj_factor" in caplog.text
    assert "provider unavailable" in caplog.text


def test_run_for_post_success_receipt_contains_processed_dates_paths_and_actions(
    monkeypatch,
    tmp_path,
    caplog,
):
    summary = _summary(
        "ok",
        processed_dates=["2026-07-13", TARGET_DATE],
        target_record=_record(),
    )
    _, registry, _, _ = _wire_run_for_post(monkeypatch, tmp_path, summary)

    with caplog.at_level(logging.INFO):
        result = new_high.run_for_post({}, TARGET_DATE, registry=registry)

    assert result["report_paths"] == {
        "md": str(tmp_path / f"{TARGET_DATE}.md"),
        "json": str(tmp_path / f"{TARGET_DATE}.json"),
    }
    assert result["report_actions"] == {"md": "written", "json": "written"}
    assert "2026-07-13" in caplog.text
    assert TARGET_DATE in caplog.text
    assert str(tmp_path / f"{TARGET_DATE}.md") in caplog.text
    assert str(tmp_path / f"{TARGET_DATE}.json") in caplog.text


def test_write_reports_anchors_relative_report_dir_to_repo_root(
    monkeypatch,
    tmp_path,
):
    repo_root = tmp_path / "repo"
    elsewhere = tmp_path / "elsewhere"
    repo_root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setattr(new_high, "REPO_ROOT", repo_root, raising=False)
    monkeypatch.setattr(new_high.C, "REPORT_DIR", "data/reports/new-high")
    monkeypatch.chdir(elsewhere)

    paths = new_high._write_reports(
        TARGET_DATE,
        f"# canonical {TARGET_DATE}\n",
        _record(),
    )

    expected_dir = repo_root / "data" / "reports" / "new-high"
    assert Path(paths["md"]) == expected_dir / f"{TARGET_DATE}.md"
    assert Path(paths["json"]) == expected_dir / f"{TARGET_DATE}.json"
    assert (expected_dir / f"{TARGET_DATE}.md").is_file()
    assert (expected_dir / f"{TARGET_DATE}.json").is_file()
    assert not (elsewhere / "data").exists()
