#!/usr/bin/env python3
"""慧博研报 workflow 的 Python 能力层 JSON helper。

JS workflow 负责状态、日志、断点续跑和并发；本 helper 只复用现有 Python 业务能力：
候选采集、预筛、PDF 下载、聚合、渲染和清理。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from services.research_digest import huibo, narrator, renderer  # noqa: E402
from utils.antigravity_diagnostics import is_global_failure  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="慧博 workflow JSON helper")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve_date = sub.add_parser("resolve-date")
    resolve_date.add_argument("--date", required=True)

    should_run = sub.add_parser("should-run")
    should_run.add_argument("--date", required=True)

    collect = sub.add_parser("collect")
    collect.add_argument("--date", required=True)
    collect.add_argument("--window-days", type=int, default=5)
    collect.add_argument("--mode", choices=["desktop_terminal", "official_api"], default="desktop_terminal")
    collect.add_argument("--out", required=True)
    collect.add_argument("--texts-out", required=True)

    prescreen = sub.add_parser("prescreen")
    prescreen.add_argument("--candidates", required=True)
    prescreen.add_argument("--texts", required=True)
    prescreen.add_argument("--reader-cap", type=int, default=20)
    prescreen.add_argument("--out", required=True)

    download = sub.add_parser("download")
    download.add_argument("--prescreened", required=True)
    download.add_argument("--raw-dir", required=True)
    download.add_argument("--out", required=True)
    download.add_argument("--events-path")

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--date", required=True)
    finalize.add_argument("--prescreened", required=True)
    finalize.add_argument("--reader-dir", required=True)
    finalize.add_argument("--summary-dir", required=True)
    finalize.add_argument("--markdown-out", required=True)
    finalize.add_argument("--recommend-cap", type=int, default=2)
    finalize.add_argument("--lookback-days", type=int, default=5)
    finalize.add_argument("--no-llm", action="store_true")
    finalize.add_argument("--events-path")
    finalize.add_argument("--antigravity-status", default="ok")
    finalize.add_argument("--antigravity-reason", default="")
    finalize.add_argument("--antigravity-message", default="")
    finalize.add_argument("--antigravity-log-file", default="")

    publish = sub.add_parser("publish")
    publish.add_argument("--date", required=True)
    publish.add_argument("--markdown", required=True)
    publish.add_argument("--huibo-summary")
    publish.add_argument("--include-base-digest", action="store_true")
    publish.add_argument("--out-root", default="data/reports/research-digest")
    publish.add_argument("--out")
    publish.add_argument("--dry-run", action="store_true")
    publish.add_argument("--no-push", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--raw-dir", required=True)
    cleanup.add_argument("--summary-dir", required=True)
    cleanup.add_argument("--raw-retention-days", type=int, default=30)
    cleanup.add_argument("--summary-retention-days", type=int, default=180)
    cleanup.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "resolve-date":
        payload = _cmd_resolve_date(args)
    elif args.command == "should-run":
        payload = _cmd_should_run(args)
    elif args.command == "collect":
        payload = _cmd_collect(args)
    elif args.command == "prescreen":
        payload = _cmd_prescreen(args)
    elif args.command == "download":
        payload = _cmd_download(args)
    elif args.command == "finalize":
        payload = _cmd_finalize(args)
    elif args.command == "publish":
        payload = _cmd_publish(args)
    elif args.command == "cleanup":
        payload = _cmd_cleanup(args)
    else:
        raise SystemExit(2)
    print(json.dumps(payload, ensure_ascii=False))


def _cmd_resolve_date(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve workflow default date for the 22:30 scheduled research digest."""
    today = str(args.date)
    resolved, source = _resolve_workflow_date(today)
    return {"status": "ok", "date": resolved, "input_date": today, "source": source}


