from __future__ import annotations

import copy
import json
import re
import subprocess
from typing import Any, Callable, Optional

from services.recommend.formatter import REDLINE_KEYWORDS


Runner = Callable[[str], Optional[dict[str, str]]]


def _build_prompt(proposal: dict[str, Any]) -> str:
    payload = json.dumps(proposal, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            "你是交易复盘资料整理助手，请为每日最票候选补充简短人工确认理由。",
            "输出必须是 JSON object，key 格式为 `{stock}|{sector}`，value 为中文理由字符串。",
            "只允许基于输入中的候选、证据和老师观点对照表述。",
            "不得给买卖建议、不得给价格目标、不得给仓位建议，不得把判断伪装成事实。",
            "理由需明确仍需人工确认。",
            "",
            "输入 proposal JSON：",
            payload,
        ]
    )


def _parse_json_object(text: str) -> dict[str, str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return None
    return {str(key): str(value) for key, value in parsed.items() if isinstance(value, str)}


def _run_llm(prompt: str) -> dict[str, str] | None:
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    cmd = build_prompt_command(config, prompt)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return _parse_json_object(result.stdout)


def _scan_redline(text: str) -> str | None:
    for keyword in REDLINE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None


def enrich_with_llm_reason(
    proposal: dict[str, Any],
    *,
    enabled: bool = True,
    runner: Runner | None = None,
) -> dict[str, Any]:
    if not enabled:
        return proposal

    try:
        prompt = _build_prompt(proposal)
        mapping = (runner or _run_llm)(prompt)
        if not mapping:
            return proposal

        enriched = copy.deepcopy(proposal)
        for item in enriched.get("top_leaders") or []:
            key = f"{item.get('stock', '')}|{item.get('sector', '')}"
            reason = mapping.get(key)
            if reason and not _scan_redline(reason):
                item["llm_reason"] = reason
        return enriched
    except Exception:
        return proposal
