from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.trinity_factor.runner import (
    FACTOR_PROMPT_VERSION,
    AntigravityStructuredRunner,
    _compose_prompt,
    _flatten_error_values,
    prompt_template_sha256,
)
from services.trinity_factor.validation import TrinityValidationError

_SCHEMA_VERSION = "test-schema-v1"
_RULESET_VERSION = "test-rules-v1"


def _run(runner: AntigravityStructuredRunner, **kwargs):
    return runner.run(
        schema_version=_SCHEMA_VERSION,
        ruleset_version=_RULESET_VERSION,
        **kwargs,
    )


def _config(monkeypatch: pytest.MonkeyPatch, *, model: str = "test-model") -> None:
    monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.setenv("ANTIGRAVITY_BIN", "/fake/agy")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "180")


def _validate(raw: str) -> dict:
    payload = json.loads(raw)
    if payload != {"schema_version": "ok", "items": []}:
        raise TrinityValidationError("schema mismatch")
    return payload


def test_runner_requires_explicit_non_empty_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_MODEL", raising=False)
    calls = []

    result = _run(
        AntigravityStructuredRunner(run_command=lambda *a, **k: calls.append((a, k))),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={"factors": []},
        validator=_validate,
    )

    assert result.status == "misconfigured"
    assert result.requested_model == ""
    assert result.attempt_count == 0
    assert result.is_cacheable is False
    assert calls == []


def test_runner_success_is_sandboxed_versioned_and_auditable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _config(monkeypatch)
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path / "logs"))
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="agy 1.2.3\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"schema_version":"ok","items":[]}',
            stderr="",
        )

    payload = {
        "factors": [
            {"factor_code": "sector_rhythm", "evidence": []},
            {"factor_code": "market_node", "evidence": []},
        ]
    }
    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload=payload,
        validator=_validate,
    )

    assert result.status == "success"
    assert result.provider == "antigravity"
    assert result.requested_model == "test-model"
    assert result.actual_model is None
    assert result.cli_version == "agy 1.2.3"
    assert result.runtime_version.startswith("python-")
    assert result.prompt_version == FACTOR_PROMPT_VERSION
    assert result.schema_version == _SCHEMA_VERSION
    assert result.ruleset_version == _RULESET_VERSION
    assert len(result.prompt_sha256) == 64
    assert result.prompt_sha256 == prompt_template_sha256(
        FACTOR_PROMPT_VERSION, "instructions"
    )
    assert result.prompt_sha256 != hashlib.sha256(b"instructions").hexdigest()
    assert len(result.input_digest) == 64
    assert result.attempt_count == 1
    assert result.is_cacheable is True
    assert result.valid_raw_json == {"schema_version": "ok", "items": []}
    assert result.parsed_output == {"schema_version": "ok", "items": []}
    assert result.raw_output_sha256 == hashlib.sha256(
        b'{"schema_version":"ok","items":[]}'
    ).hexdigest()

    command, kwargs = calls[-1]
    assert command[:3] == ["/fake/agy", "--print-timeout", "180s"]
    assert "--sandbox" in command
    assert command[command.index("--mode") + 1] == "plan"
    assert command[command.index("--model") + 1] == "test-model"
    assert "--dangerously-skip-permissions" not in command
    assert "--add-dir" not in command
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 180
    assert kwargs["cwd"]
    assert not Path(kwargs["cwd"]).exists(), "temporary cwd must be removed after the call"


def test_input_digest_and_prompt_input_are_stable_across_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    prompts: list[str] = []

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        prompts.append(command[command.index("--prompt") + 1])
        return subprocess.CompletedProcess(
            command, 0, stdout='{"schema_version":"ok","items":[]}', stderr=""
        )

    runner = AntigravityStructuredRunner(run_command=fake_run)
    first = {"factors": [{"factor_code": "sector_rhythm"}, {"factor_code": "market_node"}]}
    second = {"factors": list(reversed(first["factors"]))}
    one = _run(
        runner,
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload=first,
        validator=_validate,
    )
    two = _run(
        runner,
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload=second,
        validator=_validate,
    )

    assert one.input_digest == two.input_digest
    assert prompts[0] == prompts[1]
    assert prompts[0].index("market_node") < prompts[0].index("sector_rhythm")
    assert "untrusted_data" in prompts[0]


