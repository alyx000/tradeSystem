"""八步复盘路由。"""
from __future__ import annotations

import re
import sqlite3
from datetime import date as _date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_conn
from api.market_enrich import enrich_daily_market_row
from db import queries as Q
from db.dual_write import (
    _normalize_stock_code_for_match,
    holdings_quote_details_from_envelope,
    parse_post_market_envelope,
)
from services.holding_signals import build_holding_signals

router = APIRouter(prefix="/api/review", tags=["review"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOCK_LABEL_RE = re.compile(r"^(?P<name>.*?)(?:\((?P<code>[0-9]{6}(?:\.[A-Z]{2})?)\))?$", re.I)

# prefill 时回溯的行业信息天数
_INDUSTRY_INFO_LOOKBACK_DAYS = 7


def _validate_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(422, f"Invalid date format: {date}")
    return date


def _industry_info_date_from(date_str: str) -> str:
    """计算行业信息回溯起始日期（当日往前 N 天）。"""
    d = _date.fromisoformat(date_str) - timedelta(days=_INDUSTRY_INFO_LOOKBACK_DAYS)
    return d.isoformat()


def _extract_holding_tasks_from_step7(step7_positions: Any) -> list[dict[str, str]]:
    if not isinstance(step7_positions, dict):
        return []
    positions = step7_positions.get("positions")
    if not isinstance(positions, list):
        return []
    tasks: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        action_plan = str(item.get("action_plan") or "").strip()
        stock = str(item.get("stock") or "").strip()
        if not action_plan or not stock:
            continue
        match = _STOCK_LABEL_RE.match(stock)
        code = (match.group("code") if match else "") or ""
        name = ((match.group("name") if match else stock) or "").strip()
        if not code:
            continue
        tasks.append({
            "stock_code": code,
            "stock_name": name,
            "action_plan": action_plan,
            "status": "open",
        })
    return tasks


def _enrich_holdings_prefill_from_post_envelope(
    holdings: list[dict],
    quote_map: dict[str, dict[str, float | None]],
) -> list[dict]:
    """当日 DB 尚无 current_price 时，用 daily_market.raw_data 信封内 holdings_data 补收盘价与盈亏%。
    enrich_daily_market_row 会移除 raw_data，须在 enrich 之前解析 quote_map。
    """
    out: list[dict] = []
    for h in holdings:
        d = dict(h)
        k = _normalize_stock_code_for_match(d.get("stock_code"))
        q = quote_map.get(k) if k else None
        if q:
            close = q.get("close")
            if close is not None and d.get("current_price") is None:
                d["current_price"] = close
            pnl = q.get("pnl_pct")
            if pnl is not None:
                d["prefill_pnl_pct"] = pnl
        out.append(d)
    return out


def _section_rows(section: Any) -> list[dict[str, Any]]:
    if not section:
        return []
    if isinstance(section, list):
        return [row for row in section if isinstance(row, dict)]
    if isinstance(section, dict):
        data = section.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def _to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_row_name(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "-"


def _build_review_signals(market: dict[str, Any] | None) -> dict[str, Any]:
    signals = {
        "market": {
            "moneyflow_summary": None,
            "market_structure_rows": [],
        },
        "sectors": {
            "strongest_rows": [],
            "ths_moneyflow_rows": [],
            "dc_moneyflow_rows": [],
        },
        "emotion": {
            "ladder_rows": [],
        },
    }
    if not market:
        return signals

    market_flow_rows = _section_rows(market.get("market_moneyflow_dc"))
    if market_flow_rows:
        row = market_flow_rows[0]

        def _to_yi(value: Any) -> float | None:
            parsed = _to_number(value)
            return parsed / 1e8 if parsed is not None else None

        signals["market"]["moneyflow_summary"] = {
            "net_amount_yi": _to_yi(row.get("net_amount")),
            "net_amount_rate": _to_number(row.get("net_amount_rate")),
            "super_large_yi": _to_yi(row.get("buy_elg_amount")),
            "large_yi": _to_yi(row.get("buy_lg_amount")),
        }

    daily_info_rows = _section_rows(market.get("daily_info"))
    signals["market"]["market_structure_rows"] = [
        {
            "name": _pick_row_name(row, "market", "board", "exchange", "ts_name", "ts_code", "trade_date"),
            "amount": row.get("amount"),
            "volume": row.get("vol"),
        }
        for row in daily_info_rows[:5]
    ]

    strongest_rows = _section_rows(market.get("limit_cpt_list"))
    signals["sectors"]["strongest_rows"] = [
        {
            "rank": row.get("rank"),
            "name": _pick_row_name(row, "name", "ts_name", "ts_code"),
            "up_nums": row.get("up_nums"),
            "cons_nums": row.get("cons_nums"),
            "pct_chg": row.get("pct_chg"),
            "up_stat": row.get("up_stat"),
        }
        for row in sorted(
            strongest_rows,
            key=lambda item: (
                _to_number(item.get("rank")) if _to_number(item.get("rank")) is not None else 9999,
                -(_to_number(item.get("up_nums")) or 0),
            ),
        )[:5]
    ]

    ths_rows = _section_rows(market.get("sector_moneyflow_ths"))
    signals["sectors"]["ths_moneyflow_rows"] = [
        {
            "name": _pick_row_name(row, "name", "industry", "ts_code"),
            "net_amount": _to_number(row.get("net_amount")),
            "pct_change": _to_number(row.get("pct_change")),
            "lead_stock": row.get("lead_stock"),
        }
        for row in sorted(ths_rows, key=lambda item: -(_to_number(item.get("net_amount")) or 0))[:5]
    ]

    dc_rows = _section_rows(market.get("sector_moneyflow_dc"))
    signals["sectors"]["dc_moneyflow_rows"] = [
        {
            "name": _pick_row_name(row, "name", "industry", "ts_code"),
            "content_type": row.get("content_type"),
            "net_amount_yi": (_to_number(row.get("net_amount")) or 0) / 1e8 if _to_number(row.get("net_amount")) is not None else None,
            "pct_change": _to_number(row.get("pct_change")),
            "lead_stock": row.get("buy_sm_amount_stock"),
        }
        for row in sorted(dc_rows, key=lambda item: -(_to_number(item.get("net_amount")) or 0))[:5]
    ]

    ladder_rows = _section_rows(market.get("limit_step"))
    signals["emotion"]["ladder_rows"] = [
        {
            "name": _pick_row_name(row, "name", "ts_name", "ts_code"),
            "nums": row.get("nums"),
        }
        for row in sorted(ladder_rows, key=lambda item: -(_to_number(item.get("nums")) or 0))[:10]
    ]
    return signals


@router.get("/{date}")
def get_review(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    review = Q.get_daily_review(conn, date)
    if not review:
        return {"date": date, "exists": False}
    return {**review, "exists": True}


@router.get("/{date}/prefill")
def get_prefill(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    market = Q.get_daily_market(conn, date)
    market_for_signals = dict(market) if market else None
    env = parse_post_market_envelope(market.get("raw_data") if market else None)
    holdings_quote_map = holdings_quote_details_from_envelope(env)
    enrich_daily_market_row(market)  # 展开 raw_data 中的扩展字段（style_factors/sector_*/rhythm_* 等）
    prev_market = Q.get_prev_daily_market(conn, date)
    avg_5d = Q.get_avg_amount(conn, date, 5)
    avg_20d = Q.get_avg_amount(conn, date, 20)
    emotion = Q.get_latest_emotion(conn)
    themes = Q.get_active_themes(conn)
    holdings = _enrich_holdings_prefill_from_post_envelope(
        Q.get_holdings(conn, status="active"),
        holdings_quote_map,
    )
    calendar = Q.get_calendar_range(conn, date, date)

    notes = conn.execute(
        "SELECT n.*, t.name as teacher_name FROM teacher_notes n "
        "JOIN teachers t ON n.teacher_id = t.id WHERE n.date = ? ORDER BY n.created_at DESC",
        (date,),
    ).fetchall()
    prev_review = Q.get_prev_daily_review(conn, date)

    # 近 N 天行业信息/行业笔记（来自 industry_info 表）
    industry_info = Q.get_recent_industry_info(
        conn,
        date_from=_industry_info_date_from(date),
        date_to=date,
    )
    review_signals = _build_review_signals(market)
    holding_signals = build_holding_signals(conn, date, market_row=market_for_signals)

    return {
        "date": date,
        "market": market,
        "prev_market": prev_market,
        "avg_5d_amount": avg_5d,
        "avg_20d_amount": avg_20d,
        "teacher_notes": [dict(n) for n in notes],
        "emotion_cycle": emotion,
        "main_themes": themes,
        "holdings": holdings,
        "calendar_events": calendar,
        "prev_review": prev_review,
        "industry_info": industry_info,
        "review_signals": review_signals,
        "holding_signals": holding_signals,
    }


@router.put("/{date}")
def save_review(date: str, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    Q.upsert_daily_review(conn, date, body)
    if "step7_positions" in body:
        tasks = _extract_holding_tasks_from_step7(body.get("step7_positions"))
        Q.replace_holding_tasks(conn, trade_date=date, tasks=tasks, source="review_step7")
    conn.commit()
    return {"ok": True, "date": date}
