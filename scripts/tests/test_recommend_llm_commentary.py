"""TDD: Antigravity CLI subprocess 包装单测

L1 成功路径 / L2 超时与非零退出降级路径。
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────
# L1: subprocess 成功 → 返回 stdout
# ─────────────────────────────────────────────────────────────
def test_l1_comment_returns_stdout_on_success():
    fake_result = subprocess.CompletedProcess(
        args=["agy", "--prompt", "..."],
        returncode=0,
        stdout="半导体逻辑：周期回升。\n",
        stderr="",
    )

    from services.recommend.llm_commentary import comment

    with patch("services.recommend.llm_commentary.subprocess.run", return_value=fake_result) as mock_run:
        result = comment(payload="任意输入")

    assert result == "半导体逻辑：周期回升。"   # 去掉尾部 \n
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    # 命令行包含必要参数
    cmd = call_args[0][0] if call_args[0] else call_args[1]["args"]
    assert cmd[0].endswith("agy") or cmd[0] == "agy"
    assert "--prompt" in cmd or any("--prompt" in str(a) for a in cmd)
    assert "--print-timeout" in cmd or any("--print-timeout" in str(a) for a in cmd)


# ─────────────────────────────────────────────────────────────
# L2: 降级三态 → 都返回 None，不向上抛
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scenario, side_effect, return_value", [
    ("timeout",
     subprocess.TimeoutExpired(cmd="agy", timeout=90),
     None),
    ("non_zero_exit",
     None,
     subprocess.CompletedProcess(args=["agy"], returncode=1, stdout="", stderr="rate limited")),
    ("file_not_found",
     FileNotFoundError("[Errno 2] No such file or directory: 'agy'"),
     None),
])
def test_l2_comment_returns_none_on_failure(scenario, side_effect, return_value, caplog):
    import logging
    from services.recommend.llm_commentary import comment

    if side_effect is not None:
        patch_kwargs = {"side_effect": side_effect}
    else:
        patch_kwargs = {"return_value": return_value}

    with patch("services.recommend.llm_commentary.subprocess.run", **patch_kwargs):
        with caplog.at_level(logging.WARNING):
            result = comment(payload="任意输入")

    assert result is None, f"scenario={scenario} 应该返回 None"
    # logger 应该 warn 出降级原因
    assert len(caplog.records) >= 1, f"scenario={scenario} 应该有 warning 日志"
