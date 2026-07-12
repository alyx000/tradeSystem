"""Antigravity CLI failure diagnostics shared by research-digest callers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

GLOBAL_FAILURE_REASONS = frozenset({"quota_exhausted", "auth_required", "startup_failed"})


def diag_reason(runner) -> str | None:
    """读 runner 最近一次失败诊断的 reason（无 last_diagnostics 属性的注入型 runner 返 None）。

    与 build_diagnostics（写侧）配对的读侧入口。board_break/pk、string_yang/mainline 等
    调用方按 reason 分流（如 timeout 不重试）；此处收拢 getattr/isinstance 守卫，避免各
    caller 内联重复该读法（与 runner 构造侧的 4 处同构一并属 invoke_llm_cli 收敛 defer）。
    """
    diag = getattr(runner, "last_diagnostics", None)
    return diag.get("reason") if isinstance(diag, dict) else None


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
        "message": diagnostic_message(combined, reason=detected_reason) or detected_reason,
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
    if (
        "resource_exhausted" in lower
        or "code 429" in lower
        or "quota reached" in lower
        or "quota exceeded" in lower
        or "individual quota" in lower
    ):
        return "quota_exhausted"
    if "not a valid artifact path" in lower or "invalid tool call error" in lower:
        return "agent_tool_error"
    if (
        not _auth_recovered(lower)
        and (
            "not logged in" in lower
            or "not authenticated" in lower
            or "unauthenticated" in lower
            or "opening authentication page" in lower
            or "authorization code" in lower
        )
    ):
        return "auth_required"
    if default == "startup_failed":
        return "startup_failed"
    if "timeout" in lower:
        return "timeout"
    return default


def diagnostic_message(text: str, *, reason: str | None = None) -> str:
    if not text:
        return ""
    preferred_by_reason = {
        "quota_exhausted": ("RESOURCE_EXHAUSTED", "code 429", "quota reached", "quota exceeded", "individual quota"),
        "auth_required": ("not logged in", "not authenticated", "authentication", "authorization code"),
        "agent_tool_error": ("not a valid artifact path", "invalid tool call error", "model output error"),
        "timeout": ("timeout", "timed out"),
    }
    preferred = preferred_by_reason.get(reason or "", (
        "RESOURCE_EXHAUSTED",
        "code 429",
        "quota reached",
        "quota exceeded",
        "individual quota",
        "not a valid artifact path",
        "invalid tool call error",
        "not logged in",
        "not authenticated",
        "authentication",
        "timeout",
    ))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if any(term.lower() in lower for term in preferred):
            return line[:500]
    return lines[0][:500] if lines else ""


def _auth_recovered(lower_text: str) -> bool:
    return (
        "auth succeeded" in lower_text
        or "applyauthresult:" in lower_text
        or "experiments refreshed after login" in lower_text
    )


def read_text_if_exists(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")[:32000]
    except OSError:
        return ""


def is_global_failure(reason: str | None) -> bool:
    return str(reason or "") in GLOBAL_FAILURE_REASONS
