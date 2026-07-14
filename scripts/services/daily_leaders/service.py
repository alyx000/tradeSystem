from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

from db import queries as Q
from services.daily_leaders.candidates import build_candidates
from services.daily_leaders.llm import enrich_with_llm_reason
from services.daily_leaders.models import (
    LEADER_ROLES,
    MAX_CONFIRMATION_CANDIDATES,
    MAX_LLM_REVIEW_CANDIDATES,
    STATUS_ROLES,
)
from services.daily_leaders.selection import (
    # Keep this service-level re-export for callers that assemble the funnel in stages.
    assign_fallback_roles,
    prepare_llm_review_pool,
    select_confirmation_candidates,
    stock_identity_key,
)
from services.daily_leaders.store import _safe_date, read_proposal, write_proposal
from services.review_leaders import build_review_with_step5, sync_leader_tracking_from_step5
from services.trinity_factor.cycle import invalidate_factor_decision
from services.trinity_factor.review_input import parse_review_step


DEFAULT_MAX_CONFIRMATION_CANDIDATES = MAX_CONFIRMATION_CANDIDATES
DEFAULT_MIN_CONFIRMATION_AMOUNT_YI = 20.0

_A_SHARE_CODE_RE = re.compile(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)


def _active_history(conn) -> list[dict[str, Any]]:
    try:
        return Q.get_active_leaders(conn)
    except Exception:
        return []


def _amount_to_yi(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount):
        return None
    return round(amount / 1e5, 2)


def _code_keys(value: Any) -> tuple[str, ...]:
    full = "".join(str(value or "").split()).upper()
    if not full:
        return ()
    bare = full.split(".", 1)[0]
    return (full,) if bare == full else (full, bare)


def _canonical_a_share_code(value: Any) -> str:
    normalized = "".join(str(value or "").split()).upper()
    match = _A_SHARE_CODE_RE.fullmatch(normalized)
    return match.group(1) if match else ""


def _name_key(value: Any) -> str:
    return "".join(str(value or "").split()).upper()


