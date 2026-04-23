from __future__ import annotations

from typing import Iterable, Any

from db.dual_write import _normalize_stock_code_for_match
from providers.registry import ProviderRegistry


def _dedupe_queries(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def resolve_stock_codes(registry: ProviderRegistry, codes: Iterable[str | None]) -> dict[str, Any]:
    queries = _dedupe_queries(codes)
    result: dict[str, Any] = {
        "mode": "code",
        "resolved": [],
        "ambiguous": [],
        "not_found": [],
        "error": "",
    }
    if not queries:
        return result

    response = registry.call("get_stock_basic_batch", queries)
    if not response.success or not isinstance(response.data, list):
        result["error"] = response.error or "provider_error"
        result["not_found"] = [{"query": query, "reason": "provider_error"} for query in queries]
        return result

    rows_by_norm: dict[str, dict[str, str]] = {}
    for row in response.data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        rows_by_norm[_normalize_stock_code_for_match(code)] = {"code": code, "name": name}

    for query in queries:
        matched = rows_by_norm.get(_normalize_stock_code_for_match(query))
        if matched:
            result["resolved"].append({
                "query": query,
                "code": matched["code"],
                "name": matched["name"],
                "match_type": "code_exact",
            })
        else:
            result["not_found"].append({"query": query, "reason": "code_not_found"})
    return result


def resolve_stock_names(
    registry: ProviderRegistry,
    names: Iterable[str | None],
    target_date: str = "2000-01-01",
) -> dict[str, Any]:
    queries = _dedupe_queries(names)
    result: dict[str, Any] = {
        "mode": "name",
        "resolved": [],
        "ambiguous": [],
        "not_found": [],
        "error": "",
    }
    if not queries:
        return result

    response = registry.call("get_stock_basic_list", target_date)
    if not response.success or not isinstance(response.data, list):
        result["error"] = response.error or "provider_error"
        result["not_found"] = [{"query": query, "reason": "provider_error"} for query in queries]
        return result

    buckets: dict[str, list[dict[str, str]]] = {}
    for row in response.data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        buckets.setdefault(name, []).append({"code": code, "name": name})

    for query in queries:
        candidates = sorted(
            buckets.get(query, []),
            key=lambda item: item["code"],
        )
        if len(candidates) == 1:
            matched = candidates[0]
            result["resolved"].append({
                "query": query,
                "code": matched["code"],
                "name": matched["name"],
                "match_type": "name_exact",
            })
        elif len(candidates) > 1:
            result["ambiguous"].append({"query": query, "candidates": candidates})
        else:
            result["not_found"].append({"query": query, "reason": "name_not_found"})
    return result
