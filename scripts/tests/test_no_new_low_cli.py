"""七天横盘不创新低 CLI 副作用与送达状态（全 mock）。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager

import main as main_module
from cli import no_new_low as cli


class _Conn:
    def close(self):
        pass


class _Registry:
    def initialize_all(self):
        pass


def _args(**over):
    values = dict(date="2026-08-10", dry_run=False, no_push=False, no_llm=False)
    values.update(over)
    return argparse.Namespace(**values)


def _result():
    return {"status": "ok", "date": "2026-08-10", "pk_pool": [{"code": "600001"}]}


def _wire(monkeypatch, *, result=None, calendar_state=True, push_ok=True):
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    db_calls = []
    monkeypatch.setattr(cli, "get_readonly_connection", lambda: (db_calls.append(1), _Conn())[1])
    monkeypatch.setattr(cli, "_trade_day_from_db", lambda *_args: calendar_state)
    proxy_state = {"active": False}

    @contextmanager
    def no_proxy():
        proxy_state["active"] = True
        try:
            yield
        finally:
            proxy_state["active"] = False

    import utils.network_env as network_env
    monkeypatch.setattr(network_env, "without_standard_http_proxy", no_proxy)
    scan_calls = []
    monkeypatch.setattr(
        cli.scanner, "run_daily",
        lambda *_args: (scan_calls.append(proxy_state["active"]), result or _result())[1],
    )
    monkeypatch.setattr(cli.renderer, "render_daily", lambda *_args, **_kw: "# daily")
    monkeypatch.setattr(cli.renderer, "render_push", lambda *_args, **_kw: "# push")
    monkeypatch.setattr(cli.renderer, "render_source_failed", lambda *_args: "# failed")
    saves = []
    monkeypatch.setattr(cli.renderer, "save_artifacts", lambda *args, **kwargs: (saves.append(args), ("a", "b"))[1])
    pk_calls = []
    monkeypatch.setattr(cli.pk, "build_llm_runner", lambda: object())
    monkeypatch.setattr(cli.pk, "run_pk", lambda *args: (pk_calls.append(1), {"status": "skipped"})[1])
    pushes = []
    monkeypatch.setattr(cli, "_push_to_dingtalk", lambda *args: (pushes.append(args), push_ok)[1])
    return {"db": db_calls, "scan": scan_calls, "saves": saves, "pk": pk_calls, "pushes": pushes}


def test_dry_run_has_no_write_or_push(monkeypatch):
    wired = _wire(monkeypatch)
    assert cli._run_daily({}, _args(dry_run=True)) == 0
    assert wired["db"] == [1] and wired["scan"] == [True]
    assert wired["saves"] == [] and wired["pushes"] == []


def test_no_push_saves_both_artifacts_without_delivery(monkeypatch):
    wired = _wire(monkeypatch)
    assert cli._run_daily({}, _args(no_push=True, input_by="USER")) == 0
    assert len(wired["saves"]) == 1 and wired["pushes"] == []
    assert wired["saves"][0][1]["input_by"] == "USER"


def test_input_by_argument_accepts_explicit_requester_and_keeps_scheduler_default():
    parser = argparse.ArgumentParser()
    cli.register_subparser(parser.add_subparsers())
    assert parser.parse_args(["no-new-low", "daily", "--input-by", "USER"]).input_by == "USER"
    assert parser.parse_args(["no-new-low", "daily"]).input_by == "launchd"


def test_push_failure_is_nonzero_after_report_saved(monkeypatch):
    wired = _wire(monkeypatch, push_ok=False)
    assert cli._run_daily({}, _args()) == 2
    assert len(wired["saves"]) == 1 and len(wired["pushes"]) == 1


def test_no_llm_skips_pk_but_still_delivers(monkeypatch):
    wired = _wire(monkeypatch)
    assert cli._run_daily({}, _args(no_llm=True)) == 0
    assert wired["pk"] == [] and len(wired["pushes"]) == 1
    assert "七天横盘不创新低" in wired["pushes"][0][0]


def test_source_failed_saves_alert_pushes_and_returns_nonzero(monkeypatch):
    wired = _wire(monkeypatch, result={
        "status": "source_failed", "date": "2026-08-10", "failed_sources": {"daily": "timeout"}})
    assert cli._run_daily({}, _args()) == 1
    assert len(wired["saves"]) == 1 and len(wired["pushes"]) == 1
    assert "数据失败" in wired["pushes"][0][0]


def test_non_trading_day_returns_before_scanner(monkeypatch):
    wired = _wire(monkeypatch, calendar_state=False)
    assert cli._run_daily({}, _args()) == 0
    assert wired["scan"] == [] and wired["saves"] == [] and wired["pushes"] == []


def test_missing_trade_calendar_is_alert_not_weekday_fallback(monkeypatch):
    wired = _wire(monkeypatch, calendar_state=None)
    assert cli._run_daily({}, _args()) == 1
    assert wired["scan"] == [] and len(wired["saves"]) == len(wired["pushes"]) == 1


def test_date_argument_rejects_path_traversal():
    for raw in ("../../foo", "2026-9-1", "20260901"):
        try:
            cli._iso_date(raw)
        except argparse.ArgumentTypeError:
            continue
        raise AssertionError(f"invalid date accepted: {raw}")
