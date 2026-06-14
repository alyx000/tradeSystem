"""Antigravity CLI 诊断契约：Python/JS workflow 共用。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from utils.antigravity_diagnostics import build_diagnostics, is_global_failure

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "antigravity_diagnostics_cases.json"
JS_DIAGNOSTICS = Path(__file__).resolve().parents[1] / "workflows" / "antigravity_diagnostics.mjs"


def _cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_antigravity_diagnostics_shared_fixture_python(case, tmp_path):
    log_file = None
    if case.get("log_text"):
        log_file = tmp_path / "agy.log"
        log_file.write_text(case["log_text"], encoding="utf-8")

    diagnostics = build_diagnostics(
        stdout=case.get("stdout", ""),
        stderr=case.get("stderr", ""),
        log_file=log_file,
        reason=case.get("reason"),
        returncode=case.get("returncode"),
    )

    assert diagnostics["reason"] == case["expected_reason"]
    assert diagnostics["stdout_empty"] is case["expected_stdout_empty"]
    assert diagnostics["stderr_empty"] is case["expected_stderr_empty"]
    assert is_global_failure(diagnostics["reason"]) is case["expected_global"]
    assert case["expected_message_contains"].lower() in diagnostics["message"].lower()


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_antigravity_diagnostics_shared_fixture_js(case, tmp_path):
    case = dict(case)
    if case.get("log_text"):
        log_file = tmp_path / "agy.log"
        log_file.write_text(case["log_text"], encoding="utf-8")
        case["log_file"] = str(log_file)
    env = {**os.environ, "DIAGNOSTIC_CASE": json.dumps(case)}
    code = (
        f"import {{ diagnoseAntigravityFailure, isGlobalLlmFailure }} from {json.dumps(JS_DIAGNOSTICS.as_uri())};\n"
        "const c = JSON.parse(process.env.DIAGNOSTIC_CASE);\n"
        "const d = diagnoseAntigravityFailure({stdout: c.stdout || '', stderr: c.stderr || '', "
        "logFile: c.log_file || '', reason: c.reason || 'antigravity_failed', returncode: c.returncode ?? null});\n"
        "d.global = isGlobalLlmFailure(d.reason);\n"
        "console.log(JSON.stringify(d));\n"
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    diagnostics = json.loads(result.stdout)

    assert diagnostics["reason"] == case["expected_reason"]
    assert diagnostics["stdout_empty"] is case["expected_stdout_empty"]
    assert diagnostics["stderr_empty"] is case["expected_stderr_empty"]
    assert diagnostics["global"] is case["expected_global"]
    assert case["expected_message_contains"].lower() in diagnostics["message"].lower()


def test_antigravity_diagnostics_classifies_quota_from_log(tmp_path):
    log = tmp_path / "agy.log"
    log.write_text("RESOURCE_EXHAUSTED (code 429): Individual quota reached\n", encoding="utf-8")

    diagnostics = build_diagnostics(stdout="", stderr="", log_file=log, reason="empty_stdout")

    assert diagnostics["reason"] == "quota_exhausted"
    assert "RESOURCE_EXHAUSTED" in diagnostics["message"]
    assert diagnostics["log_file"] == str(log)
    assert diagnostics["stdout_empty"] is True
    assert diagnostics["stderr_empty"] is True
    assert is_global_failure(diagnostics["reason"]) is True


def test_antigravity_diagnostics_classifies_auth_and_startup():
    auth = build_diagnostics(
        stdout="Opening authentication page in your browser. Do you want to continue?",
        stderr="",
        reason="antigravity_failed",
    )
    startup = build_diagnostics(stdout="", stderr="ENOENT: agy", reason="startup_failed")

    assert auth["reason"] == "auth_required"
    assert is_global_failure(auth["reason"]) is True
    assert startup["reason"] == "startup_failed"
    assert is_global_failure(startup["reason"]) is True


def test_antigravity_diagnostics_keeps_parse_failed_local():
    diagnostics = build_diagnostics(stdout="not json", stderr="", reason="parse_failed")

    assert diagnostics["reason"] == "parse_failed"
    assert is_global_failure(diagnostics["reason"]) is False
