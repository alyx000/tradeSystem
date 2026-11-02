"""业务 LLM 命令构造：默认 Antigravity，可显式选择 OpenAI Responses。"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LlmCliConfig:
    bin_path: str
    model: str
    timeout_seconds: int
    provider: str = "antigravity"


def resolve_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "antigravity").strip().lower()


def resolve_config(*, default_timeout: int = 180, provider: str | None = None) -> LlmCliConfig:
    timeout_seconds = _int_env("LLM_TIMEOUT_SECONDS", default_timeout)
    selected = provider or resolve_provider()
    model = ((os.getenv("ANTIGRAVITY_MODEL") or "")
             if provider == "antigravity" and resolve_provider() != "antigravity"
             else (os.getenv("LLM_MODEL") or ""))
    if selected == "antigravity" and not model:
        model = os.getenv("ANTIGRAVITY_MODEL") or ""
    return LlmCliConfig(
        bin_path=_resolve_bin(),
        model=model,
        timeout_seconds=timeout_seconds,
        provider=selected,
    )


def build_prompt_command(
    config: LlmCliConfig,
    prompt: str,
    *,
    add_dirs: list[str] | None = None,
    skip_permissions: bool = False,
    log_file: str | None = None,
    sandbox: bool = False,
    mode: str | None = None,
) -> list[str]:
    if config.provider != "antigravity":
        # 绝对路径允许评分器在空临时 cwd 运行；密钥只通过环境继承，不进 argv。
        cmd = [sys.executable, str(Path(__file__).with_name("openai_responses.py")),
               "--provider", config.provider, "--model", config.model,
               "--timeout", str(config.timeout_seconds), "--prompt", prompt]
        if add_dirs:
            # 纯文本 API 不具备本地文件读取能力，不把路径当作已读证据。
            cmd.append("--unsupported-local-files")
        return cmd
    cmd = [config.bin_path, "--print-timeout", f"{config.timeout_seconds}s"]
    if log_file:
        cmd += ["--log-file", log_file]
    for add_dir in add_dirs or []:
        cmd += ["--add-dir", add_dir]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if sandbox:
        cmd.append("--sandbox")
    if mode:
        cmd += ["--mode", mode]
    if config.model:
        cmd += ["--model", config.model]
    cmd += ["--prompt", prompt]
    return cmd


def _resolve_bin() -> str:
    return os.getenv("ANTIGRAVITY_BIN") or os.getenv("AGY_BIN") or shutil.which("agy") or "agy"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