def _cmd_should_run(args: argparse.Namespace) -> dict[str, Any]:
    """Return whether the scheduled job should run today.

    Run on A-share trading days and on the calendar day before an A-share trading day.
    """
    today = str(args.date)
    decision = _trade_day_or_pre_trade_day(today)
    return {"status": "ok", "date": today, **decision}


def _trade_day_or_pre_trade_day(today: str) -> dict[str, Any]:
    next_day = (datetime.strptime(today, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    flags = _trade_day_flags_from_db(today, next_day)
    if flags.get("source") == "trade_calendar_db":
        today_open = flags.get("today_is_trade_day")
        next_open = flags.get("next_is_trade_day")
        if today_open is not None and next_open is not None:
            should_run = bool(today_open or next_open)
            reason = "trade_day" if today_open else ("pre_trade_day" if next_open else "not_trade_or_pre_trade_day")
            return {"should_run": should_run, "reason": reason, **flags}

    today_weekday = _is_weekday(today)
    next_weekday = _is_weekday(next_day)
    should_run = bool(today_weekday or next_weekday)
    reason = "trade_day_weekday_fallback" if today_weekday else (
        "pre_trade_day_weekday_fallback" if next_weekday else "not_trade_or_pre_trade_day_weekday_fallback"
    )
    return {
        "should_run": should_run,
        "reason": reason,
        "source": "weekday_fallback",
        "today_is_trade_day": today_weekday,
        "next_date": next_day,
        "next_is_trade_day": next_weekday,
    }


def _trade_day_flags_from_db(today: str, next_day: str) -> dict[str, Any]:
    from db.connection import _DEFAULT_DB_PATH  # noqa: PLC2701
    from db import queries as Q

    db_path = Path(os.environ.get("TRADE_DB_PATH") or _DEFAULT_DB_PATH)
    if not db_path.exists():
        return {"source": "missing_db", "next_date": next_day}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return {"source": "db_error", "next_date": next_day}
    try:
        return {
            "source": "trade_calendar_db",
            "today_is_trade_day": Q.is_trade_day_from_db(conn, today),
            "next_date": next_day,
            "next_is_trade_day": Q.is_trade_day_from_db(conn, next_day),
        }
    except sqlite3.Error:
        return {"source": "db_error", "next_date": next_day}
    finally:
        conn.close()


def _is_weekday(date: str) -> bool:
    return datetime.strptime(date, "%Y-%m-%d").date().weekday() < 5


def _resolve_workflow_date(today: str) -> tuple[str, str]:
    next_day = (datetime.strptime(today, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    flags = _trade_day_flags_from_db(today, next_day)
    if flags.get("source") == "trade_calendar_db":
        if flags.get("today_is_trade_day") is True or flags.get("next_is_trade_day") is True:
            return today, "trade_calendar_db"
        if flags.get("today_is_trade_day") is False and flags.get("next_is_trade_day") is False:
            db_date = _prev_trade_date_from_db(today)
            if db_date:
                return db_date, "prev_trade_calendar_db"

    today_weekday = _is_weekday(today)
    next_weekday = _is_weekday(next_day)
    if today_weekday or next_weekday:
        return today, "weekday_fallback"
    return _prev_weekday(today), "prev_weekday_fallback"


def _resolve_prev_trade_date(today: str) -> tuple[str, str]:
    db_date = _prev_trade_date_from_db(today)
    if db_date:
        return db_date, "trade_calendar_db"
    return _prev_weekday(today), "weekday_fallback"


def _prev_trade_date_from_db(today: str) -> str | None:
    from db.connection import _DEFAULT_DB_PATH  # noqa: PLC2701
    from db import queries as Q

    db_path = Path(os.environ.get("TRADE_DB_PATH") or _DEFAULT_DB_PATH)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        return Q.get_prev_trade_date_from_db(conn, today)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _prev_weekday(today: str) -> str:
    d = datetime.strptime(today, "%Y-%m-%d").date()
    for delta in range(1, 16):
        candidate = d - timedelta(days=delta)
        if candidate.weekday() < 5:
            return candidate.isoformat()
    return (d - timedelta(days=1)).isoformat()


def _cmd_collect(args: argparse.Namespace) -> dict[str, Any]:
    source = huibo.build_source_from_env(args.mode)
    candidates, texts = source(None, args.date, args.window_days) if source else ([], {})
    rows = [_candidate_json(c) for c in candidates]
    _write_json(args.out, rows)
    _write_json(args.texts_out, texts)
    return {"status": "ok", "candidate_count": len(rows), "out": args.out, "texts_out": args.texts_out}


def _cmd_prescreen(args: argparse.Namespace) -> dict[str, Any]:
    candidates = [_candidate_from_json(row) for row in _read_json(args.candidates)]
    texts = _read_json(args.texts)
    prescreened = huibo.prescreen_candidates(candidates, reader_cap=args.reader_cap, preview_texts=texts)
    rows = [_prescreened_json(item) for item in prescreened]
    _write_json(args.out, rows)
    return {"status": "ok", "prescreened_count": len(rows), "out": args.out}


def _cmd_download(args: argparse.Namespace) -> dict[str, Any]:
    items = [_prescreened_from_json(row) for row in _read_json(args.prescreened)]
    out = []
    downloaded = 0
    missing = 0
    missing_reasons: list[str] = []
    for item in items:
        candidate, download_diag = huibo._ensure_pdf_available_with_diagnostics(item.candidate, args.raw_dir)  # noqa: SLF001
        item = huibo.PrescreenedCandidate(
            candidate=candidate,
            score=item.score,
            reasons=item.reasons,
            topic_key=item.topic_key,
        )
        if candidate.pdf_path and Path(candidate.pdf_path).exists():
            downloaded += 1
        else:
            missing += 1
            missing_reasons.append(str(download_diag.get("reason") or "unknown"))
            _write_event(getattr(args, "events_path", None), "pdf_download_missing", {
                "report_id": candidate.report_id,
                "title": candidate.title,
                "diagnostics": download_diag,
            })
        row = _prescreened_json(item)
        row["pdf_download"] = download_diag
        out.append(row)
    _write_json(args.out, out)
    result = {"status": "ok", "downloaded_count": downloaded, "missing_pdf_count": missing, "out": args.out}
    if items and downloaded == 0 and missing == len(items) and set(missing_reasons) == {"hibor_mb404"}:
        result.update({
            "source_status": "huibo_token_expired",
            "reason": "hibor_mb404_all",
            "message": (
                "慧博 PDF 全部跳转 mb404，通常表示 HUIBO_HOT_REPORT_URL token 已过期；"
                "请打开/刷新慧博终端，或启用 HUIBO_REFRESH_URL_FROM_APP 自动读取当前终端 URL。"
            ),
        })
        _write_event(getattr(args, "events_path", None), "huibo_token_expired", {
            "reason": "hibor_mb404_all",
            "candidate_count": len(items),
            "message": result["message"],
        })
    if items and downloaded == 0 and missing == len(items) and set(missing_reasons) == {"terminal_pdf_missing"}:
        result.update({
            "source_status": "terminal_pdf_missing",
            "reason": "terminal_pdf_missing_all",
            "message": (
                "慧博候选已采集，但未找到慧博终端实际下载/导出的本地 PDF；"
                "请确认 HUIBO_REPORT_PDF_DIR 指向慧博终端下载目录。"
            ),
        })
        _write_event(getattr(args, "events_path", None), "terminal_pdf_missing", {
            "reason": "terminal_pdf_missing_all",
            "candidate_count": len(items),
            "message": result["message"],
        })
    return result


def _cmd_finalize(args: argparse.Namespace) -> dict[str, Any]:
    antigravity_meta = _antigravity_meta_from_args(args)
    if args.no_llm:
        antigravity_meta = {"status": "off"}
    antigravity_unavailable = antigravity_meta.get("status") == "unavailable"
    items = [_prescreened_from_json(row) for row in _read_json(args.prescreened)]
    reader_dir = Path(args.reader_dir)
    reader_results = []
    for item in items:
        c = item.candidate
        path = reader_dir / f"{c.report_id}.json"
        if path.exists():
            payload = _read_json(path)
            reader = payload.get("reader") if isinstance(payload, dict) and "reader" in payload else payload
            if not isinstance(reader, dict):
                reader = huibo._reader_error(c, "reader_failed")  # noqa: SLF001
            elif not reader.get("error"):
                reader = huibo._normalize_reader_result(reader)  # noqa: SLF001
                reader = _apply_reader_quality(reader)
        else:
            reader = huibo._reader_error(c, "reader_failed")  # noqa: SLF001
        reader_results.append(huibo._reader_row(item, reader))  # noqa: SLF001

    has_reader_json = any(not (r.get("reader") or {}).get("error") for r in reader_results)
    if args.no_llm or antigravity_unavailable:
        llm_runner = None
    else:
        llm_runner = huibo.build_role_runner(narrator.build_antigravity_runner())
    agent_status: dict[str, Any] = {}
    skip_reason = _finalize_skip_reason(
        llm_runner,
        has_reader_json,
        antigravity_unavailable=antigravity_unavailable,
    )
    aggregate_payload = {"date": args.date, "reports": reader_results}
    industry_summary, llm_runner, skip_reason, antigravity_meta, antigravity_unavailable = (
        _call_finalize_agent_guarded(
            args.events_path,
            llm_runner,
            "industry_aggregator",
            aggregate_payload,
            enabled=llm_runner is not None and has_reader_json,
            skip_reason=skip_reason,
            status_sink=agent_status,
            antigravity_meta=antigravity_meta,
            antigravity_unavailable=antigravity_unavailable,
        )
    )
    trend_payload = {
        "date": args.date,
        "lookback_days": args.lookback_days,
        "history": huibo._load_summary_history(Path(args.summary_dir), args.date, args.lookback_days),  # noqa: SLF001
        "today": {"reports": reader_results, "industry_summary": industry_summary},
    }
    trend_summary, llm_runner, skip_reason, antigravity_meta, antigravity_unavailable = (
        _call_finalize_agent_guarded(
            args.events_path,
            llm_runner,
            "trend_aggregator",
            trend_payload,
            enabled=llm_runner is not None and has_reader_json,
            skip_reason=skip_reason,
            status_sink=agent_status,
            antigravity_meta=antigravity_meta,
            antigravity_unavailable=antigravity_unavailable,
        )
    )
    rank_payload = {
        "date": args.date,
        "recommend_cap": args.recommend_cap,
        "reports": reader_results,
        "industry_summary": industry_summary,
        "trend_summary": trend_summary,
    }
    rank_result, llm_runner, skip_reason, antigravity_meta, antigravity_unavailable = (
        _call_finalize_agent_guarded(
            args.events_path,
            llm_runner,
            "ranker",
            rank_payload,
            enabled=llm_runner is not None and has_reader_json,
            skip_reason=skip_reason,
            status_sink=agent_status,
            antigravity_meta=antigravity_meta,
            antigravity_unavailable=antigravity_unavailable,
        )
    )
    recommendations = huibo._normalize_recommendations(  # noqa: SLF001
        rank_result,
        args.recommend_cap,
        reader_results=reader_results,
    )
    ranker_used = bool(recommendations)
    if not recommendations:
        recommendations = huibo._rank_recommendations(reader_results, recommend_cap=args.recommend_cap)  # noqa: SLF001
    ranker_status = {
        "status": "agent" if ranker_used else "fallback",
        "reason": "" if ranker_used else _fallback_reason(
            antigravity_meta,
            llm_runner=llm_runner,
            has_reader_json=has_reader_json,
        ),
    }
    if antigravity_meta:
        ranker_status["antigravity"] = antigravity_meta
    llm_agent_counts = _llm_agent_counts(agent_status)
    meta = {"agents": agent_status, "ranker": ranker_status, **llm_agent_counts}
    if antigravity_meta:
        meta["antigravity"] = antigravity_meta

    digest = huibo.HuiboDigest(
        date=args.date,
        prescreened=items,
        reader_results=reader_results,
        industry_summary=industry_summary,
        trend_summary=trend_summary,
        recommendations=recommendations,
        meta=meta,
    )
    summary_dir = Path(args.summary_dir)
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"{args.date}.json"
    payload = digest.to_jsonable()
    _write_json(summary_path, payload)
    _title, markdown = renderer.render_md(args.date, [], [], [], huibo_digest=payload)
    markdown_path = Path(args.markdown_out)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "status": "ok",
        "reader_count": len(reader_results),
        "llm_agent_count": llm_agent_counts["llm_agent_used_count"],
        "llm_agent_attempted_count": llm_agent_counts["llm_agent_attempted_count"],
        "llm_agent_skipped_count": llm_agent_counts["llm_agent_skipped_count"],
        "llm_agent_used_count": llm_agent_counts["llm_agent_used_count"],
        "recommendation_count": len(recommendations),
        "llm_status": antigravity_meta.get("status") or ("off" if args.no_llm else "ok"),
        "llm_failure_reason": antigravity_meta.get("reason", "") if antigravity_meta.get("status") == "unavailable" else "",
        "llm_failure_message": antigravity_meta.get("message", "") if antigravity_meta.get("status") == "unavailable" else "",
        "llm_failure_log_file": antigravity_meta.get("log_file", "") if antigravity_meta.get("status") == "unavailable" else "",
        "reader_success_count": _reader_success_count(reader_results),
        "reader_failed_count": _reader_failed_count(reader_results),
        "reader_skipped_count": _reader_skipped_count(reader_results),
        "ranker_status": ranker_status["status"],
        "summary": str(summary_path),
        "markdown": str(markdown_path),
    }


def _call_finalize_agent_guarded(
    events_path: str | None,
    llm_runner,
    role: str,
    payload: dict[str, Any],
    *,
    enabled: bool,
    skip_reason: str,
    status_sink: dict[str, Any],
    antigravity_meta: dict[str, Any],
    antigravity_unavailable: bool,
) -> tuple[dict[str, Any], Any, str, dict[str, Any], bool]:
    result = _call_finalize_agent(
        events_path,
        llm_runner,
        role,
        payload,
        enabled=enabled,
        skip_reason=skip_reason,
        status_sink=status_sink,
    ) or {}
    global_diagnostics = _global_failure_from_agent_status(status_sink.get(role))
    if global_diagnostics:
        antigravity_meta = _antigravity_meta_from_diagnostics(global_diagnostics)
        antigravity_unavailable = True
        llm_runner = None
        skip_reason = "antigravity_unavailable"
    return result, llm_runner, skip_reason, antigravity_meta, antigravity_unavailable


def _call_finalize_agent(
    events_path: str | None,
    llm_runner,
    role: str,
    payload: dict[str, Any],
    *,
    enabled: bool,
    skip_reason: str,
    status_sink: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not enabled:
        _write_event(events_path, "finalize_agent_skip", {"role": role, "reason": skip_reason})
        if status_sink is not None:
            status_sink[role] = {"status": "skipped", "reason": skip_reason}
        return None

    _write_event(events_path, "finalize_agent_start", {"role": role})
    started = time.perf_counter()
    result = huibo._safe_llm_call(llm_runner, role, payload)  # noqa: SLF001
    duration_ms = int((time.perf_counter() - started) * 1000)
    used = isinstance(result, dict) and bool(result)
    diagnostics = _runner_diagnostics(llm_runner)
    _write_event(
        events_path,
        "finalize_agent_end",
        {
            "role": role,
            "duration_ms": duration_ms,
            "used": used,
            "keys": sorted(result.keys()) if isinstance(result, dict) else [],
            **({"diagnostics": diagnostics} if diagnostics else {}),
        },
    )
    if status_sink is not None:
        status_sink[role] = {
            "status": "used" if used else "failed",
            **({"diagnostics": diagnostics} if diagnostics else {}),
        }
    return result


def _finalize_skip_reason(llm_runner, has_reader_json: bool, *, antigravity_unavailable: bool = False) -> str:
    if antigravity_unavailable:
        return "antigravity_unavailable"
    if llm_runner is None:
        return "llm_disabled"
    if not has_reader_json:
        return "no_successful_reader"
    return ""


def _fallback_reason(antigravity_meta: dict[str, Any], *, llm_runner, has_reader_json: bool) -> str:
    if antigravity_meta.get("status") == "unavailable":
        return str(antigravity_meta.get("reason") or "antigravity_unavailable")
    if llm_runner is None:
        return "llm_disabled"
    if not has_reader_json:
        return "no_successful_reader"
    return "ranker_failed"


def _global_failure_from_agent_status(status: Any) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {}
    diagnostics = status.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return {}
    return diagnostics if is_global_failure(str(diagnostics.get("reason") or "")) else {}


def _antigravity_meta_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "status": "unavailable",
        "reason": str(diagnostics.get("reason") or "antigravity_unavailable"),
        "message": str(diagnostics.get("message") or diagnostics.get("reason") or "antigravity_unavailable"),
        "log_file": str(diagnostics.get("log_file") or ""),
    }
    return {k: v for k, v in meta.items() if v}


def _runner_diagnostics(llm_runner) -> dict[str, Any]:
    diagnostics = getattr(llm_runner, "last_diagnostics", None)
    return _sanitize_public_diagnostics(diagnostics)


def _antigravity_meta_from_args(args: argparse.Namespace) -> dict[str, Any]:
    status = str(getattr(args, "antigravity_status", "") or "").strip()
    if not status or status == "ok":
        return {"status": "ok"}
    meta = {
        "status": status,
        "reason": str(getattr(args, "antigravity_reason", "") or "").strip(),
        "message": str(getattr(args, "antigravity_message", "") or "").strip(),
        "log_file": str(getattr(args, "antigravity_log_file", "") or "").strip(),
    }
    return {k: v for k, v in meta.items() if v}


def _sanitize_public_diagnostics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = ("reason", "message", "log_file", "returncode", "stdout_empty", "stderr_empty")
    return {k: value[k] for k in allowed if k in value and value[k] not in ("", None)}


def _reader_success_count(reader_results: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in reader_results
        if isinstance(row, dict)
        and isinstance(row.get("reader"), dict)
        and not row["reader"].get("error")
    )


def _reader_skipped_count(reader_results: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in reader_results
        if isinstance(row, dict)
        and isinstance(row.get("reader"), dict)
        and row["reader"].get("error") == "skipped_llm_unavailable"
    )


def _reader_failed_count(reader_results: list[dict[str, Any]]) -> int:
    return max(0, len(reader_results) - _reader_success_count(reader_results) - _reader_skipped_count(reader_results))


def _apply_reader_quality(reader: dict[str, Any]) -> dict[str, Any]:
    quality = _reader_quality(reader)
    out = {**reader, "quality": quality}
    if quality["status"] == "failed":
        out["error"] = "quality_failed"
    return out


def _reader_quality(reader: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    text = " ".join(
        str(part or "")
        for part in [
            reader.get("viewpoint"),
            reader.get("recommend_reason"),
            *(reader.get("key_points") or []),
            *[
                f"{stock.get('viewpoint', '')} {stock.get('source', '')}"
                for stock in (reader.get("mentioned_stocks") or [])
                if isinstance(stock, dict)
            ],
        ]
    )
    redline_terms = ("目标价", "买入", "卖出", "仓位", "止损", "止盈", "target price")
    if any(term.lower() in text.lower() for term in redline_terms):
        issues.append("redline_terms")
    if any(isinstance(stock, dict) and not str(stock.get("source") or "").strip() for stock in reader.get("mentioned_stocks") or []):
        issues.append("missing_stock_source")
    if not str(reader.get("viewpoint") or "").strip() and not reader.get("key_points"):
        issues.append("empty_viewpoint")
    status = "failed" if "redline_terms" in issues else ("warning" if issues else "pass")
    return {"status": status, "issues": issues}


def _llm_agent_counts(agent_status: dict[str, Any]) -> dict[str, int]:
    statuses = [row.get("status") for row in agent_status.values() if isinstance(row, dict)]
    attempted = sum(1 for status in statuses if status in {"used", "failed"})
    skipped = sum(1 for status in statuses if status == "skipped")
    used = sum(1 for status in statuses if status == "used")
    return {
        "llm_agent_attempted_count": attempted,
        "llm_agent_skipped_count": skipped,
        "llm_agent_used_count": used,
    }


def _cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    markdown_path = Path(args.markdown)
    markdown = markdown_path.read_text(encoding="utf-8")
    title = _markdown_title(markdown, args.date)
    include_base_digest = bool(getattr(args, "include_base_digest", False))
    base_digest_included = False
    base_digest_error = ""
    base_digest_duration_ms = 0
    if include_base_digest:
        started = time.perf_counter()
        try:
            base_digest = _render_base_digest(args)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[huibo-workflow] 基础研报速读渲染失败，继续发布慧博正文: %s", exc)
            base_digest = None
            base_digest_error = str(exc)
        finally:
            base_digest_duration_ms = int((time.perf_counter() - started) * 1000)
        if base_digest is not None and getattr(base_digest, "markdown", ""):
            title = getattr(base_digest, "title", title) or title
            markdown = _merge_base_and_huibo_markdown(base_digest.markdown, markdown)
            base_digest_included = True
    huibo_summary = _read_json(args.huibo_summary) if args.huibo_summary else {}
    huibo_meta = _huibo_meta_from_summary(huibo_summary)
    out_root = Path(args.out_root)
    output_md = out_root / f"{args.date}.md"
    published = {
        "status": "dry_run" if args.dry_run else "published",
        "date": args.date,
        "title": title,
        "markdown": str(markdown_path if args.dry_run else output_md),
        "pushed": False,
        "dry_run": bool(args.dry_run),
        "include_base_digest": include_base_digest,
        "base_digest_included": base_digest_included,
        "base_digest_error": base_digest_error,
        "base_digest_duration_ms": base_digest_duration_ms,
        "huibo_antigravity": huibo_meta.get("antigravity", {}),
        "huibo_ranker": huibo_meta.get("ranker", {}),
        "llm_agent_attempted_count": huibo_meta.get("llm_agent_attempted_count", 0),
        "llm_agent_skipped_count": huibo_meta.get("llm_agent_skipped_count", 0),
        "llm_agent_used_count": huibo_meta.get("llm_agent_used_count", 0),
    }
    if not args.dry_run:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(markdown, encoding="utf-8")
        if not args.no_push:
            published["pushed"] = _push_to_dingtalk(title, markdown)
    out = Path(args.out) if args.out else markdown_path.with_name("published.json")
    _write_json(out, published)
    return published


def _render_base_digest(args: argparse.Namespace):
    from main import load_config, setup_providers
    from services.research_digest import run_daily_digest

    config = load_config()
    registry = setup_providers(config)
    registry.initialize_all()
    return run_daily_digest(
        registry,
        args.date,
        no_llm=True,
        huibo_mode="off",
    )


def _merge_base_and_huibo_markdown(base_markdown: str, huibo_markdown: str) -> str:
    huibo_body = _extract_huibo_markdown_body(huibo_markdown).strip()
    if not huibo_body:
        return base_markdown
    marker = "\n---\n> 本报告"
    idx = base_markdown.find(marker)
    if idx != -1:
        return base_markdown[:idx].rstrip() + "\n\n" + huibo_body + "\n" + base_markdown[idx:]
    return base_markdown.rstrip() + "\n\n" + huibo_body


def _extract_huibo_markdown_body(markdown: str) -> str:
    body = _strip_markdown_title(markdown)
    lines = body.splitlines()
    start = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and "慧博" in stripped:
            start = idx
            break
    if start is None:
        notices = [
            line
            for line in lines
            if "ranker=fallback" in line or "Antigravity 不可用" in line
        ]
        return "\n".join(notices).strip()

    out: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## 🇨🇳 A股机构评级") or stripped.startswith("## 🇺🇸 美股评级变动"):
            break
        if stripped == "---":
            break
        out.append(line)
    return "\n".join(out).strip()


def _strip_report_disclaimer(markdown: str) -> str:
    marker = "\n---\n> 本报告"
    idx = markdown.find(marker)
    if idx != -1:
        return markdown[:idx]
    return markdown


def _strip_markdown_title(markdown: str) -> str:
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines)


def _huibo_meta_from_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    meta = summary.get("meta") if isinstance(summary.get("meta"), dict) else {}
    antigravity = meta.get("antigravity") if isinstance(meta.get("antigravity"), dict) else {}
    ranker = meta.get("ranker") if isinstance(meta.get("ranker"), dict) else {}
    return {
        "antigravity": antigravity,
        "ranker": ranker,
        "llm_agent_attempted_count": int(meta.get("llm_agent_attempted_count") or 0),
        "llm_agent_skipped_count": int(meta.get("llm_agent_skipped_count") or 0),
        "llm_agent_used_count": int(meta.get("llm_agent_used_count") or 0),
    }


def _markdown_title(markdown: str, date: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip() or f"研报速读 · {date}"
    return f"研报速读 · {date}"


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        return False
    return pusher.send_markdown(title=title, content=markdown)


def _write_event(events_path: str | None, event_name: str, payload: dict[str, Any]) -> None:
    if not events_path:
        return
    path = Path(events_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event_name,
        "invocation_id": os.getenv("HUIBO_WORKFLOW_INVOCATION_ID", ""),
        **payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _cmd_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    result = huibo.cleanup_storage(
        args.raw_dir,
        args.summary_dir,
        raw_retention_days=args.raw_retention_days,
        summary_retention_days=args.summary_retention_days,
        dry_run=args.dry_run,
    )
    return {
        "status": "ok",
        "raw_count": len(result.raw_files),
        "summary_count": len(result.summary_files),
        "dry_run": result.dry_run,
    }


def _candidate_json(candidate: huibo.HuiboCandidate) -> dict[str, Any]:
    data = asdict(candidate)
    data["report_id"] = candidate.report_id
    return data


def _candidate_from_json(row: dict[str, Any]) -> huibo.HuiboCandidate:
    data = dict(row)
    data.pop("report_id", None)
    return huibo.HuiboCandidate(**data)


def _prescreened_json(item: huibo.PrescreenedCandidate) -> dict[str, Any]:
    return {
        "candidate": _candidate_json(item.candidate),
        "score": item.score,
        "reasons": item.reasons,
        "topic_key": item.topic_key,
    }


def _prescreened_from_json(row: dict[str, Any]) -> huibo.PrescreenedCandidate:
    return huibo.PrescreenedCandidate(
        candidate=_candidate_from_json(row["candidate"]),
        score=float(row.get("score") or 0),
        reasons=list(row.get("reasons") or []),
        topic_key=str(row.get("topic_key") or ""),
    )


def _read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
