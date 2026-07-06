"""LLM-assisted mainline concept filtering for trend-leader.

LLM is deliberately scoped to the mainline gate only:
- It may accept/reject THS concepts that are already present in deterministic data.
- It must not add concepts, stocks, sectors, prices, or trade actions.
- It never vetoes SW L2 Top-K sectors; scanner keeps those deterministic.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from services.recommend.formatter import REDLINE_KEYWORDS

logger = logging.getLogger(__name__)

Runner = Callable[[str, dict[str, Any]], Optional[dict[str, Any]]]


def filter_concepts(
    *,
    date: str,
    main_sectors: set[str],
    main_concepts: set[str],
    candidates: list[dict[str, Any]],
    runner: Runner | None,
) -> tuple[set[str], dict[str, Any]]:
    """Return effective THS concepts plus metadata.

    No runner means deterministic fallback without warning. A runner returning
    invalid/out-of-universe JSON falls back to the unfiltered deterministic
    concept set and marks status=fallback.
    """
    concepts = set(main_concepts or set())
    meta: dict[str, Any] = {
        "enabled": runner is not None,
        "status": "disabled" if runner is None else "pending",
        "accepted_concepts": sorted(concepts),
        "rejected": [],
    }
    if runner is None or not concepts:
        if runner is not None:
            meta["status"] = "skipped_empty_concepts"
        return concepts, meta

    payload = {
        "date": date,
        "main_sectors": sorted(main_sectors or set()),
        "main_concepts": sorted(concepts),
        "candidates": candidates,
    }
    try:
        result = runner(_build_prompt(), payload)
    except Exception as exc:  # noqa: BLE001 - LLM failure must degrade, not stop EOD scan.
        logger.warning("[trend-leader] mainline LLM failed, fallback to deterministic concepts: %s", exc)
        meta.update({"status": "fallback", "reason": "runner_exception"})
        return concepts, meta

    parsed = _parse_result(result, concepts)
    if parsed is None:
        meta.update({"status": "fallback", "reason": "invalid_output"})
        return concepts, meta

    accepted, rejected = parsed
    meta.update({
        "status": "ok",
        "accepted_concepts": sorted(accepted),
        "rejected": rejected,
    })
    return accepted, meta


def _build_prompt() -> str:
    return (
        "你是A股短线复盘中的主线筛选助手。请只筛选主线概念：从输入的同花顺概念中判断哪些"
        "属于当前趋势主线或主线窄分支，过滤明显容器概念、防御篮子、宽泛资格标签。"
        "申万二级主线由程序保留，你不得否决申万二级。"
        "只能基于输入事实判断，不得新增概念、股票或事实，不得给买卖建议、价格目标、仓位建议。"
        "输出 JSON object，格式："
        "{\"accepted_concepts\":[\"概念名\"],\"rejected\":[{\"name\":\"概念名\",\"reason\":\"≤20字\"}]}"
    )


def _parse_result(result: Any, universe: set[str]) -> tuple[set[str], list[dict[str, str]]] | None:
    if not isinstance(result, dict):
        return None
    if _scan_redline(json.dumps(result, ensure_ascii=False)):
        return None
    raw_accepted = result.get("accepted_concepts")
    if not isinstance(raw_accepted, list):
        return None
    accepted = {str(x).strip() for x in raw_accepted if str(x or "").strip()}
    if not accepted.issubset(universe):
        return None

    rejected: list[dict[str, str]] = []
    raw_rejected = result.get("rejected") or []
    if isinstance(raw_rejected, list):
        for row in raw_rejected:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name or name not in universe:
                continue
            reason = str(row.get("reason") or "").strip()[:40]
            rejected.append({"name": name, "reason": reason})
    return accepted, rejected


def _scan_redline(text: str) -> str | None:
    for keyword in REDLINE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None
