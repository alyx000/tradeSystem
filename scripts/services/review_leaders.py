"""Shared helpers for review step5 leader writeback."""
from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

from db import queries as Q
from services.daily_leaders.selection import (
    canonical_stock_code,
    normalize_sector_key,
    normalize_stock_display,
)


def _coerce_step5(step5: Any) -> dict[str, Any] | None:
    if not step5:
        return None
    if isinstance(step5, str):
        try:
            step5 = json.loads(step5)
        except (json.JSONDecodeError, TypeError):
            return None
    return step5 if isinstance(step5, dict) else None


def _embedded_stock_code(value: Any) -> str:
    tokens = normalize_stock_display(value).split()
    return canonical_stock_code(tokens[0]) if tokens else ""


def _stock_name_identity(value: Any) -> str:
    display = normalize_stock_display(value)
    tokens = display.split()
    if tokens and canonical_stock_code(tokens[0]):
        display = normalize_stock_display(" ".join(tokens[1:]))
    return "".join(display.split()).upper()


def _reconcile_tracking_identity(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    stock_name: str,
    sector: str,
    attribute_type: str,
) -> None:
    target_name = _stock_name_identity(stock_name)
    rows = [
        row
        for row in Q.get_leader_tracking_by_attribute_type(
            conn,
            attribute_type=attribute_type,
        )
        if normalize_sector_key(row.get("sector")) == sector
    ]
    conflicting_named_code = any(
        (row_code := _embedded_stock_code(row.get("stock_code")))
        and row_code != stock_code
        and target_name
        and target_name == _stock_name_identity(row.get("stock_name"))
        for row in rows
    )
    matching_ids: list[int] = []
    for row in rows:
        row_stock_code = row.get("stock_code")
        row_code = _embedded_stock_code(row_stock_code)
        if row_code:
            if row_code == stock_code:
                matching_ids.append(int(row["id"]))
            continue
        if not conflicting_named_code and target_name and target_name in {
            _stock_name_identity(row_stock_code),
            _stock_name_identity(row.get("stock_name")),
        }:
            matching_ids.append(int(row["id"]))
    if matching_ids:
        Q.merge_leader_tracking_identity(
            conn,
            record_ids=matching_ids,
            stock_code=stock_code,
            stock_name=stock_name,
            sector=sector,
            attribute_type=attribute_type,
        )


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
        sector = normalize_sector_key(sector_raw)
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
        attribute_type = item.get("attribute_type") or item.get("attribute") or ""
        if raw_stock_code not in (None, ""):
            _reconcile_tracking_identity(
                conn,
                stock_code=stock_code,
                stock_name=stock,
                sector=sector,
                attribute_type=attribute_type,
            )
        Q.upsert_leader_tracking(
            conn,
            stock_code=stock_code,
            stock_name=stock,
            sector=sector,
            attribute_type=attribute_type,
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