def test_runner_rejects_oversized_multibyte_prompt_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    marker = "OVERSIZED_PROMPT_MARKER_59ab"
    payload = {"notes": marker + "界" * 50_000}
    canonical_input = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(
        _compose_prompt(FACTOR_PROMPT_VERSION, "instructions", canonical_input).encode(
            "utf-8"
        )
    ) > 128 * 1024
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"schema_version":"ok","items":[]}',
            stderr="",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload=payload,
        validator=_validate,
    )

    serialized = json.dumps(result.to_record(), ensure_ascii=False)
    assert result.status == "input_too_large"
    assert result.attempt_count == 0
    assert result.is_cacheable is False
    assert result.parsed_output is None
    assert result.valid_raw_json is None
    assert result.raw_output_sha256 is None
    assert result.diagnostics == {
        "reason": "input_too_large",
        "message": "structured prompt exceeds UTF-8 byte limit",
    }
    assert marker not in serialized
    assert calls == []


def test_runner_allows_multibyte_prompt_below_argv_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    payload = {"notes": "界" * 40_000}
    prompts = []

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        prompts.append(command[command.index("--prompt") + 1])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"schema_version":"ok","items":[]}',
            stderr="",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload=payload,
        validator=_validate,
    )

    assert result.status == "success"
    assert len(prompts) == 1
    assert len(prompts[0].encode("utf-8")) < 128 * 1024


def test_empty_output_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr="configured timeout: 180s"
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


def test_invalid_json_or_schema_retries_only_once_and_never_retains_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    invalid = "```json\n{bad and 买入}\n```"
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 0, stdout=invalid, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "schema_invalid"
    assert result.attempt_count == 2
    assert result.valid_raw_json is None
    assert result.parsed_output is None
    assert result.raw_output_sha256 == hashlib.sha256(invalid.encode()).hexdigest()
    assert invalid not in json.dumps(result.diagnostics, ensure_ascii=False)
    assert "买入" not in json.dumps(result.to_record(), ensure_ascii=False)
    assert attempts == 2


def test_schema_validation_exception_text_never_enters_result_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _config(monkeypatch)
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path / "logs"))
    marker = "INVALID_SCHEMA_MARKER_8d33a1"
    invalid = json.dumps({"unexpected": marker})
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 0, stdout=invalid, stderr="")

    def reject_with_raw_marker(raw: str) -> dict:
        assert marker in raw
        raise TrinityValidationError(f"unknown field contains {marker}")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=reject_with_raw_marker,
    )

    serialized = json.dumps(result.to_record(), ensure_ascii=False)
    assert result.status == "schema_invalid"
    assert result.attempt_count == 2
    assert result.valid_raw_json is None
    assert result.parsed_output is None
    assert result.raw_output_sha256 == hashlib.sha256(invalid.encode()).hexdigest()
    assert result.diagnostics["reason"] == "schema_invalid"
    assert result.diagnostics["message"] == "structured output failed schema validation"
    assert set(result.diagnostics) == {"reason", "message", "log_file"}
    assert marker not in serialized
    assert attempts == 2


def test_pathologically_deep_json_retries_once_as_schema_invalid_without_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    invalid = "[" * 1200 + "]" * 1200
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 0, stdout=invalid, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=lambda raw: {"validated": True},
    )

    assert result.status == "schema_invalid"
    assert result.attempt_count == 2
    assert result.valid_raw_json is None
    assert result.parsed_output is None
    assert result.raw_output_sha256 == hashlib.sha256(invalid.encode()).hexdigest()
    assert invalid not in json.dumps(result.diagnostics, ensure_ascii=False)
    assert invalid not in json.dumps(result.to_record(), ensure_ascii=False)
    assert attempts == 2


def test_flatten_deep_error_object_is_iterative_and_preserves_traversal_order() -> None:
    depth = 985
    invalid = (
        '{"status":"error","error":{"first":"one","nested":'
        + '{"x":' * depth
        + '"ordinary schema problem"'
        + "}" * depth
        + ',"last":"three"}}'
    )

    def parse_and_flatten() -> list[str]:
        payload = json.loads(invalid)
        assert payload["status"] == "error"
        return _flatten_error_values(payload["error"])

    with ThreadPoolExecutor(max_workers=1) as executor:
        values = executor.submit(parse_and_flatten).result()

    assert values == ["one", "ordinary schema problem", "three"]


