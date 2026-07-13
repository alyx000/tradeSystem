"""尾盘扫描的当前概念归属与 T-1 热概念上下文。"""
from __future__ import annotations

import math
import re

from services.tail_scan import constants as C


_MEMBERSHIP_STATUSES = {"ok", "missing", "source_failed"}
_SEMANTIC_STOP_WORDS = ("概念", "科技", "行业", "产业", "业务")
_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")


def _clean_text(value) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set, frozenset)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return "" if not text or text.lower() == "nan" else text


def _normalize_code(value) -> str:
    code = _clean_text(value).upper()
    if not code or "." in code:
        return code
    if code.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{code}.BJ"
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _finite_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_integer(value) -> int | None:
    number = _finite_float(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _net_amount(row: dict) -> float | None:
    primary = row.get("net_amount_yi")
    raw = row.get("net_amount") if primary is None or primary == "" else primary
    return _finite_float(raw)


def select_hot_concepts(rows: list[dict], top_m: int) -> tuple[list[str], str]:
    """稳定选出剔除容器后的资金流 Top-M；覆盖不完整时 fail-closed。"""
    if not isinstance(rows, list) or not rows:
        return [], "source_failed"
    if top_m <= 0:
        return [], "ok"

    ranked: list[tuple[float, int, str, dict]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = _clean_text(row.get("name"))
        amount = _net_amount(row)
        if not name or amount is None:
            continue
        ranked.append((amount, index, name, row))
    ranked.sort(key=lambda item: -item[0])  # Python 稳定排序保留同额原始次序。

    selected: list[str] = []
    seen: set[str] = set()
    for _amount, _index, name, row in ranked:
        if len(selected) >= top_m:
            break
        member_count = _positive_integer(row.get("company_num"))
        if member_count is None:
            return [], "coverage_failed"
        if member_count > C.CONCEPT_CONTAINER_MAX_MEMBERS:
            continue
        if name in seen:
            continue
        seen.add(name)
        selected.append(name)

    if len(selected) < top_m:
        return [], "coverage_failed"
    return selected, "ok"


def _filter_memberships(raw_memberships) -> list[dict]:
    if not isinstance(raw_memberships, list):
        return []
    output: list[dict] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    for item in raw_memberships:
        if not isinstance(item, dict):
            continue
        concept_code = _clean_text(item.get("concept_code")).upper()
        name = _clean_text(item.get("name"))
        member_count = _positive_integer(item.get("member_count"))
        if (
            not concept_code
            or not name
            or member_count is None
            or member_count > C.CONCEPT_CONTAINER_MAX_MEMBERS
            or concept_code in seen_codes
            or name in seen_names
        ):
            continue
        seen_codes.add(concept_code)
        seen_names.add(name)
        output.append(
            {
                "concept_code": concept_code,
                "name": name,
                "member_count": member_count,
            }
        )
    return output


def _semantic_runs(value) -> tuple[list[str], list[str]]:
    text = _clean_text(value).lower()
    for word in _SEMANTIC_STOP_WORDS:
        text = text.replace(word, " ")
    return _CHINESE_RUN.findall(text), _ALNUM_RUN.findall(text)


def _longest_common_substring(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            value = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(value)
            longest = max(longest, value)
        previous = current
    return longest


def _semantic_relevance(name: str, semantic_texts: list[str]) -> int:
    name_chinese, name_alnum = _semantic_runs(name)
    best = 0
    for text in semantic_texts:
        text_chinese, text_alnum = _semantic_runs(text)
        for left in name_chinese:
            for right in text_chinese:
                length = _longest_common_substring(left, right)
                if length >= 2:
                    best = max(best, length)
        for left in name_alnum:
            for right in text_alnum:
                length = _longest_common_substring(left, right)
                if length >= 3:
                    best = max(best, length)
    return best


def rank_stock_concepts(
    memberships: list[dict],
    hot_names: list[str],
    semantic_texts: list[str],
) -> list[str]:
    """按热榜、受控语义相关度、成员数和名称稳定排序，不改写概念名。"""
    filtered = _filter_memberships(memberships)
    hot_order = {
        name: index
        for index, name in enumerate(dict.fromkeys(_clean_text(x) for x in hot_names))
        if name
    }
    semantic = []
    for value in semantic_texts:
        text = _clean_text(value)
        if text:
            semantic.append(text)
    return [
        item["name"]
        for item in sorted(
            filtered,
            key=lambda item: (
                0 if item["name"] in hot_order else 1,
                hot_order.get(item["name"], len(hot_order)),
                -_semantic_relevance(item["name"], semantic),
                item["member_count"],
                item["name"],
            ),
        )
    ]


def _membership_result(registry, codes: list[str]):
    try:
        return registry.call("get_stock_concept_memberships", codes)
    except Exception:
        return None


def _hot_result(registry, concept_date: str | None, top_m: int) -> tuple[list[str], str]:
    if concept_date is None:
        return [], "source_failed"
    try:
        result = registry.call("get_concept_moneyflow_ths", concept_date)
    except Exception:
        return [], "source_failed"
    if not getattr(result, "success", False) or not isinstance(result.data, list):
        return [], "source_failed"
    return select_hot_concepts(result.data, top_m)


def build_concept_context(
    registry,
    ts_codes: list[str],
    *,
    concept_date: str | None,
    top_m: int,
) -> dict[str, dict]:
    """批量构建当前归属和上一交易日热概念命中，失败只降级概念维度。"""
    codes = list(
        dict.fromkeys(
            code for code in (_normalize_code(raw) for raw in ts_codes or []) if code
        )
    )
    if not codes:
        return {}

    membership_result = _membership_result(registry, codes)
    member_batch_ok = (
        getattr(membership_result, "success", False)
        and isinstance(getattr(membership_result, "data", None), dict)
        and isinstance(membership_result.data.get("stocks"), dict)
    )
    stocks = membership_result.data["stocks"] if member_batch_ok else {}
    member_source = _clean_text(getattr(membership_result, "source", ""))
    snapshot_at = _clean_text(getattr(membership_result, "fetched_at", ""))

    hot_names, global_hot_status = _hot_result(registry, concept_date, top_m)
    output: dict[str, dict] = {}
    for code in codes:
        raw_row = stocks.get(code) if member_batch_ok else None
        if not isinstance(raw_row, dict):
            raw_status = "source_failed"
            raw_memberships = []
        else:
            raw_status = raw_row.get("status")
            if raw_status not in _MEMBERSHIP_STATUSES:
                raw_status = "source_failed"
            raw_memberships = raw_row.get("concepts")
            if raw_status == "ok" and not isinstance(raw_memberships, list):
                raw_status = "source_failed"
                raw_memberships = []

        memberships = _filter_memberships(raw_memberships) if raw_status == "ok" else []
        stock_status = raw_status
        if stock_status == "ok" and not memberships:
            stock_status = "missing"

        ranked_names = rank_stock_concepts(memberships, hot_names, [])
        by_name = {item["name"]: item for item in memberships}
        ranked_memberships = [by_name[name] for name in ranked_names]
        hits = [name for name in hot_names if name in set(ranked_names)]

        if global_hot_status in {"source_failed", "coverage_failed"}:
            concept_status = global_hot_status
        elif stock_status == "source_failed":
            concept_status = "member_failed"
        else:
            concept_status = "ok"

        output[code] = {
            "stock_concept_names": ranked_names,
            "stock_concept_total": len(ranked_names),
            "stock_concept_status": stock_status,
            "stock_concept_source": member_source,
            "stock_concept_snapshot_at": snapshot_at,
            "concept_names": hits,
            "concept_status": concept_status,
            "in_hot_concept": bool(hits),
            "stock_concept_memberships": ranked_memberships,
        }
    return output
