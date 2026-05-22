"""调本机 gemini CLI 提炼「大盘判断」文本（subprocess 包装 + 降级）。

返回值：
- str  → LLM 文本（formatter 调红线扫描后决定是否使用）
- None → 不可用（gemini 不存在 / timeout / 非零退出），formatter 降级展示原始 core_view
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

GEMINI_BIN = os.getenv("GEMINI_BIN", "/opt/homebrew/bin/gemini")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")   # 空字符串 = 用 gemini 默认模型


def comment(payload: str) -> str | None:
    """调 gemini CLI 拿点评。成功返 str；任何异常 / 非零退出返 None。

    TODO: 超长 prompt（接近命令行参数长度上限）走 stdin 备选：
        echo "$payload" | gemini -p "" --output-format text --approval-mode yolo
    本期暂以命令行参数传入，行业推荐 Top 3 + 摘要远未触达上限。
    """
    cmd = [
        GEMINI_BIN,
        "--prompt", payload,
        "--output-format", "text",
        "--approval-mode", "yolo",
    ]
    if GEMINI_MODEL:
        cmd.extend(["-m", GEMINI_MODEL])

    try:
        result = subprocess.run(
            cmd,
            timeout=LLM_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            check=False,   # 主动检查 returncode，避免 CalledProcessError 逃逸
        )
    except subprocess.TimeoutExpired:
        logger.warning("gemini timeout after %ds", LLM_TIMEOUT_SECONDS)
        return None
    except FileNotFoundError:
        logger.warning("gemini binary not found at %s", GEMINI_BIN)
        return None

    if result.returncode != 0:
        logger.warning("gemini exit %d: %s", result.returncode, result.stderr[:200])
        return None

    return result.stdout.strip()