def test_deep_error_object_retries_once_as_schema_invalid_without_recursion_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    depth = 900
    invalid = (
        '{"status":"error","error":'
        + '{"x":' * depth
        + '"ordinary schema problem"'
        + "}" * depth
        + "}"
    )
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 0, stdout=invalid, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=lambda raw: (_ for _ in ()).throw(
            TrinityValidationError("schema mismatch")
        ),
    )

    assert result.status == "schema_invalid"
    assert result.attempt_count == 2
    assert result.valid_raw_json is None
    assert result.parsed_output is None
    assert result.raw_output_sha256 == hashlib.sha256(invalid.encode()).hexdigest()
    assert invalid not in json.dumps(result.diagnostics, ensure_ascii=False)
    assert invalid not in json.dumps(result.to_record(), ensure_ascii=False)
    assert attempts == 2


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (subprocess.TimeoutExpired(cmd="agy", timeout=180), "timeout"),
        (
            subprocess.CompletedProcess(
                ["agy"], 1, stdout="", stderr="RESOURCE_EXHAUSTED quota exceeded"
            ),
            "quota_exhausted",
        ),
        (
            subprocess.CompletedProcess(
                ["agy"], 1, stdout="", stderr="not authenticated; authorization code required"
            ),
            "auth_required",
        ),
        (OSError("binary cannot start"), "startup_failed"),
    ],
)
def test_terminal_failures_do_not_retry(
    monkeypatch: pytest.MonkeyPatch, failure: object, expected_status: str
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        if isinstance(failure, BaseException):
            raise failure
        return failure

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == expected_status
    assert result.attempt_count == 1
    assert result.is_cacheable is False
    assert attempts == 1


def test_generic_nonzero_runtime_failure_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="runtime failed")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "runtime_failed"
    assert result.attempt_count == 1
    assert attempts == 1


@pytest.mark.parametrize(
    ("stderr", "expected_status"),
    [
        ("RESOURCE_EXHAUSTED quota exceeded", "quota_exhausted"),
        ("not authenticated; authorization code required", "auth_required"),
        ("internal timeout while waiting for model", "timeout"),
        (
            "configured timeout: 180s\ninternal timeout while waiting for model",
            "timeout",
        ),
        (
            "configured timeout: 180s; internal timeout while waiting for model",
            "timeout",
        ),
        ("process timed out after 240000ms", "timeout"),
        ("HTTP 429 Too Many Requests", "quota_exhausted"),
        (
            "rpc error: code = ResourceExhausted desc = quota reached",
            "quota_exhausted",
        ),
        ("agy: internal timeout while waiting for model", "timeout"),
        (
            "2026-07-11 21:03:12,123 ERROR agy: internal timeout while waiting",
            "timeout",
        ),
        ("rpc error: code = DeadlineExceeded desc = model stalled", "timeout"),
        ("rpc error: code = Unauthenticated desc = login required", "auth_required"),
        ("HTTP/1.1 401 Unauthorized", "auth_required"),
        ("request timed out after 180s", "timeout"),
        ("command timed out after 180s", "timeout"),
        ("operation timed out after 180s", "timeout"),
        (
            "error getting token source: You are not logged into Antigravity",
            "auth_required",
        ),
    ],
)
def test_zero_exit_global_failure_with_empty_stdout_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, stderr: str, expected_status: str
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(command, 0, stdout="", stderr=stderr)

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == expected_status
    assert result.attempt_count == 1
    assert attempts == 1


