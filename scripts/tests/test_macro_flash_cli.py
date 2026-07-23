"""CLI 单测:macro-flash 门2 codex review 修复项。

覆盖:
- --dry-run/--no-push/--repush 互斥(parser 层)
- --repush 与 --force-refresh 拒同用(handle_command 层)
- --date 严格校验(拒绝 3.11+ 紧凑/周日期形式)
- --lookback-hours 正整数校验
- _show 对 run_error manifest(缺 raw_count/matched_count)容错
- _show 仅在 manifest files.digest.sha256 与当前 digest.md 匹配时才展示正文
  (run_error 陈旧 digest / sha 篡改 / sha 匹配 三种场景)
- _show 仅在 source_status == complete 时才展示正文(source_failed 的"无命中"空 digest
  即便 sha 匹配也不能当正常空结果展示,门2 codex round-3)
"""
from __future__ import annotations

import argparse
import hashlib
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


# ── _show 仅在 manifest sha 与 digest.md 匹配时展示正文(门2 codex round-2)──

def test_show_hides_stale_digest_on_run_error(tmp_path, monkeypatch, capsys) -> None:
    """run_error 只重写 manifest,不动 digest.md;不能把上一代旧速读当当期展示。"""
    date_str = "2026-07-23"
    day_dir = tmp_path / date_str
    day_dir.mkdir(parents=True)
    (day_dir / "digest.md").write_text("OLD DIGEST 旧速读", encoding="utf-8")
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
    assert "OLD DIGEST" not in out
    assert "run_error" in out
    assert "boom" in out


def test_show_hides_digest_on_sha_mismatch(tmp_path, monkeypatch, capsys) -> None:
    """manifest 记录的 sha 与实际 digest.md 内容不符(撕裂/篡改)时不展示正文。"""
    date_str = "2026-07-23"
    day_dir = tmp_path / date_str
    day_dir.mkdir(parents=True)
    (day_dir / "digest.md").write_text("REAL DIGEST 内容", encoding="utf-8")
    manifest = {
        "source_status": "complete",
        "window_start": "2026-07-22T16:30:00",
        "window_end": "2026-07-23T16:30:00",
        "push_status": "sent",
        "files": {"digest": {"path": "digest.md", "sha256": "deadbeef"}},
    }
    (day_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(service, "BASE_DIR", tmp_path)

    macro_flash._show(argparse.Namespace(date=date_str, json=False))

    out = capsys.readouterr().out
    assert "REAL DIGEST" not in out


def test_show_hides_digest_on_source_failed(tmp_path, monkeypatch, capsys) -> None:
    """source_failed 仍会归档"无命中"空 digest 且 sha 有效(formatter 无候选时的合法空正文),
    但采集本身是失败;不能因 sha 校验通过就把失败展示成正常空结果,误导人工/Agent 确认入库。"""
    date_str = "2026-07-23"
    day_dir = tmp_path / date_str
    day_dir.mkdir(parents=True)
    body = "窗口内无命中宏观快讯(原始 0 条)。"
    (day_dir / "digest.md").write_text(body, encoding="utf-8")
    real_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "source_status": "source_failed",
        "window_start": "2026-07-22T16:30:00",
        "window_end": "2026-07-23T16:30:00",
        "error": "boom",
        "push_status": "skipped",
        "files": {"digest": {"path": "digest.md", "sha256": real_sha}},
    }
    (day_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(service, "BASE_DIR", tmp_path)

    macro_flash._show(argparse.Namespace(date=date_str, json=False))

    out = capsys.readouterr().out
    assert "窗口内无命中" not in out
    assert "source_failed" in out


def test_show_prints_digest_when_sha_matches(tmp_path, monkeypatch, capsys) -> None:
    """manifest 声明的 sha 与当前 digest.md 匹配时正常展示正文。"""
    date_str = "2026-07-23"
    day_dir = tmp_path / date_str
    day_dir.mkdir(parents=True)
    body = "VALID DIGEST 正文"
    (day_dir / "digest.md").write_text(body, encoding="utf-8")
    real_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "source_status": "complete",
        "window_start": "2026-07-22T16:30:00",
        "window_end": "2026-07-23T16:30:00",
        "push_status": "sent",
        "files": {"digest": {"path": "digest.md", "sha256": real_sha}},
    }
    (day_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(service, "BASE_DIR", tmp_path)

    macro_flash._show(argparse.Namespace(date=date_str, json=False))

    out = capsys.readouterr().out
    assert "VALID DIGEST" in out
