from __future__ import annotations

import copy
import html
import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from services.daily_leaders.models import LEADER_ROLES, STATUS_ROLES
from services.recommend.formatter import REDLINE_KEYWORDS
from utils.antigravity_diagnostics import classify_failure


@dataclass(frozen=True)
class _LlmError:
    reason: str
    detail: str = ""


LlmResult = Optional[Union[dict[str, Any], _LlmError]]
Runner = Callable[[str], Optional[dict[str, Any]]]
LEADER_ATTRIBUTE_ROLES = LEADER_ROLES
ALLOWED_ROLES = LEADER_ATTRIBUTE_ROLES | STATUS_ROLES
JUDGEMENT_FIELDS = {"rank", "role", "reason", "risk_flags"}
INTERNAL_LLM_FAILURE_REASONS = {
    "auth_required",
    "empty_output",
    "invalid_json",
    "nonzero_exit",
    "quota_exhausted",
    "timeout",
}
MAX_REASON_LENGTH = 320
MAX_RISK_FLAGS = 5
MAX_RISK_FLAG_LENGTH = 24
EVIDENCE_LABEL_PATTERN = re.compile(
    r"(?:\[\s*(?:事实|老师观点|研报观点|来源陈述)\s*\]"
    r"|【\s*(?:事实|老师观点|研报观点|来源陈述)\s*】)"
)
BLOCK_MARKDOWN_PATTERN = re.compile(
    r"^\s*(?:#{1,6}(?:\s|$)|>|`{3,}|~{3,}|(?:[-+*]|\d+[.)])(?:\s|$))"
)
INLINE_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]\r\n]*\]\([^\)\r\n]*\)")
MARKDOWN_EMPHASIS_PATTERN = re.compile(
    r"(?:\*\*[^*\r\n]+\*\*|__[^_\r\n]+__|\*[^*\r\n]+\*"
    r"|(?<!\w)_[^_\r\n]+_(?!\w)|~~[^~\r\n]+~~)"
)
HTML_ENTITY_PATTERN = re.compile(
    r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"
)
HTML_STRUCTURE_PATTERN = re.compile(r"<[^>]*>")
HORIZONTAL_RULE_PATTERN = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
FENCED_JSON_PATTERN = re.compile(
    r"```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```",
    flags=re.IGNORECASE | re.DOTALL,
)
HTTP_STATUS_PATTERN = re.compile(
    r"\bhttp(?:/\d(?:\.\d)?)?(?:\s+(?:error|status))?[\s:=_-]*(\d{3})\b",
    flags=re.IGNORECASE,
)
AUTH_STATUS_CODE_PATTERN = re.compile(
    r"\b(?:status(?:[\s_-]+code)?|code)\s*[:=_-]?\s*401\b",
    flags=re.IGNORECASE,
)