def _section_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("data")
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _limit_height_indexes(market: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    by_code: dict[str, int] = {}
    by_name: dict[str, int] = {}
    codes_by_name: dict[str, set[str]] = {}
    for row in _section_rows(market.get("limit_step")):
        name = _name_key(row.get("name") or row.get("stock_name"))
        canonical_code = _canonical_a_share_code(
            row.get("ts_code") or row.get("code")
        )
        if name and canonical_code:
            codes_by_name.setdefault(name, set()).add(canonical_code)
        raw_height = row.get("nums")
        if raw_height in (None, "") or isinstance(raw_height, bool):
            continue
        try:
            numeric_height = float(raw_height)
        except (TypeError, ValueError):
            continue
        if (
            not math.isfinite(numeric_height)
            or numeric_height < 0
            or not numeric_height.is_integer()
        ):
            continue
        height = int(numeric_height)
        if canonical_code:
            for key in _code_keys(row.get("ts_code") or row.get("code")):
                by_code[key] = max(by_code.get(key, 0), height)
        if name:
            by_name[name] = max(by_name.get(name, 0), height)
    for name, codes in codes_by_name.items():
        if len(codes) > 1:
            by_name.pop(name, None)
    return by_code, by_name


def attach_market_quotes(
    prefill: dict[str, Any],
    date: str,
    registry: Any | None,
) -> dict[str, Any]:
    """Attach all-market daily quote rows for LLM candidate evidence.

    Tushare `daily.amount` is in thousand yuan, so amount / 1e5 = 亿元.
    This is best-effort: provider failures are recorded in market.stock_quotes.error.
    """
    out = copy.deepcopy(prefill)
    market = out.get("market")
    if not isinstance(market, dict):
        market = {}
        out["market"] = market
    if registry is None:
        return out
    initializer = getattr(registry, "initialize_all", None)
    if callable(initializer):
        initializer()
    result = None
    quote_error = ""
    try:
        result = registry.call("get_market_daily_quotes", date)
    except Exception as exc:  # noqa: BLE001 - quote enrichment is non-critical evidence.
        quote_error = str(exc)
    quote_success = bool(getattr(result, "success", False))
    if not quote_success:
        quote_error = quote_error or str(
            getattr(result, "error", "") or "quote_fetch_failed"
        )
        market["stock_quotes"] = {"data": [], "error": quote_error}
    name_by_code: dict[str, str] = {}
    if quote_success:
        try:
            basic_result = registry.call("get_stock_basic_list", date)
            if getattr(basic_result, "success", False):
                for row in getattr(basic_result, "data", None) or []:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
                    name = str(row.get("name") or row.get("stock_name") or "").strip()
                    if code and name:
                        for key in _code_keys(code):
                            name_by_code[key] = name
        except Exception:
            name_by_code = {}

    industry_by_code: dict[str, dict[str, Any]] = {}
    industry_source = ""
    industry_status = "source_failed"
    industry_error = ""
    try:
        industry_result = registry.call("get_stock_sw_industry_map")
        industry_source = str(
            getattr(industry_result, "source", "") or "tushare:index_member_all"
        )
        if getattr(industry_result, "success", False) and isinstance(
            getattr(industry_result, "data", None), dict
        ):
            for raw_code, entry in industry_result.data.items():
                if not isinstance(entry, dict):
                    continue
                for key in _code_keys(raw_code):
                    industry_by_code[key] = entry
            industry_status = "success"
        else:
            industry_error = str(
                getattr(industry_result, "error", "") or "industry_map_fetch_failed"
            )
    except Exception as exc:  # noqa: BLE001 - industry mapping is best-effort evidence.
        industry_error = str(exc)
    market["stock_industry_map"] = {
        "status": industry_status,
        "source": industry_source,
        "error": industry_error,
    }
    if not quote_success:
        return out

    limit_by_code, limit_by_name = _limit_height_indexes(market)
    rows = []
    for row in getattr(result, "data", None) or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ts_code") or row.get("code") or "").strip()
        code_keys = _code_keys(code)
        industry_entry = next(
            (industry_by_code[key] for key in code_keys if key in industry_by_code),
            {},
        )
        name = str(
            row.get("name")
            or row.get("stock_name")
            or next((name_by_code[key] for key in code_keys if key in name_by_code), "")
            or industry_entry.get("name")
            or ""
        ).strip()
        if not name and not code:
            continue
        sw_l2 = str(industry_entry.get("sw_l2") or "").strip()
        if _canonical_a_share_code(code):
            limit_heights = [
                limit_by_code[key] for key in code_keys if key in limit_by_code
            ]
        else:
            name_key = _name_key(name)
            limit_heights = (
                [limit_by_name[name_key]] if name_key in limit_by_name else []
            )
        quote_row = {
            "code": code,
            "name": name,
            "pct_chg": row.get("pct_chg"),
            "amount_yi": _amount_to_yi(row.get("amount")),
            "sw_l2": sw_l2,
            "sector_source": industry_source if sw_l2 else "",
        }
        if limit_heights:
            quote_row["limit_height"] = max(limit_heights)
        rows.append(quote_row)
    market["stock_quotes"] = {"data": rows}
    return out


def _validate_max_candidates(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("max_candidates must be between 1 and 15")
    if not 1 <= value <= MAX_CONFIRMATION_CANDIDATES:
        raise ValueError("max_candidates must be between 1 and 15")
    return value


def _without_internal_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in items
    ]


def _raw_stock_counts(items: list[dict[str, Any]]) -> tuple[int, int]:
    seen: set[str] = set()
    duplicate_count = 0
    for item in items:
        key = stock_identity_key(item)
        if not key:
            continue
        if key in seen:
            duplicate_count += 1
        else:
            seen.add(key)
    return len(seen), duplicate_count


def propose(
    conn,
    date: str,
    prefill: dict[str, Any],
    no_llm: bool = False,
    output_root: str | Path | None = None,
    registry: Any | None = None,
    max_candidates: int = DEFAULT_MAX_CONFIRMATION_CANDIDATES,
) -> dict[str, Any]:
    max_candidates = _validate_max_candidates(max_candidates)
    if prefill.get("is_trading_day") is False:
        proposal = {
            "date": date,
            "top_leaders": [],
            "skipped": {
                "reason": "non_trading_day",
                "prev_trade_date": prefill.get("prev_trade_date"),
            },
        }
        paths = write_proposal(proposal, root=output_root)
        proposal["paths"] = {key: str(value) for key, value in paths.items()}
        return proposal

    prefill = attach_market_quotes(prefill, date, registry)
    proposal = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=_active_history(conn),
        date=date,
        min_amount_yi=DEFAULT_MIN_CONFIRMATION_AMOUNT_YI,
    )
    proposal["candidate_filters"] = {
        "min_amount_yi": DEFAULT_MIN_CONFIRMATION_AMOUNT_YI,
    }
    raw_items = [
        item for item in proposal.get("top_leaders") or [] if isinstance(item, dict)
    ]
    original_count = len(raw_items)
    deduped_count, duplicate_trimmed_count = _raw_stock_counts(raw_items)
    review_pool = prepare_llm_review_pool(
        raw_items,
        limit=MAX_LLM_REVIEW_CANDIDATES,
    )
    proposal["top_leaders"] = review_pool
    if no_llm:
        proposal["llm_status"] = {"ok": False, "reason": "disabled"}
    else:
        proposal = enrich_with_llm_reason(proposal, enabled=True)
        if not isinstance(proposal.get("llm_status"), dict):
            proposal["llm_status"] = {"ok": False, "reason": "missing_status"}

    llm_ok = bool((proposal.get("llm_status") or {}).get("ok"))
    selected, selection_stats = select_confirmation_candidates(
        proposal.get("top_leaders") or [],
        max_candidates=max_candidates,
        llm_ok=llm_ok,
    )
    final_count = len(selected)
    proposal["top_leaders"] = _without_internal_fields(selected)
    proposal["candidate_limit"] = {
        "max_candidates": max_candidates,
        "original_count": original_count,
        "deduped_count": deduped_count,
        "duplicate_trimmed_count": duplicate_trimmed_count,
        "trimmed_count": max(0, original_count - final_count),
        "review_pool_count": len(review_pool),
        "review_pool_trimmed_count": max(0, len(raw_items) - len(review_pool)),
        "sector_role_trimmed_count": selection_stats.get(
            "sector_role_trimmed_count", 0
        ),
        "stock_duplicate_trimmed_count": selection_stats.get(
            "stock_duplicate_trimmed_count", 0
        ),
        "final_count": final_count,
    }
    paths = write_proposal(proposal, root=output_root)
    proposal["paths"] = {key: str(value) for key, value in paths.items()}
    return proposal


