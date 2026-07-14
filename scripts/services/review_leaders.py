"""Shared helpers for review step5 leader writeback."""
from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

from db import queries as Q
from services.daily_leaders.selection import canonical_stock_code


def _coerce_step5(step5: Any) -> dict[str, Any] | None:
    if not step5:
        return None
    if isinstance(step5, str):
        try:
            step5 = json.loads(step5)
        except (json.JSONDecodeError, TypeError):
            return None
    return step5 if isinstance(step5, dict) else None


def sync_leader_tracking_from_step5(
    conn: sqlite3.Connection,
    review_date: str,
    step5: Any,
) -> int:
    """Sync confirmed review step5 leaders into leader_tracking."""
    payload = _coerce_step5(step5)
    if not payload:
        return 0
    leaders = payload.get("top_leaders")
    if not isinstance(leaders, list):
        return 0
    synced = 0
    for item in leaders:
        if not isinstance(item, dict):
            continue
        stock_raw = item.get("stock")
        sector_raw = item.get("sector")
        if not isinstance(stock_raw, str) or not isinstance(sector_raw, str):
            continue
        stock = stock_raw.strip()
        sector = sector_raw.strip()
        if not stock or not sector:
            continue
        raw_stock_code = item.get("stock_code")
        if raw_stock_code not in (None, ""):
            if not isinstance(raw_stock_code, str):
                continue
            stock_code = canonical_stock_code(raw_stock_code)
            if not stock_code:
                continue
        else:
            # Legacy review payloads predate canonical stock identities.
            stock_code = stock
        Q.upsert_leader_tracking(
            conn,
            stock_code=stock_code,
            stock_name=stock,
            sector=sector,
            attribute_type=item.get("attribute_type") or item.get("attribute") or "",
            seen_date=review_date,
            current_phase=item.get("position") or None,
        )
        synced += 1
    return synced


def build_review_with_step5(existing: dict[str, Any] | None, step5: dict[str, Any]) -> dict[str, Any]:
    """Return a review payload preserving existing sections while replacing step5."""
    merged = copy.deepcopy(existing or {})
    merged["step5_leaders"] = step5
    return merged
