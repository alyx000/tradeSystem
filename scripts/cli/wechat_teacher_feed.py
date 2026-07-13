from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from services.content_identity import (
    canonical_content_sha256,
    canonicalize_raw_content,
)
from services.wechat_teacher_feed.client import WeRSSClient
from services.wechat_teacher_feed.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_REFRESH_END_PAGE,
    DEFAULT_REFRESH_GRACE_SECONDS,
    MAX_REFRESH_END_PAGE,
    MAX_REFRESH_GRACE_SECONDS,
    WHITELIST,
)
from services.wechat_teacher_feed.models import CollectionOutcome, FeedError
from services.wechat_teacher_feed.normalize import normalize_wechat_url
from services.wechat_teacher_feed.schedule import PHASES, decide_phase
from services.wechat_teacher_feed.service import collect_phase
from services.wechat_teacher_feed.store import FeedStore


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REAL_WERSS_CLIENT = WeRSSClient
_OK_STATUSES = {"ok", "run", "skip", "empty", "success"}
_BLOCKED_STATUSES = {"blocked"}
_FATAL_IDENTITY_REASONS = {
    "ambiguous_article_identity",
    "ambiguous_article_provenance",
    "invalid_source_url",
    "source_content_changed",
    "source_identity_changed",
}


def status_exit_code(status: str) -> int:
    if status in _BLOCKED_STATUSES:
        return EXIT_BLOCKED
    if status in _OK_STATUSES:
        return EXIT_OK
    return EXIT_FAILED


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "wechat-teacher-feed",
        help="微信公众号老师观点白名单采集与候选查看",
    )
    commands = parser.add_subparsers(dest="wechat_teacher_feed_command")

    should_run = commands.add_parser("should-run", help="检查 phase 交易日门禁")
    should_run.add_argument("--phase", required=True, choices=PHASES)
    should_run.add_argument("--date", default=date.today().isoformat())
    should_run.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="检查 WeRSS 配置与白名单")
    doctor.add_argument("--json", action="store_true")

    collect = commands.add_parser("collect", help="采集并归档白名单公众号原文")
    collect.add_argument("--phase", required=True, choices=PHASES)
    collect.add_argument("--date", default=date.today().isoformat())
    collect.add_argument("--input-by", required=True)
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--force", action="store_true")
    collect.add_argument("--cached-only", action="store_true")
    collect.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="合并当日 manifest 并过滤已录笔记")
    show.add_argument("--date", default=date.today().isoformat())
    show.add_argument("--phase", choices=PHASES, default=None)
    show.add_argument("--json", action="store_true")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    del config
    command = getattr(args, "wechat_teacher_feed_command", None)
    if command == "should-run":
        payload = _handle_should_run(args)
    elif command == "doctor":
        payload = _handle_doctor(args)
    elif command == "collect":
        payload = _handle_collect(args)
    elif command == "show":
        payload = _handle_show(args)
    else:
        payload = {"status": "blocked", "reason": "subcommand_required"}
    _emit(payload, bool(getattr(args, "json", False)))
    return status_exit_code(str(payload.get("status") or ""))


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"[{payload.get('status', 'unknown')}] {payload.get('reason', '')}")
    for key in (
        "run_date",
        "phase",
        "target_trade_date",
        "new_count",
        "recorded_count",
        "manifest_path",
    ):
        if key in payload:
            print(f"  {key}: {payload[key]}")
    for source in payload.get("failed_sources") or []:
        print(
            "  source: "
            f"{source.get('teacher_name')} {source.get('status')} {source.get('reason')}"
        )


def _configured_db_path() -> Path:
    raw = os.environ.get("TRADE_DB_PATH")
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (PROJECT_ROOT / "data" / "trade.db").resolve()
    )


def _open_readonly_db() -> sqlite3.Connection:
    path = _configured_db_path()
    if not path.is_file():
        raise sqlite3.OperationalError("configured database is missing")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _calendar_lookup(target_date: str) -> bool | None:
    try:
        conn = _open_readonly_db()
        try:
            row = conn.execute(
                "SELECT is_open FROM trade_calendar WHERE date = ?",
                (target_date,),
            ).fetchone()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None
    if row is None:
        return None
    value = row[0]
    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1}:
        return None
    return bool(value)


