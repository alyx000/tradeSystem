"""margin_index_correlation CLI 接线测试：run_for_post 折入盘后采集的契约。"""
from __future__ import annotations

from cli import margin_index_correlation as cli


def test_run_for_post_persists_and_pushes(monkeypatch):
    """cmd_post 折入路径：必须落库 + 推送（persist=True, push=True）。"""
    seen = {}

    def _fake_execute(config, date, *, persist, push, **kw):
        seen.update(date=date, persist=persist, push=push)
        return "MD"

    monkeypatch.setattr(cli, "_execute", _fake_execute)
    out = cli.run_for_post({"x": 1}, "2026-06-19")
    assert out == "MD"
    assert seen == {"date": "2026-06-19", "persist": True, "push": True}


def test_daily_dry_run_no_persist_no_push(monkeypatch):
    """--dry-run：persist=False, push=False（不落库不推送）。"""
    seen = {}

    def _fake_execute(config, date, *, persist, push, **kw):
        seen.update(persist=persist, push=push)
        return "MD"

    monkeypatch.setattr(cli, "_execute", _fake_execute)
    args = type("A", (), {"date": "2026-06-19", "dry_run": True, "no_push": False,
                          "windows": None, "divergence_windows": None,
                          "divergence_gap": None, "max_lag": None})()
    cli._run_daily({}, args)
    assert seen == {"persist": False, "push": False}


def test_daily_no_push_persists_but_no_push(monkeypatch):
    """--no-push：persist=True（落库）, push=False（不推送）。"""
    seen = {}
    monkeypatch.setattr(cli, "_execute",
                        lambda config, date, *, persist, push, **kw: seen.update(persist=persist, push=push) or "MD")
    args = type("A", (), {"date": "2026-06-19", "dry_run": False, "no_push": True,
                          "windows": None, "divergence_windows": None,
                          "divergence_gap": None, "max_lag": None})()
    cli._run_daily({}, args)
    assert seen == {"persist": True, "push": False}
