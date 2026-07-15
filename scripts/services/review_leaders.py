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
    parse_stock_display_identity,
    stock_name_identity,
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
    identity = parse_stock_display_identity(value)
    return "" if identity.malformed_code_prefix else identity.code


def _reconcile_tracking_identity(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    stock_name: str,
    sector: str,
    attribute_type: str,
    name_codes_in_batch: set[str],
) -> None:
    target_name = stock_name_identity(stock_name)
    all_rows = Q.get_all_leader_tracking(conn)
    scoped_rows = [
        row
        for row in all_rows
        if row.get("attribute_type") == attribute_type
        and normalize_sector_key(row.get("sector")) == sector
    ]
    existing_name_codes = {
        row_code
        for row in all_rows
        if (row_code := _embedded_stock_code(row.get("stock_code")))
        and target_name
        in {
            stock_name_identity(row.get("stock_code")),
            stock_name_identity(row.get("stock_name")),
        }
    }
    conflicting_named_code = any(
        code != stock_code for code in existing_name_codes | name_codes_in_batch
    )
    matching_ids: list[int] = []
    for row in scoped_rows:
        row_stock_code = row.get("stock_code")
        row_code = _embedded_stock_code(row_stock_code)
        if row_code:
            if row_code == stock_code:
                matching_ids.append(int(row["id"]))
            continue
        if not conflicting_named_code and target_name and target_name in {
            stock_name_identity(row_stock_code),
            stock_name_identity(row.get("stock_name")),
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
    batch_codes_by_name: dict[str, set[str]] = {}
    for item in leaders:
        if not isinstance(item, dict):
            continue
        raw_stock = item.get("stock")
        raw_sector = item.get("sector")
        raw_stock_code = item.get("stock_code")
        if not isinstance(raw_stock, str) or not raw_stock.strip():
            continue
        display_identity = parse_stock_display_identity(raw_stock)
        if display_identity.malformed_code_prefix:
            raise ValueError("leader stock display contains a malformed stock code suffix")
        has_explicit_code = raw_stock_code not in (None, "")
        if has_explicit_code and not isinstance(raw_stock_code, str):
            continue
        explicit_code = canonical_stock_code(raw_stock_code)
        if has_explicit_code and not explicit_code:
            continue
        if (
            explicit_code
            and display_identity.code
            and explicit_code != display_identity.code
        ):
            raise ValueError("leader stock display and stock_code contain conflicting stock codes")
        if not isinstance(raw_sector, str) or not normalize_sector_key(raw_sector):
            continue
        effective_code = explicit_code or display_identity.code
        name_key = display_identity.name_key
        if effective_code and name_key:
            batch_codes_by_name.setdefault(name_key, set()).add(effective_code)
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
                name_codes_in_batch=batch_codes_by_name.get(
                    stock_name_identity(stock), set()
                ),
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