def _handle_should_run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return asdict(
            decide_phase(args.date, args.phase, lookup=_calendar_lookup)
        )
    except ValueError as exc:
        return {"status": "blocked", "reason": _safe_reason(exc)}


def _manifest_path(store: FeedStore, run_date: str, phase: str) -> str:
    return str(store.root / run_date / phase / "manifest.json")


def _collect_payload(
    manifest: dict[str, Any], manifest_path: str | None
) -> dict[str, Any]:
    failed_sources = [
        {
            "teacher_name": row.get("teacher_name"),
            "status": row.get("status"),
            "reason": row.get("reason"),
        }
        for row in manifest.get("source_results") or []
        if row.get("status") != "ok"
    ]
    counts = manifest.get("counts") or {}
    return {
        "status": manifest.get("status", "source_failed"),
        "reason": manifest.get("reason", "unknown"),
        "run_date": manifest.get("run_date"),
        "phase": manifest.get("phase"),
        "target_trade_date": manifest.get("target_trade_date"),
        "new_count": int(counts.get("new") or 0),
        "candidate_count": int(counts.get("candidate") or 0),
        "pending_count": int(counts.get("pending") or 0),
        "failed_sources": failed_sources,
        "manifest_path": manifest_path,
        "manifest_digest": manifest.get("manifest_digest"),
        "dry_run": manifest.get("commit_state") == "dry_run",
        "cached_only": bool(manifest.get("cached_only")),
    }


def _failure_outcome(
    decision,
    input_by: str,
    *,
    status: str,
    reason: str,
    dry_run: bool,
    cached_only: bool,
) -> CollectionOutcome:
    return CollectionOutcome(
        run_date=decision.run_date,
        phase=decision.phase,
        target_trade_date=decision.target_trade_date,
        input_by=input_by,
        status=status,
        reason=reason,
        exit_code=status_exit_code(status),
        source_results=(),
        observations=(),
        dry_run=dry_run,
        cached_only=cached_only,
    )


def _persist_failure(
    store: FeedStore,
    decision,
    input_by: str,
    *,
    status: str,
    reason: str,
    dry_run: bool,
    cached_only: bool,
) -> dict[str, Any]:
    result = store.persist_phase(
        _failure_outcome(
            decision,
            input_by,
            status=status,
            reason=reason,
            dry_run=dry_run,
            cached_only=cached_only,
        )
    )
    return _collect_payload(result.manifest, result.manifest_path)


