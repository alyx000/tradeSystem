"""CLI 单测:macro-flash 门2 codex review 修复项。

覆盖:
- --dry-run/--no-push/--repush 互斥(parser 层)
- --repush 与 --force-refresh 拒同用(handle_command 层)
- --date 严格校验(拒绝 3.11+ 紧凑/周日期形式)
- --lookback-hours 正整数校验
- _show 对 run_error manifest(缺 raw_count/matched_count)容错
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from cli import macro_flash  # noqa: E402
from services.macro_flash import service  # noqa: E402


def _build_main_parser() -> argparse.ArgumentParser:
    from main import build_parser
    return build_parser()


# ── 模式互斥(parser 层) ─────────────────────────────────────────

def test_repush_dry_run_mutually_exclusive() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--repush", "--dry-run"])
    assert exc_info.value.code == 2


def test_repush_no_push_mutually_exclusive() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--repush", "--no-push"])
    assert exc_info.value.code == 2


# ── --repush 与 --force-refresh 拒同用(handle_command 层) ───────

def test_repush_force_refresh_rejected_at_handle_command() -> None:
    parser = _build_main_parser()
    # 两者不冲突的 parser 层组合可以正常解析
    args = parser.parse_args(["macro-flash", "run", "--repush", "--force-refresh"])
    assert args.repush is True
    assert args.force_refresh is True
    with pytest.raises(SystemExit) as exc_info:
        macro_flash.handle_command(config={}, args=args)
    assert exc_info.value.code == 2


# ── --date 严格校验 ──────────────────────────────────────────────

def test_date_rejects_compact_form() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--date", "20260723"])
    assert exc_info.value.code == 2


def test_date_rejects_week_form() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--date", "2026-W30-4"])
    assert exc_info.value.code == 2


def test_date_accepts_canonical_form() -> None:
    parser = _build_main_parser()
    args = parser.parse_args(["macro-flash", "run", "--date", "2026-07-23"])
    assert args.date == "2026-07-23"


# ── --lookback-hours 正整数校验 ──────────────────────────────────

def test_lookback_hours_rejects_zero() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--lookback-hours", "0"])
    assert exc_info.value.code == 2


def test_lookback_hours_rejects_negative() -> None:
    parser = _build_main_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["macro-flash", "run", "--lookback-hours", "-1"])
    assert exc_info.value.code == 2


def test_lookback_hours_accepts_positive() -> None:
    parser = _build_main_parser()
    args = parser.parse_args(["macro-flash", "run", "--lookback-hours", "54"])
    assert args.lookback_hours == 54


# ── _show 对 run_error manifest 容错 ─────────────────────────────

def test_show_handles_run_error_manifest_without_counts(tmp_path, monkeypatch, capsys) -> None:
    date_str = "2026-07-23"
    day_dir = tmp_path / date_str
    day_dir.mkdir(parents=True)
    manifest = {
        "source_status": "run_error",
        "window_start": "2026-07-22T16:30:00",
        "window_end": "2026-07-23T16:30:00",
        "error": "boom",
        "push_status": "skipped",
    }
    (day_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(service, "BASE_DIR", tmp_path)

    macro_flash._show(argparse.Namespace(date=date_str, json=False))

    out = capsys.readouterr().out
    assert "run_error" in out
    assert "错误" in out
    assert "boom" in out
