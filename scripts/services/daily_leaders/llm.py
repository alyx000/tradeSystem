from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from services.recommend.formatter import REDLINE_KEYWORDS


Runner = Callable[[str], Optional[dict[str, Any]]]
ALLOWED_ROLES = {"走势引领", "容量中军", "分支核心", "备选", "剔除"}
ERROR_KEY = "__llm_error__"


def _build_prompt(proposal: dict[str, Any]) -> str:
    payload = json.dumps(proposal, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            "你是交易复盘资料整理助手，请在候选池内识别每日最票优先级。",
            "输出必须是 JSON object，key 格式为 `{stock}|{sector}`，不得新增输入外候选。",
            "value 可以是中文理由字符串，也可以是 object：",
            "{\"rank\": 正整数, \"role\": \"走势引领|容量中军|分支核心|备选|剔除\", \"reason\": \"中文理由\", \"risk_flags\": [\"风险标签\"]}",
            "排序依据优先考虑：板块主线审美、个股在板块内引领性、资金强度、涨停/涨幅强度、老师观点对照、候选证据质量。",
            "老师明确支持的主线预填票若已补充当日成交额/涨幅/板块强度，不得仅因来源是复盘预填或缺少资金流字段而大幅降权。",
            "不得给买卖建议、不得给价格目标、不得给仓位建议，不得把判断伪装成事实。",
            "理由需明确仍需人工确认；证据不足时 role 用“备选”或“剔除”。",
            "",
            "输入 proposal JSON：",
            payload,
        ]
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S)
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
    return {str(key): value for key, value in parsed.items() if isinstance(value, (str, dict))}


def _run_llm(prompt: str) -> dict[str, Any] | None:
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    log_dir = Path(os.getenv("ANTIGRAVITY_LOG_DIR", "/private/tmp/tradesystem-antigravity-logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"daily-leaders-{os.getpid()}-{int(time.time() * 1000)}.log"
    cmd = build_prompt_command(config, prompt, log_file=str(log_file))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        stdin=subprocess.DEVNULL,
    )
    log_text = ""
    try:
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        log_text = ""
    if result.returncode != 0:
        return _llm_error("nonzero_exit", f"returncode={result.returncode}")
    if not (result.stdout or "").strip():
        return _llm_error(_classify_empty_output(result.stderr, log_text), "")
    return _parse_json_object(result.stdout)


def _llm_error(reason: str, detail: str = "") -> dict[str, Any]:
    return {ERROR_KEY: {"ok": False, "reason": reason, "detail": detail}}


def _classify_empty_output(stderr: str, log_text: str) -> str:
    text = f"{stderr or ''}\n{log_text or ''}"
    if "RESOURCE_EXHAUSTED" in text or "quota reached" in text:
        return "quota_exhausted"
    if "not logged into Antigravity" in text:
        return "auth_required"
    return "empty_output"


def _scan_redline(text: str) -> str | None:
    for keyword in REDLINE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None


def _safe_rank(value: Any) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _clean_risk_flags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and not _scan_redline(text):
            out.append(text[:24])
    return out[:5]


def _apply_llm_judgement(item: dict[str, Any], raw: Any) -> bool:
    if isinstance(raw, str):
        reason = raw.strip()
        if reason and not _scan_redline(reason):
            item["llm_reason"] = reason
            return True
        return False

    if not isinstance(raw, dict):
        return False

    reason = str(raw.get("reason") or "").strip()
    role = str(raw.get("role") or "").strip()
    rank = _safe_rank(raw.get("rank"))
    if reason and _scan_redline(reason):
        return False
    if role and role not in ALLOWED_ROLES:
        role = ""
    if role and _scan_redline(role):
        return False

    applied = False
    if rank is not None:
        item["llm_rank"] = rank
        applied = True
    if role:
        item["llm_role"] = role
        applied = True
    if reason:
        item["llm_reason"] = reason
        applied = True
    risk_flags = _clean_risk_flags(raw.get("risk_flags"))
    if risk_flags:
        item["risk_flags"] = risk_flags
        applied = True
    return applied


def _sort_with_llm(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(items))
    return [
        item for _, item in sorted(
            indexed,
            key=lambda pair: (
                1 if pair[1].get("llm_role") == "剔除" else 0,
                int(pair[1].get("llm_rank") or 10_000),
                pair[0],
            ),
        )
    ]


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
            enriched = copy.deepcopy(proposal)
            enriched["llm_status"] = {"ok": False, "reason": "empty_mapping"}
            return enriched
        if ERROR_KEY in mapping:
            enriched = copy.deepcopy(proposal)
            error = mapping.get(ERROR_KEY) if isinstance(mapping.get(ERROR_KEY), dict) else {}
            enriched["llm_status"] = {
                "ok": False,
                "reason": str(error.get("reason") or "unknown_error"),
            }
            if error.get("detail"):
                enriched["llm_status"]["detail"] = str(error.get("detail"))
            return enriched

        enriched = copy.deepcopy(proposal)
        valid_keys = {
            f"{item.get('stock', '')}|{item.get('sector', '')}"
            for item in enriched.get("top_leaders") or []
            if isinstance(item, dict)
        }
        for item in enriched.get("top_leaders") or []:
            key = f"{item.get('stock', '')}|{item.get('sector', '')}"
            if key not in valid_keys:
                continue
            _apply_llm_judgement(item, mapping.get(key))
        enriched["top_leaders"] = _sort_with_llm(enriched.get("top_leaders") or [])
        enriched["llm_status"] = {"ok": True}
        return enriched
    except Exception:
        enriched = copy.deepcopy(proposal)
        enriched["llm_status"] = {"ok": False, "reason": "exception"}
        return enriched