def _refresh_grace_seconds() -> float:
    raw = os.environ.get("WERSS_REFRESH_GRACE_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_REFRESH_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("invalid_refresh_grace_seconds") from exc
    if (
        not math.isfinite(value)
        or value < 0
        or value > MAX_REFRESH_GRACE_SECONDS
    ):
        raise ValueError("invalid_refresh_grace_seconds")
    return value


def _refresh_end_page() -> int:
    raw = os.environ.get("WERSS_REFRESH_END_PAGE")
    if raw is None or not raw.strip():
        return DEFAULT_REFRESH_END_PAGE
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError("invalid_refresh_end_page") from exc
    if value < 1 or value > MAX_REFRESH_END_PAGE:
        raise ValueError("invalid_refresh_end_page")
    return value


def _client_credentials() -> tuple[str, str, str]:
    base_url = (os.environ.get("WERSS_BASE_URL") or DEFAULT_BASE_URL).strip()
    access_key = (os.environ.get("WERSS_ACCESS_KEY") or "").strip()
    secret_key = (os.environ.get("WERSS_SECRET_KEY") or "").strip()
    if not access_key or not secret_key:
        raise FeedError("blocked", "missing_credentials")
    return base_url, access_key, secret_key


def _handle_collect(args: argparse.Namespace) -> dict[str, Any]:
    try:
        decision = decide_phase(
            args.date,
            args.phase,
            lookup=_calendar_lookup,
            force=bool(args.force),
        )
    except ValueError as exc:
        return {"status": "blocked", "reason": _safe_reason(exc)}

    store = FeedStore()
    if decision.status == "run" and not args.force:
        try:
            existing = store.read_manifest(decision.run_date, decision.phase)
        except FeedError as exc:
            return {
                "status": exc.status,
                "reason": exc.reason,
                "run_date": decision.run_date,
                "phase": decision.phase,
                "manifest_path": None,
            }
        if existing is not None:
            return _collect_payload(
                existing,
                _manifest_path(store, decision.run_date, decision.phase),
            )

    if decision.status in {"blocked", "skip"}:
        try:
            result = collect_phase(
                None,
                store,
                decision,
                args.input_by,
                dry_run=bool(args.dry_run),
                cached_only=bool(args.cached_only),
            )
            return _collect_payload(result.manifest, result.manifest_path)
        except (FeedError, OSError, ValueError) as exc:
            return {
                "status": getattr(exc, "status", "source_failed"),
                "reason": _safe_reason(exc),
                "run_date": decision.run_date,
                "phase": decision.phase,
                "manifest_path": None,
            }

    try:
        refresh_grace_seconds = _refresh_grace_seconds()
        refresh_end_page = _refresh_end_page()
        base_url, access_key, secret_key = _client_credentials()
        client = WeRSSClient(base_url, access_key, secret_key)
        result = collect_phase(
            client,
            store,
            decision,
            args.input_by,
            dry_run=bool(args.dry_run),
            cached_only=bool(args.cached_only),
            refresh_grace_seconds=refresh_grace_seconds,
            refresh_end_page=refresh_end_page,
        )
        return _collect_payload(result.manifest, result.manifest_path)
    except FeedError as exc:
        try:
            return _persist_failure(
                store,
                decision,
                args.input_by,
                status=exc.status,
                reason=exc.reason,
                dry_run=bool(args.dry_run),
                cached_only=bool(args.cached_only),
            )
        except (FeedError, OSError, ValueError):
            return {
                "status": exc.status,
                "reason": exc.reason,
                "run_date": decision.run_date,
                "phase": decision.phase,
                "manifest_path": None,
            }
    except (OSError, ValueError) as exc:
        try:
            return _persist_failure(
                store,
                decision,
                args.input_by,
                status="blocked",
                reason=_safe_reason(exc),
                dry_run=bool(args.dry_run),
                cached_only=bool(args.cached_only),
            )
        except (FeedError, OSError, ValueError):
            return {
                "status": "blocked",
                "reason": _safe_reason(exc),
                "run_date": decision.run_date,
                "phase": decision.phase,
                "manifest_path": None,
            }


def _doctor_base_payload() -> dict[str, Any]:
    return {
        "missing_env": [],
        "whitelist_total": len(WHITELIST),
    }


def _handle_doctor(args: argparse.Namespace) -> dict[str, Any]:
    del args
    payload = _doctor_base_payload()
    base_url = (os.environ.get("WERSS_BASE_URL") or DEFAULT_BASE_URL).strip()
    try:
        _REAL_WERSS_CLIENT._validate_base_url(base_url)
        _refresh_grace_seconds()
        _refresh_end_page()
    except (FeedError, ValueError) as exc:
        payload.update(
            status="blocked",
            reason=getattr(exc, "reason", _safe_reason(exc)),
        )
        return payload

    missing_env = [
        name
        for name in ("WERSS_ACCESS_KEY", "WERSS_SECRET_KEY")
        if not (os.environ.get(name) or "").strip()
    ]
    if missing_env:
        payload.update(
            status="blocked",
            reason="missing_credentials",
            missing_env=missing_env,
        )
        return payload

    try:
        client = WeRSSClient(
            base_url,
            str(os.environ["WERSS_ACCESS_KEY"]).strip(),
            str(os.environ["WERSS_SECRET_KEY"]).strip(),
        )
        enabled = [source for source in client.list_sources() if source.status == 1]
    except FeedError as exc:
        payload.update(status=exc.status, reason=exc.reason)
        return payload
    except Exception:
        payload.update(status="source_failed", reason="doctor_request_failed")
        return payload

    missing_sources: list[str] = []
    ambiguous_sources: list[str] = []
    matched = 0
    for source in WHITELIST:
        matches = [row for row in enabled if row.mp_name == source.teacher_name]
        if len(matches) == 1:
            matched += 1
        elif not matches:
            missing_sources.append(source.teacher_name)
        else:
            ambiguous_sources.append(source.teacher_name)
    if ambiguous_sources:
        status, reason = "source_failed", "ambiguous_source"
    elif missing_sources:
        status, reason = "source_missing", "source_missing"
    else:
        status, reason = "ok", "whitelist_ready"
    payload.update(
        status=status,
        reason=reason,
        matched=matched,
        missing=len(missing_sources),
        missing_sources=missing_sources,
        ambiguous_sources=ambiguous_sources,
    )
    return payload


def _safe_archive_path(store: FeedStore, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise FeedError("source_failed", "invalid_raw_content_path")
    try:
        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or relative == Path(".")
            or ".." in relative.parts
            or not relative.parts
            or store.root.is_symlink()
        ):
            raise ValueError("unsafe archive path")
        resolved_root = store.root.resolve(strict=False)
        current = store.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlinked archive path")
            current.resolve(strict=False).relative_to(resolved_root)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise FeedError("source_failed", "invalid_raw_content_path") from exc
    if current == store.root:
        raise FeedError("source_failed", "invalid_raw_content_path")
    return current


def _validate_candidate_archive(store: FeedStore, candidate: dict[str, Any]) -> None:
    path = _safe_archive_path(store, candidate.get("raw_content_path"))
    try:
        raw_content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FeedError("source_failed", "raw_content_missing") from exc
    except UnicodeError as exc:
        raise FeedError("source_failed", "content_hash_mismatch") from exc
    canonical = canonicalize_raw_content(raw_content)
    if (
        not canonical
        or canonical != raw_content
        or canonical_content_sha256(raw_content)
        != str(candidate.get("content_sha256") or "").lower()
    ):
        raise FeedError("source_failed", "content_hash_mismatch")


def _fallback_identity(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(candidate.get("teacher_name") or ""),
        str(candidate.get("date") or ""),
        str(candidate.get("title") or ""),
        str(candidate.get("content_sha256") or "").lower(),
    )


def _candidate_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_id = (
        str(left.get("source_platform") or ""),
        str(left.get("source_article_id") or ""),
    )
    right_id = (
        str(right.get("source_platform") or ""),
        str(right.get("source_article_id") or ""),
    )
    if all(left_id) and left_id == right_id:
        return True
    left_url = str(left.get("source_url") or "")
    right_url = str(right.get("source_url") or "")
    if left_url and left_url == right_url:
        return True
    fallback = _fallback_identity(left)
    return all(fallback) and fallback == _fallback_identity(right)


def _same_strong_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_identity = (
        str(left.get("source_platform") or ""),
        str(left.get("source_article_id") or ""),
    )
    right_identity = (
        str(right.get("source_platform") or ""),
        str(right.get("source_article_id") or ""),
    )
    return all(left_identity) and left_identity == right_identity


def _same_source_url_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_url = str(left.get("source_url") or "")
    right_url = str(right.get("source_url") or "")
    return bool(left_url) and left_url == right_url


def _normalized_candidate_url(candidate: dict[str, Any]) -> str:
    try:
        return normalize_wechat_url(str(candidate.get("source_url") or ""))
    except ValueError as exc:
        raise FeedError("source_failed", "invalid_source_url") from exc


def _merge_candidate(
    candidates: list[dict[str, Any]], incoming: dict[str, Any]
) -> None:
    matches = [
        index
        for index, existing in enumerate(candidates)
        if _candidate_matches(existing, incoming)
    ]
    if len(matches) > 1:
        raise FeedError("source_failed", "ambiguous_article_identity")
    if not matches:
        candidates.append(incoming)
        return
    existing = candidates[matches[0]]
    if str(existing.get("content_sha256") or "").lower() != str(
        incoming.get("content_sha256") or ""
    ).lower():
        raise FeedError("source_failed", "source_content_changed")
    for field in ("teacher_name", "source_platform", "source_account_id"):
        if str(existing.get(field) or "") != str(incoming.get(field) or ""):
            raise FeedError("source_failed", "ambiguous_article_provenance")
    same_strong_identity = _same_strong_identity(existing, incoming)
    if same_strong_identity or _same_source_url_identity(existing, incoming):
        stable_fields = ("title", "date", "published_at")
        stable_conflict = any(
            str(existing.get(field) or "") != str(incoming.get(field) or "")
            for field in stable_fields
        )
        url_conflict = same_strong_identity and (
            _normalized_candidate_url(existing) != _normalized_candidate_url(incoming)
        )
        if stable_conflict or url_conflict:
            raise FeedError("source_failed", "ambiguous_article_provenance")


def _existing_note(
    conn: sqlite3.Connection, candidate: dict[str, Any]
) -> tuple[int, str] | None:
    article_row = conn.execute(
        """
        SELECT id, content_sha256 FROM teacher_notes
        WHERE source_platform = ? AND source_article_id = ?
        """,
        (
            candidate.get("source_platform"),
            candidate.get("source_article_id"),
        ),
    ).fetchone()
    url_row = conn.execute(
        "SELECT id, content_sha256 FROM teacher_notes WHERE source_url = ?",
        (candidate.get("source_url"),),
    ).fetchone()
    if article_row is not None and url_row is not None and article_row["id"] != url_row["id"]:
        raise FeedError("source_failed", "ambiguous_provenance")
    row = article_row or url_row
    if row is not None:
        if str(row["content_sha256"] or "").lower() != str(
            candidate.get("content_sha256") or ""
        ).lower():
            raise FeedError("source_failed", "source_content_changed")
        return int(row["id"]), (
            "source_article_id" if article_row is not None else "source_url"
        )
    fallback = conn.execute(
        """
        SELECT tn.id FROM teacher_notes AS tn
        JOIN teachers AS t ON t.id = tn.teacher_id
        WHERE t.name = ? AND tn.date = ? AND tn.title = ?
          AND tn.content_sha256 = ?
        """,
        _fallback_identity(candidate),
    ).fetchone()
    if fallback is None:
        return None
    return int(fallback["id"]), "content_fallback"


def _issue(
    *, phase: str | None, reason: str, article_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reason": reason}
    if phase is not None:
        payload["phase"] = phase
    if article_id is not None:
        payload["source_article_id"] = article_id
    return payload


def _filtered_transactions(
    store: FeedStore, run_date: str, phase: str | None
) -> list[str]:
    prefix = f"{run_date}/{phase}/" if phase else f"{run_date}/"
    return [path for path in store.pending_transactions() if path.startswith(prefix)]


def _handle_show(args: argparse.Namespace) -> dict[str, Any]:
    try:
        parsed_date = date.fromisoformat(args.date)
        if parsed_date.isoformat() != args.date:
            raise ValueError("date must be valid YYYY-MM-DD")
    except (TypeError, ValueError):
        return {
            "run_date": args.date,
            "phase": args.phase,
            "status": "blocked",
            "reason": "date must be valid YYYY-MM-DD",
        }
    store = FeedStore()
    phases = (args.phase,) if args.phase else PHASES
    manifests: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    fatal_identity_conflict = False

    for phase in phases:
        try:
            manifest = store.read_manifest(args.date, phase)
        except FeedError as exc:
            issues.append(_issue(phase=phase, reason=exc.reason))
            continue
        if manifest is None:
            continue
        path = _manifest_path(store, args.date, phase)
        manifests.append(
            {
                "phase": phase,
                "status": manifest.get("status"),
                "reason": manifest.get("reason"),
                "manifest_path": path,
                "manifest_digest": manifest.get("manifest_digest"),
                "new_count": int((manifest.get("counts") or {}).get("new") or 0),
                "candidate_count": int(
                    (manifest.get("counts") or {}).get("candidate") or 0
                ),
            }
        )
        source_reasons = [
            str(row.get("reason") or "")
            for row in manifest.get("source_results") or []
            if isinstance(row, dict)
        ]
        fatal_reason = next(
            (
                reason
                for reason in [str(manifest.get("reason") or ""), *source_reasons]
                if reason in _FATAL_IDENTITY_REASONS
            ),
            None,
        )
        if fatal_reason is not None:
            fatal_identity_conflict = True
            issues.append(_issue(phase=phase, reason=fatal_reason))
            continue
        for raw_candidate in manifest.get("candidate_articles") or []:
            if not isinstance(raw_candidate, dict):
                issues.append(_issue(phase=phase, reason="invalid_candidate"))
                continue
            candidate = copy.deepcopy(raw_candidate)
            candidate["phase"] = phase
            candidate["manifest_digest"] = manifest.get("manifest_digest")
            candidate["manifest_path"] = path
            try:
                _validate_candidate_archive(store, candidate)
                _merge_candidate(merged, candidate)
            except FeedError as exc:
                if exc.reason in _FATAL_IDENTITY_REASONS:
                    fatal_identity_conflict = True
                issues.append(
                    _issue(
                        phase=phase,
                        reason=exc.reason,
                        article_id=str(candidate.get("source_article_id") or "") or None,
                    )
                )

    try:
        pending_transactions = _filtered_transactions(store, args.date, args.phase)
    except (FeedError, OSError, ValueError) as exc:
        pending_transactions = []
        fatal_identity_conflict = True
        issues.append(_issue(phase=args.phase, reason=_safe_reason(exc)))
    if fatal_identity_conflict:
        merged.clear()
    candidates: list[dict[str, Any]] = []
    recorded: list[dict[str, Any]] = []
    if merged:
        try:
            conn = _open_readonly_db()
            try:
                for candidate in merged:
                    try:
                        existing = _existing_note(conn, candidate)
                    except FeedError as exc:
                        issues.append(
                            _issue(
                                phase=str(candidate.get("phase") or "") or None,
                                reason=exc.reason,
                                article_id=str(
                                    candidate.get("source_article_id") or ""
                                ) or None,
                            )
                        )
                        continue
                    if existing is None:
                        candidates.append(candidate)
                        continue
                    note_id, matched_by = existing
                    item = copy.deepcopy(candidate)
                    item["already_recorded_note_id"] = note_id
                    item["matched_by"] = matched_by
                    recorded.append(item)
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            return {
                "run_date": args.date,
                "phase": args.phase,
                "status": "blocked",
                "reason": "teacher_notes_unavailable",
                "new_count": 0,
                "recorded_count": 0,
                "candidates": [],
                "recorded": [],
                "manifests": manifests,
                "issues": issues
                + [_issue(phase=args.phase, reason=_safe_reason(exc))],
                "pending_transactions": pending_transactions,
            }

    manifest_statuses = [str(item.get("status") or "") for item in manifests]
    if pending_transactions:
        status, reason = "partial", "transaction_pending"
    elif issues:
        status = "partial" if candidates else "source_failed"
        reason = str(issues[0].get("reason") or "show_integrity_failed")
    elif not manifests:
        status, reason = "source_missing", "no_complete_manifest"
    elif any(status_exit_code(item) != EXIT_OK for item in manifest_statuses):
        first = next(item for item in manifest_statuses if status_exit_code(item) != EXIT_OK)
        status, reason = (
            ("partial", "source_manifest_partial")
            if candidates
            else (first, "source_manifest_failed")
        )
    elif candidates:
        status, reason = "success", "candidates_ready"
    else:
        status, reason = "empty", "no_unrecorded_candidates"

    return {
        "run_date": args.date,
        "phase": args.phase,
        "status": status,
        "reason": reason,
        "new_count": len(candidates),
        "recorded_count": len(recorded),
        "candidates": candidates,
        "recorded": recorded,
        "manifests": manifests,
        "issues": issues,
        "pending_transactions": pending_transactions,
    }


def _safe_reason(exc: BaseException) -> str:
    if isinstance(exc, FeedError):
        return exc.reason
    text = str(exc).strip()
    allowed = {
        "invalid_refresh_grace_seconds",
        "invalid_refresh_end_page",
        "date must be valid YYYY-MM-DD",
    }
    return text if text in allowed else exc.__class__.__name__.lower()
