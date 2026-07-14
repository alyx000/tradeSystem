from __future__ import annotations

import math
import re
from typing import Any

from services.daily_leaders.models import (
    LEADER_ROLES,
    MAX_CONFIRMATION_CANDIDATES,
    MAX_LLM_REVIEW_CANDIDATES,
)


def normalize_stock_display(value: Any) -> str:
    """Normalize whitespace without collapsing a code/name display pair."""
    tokens = str(value or "").split()
    if not tokens:
        return ""
    for unit_length in range(1, len(tokens) // 2 + 1):
        if len(tokens) % unit_length:
            continue
        unit = tokens[:unit_length]
        if unit * (len(tokens) // unit_length) == tokens:
            return " ".join(unit)
    return " ".join(tokens)


def _normalize_stock_code(value: Any) -> str:
    code = "".join(str(value or "").split()).upper()
    return re.sub(r"\.(?:SH|SZ|SS|BJ)$", "", code)


def stock_identity_key(item: dict[str, Any]) -> str:
    """Return the canonical code when present, otherwise the normalized display."""
    for value in (item.get("stock_code"), item.get("code")):
        code = _normalize_stock_code(value)
        if code:
            return code
    for value in (item.get("stock"), item.get("stock_name"), item.get("name")):
        display = normalize_stock_display(value).upper()
        if display:
            return display
    return ""


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_limit_core(item: dict[str, Any]) -> bool:
    height = _finite_number(item.get("limit_height"))
    return height is not None and height >= 2


def _sector_key(value: Any) -> str:
    return " ".join(str(value or "").split())


def _selection_score(item: dict[str, Any]) -> float:
    return _finite_number(item.get("_selection_score")) or 0.0


def assign_fallback_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign deterministic roles on copies of the supplied candidates."""
    assigned = [dict(item) for item in items if isinstance(item, dict)]
    if assigned and all(item.get("fallback_role") in LEADER_ROLES for item in assigned):
        return assigned
    sector_amount_winners: dict[str, tuple[float, int]] = {}

    for index, item in enumerate(assigned):
        amount_yi = _finite_number(item.get("amount_yi"))
        if amount_yi is None:
            continue
        sector = _sector_key(item.get("sector"))
        current = sector_amount_winners.get(sector)
        if current is None or amount_yi > current[0]:
            sector_amount_winners[sector] = (amount_yi, index)

    winner_indexes = {winner[1] for winner in sector_amount_winners.values()}
    for index, item in enumerate(assigned):
        if _is_limit_core(item):
            role = "连板核心"
        elif index in winner_indexes:
            role = "趋势中军"
        elif item.get("board_type") in {"20cm", "30cm"}:
            role = "弹性前排"
        else:
            role = "前排活跃"
        item["fallback_role"] = role
    return assigned


def _ensure_fallback_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    originals = [dict(item) for item in items if isinstance(item, dict)]
    assigned = assign_fallback_roles(originals)
    for original, candidate in zip(originals, assigned):
        existing_role = original.get("fallback_role")
        if isinstance(existing_role, str) and existing_role.strip() in LEADER_ROLES:
            candidate["fallback_role"] = existing_role.strip()
    return assigned


def prepare_llm_review_pool(
    items: list[dict[str, Any]],
    limit: int = MAX_LLM_REVIEW_CANDIDATES,
) -> list[dict[str, Any]]:
    """Build a stable score-ranked pool with at most two candidates per role group."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    global_limit = min(limit, MAX_LLM_REVIEW_CANDIDATES)

    assigned = assign_fallback_roles(items)
    ranked = sorted(
        enumerate(assigned),
        key=lambda pair: (-_selection_score(pair[1]), pair[0]),
    )
    group_counts: dict[tuple[str, str], int] = {}
    seen_stocks: set[str] = set()
    pool: list[dict[str, Any]] = []
    for _, item in ranked:
        stock_key = stock_identity_key(item)
        if stock_key and stock_key in seen_stocks:
            continue
        group = (_sector_key(item.get("sector")), item["fallback_role"])
        if group_counts.get(group, 0) >= 2:
            continue
        group_counts[group] = group_counts.get(group, 0) + 1
        if stock_key:
            seen_stocks.add(stock_key)
        pool.append(item)
        if len(pool) >= global_limit:
            break
    return pool


def _positive_rank(value: Any) -> int | None:
    if type(value) is not int:
        return None
    return value if value > 0 else None


def _candidate_for_selection(
    item: dict[str, Any],
    *,
    llm_ok: bool,
) -> tuple[dict[str, Any] | None, int, int | None]:
    candidate = dict(item)
    fallback_role = candidate["fallback_role"]
    llm_role = str(candidate.get("llm_role") or "").strip()
    parsed_llm_rank = _positive_rank(candidate.get("llm_rank")) if llm_ok else None
    effective_llm_rank = None
    backup_penalty = 0

    if llm_ok and llm_role == "剔除":
        return None, backup_penalty, effective_llm_rank
    if llm_ok and llm_role == "备选":
        leader_role = fallback_role
        selection_basis = "deterministic_fallback"
        backup_penalty = 1
    elif llm_ok and llm_role in LEADER_ROLES:
        leader_role = llm_role
        selection_basis = "llm"
        effective_llm_rank = parsed_llm_rank
    else:
        leader_role = fallback_role
        selection_basis = "deterministic_fallback"

    if "stock" in candidate:
        candidate["stock"] = normalize_stock_display(candidate.get("stock"))
    candidate["leader_role"] = leader_role
    candidate["attribute_type"] = leader_role
    candidate["selection_basis"] = selection_basis
    return candidate, backup_penalty, effective_llm_rank


def select_confirmation_candidates(
    items: list[dict[str, Any]],
    max_candidates: int = MAX_CONFIRMATION_CANDIDATES,
    llm_ok: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply ranking and hard confirmation constraints in a deterministic order."""
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CONFIRMATION_CANDIDATES:
        raise ValueError("max_candidates must be between 1 and 15")
    final_limit = max_candidates

    original_count = len(items)
    assigned = _ensure_fallback_roles(items)
    sortable: list[tuple[int, int, float, int, dict[str, Any]]] = []
    for index, item in enumerate(assigned):
        candidate, backup_penalty, llm_rank = _candidate_for_selection(item, llm_ok=llm_ok)
        if candidate is None:
            continue
        rank_key = llm_rank if llm_rank is not None else 1_000_000_000
        sortable.append((backup_penalty, rank_key, -_selection_score(candidate), index, candidate))

    if llm_ok:
        ordered = [entry[-1] for entry in sorted(sortable, key=lambda entry: entry[:-1])]
    else:
        ordered = [entry[-1] for entry in sorted(sortable, key=lambda entry: (entry[2], entry[3]))]

    sector_role_trimmed_count = 0
    seen_sector_roles: set[tuple[str, str]] = set()
    sector_role_unique: list[dict[str, Any]] = []
    for item in ordered:
        sector_role = (_sector_key(item.get("sector")), item["leader_role"])
        if sector_role in seen_sector_roles:
            sector_role_trimmed_count += 1
            continue
        seen_sector_roles.add(sector_role)
        sector_role_unique.append(item)

    stock_duplicate_trimmed_count = 0
    seen_stocks: set[str] = set()
    stock_unique: list[dict[str, Any]] = []
    for item in sector_role_unique:
        stock_key = stock_identity_key(item)
        if stock_key in seen_stocks:
            stock_duplicate_trimmed_count += 1
            continue
        seen_stocks.add(stock_key)
        stock_unique.append(item)

    selected = stock_unique[:final_limit]
    stats = {
        "original_count": original_count,
        "review_pool_count": len(items),
        "final_count": len(selected),
        "sector_role_trimmed_count": sector_role_trimmed_count,
        "stock_duplicate_trimmed_count": stock_duplicate_trimmed_count,
        "trimmed_count": max(0, original_count - len(selected)),
    }
    return selected, stats
