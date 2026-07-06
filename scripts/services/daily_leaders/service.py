from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from db import queries as Q
from services.daily_leaders.candidates import build_candidates
from services.daily_leaders.llm import enrich_with_llm_reason
from services.daily_leaders.store import _safe_date, read_proposal, write_proposal
from services.review_leaders import build_review_with_step5, sync_leader_tracking_from_step5


def _active_history(conn) -> list[dict[str, Any]]:
    try:
        return Q.get_active_leaders(conn)
    except Exception:
        return []


def _trend_pool(conn) -> list[dict[str, Any]]:
    try:
        from services.trend_leader.pool import list_pool

        return list_pool(conn, status="active")
    except Exception:
        return []


def _amount_to_yi(value: Any) -> float | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if abs(amount) >= 1_000_000:
        return round(amount / 1e5, 2)
    return round(amount, 2)


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
    try:
        result = registry.call("get_market_daily_quotes", date)
    except Exception as exc:  # noqa: BLE001 - quote enrichment is non-critical evidence.
        market["stock_quotes"] = {"data": [], "error": str(exc)}
        return out
    if not getattr(result, "success", False):
        market["stock_quotes"] = {"data": [], "error": str(getattr(result, "error", "") or "quote_fetch_failed")}
        return out
    name_by_code: dict[str, str] = {}
    try:
        basic_result = registry.call("get_stock_basic_list", date)
        if getattr(basic_result, "success", False):
            for row in getattr(basic_result, "data", None) or []:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
                name = str(row.get("name") or row.get("stock_name") or "").strip()
                if code and name:
                    name_by_code[code] = name
    except Exception:
        name_by_code = {}
    rows = []
    for row in getattr(result, "data", None) or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ts_code") or row.get("code") or "").strip()
        name = str(row.get("name") or row.get("stock_name") or name_by_code.get(code.upper(), "")).strip()
        if not name and not code:
            continue
        rows.append({
            "code": code,
            "name": name,
            "pct_chg": row.get("pct_chg"),
            "amount_yi": _amount_to_yi(row.get("amount")),
        })
    market["stock_quotes"] = {"data": rows}
    return out


def propose(
    conn,
    date: str,
    prefill: dict[str, Any],
    no_llm: bool = False,
    output_root: str | Path | None = None,
    registry: Any | None = None,
) -> dict[str, Any]:
    prefill = attach_market_quotes(prefill, date, registry)
    proposal = build_candidates(
        prefill=prefill,
        trend_pool=_trend_pool(conn),
        history=_active_history(conn),
        date=date,
    )
    proposal = enrich_with_llm_reason(proposal, enabled=not no_llm)
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
        step5_leaders.append({
            "stock": stock,
            "sector": sector,
            "attribute_type": item.get("attribute_type"),
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
    existing = Q.get_daily_review(conn, date) or {}
    payload = build_review_with_step5(existing, step5)
    Q.upsert_daily_review(conn, date, payload)
    synced = sync_leader_tracking_from_step5(conn, date, step5)
    conn.commit()
    return {
        "ok": True,
        "date": date,
        "synced_leader_tracking": synced,
        "step5_leaders": step5_leaders,
    }