def test_terminal_signature_in_invalid_stdout_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(
            command, 0, stdout="internal timeout while waiting for model", stderr=""
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "timeout"
    assert result.attempt_count == 1
    assert result.valid_raw_json is None
    assert result.diagnostics["reason"] == "timeout"
    assert attempts == 1


def test_parseable_schema_invalid_business_text_does_not_fake_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = (
            '{"schema_version":"bad","note":"timeout is a risk; not authenticated"}'
            if attempts == 1
            else '{"schema_version":"ok","items":[]}'
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


def test_non_json_business_explanation_does_not_fake_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = (
            "模型解释：timeout 只是业务风险因子，不是调用故障"
            if attempts == 1
            else '{"schema_version":"ok","items":[]}'
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


def test_echoed_business_text_in_stderr_does_not_control_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=stdout,
            stderr="prompt evidence says timeout is a business risk",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


@pytest.mark.parametrize(
    "stderr",
    [
        "INFO: request timeout is a business factor",
        "DEBUG: quota exceeded is quoted from teacher notes",
    ],
)
def test_non_error_log_levels_never_promote_business_echo_to_terminal(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


@pytest.mark.parametrize(
    "recovery_line",
    ["Auth succeeded", "INFO: Auth succeeded", "INFO - Auth succeeded"],
)
def test_recovered_auth_log_does_not_block_retry(
    monkeypatch: pytest.MonkeyPatch, recovery_line: str
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=stdout,
            stderr=f"ERROR: not authenticated\n{recovery_line}",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert attempts == 2


def test_explicit_json_error_envelope_is_terminal_and_diagnostics_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"error":{"code":429,"message":"quota exceeded"}}',
            stderr="",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "quota_exhausted"
    assert result.diagnostics["reason"] == "quota_exhausted"
    assert result.attempt_count == 1
    assert attempts == 1


def test_explicit_json_401_error_envelope_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = (
            '{"error":{"code":401,"message":"Unauthorized"}}'
            if attempts == 1
            else '{"schema_version":"ok","items":[]}'
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "auth_required"
    assert result.attempt_count == 1
    assert attempts == 1


def test_low_level_log_in_prefixed_chain_never_becomes_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=stdout,
            stderr="[thread-1] [INFO] request timeout is a business factor",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


def test_auth_recovery_before_later_error_does_not_hide_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=stdout,
            stderr="Auth succeeded\nERROR: not authenticated",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "auth_required"
    assert result.attempt_count == 1
    assert attempts == 1


def test_stdout_auth_envelope_is_not_hidden_by_recovery_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"error":{"code":401,"message":"Unauthorized"}}',
            stderr="Auth succeeded",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "auth_required"
    assert result.attempt_count == 1
    assert attempts == 1


def test_valid_json_wins_over_benign_timeout_text_in_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"schema_version":"ok","items":[]}',
            stderr="configured timeout: 180s",
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 1
    assert attempts == 1


def test_benign_timeout_configuration_does_not_block_schema_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        attempts += 1
        stdout = "not-json" if attempts == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr="configured timeout: 180s"
        )

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert result.attempt_count == 2
    assert attempts == 2


def test_retry_uses_a_fresh_empty_temporary_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(monkeypatch)
    workdirs: list[Path] = []

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="1.0", stderr="")
        cwd = Path(kwargs["cwd"])
        assert list(cwd.iterdir()) == []
        workdirs.append(cwd)
        (cwd / "attempt-artifact").write_text("must not leak", encoding="utf-8")
        stdout = "" if len(workdirs) == 1 else '{"schema_version":"ok","items":[]}'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = _run(
        AntigravityStructuredRunner(run_command=fake_run),
        prompt_version=FACTOR_PROMPT_VERSION,
        prompt="instructions",
        input_payload={},
        validator=_validate,
    )

    assert result.status == "success"
    assert len(workdirs) == 2
    assert workdirs[0] != workdirs[1]
    assert all(not path.exists() for path in workdirs)


@pytest.mark.parametrize(
    ("prompt_version", "schema_version", "ruleset_version"),
    [
        ("", _SCHEMA_VERSION, _RULESET_VERSION),
        (FACTOR_PROMPT_VERSION, "", _RULESET_VERSION),
        (FACTOR_PROMPT_VERSION, _SCHEMA_VERSION, ""),
    ],
)
def test_missing_audit_version_is_misconfigured_and_not_cacheable(
    monkeypatch: pytest.MonkeyPatch,
    prompt_version: str,
    schema_version: str,
    ruleset_version: str,
) -> None:
    _config(monkeypatch)
    calls = []

    result = AntigravityStructuredRunner(
        run_command=lambda *args, **kwargs: calls.append((args, kwargs))
    ).run(
        prompt_version=prompt_version,
        prompt="instructions",
        input_payload={},
        validator=_validate,
        schema_version=schema_version,
        ruleset_version=ruleset_version,
    )

    assert result.status == "misconfigured"
    assert result.is_cacheable is False
    assert result.attempt_count == 0
    assert calls == []
