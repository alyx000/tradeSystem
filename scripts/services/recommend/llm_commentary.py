"""调本机 Antigravity CLI 提炼「大盘判断」文本（subprocess 包装 + 降级）。

返回值：
- str  → LLM 文本（formatter 调红线扫描后决定是否使用）
- None → 不可用（agy 不存在 / timeout / 非零退出），formatter 降级展示原始 core_view
"""
from __future__ import annotations

import logging
import subprocess

from utils.llm_cli import build_prompt_command, resolve_config

logger = logging.getLogger(__name__)


def comment(payload: str) -> str | None:
    """调 Antigravity CLI 拿点评。成功返 str；任何异常 / 非零退出返 None。

    TODO: 超长 prompt（接近命令行参数长度上限）走 stdin 备选：
        echo "$payload" | agy --prompt ...
    本期暂以命令行参数传入，行业推荐 Top 3 + 摘要远未触达上限。
    """
    config = resolve_config(default_timeout=90)
    cmd = build_prompt_command(config, payload)

    try:
        result = subprocess.run(
            cmd,
            timeout=config.timeout_seconds,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,   # 主动检查 returncode，避免 CalledProcessError 逃逸
        )
    except subprocess.TimeoutExpired:
        logger.warning("antigravity timeout after %ds", config.timeout_seconds)
        return None
    except FileNotFoundError:
        logger.warning("agy binary not found at %s", config.bin_path)
        return None

    if result.returncode != 0:
        logger.warning("antigravity exit %d: %s", result.returncode, result.stderr[:200])
        return None

    return result.stdout.strip()
