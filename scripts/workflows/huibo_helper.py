#!/usr/bin/env python3
"""慧博研报 workflow 的 Python 能力层 JSON helper。

JS workflow 负责状态、日志、断点续跑和并发；本 helper 只复用现有 Python 业务能力：
候选采集、预筛、PDF 下载、聚合、渲染和清理。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from services.research_digest import huibo, narrator, renderer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="慧博 workflow JSON helper")
    sub = parser.add_subparsers(dest="command", required=True)

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

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--raw-dir", required=True)
    cleanup.add_argument("--summary-dir", required=True)
    cleanup.add_argument("--raw-retention-days", type=int, default=30)
    cleanup.add_argument("--summary-retention-days", type=int, default=180)
    cleanup.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "collect":
        payload = _cmd_collect(args)
    elif args.command == "prescreen":
        payload = _cmd_prescreen(args)
    elif args.command == "download":
        payload = _cmd_download(args)
    elif args.command == "finalize":
        payload = _cmd_finalize(args)
    elif args.command == "cleanup":
        payload = _cmd_cleanup(args)
    else:
        raise SystemExit(2)
    print(json.dumps(payload, ensure_ascii=False))


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
    for item in items:
        before = item.candidate.pdf_path
        candidate = huibo._ensure_pdf_available(item.candidate, args.raw_dir)  # noqa: SLF001
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
        out.append(_prescreened_json(item))
    _write_json(args.out, out)
    return {"status": "ok", "downloaded_count": downloaded, "missing_pdf_count": missing, "out": args.out}


def _cmd_finalize(args: argparse.Namespace) -> dict[str, Any]:
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
        else:
            reader = huibo._reader_error(c, "reader_failed")  # noqa: SLF001
        reader_results.append(huibo._reader_row(item, reader))  # noqa: SLF001

    has_reader_json = any(not (r.get("reader") or {}).get("error") for r in reader_results)
    llm_runner = None if args.no_llm else huibo.build_role_runner(narrator.build_antigravity_runner())
    aggregate_payload = {"date": args.date, "reports": reader_results}
    industry_summary = _call_finalize_agent(
        args.events_path,
        llm_runner,
        "industry_aggregator",
        aggregate_payload,
        enabled=llm_runner is not None and has_reader_json,
        skip_reason=_finalize_skip_reason(llm_runner, has_reader_json),
    ) or {}
    trend_payload = {
        "date": args.date,
        "lookback_days": args.lookback_days,
        "history": huibo._load_summary_history(Path(args.summary_dir), args.date, args.lookback_days),  # noqa: SLF001
        "today": {"reports": reader_results, "industry_summary": industry_summary},
    }
    trend_summary = _call_finalize_agent(
        args.events_path,
        llm_runner,
        "trend_aggregator",
        trend_payload,
        enabled=llm_runner is not None and has_reader_json,
        skip_reason=_finalize_skip_reason(llm_runner, has_reader_json),
    ) or {}
    rank_payload = {
        "date": args.date,
        "recommend_cap": args.recommend_cap,
        "reports": reader_results,
        "industry_summary": industry_summary,
        "trend_summary": trend_summary,
    }
    rank_result = _call_finalize_agent(
        args.events_path,
        llm_runner,
        "ranker",
        rank_payload,
        enabled=llm_runner is not None and has_reader_json,
        skip_reason=_finalize_skip_reason(llm_runner, has_reader_json),
    ) or {}
    recommendations = huibo._normalize_recommendations(  # noqa: SLF001
        rank_result,
        args.recommend_cap,
        reader_results=reader_results,
    )
    if not recommendations:
        recommendations = huibo._rank_recommendations(reader_results, recommend_cap=args.recommend_cap)  # noqa: SLF001

    digest = huibo.HuiboDigest(
        date=args.date,
        prescreened=items,
        reader_results=reader_results,
        industry_summary=industry_summary,
        trend_summary=trend_summary,
        recommendations=recommendations,
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
        "llm_agent_count": 0 if llm_runner is None or not has_reader_json else 3,
        "recommendation_count": len(recommendations),
        "summary": str(summary_path),
        "markdown": str(markdown_path),
    }


def _call_finalize_agent(
    events_path: str | None,
    llm_runner,
    role: str,
    payload: dict[str, Any],
    *,
    enabled: bool,
    skip_reason: str,
) -> dict[str, Any] | None:
    if not enabled:
        _write_event(events_path, "finalize_agent_skip", {"role": role, "reason": skip_reason})
        return None

    _write_event(events_path, "finalize_agent_start", {"role": role})
    started = time.perf_counter()
    result = huibo._safe_llm_call(llm_runner, role, payload)  # noqa: SLF001
    duration_ms = int((time.perf_counter() - started) * 1000)
    _write_event(
        events_path,
        "finalize_agent_end",
        {
            "role": role,
            "duration_ms": duration_ms,
            "used": isinstance(result, dict) and bool(result),
            "keys": sorted(result.keys()) if isinstance(result, dict) else [],
        },
    )
    return result


def _finalize_skip_reason(llm_runner, has_reader_json: bool) -> str:
    if llm_runner is None:
        return "llm_disabled"
    if not has_reader_json:
        return "no_successful_reader"
    return ""


def _write_event(events_path: str | None, event_name: str, payload: dict[str, Any]) -> None:
    if not events_path:
        return
    path = Path(events_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "event": event_name, **payload}
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