def _build_prompt(proposal: dict[str, Any]) -> str:
    payload = json.dumps(proposal, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            "你是交易复盘资料整理助手，请在候选池内识别每日最票优先级。",
            "输出必须是 JSON object，key 格式为 `{stock}|{sector}`，不得新增输入外候选。",
            "value 必须是 object；每个输入候选都必须返回，且 rank 与 role 必填：",
            "{\"rank\": 正整数, \"role\": \"趋势中军|连板核心|前排活跃|弹性前排|备选|剔除\", \"reason\": \"中文理由\", \"risk_flags\": [\"风险标签\"]}",
            "role 是最票属性：趋势中军=板块内成交额容量核心；连板核心=连板高度核心；前排活跃=涨停、日内强势或资金流领涨前排；弹性前排=20cm/30cm 日内强势前排；备选/剔除仅用于证据不足或降级。",
            "10cm/20cm/30cm 只作为 board_type 事实，不得作为 role。",
            "同一板块、同一 role 只选最好一个；同一股票只保留一个判断。",
            "LLM 输出总结果不是程序最终约束，程序后处理仍会执行板块角色唯一、股票唯一和数量上限。",
            "排序优先级依次为：涨停/涨幅强度、成交额、板块主线审美、个股在板块内引领性、候选证据质量。",
            "同一涨停/涨幅强度档位内，成交额更大的候选优先；低涨幅容量票可标为趋势中军，但不得压过更高涨幅强度档位的候选。",
            "概念资金流和老师观点只作为辅助证据，用于解释主线审美和人工确认重点，不作为主排序第一权重。",
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
    fenced = FENCED_JSON_PATTERN.search(raw)
    if fenced:
        parsed = json.loads(fenced.group(1).strip())
        return (
            {str(key): value for key, value in parsed.items()}
            if isinstance(parsed, dict)
            else None
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as parse_error:
        decoder = json.JSONDecoder()
        start = next(
            (index for index, char in enumerate(raw) if char in "[{"),
            None,
        )
        if start is None:
            raise parse_error
        try:
            parsed, _ = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            raise parse_error
    if not isinstance(parsed, dict):
        return None
    return {str(key): value for key, value in parsed.items()}


def _run_llm(prompt: str) -> LlmResult:
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    log_dir = Path(os.getenv("ANTIGRAVITY_LOG_DIR", "/private/tmp/tradesystem-antigravity-logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"daily-leaders-{os.getpid()}-{int(time.time() * 1000)}.log"
    cmd = build_prompt_command(config, prompt, log_file=str(log_file))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return _llm_error(
            "timeout",
            f"timeout_seconds={config.timeout_seconds}; log_file={log_file}",
        )
    stdout = (result.stdout or "").strip()
    parsed: dict[str, Any] | None = None
    if result.returncode == 0 and stdout:
        try:
            direct_json = json.loads(stdout)
        except json.JSONDecodeError:
            direct_json = None
        else:
            if not isinstance(direct_json, dict):
                return _llm_error("invalid_json", f"log_file={log_file}")
        try:
            parsed = _parse_json_object(stdout)
        except json.JSONDecodeError:
            parsed = None
        else:
            if parsed is None:
                return _llm_error("invalid_json", f"log_file={log_file}")
        if parsed is not None:
            return parsed

    try:
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        log_text = ""
    fallback_reason = (
        "nonzero_exit"
        if result.returncode != 0
        else "empty_output" if not stdout else "invalid_json"
    )
    reason = _classify_cli_failure(
        stdout,
        result.stderr,
        log_text,
        default=fallback_reason,
    )
    detail = f"log_file={log_file}"
    if result.returncode != 0:
        detail = f"returncode={result.returncode}; {detail}"
    return _llm_error(reason, detail)


def _llm_error(reason: str, detail: str = "") -> _LlmError:
    return _LlmError(reason=reason, detail=detail)


def _short_detail(value: Any, limit: int = 320) -> str:
    detail = str(value or "").strip()
    if len(detail) <= limit:
        return detail
    return detail[: limit - 1] + "…"


def _classify_cli_failure(
    stdout: str,
    stderr: str,
    log_text: str,
    *,
    default: str,
) -> str:
    text = "\n".join(part for part in (stdout, stderr, log_text) if part)
    http_codes = HTTP_STATUS_PATTERN.findall(text)
    has_auth_status = "401" in http_codes or bool(AUTH_STATUS_CODE_PATTERN.search(text))
    if http_codes:
        text = "\n".join((text, *(f"code {code}" for code in http_codes)))
    if has_auth_status:
        text = "\n".join((text, "not authenticated"))
    reason = classify_failure(text, default=default)
    if reason in {"auth_required", "quota_exhausted"}:
        return reason
    return default


def _scan_redline(text: str) -> str | None:
    for keyword in REDLINE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None


def is_safe_single_line_plain_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    normalized = unicodedata.normalize("NFKC", text)
    if any(
        unicodedata.category(char).startswith("C")
        or unicodedata.category(char) in {"Zl", "Zp"}
        for char in normalized
    ):
        return False
    if EVIDENCE_LABEL_PATTERN.search(normalized):
        return False
    if HTML_ENTITY_PATTERN.search(normalized) or html.unescape(normalized) != normalized:
        return False
    if "`" in normalized:
        return False
    if INLINE_MARKDOWN_LINK_PATTERN.search(normalized):
        return False
    if MARKDOWN_EMPHASIS_PATTERN.search(normalized):
        return False
    if HTML_STRUCTURE_PATTERN.search(normalized):
        return False
    if HORIZONTAL_RULE_PATTERN.search(normalized):
        return False
    return BLOCK_MARKDOWN_PATTERN.search(normalized) is None


def is_safe_llm_reason(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    reason = value.strip()
    return (
        len(reason) <= MAX_REASON_LENGTH
        and is_safe_single_line_plain_text(value)
        and not _scan_redline(reason)
    )


def is_safe_llm_risk_flag(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    flag = value.strip()
    return bool(
        flag
        and len(flag) <= MAX_RISK_FLAG_LENGTH
        and is_safe_single_line_plain_text(value)
        and not _scan_redline(flag)
    )


def _safe_rank(value: Any) -> int | None:
    if type(value) is not int:
        return None
    return value if value > 0 else None


def _apply_llm_judgement(item: dict[str, Any], raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False

    if set(raw) - JUDGEMENT_FIELDS:
        return False
    raw_reason = raw.get("reason")
    if "reason" in raw and not is_safe_llm_reason(raw_reason):
        return False
    reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
    raw_role = raw.get("role")
    if not isinstance(raw_role, str):
        return False
    role = raw_role.strip()
    rank = _safe_rank(raw.get("rank"))
    raw_risk_flags = raw.get("risk_flags")
    if role not in ALLOWED_ROLES or _scan_redline(role):
        return False
    if rank is None:
        return False
    risk_flags: list[str] = []
    if "risk_flags" in raw:
        if not isinstance(raw_risk_flags, list) or len(raw_risk_flags) > MAX_RISK_FLAGS:
            return False
        for raw_flag in raw_risk_flags:
            if not is_safe_llm_risk_flag(raw_flag):
                return False
            flag = raw_flag.strip()
            risk_flags.append(flag)

    item["llm_rank"] = rank
    item["llm_role"] = role
    if reason:
        item["llm_reason"] = reason
    if risk_flags:
        item["risk_flags"] = risk_flags
    return True


def _candidate_key(item: dict[str, Any]) -> str | None:
    stock = str(item.get("stock") or "").strip()
    sector = str(item.get("sector") or "").strip()
    return f"{stock}|{sector}" if stock and sector else None


def _mapping_failure(
    proposal: dict[str, Any],
    reason: str,
    detail: str = "",
) -> dict[str, Any]:
    fallback = copy.deepcopy(proposal)
    fallback["llm_status"] = {"ok": False, "reason": reason}
    if detail:
        fallback["llm_status"]["detail"] = detail
    return fallback


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
        using_internal_runner = runner is None
        result = _run_llm(prompt) if using_internal_runner else runner(prompt)
        if isinstance(result, _LlmError):
            if not using_internal_runner:
                return _mapping_failure(proposal, "invalid_mapping")
            reason = (
                result.reason
                if isinstance(result.reason, str)
                and result.reason in INTERNAL_LLM_FAILURE_REASONS
                else "unknown_error"
            )
            return _mapping_failure(
                proposal,
                reason,
                _short_detail(result.detail),
            )
        mapping = result
        if not mapping:
            return _mapping_failure(proposal, "empty_mapping")
        if not isinstance(mapping, dict):
            return _mapping_failure(proposal, "invalid_mapping")

        enriched = copy.deepcopy(proposal)
        raw_candidates = enriched.get("top_leaders") or []
        if not isinstance(raw_candidates, list):
            return _mapping_failure(proposal, "invalid_mapping")
        candidates = [item for item in raw_candidates if isinstance(item, dict)]
        if len(candidates) != len(raw_candidates) or any(
            _candidate_key(item) is None for item in candidates
        ):
            return _mapping_failure(proposal, "invalid_mapping")
        candidate_keys = [_candidate_key(item) for item in candidates]
        valid_keys = set(candidate_keys)
        if len(candidate_keys) != len(valid_keys):
            return _mapping_failure(proposal, "invalid_mapping")
        if set(mapping) - valid_keys:
            return _mapping_failure(proposal, "invalid_mapping")
        applied_keys: set[str] = set()
        for item in candidates:
            key = _candidate_key(item)
            if key is None:
                continue
            if _apply_llm_judgement(item, mapping.get(key)):
                applied_keys.add(key)
        if not applied_keys:
            return _mapping_failure(proposal, "invalid_mapping")
        if applied_keys != valid_keys:
            return _mapping_failure(proposal, "incomplete_mapping")
        enriched["top_leaders"] = _sort_with_llm(enriched.get("top_leaders") or [])
        enriched["llm_status"] = {"ok": True}
        return enriched
    except subprocess.TimeoutExpired as exc:
        return _mapping_failure(
            proposal,
            "timeout",
            f"timeout_seconds={exc.timeout}",
        )
    except json.JSONDecodeError:
        return _mapping_failure(proposal, "invalid_json")
    except Exception:
        return _mapping_failure(proposal, "exception")
