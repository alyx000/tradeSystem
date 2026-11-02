from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest
import requests

from utils import llm_cli, openai_responses as api


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "ANTIGRAVITY_MODEL", "OPENAI_API_KEY",
                 "OPENAI_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def response_payload(text="已连接"):
    return {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "status": "completed",
         "content": [{"type": "output_text", "text": text}]},
    ]}


@pytest.fixture
def post(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-print")
    mock = Mock(return_value=Mock(status_code=200, json=Mock(return_value=response_payload())))
    monkeypatch.setattr(api.requests, "post", mock)
    return mock


def test_success_fixed_endpoint_no_storage_redirect_or_secret_argv(post, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    cmd = llm_cli.build_prompt_command(llm_cli.resolve_config(), "输入证据")
    assert "test-secret-never-print" not in " ".join(cmd)
    assert api.generate("输入证据", model="test-model", timeout=12) == "已连接"
    args, kwargs = post.call_args
    assert args == ("https://api.openai.com/v1/responses",)
    assert kwargs["allow_redirects"] is False
    assert kwargs["json"] == {"model": "test-model", "input": "输入证据",
                              "store": False, "max_output_tokens": 4096}
    assert kwargs["timeout"] == 12


@pytest.mark.parametrize("status,reason", [(401, "not authenticated"), (403, "not authenticated"),
                                          (429, "code 429"), (500, "HTTP 500"), (302, "HTTP 302")])
def test_http_errors_are_nonzero_without_response_leak(post, capsys, status, reason):
    post.return_value.status_code = status
    post.return_value.text = "test-secret-never-print upstream error"
    assert api.main(["--model", "m", "--prompt", "p"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert reason in out.err
    assert "test-secret" not in out.err


@pytest.mark.parametrize("failure", [requests.Timeout("secret"), requests.ConnectionError("secret")])
def test_network_failure_redacted(post, failure):
    post.side_effect = failure
    with pytest.raises(api.ResponseFailure) as exc:
        api.generate("p", model="m", timeout=1)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("payload", [None, {}, {"status": "incomplete", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "message", "status": "in_progress", "content": []}]},
    {"status": "completed", "output": [{"type": "message", "status": "completed",
                                         "content": [{"type": "refusal"}]}]},
    response_payload(" ")])
def test_incomplete_refusal_empty_never_success(post, payload):
    post.return_value.json.return_value = payload
    with pytest.raises(api.ResponseFailure):
        api.generate("p", model="m", timeout=1)


def test_invalid_json_fails(post):
    post.return_value.json.side_effect = ValueError("private payload")
    with pytest.raises(api.ResponseFailure, match="invalid OpenAI JSON"):
        api.generate("p", model="m", timeout=1)


@pytest.mark.parametrize("setting,value", [("OPENAI_API_KEY", ""), ("OPENAI_MAX_OUTPUT_TOKENS", "0"),
                                          ("OPENAI_MAX_OUTPUT_TOKENS", "bad")])
def test_missing_or_invalid_config_no_network(post, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(api.ResponseFailure):
        api.generate("p", model="m", timeout=1)
    post.assert_not_called()


def test_unknown_provider_and_local_files_fail_before_network(post):
    for extra in (["--provider", "typo"], ["--unsupported-local-files"]):
        assert api.main(["--model", "m", "--prompt", "p", *extra]) == 1
    post.assert_not_called()


def test_original_cli_and_pdf_model_preserved(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "existing-model")
    config = llm_cli.resolve_config(provider="antigravity")
    assert config.model == "existing-model"
    assert "--print-timeout" in llm_cli.build_prompt_command(config, "p")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ANTIGRAVITY_MODEL", "pdf-model")
    assert llm_cli.resolve_config(provider="antigravity").model == "pdf-model"
    assert llm_cli.resolve_config().model == "existing-model"


def test_child_runs_from_empty_cwd_and_fails_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    result = subprocess.run(llm_cli.build_prompt_command(llm_cli.resolve_config(), "p"),
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "OPENAI_API_KEY missing" in result.stderr


def test_business_comment_routes_through_openai(post, monkeypatch):
    from services.recommend import llm_commentary
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    seen = []
    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, api.generate("p", model="test-model", timeout=1), "")
    monkeypatch.setattr(llm_commentary.subprocess, "run", run)
    assert llm_commentary.comment("p") == "已连接"
    assert seen[0][1].endswith("openai_responses.py")


def test_structured_runner_records_actual_provider(monkeypatch):
    from services.trinity_factor.runner import AntigravityStructuredRunner
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    seen = []
    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true}', "")
    result = AntigravityStructuredRunner(run_command=run).run(
        prompt_version="v1", prompt="p", input_payload={}, validator=lambda raw: {"ok": True},
        schema_version="v1", ruleset_version="v1")
    assert result.provider == "openai"
    assert result.cli_version == "openai-responses-v1"
    assert result.is_cacheable
    assert seen[0][1].endswith("openai_responses.py")
