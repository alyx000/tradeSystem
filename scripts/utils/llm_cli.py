"""本地 Antigravity CLI 命令构造。"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmCliConfig:
    bin_path: str
    model: str
    timeout_seconds: int


def resolve_config(*, default_timeout: int = 180) -> LlmCliConfig:
    timeout_seconds = _int_env("LLM_TIMEOUT_SECONDS", default_timeout)
    model = os.getenv("LLM_MODEL") or os.getenv("ANTIGRAVITY_MODEL") or ""
    return LlmCliConfig(
        bin_path=_resolve_bin(),
        model=model,
        timeout_seconds=timeout_seconds,
    )


def build_prompt_command(
    config: LlmCliConfig,
    prompt: str,
    *,
    add_dirs: list[str] | None = None,
    skip_permissions: bool = False,
) -> list[str]:
    cmd = [config.bin_path, "--print-timeout", f"{config.timeout_seconds}s"]
    for add_dir in add_dirs or []:
        cmd += ["--add-dir", add_dir]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
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
