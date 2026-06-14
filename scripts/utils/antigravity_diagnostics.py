"""Antigravity CLI failure diagnostics shared by research-digest callers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

GLOBAL_FAILURE_REASONS = frozenset({"quota_exhausted", "auth_required", "startup_failed"})


def build_diagnostics(
    *,
    stdout: str,
    stderr: str,
    log_file: str | Path | None = None,
    reason: str | None = None,
    returncode: int | None = None,
) -> dict[str, Any]:
    log_text = read_text_if_exists(log_file)
    combined = "\n".join(part for part in (stdout or "", stderr or "", log_text or "") if part)
    detected_reason = classify_failure(combined, default=reason or "antigravity_failed")
    out: dict[str, Any] = {
        "reason": detected_reason,
        "message": diagnostic_message(combined) or detected_reason,
        "stdout_empty": not bool((stdout or "").strip()),
        "stderr_empty": not bool((stderr or "").strip()),
    }
    if log_file:
        out["log_file"] = str(log_file)
    if returncode is not None:
        out["returncode"] = returncode
    return out


def classify_failure(text: str, *, default: str) -> str:
    lower = (text or "").lower()
    if "resource_exhausted" in lower or "code 429" in lower or "quota" in lower:
        return "quota_exhausted"
    if (
        "not logged in" in lower
        or "not authenticated" in lower
        or "unauthenticated" in lower
        or "opening authentication page" in lower
        or "authorization code" in lower
    ):
        return "auth_required"
    if default == "startup_failed":
        return "startup_failed"
    if "timeout" in lower:
        return "timeout"
    return default


def diagnostic_message(text: str) -> str:
    if not text:
        return ""
    preferred = (
        "RESOURCE_EXHAUSTED",
        "code 429",
        "quota",
        "not logged in",
        "not authenticated",
        "authentication",
        "timeout",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if any(term.lower() in lower for term in preferred):
            return line[:500]
    return lines[0][:500] if lines else ""


def read_text_if_exists(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""


def is_global_failure(reason: str | None) -> bool:
    return str(reason or "") in GLOBAL_FAILURE_REASONS