def show(date: str, output_root: str | Path | None = None) -> dict[str, Any]:
    return read_proposal(date, root=output_root)


def _confirmed_step5_leaders(source: dict[str, Any], date: str) -> list[dict[str, Any]]:
    source_date = str(source.get("date") or "").strip()
    if source_date:
        source_date = _safe_date(source_date)
    if source_date and source_date != date:
        raise ValueError(f"leaders source date {source_date} does not match CLI date {date}")

    raw_leaders = source.get("top_leaders")
    if not isinstance(raw_leaders, list):
        raise ValueError("top_leaders must be a list")

    step5_leaders = []
    for index, item in enumerate(raw_leaders, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"top_leaders[{index}] must be an object")
        stock_raw = item.get("stock")
        sector_raw = item.get("sector")
        if not isinstance(stock_raw, str) or not isinstance(sector_raw, str):
            raise ValueError(f"top_leaders[{index}] stock and sector must be strings")
        stock = stock_raw.strip()
        sector = sector_raw.strip()
        if not stock or not sector:
            raise ValueError(f"top_leaders[{index}] stock and sector are required")
        leader_role = str(item.get("leader_role") or "").strip()
        llm_role = str(item.get("llm_role") or "").strip()
        legacy_attribute = item.get("attribute_type")
        legacy_text = str(legacy_attribute or "").strip()
        if leader_role in LEADER_ROLES:
            attribute_type = leader_role
        elif llm_role in LEADER_ROLES:
            attribute_type = llm_role
        elif legacy_text in STATUS_ROLES:
            attribute_type = None
        else:
            attribute_type = legacy_attribute
        step5_leaders.append({
            "stock": stock,
            "sector": sector,
            "attribute_type": attribute_type,
            "attribute": item.get("attribute"),
            "clarity": item.get("clarity"),
            "position": item.get("position"),
            "is_new": item.get("is_new"),
        })
    return step5_leaders


def confirm(
    conn,
    date: str,
    input_by: str,
    leaders_file: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    actor = str(input_by or "").strip()
    if not actor:
        raise ValueError("--input-by is required")
    date = _safe_date(date)

    if leaders_file:
        source = json.loads(Path(leaders_file).read_text(encoding="utf-8"))
    else:
        source = read_proposal(date, root=output_root)

    if not isinstance(source, dict):
        raise ValueError("leaders source must be an object")
    step5_leaders = _confirmed_step5_leaders(source, date)
    step5 = {
        "top_leaders": step5_leaders,
        "notes": f"daily-leaders confirmed by {actor}",
    }
    if conn.in_transaction:
        raise RuntimeError("daily-leaders confirm requires a clean transaction boundary")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = Q.get_daily_review(conn, date) or {}
        payload = build_review_with_step5(existing, step5)
        if parse_review_step(existing.get("step5_leaders")) != step5:
            cleared_step8, invalidated = invalidate_factor_decision(
                existing.get("step8_plan")
            )
            if invalidated:
                payload["step8_plan"] = cleared_step8
        Q.upsert_daily_review(conn, date, payload)
        synced = sync_leader_tracking_from_step5(conn, date, step5)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "ok": True,
        "date": date,
        "synced_leader_tracking": synced,
        "step5_leaders": step5_leaders,
    }
