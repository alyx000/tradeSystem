"""八步复盘路由。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date as _date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_provider_registry
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


def _apply_market_ma5w_fallback(conn: sqlite3.Connection, row: dict | None) -> dict | None:
    if not row:
        return row
    if row.get("sh_above_ma5w") is not None and row.get("sz_above_ma5w") is not None:
        return row
    flags = Q.compute_ma5w_flags_from_history(
        conn,
        target_date=str(row.get("date") or ""),
        sh_close=row.get("sh_index_close"),
        sz_close=row.get("sz_index_close"),
    )
    for key, value in flags.items():
        if row.get(key) is None and value is not None:
            row[key] = value
    return row


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


def _to_amount_yi(value: Any) -> float | None:
    parsed = _to_number(value)
    if parsed is None:
        return None
    if abs(parsed) >= 1_000_000:
        return round(parsed / 1e8, 2)
    return round(parsed, 2)


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
            "industry_moneyflow_rows": [],
            "concept_moneyflow_rows": [],
            "projection_candidates": [],
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

        signals["market"]["moneyflow_summary"] = {
            "net_amount_yi": _to_amount_yi(row.get("net_amount")),
            "net_amount_rate": _to_number(row.get("net_amount_rate")),
            "super_large_yi": _to_amount_yi(row.get("buy_elg_amount")),
            "large_yi": _to_amount_yi(row.get("buy_lg_amount")),
        }

    _A_SHARE_CODES = {"SH_A", "SZ_A", "SZ_MAIN_A", "SZ_GEM", "SZ_SME"}
    daily_info_rows = _section_rows(market.get("daily_info"))
    signals["market"]["market_structure_rows"] = [
        {
            "name": _pick_row_name(row, "ts_name", "market", "board", "exchange", "ts_code"),
            "amount": row.get("amount"),
            "volume": row.get("vol"),
            "pe": row.get("pe"),
            "turnover_rate": row.get("tr"),
            "com_count": row.get("com_count"),
        }
        for row in daily_info_rows
        if str(row.get("ts_code", "")).upper() in _A_SHARE_CODES
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
    dc_ind_rows = _section_rows(market.get("sector_moneyflow_dc"))
    ind_rows = ths_rows or dc_ind_rows

    def _sort_by_net_yi(rows):
        def _key(item):
            v = _to_number(item.get("net_amount_yi"))
            if v is not None:
                return -v
            return -(_to_number(item.get("net_amount")) or 0)
        return sorted(rows, key=_key)

    def _clean_lead_stock(row: dict[str, Any]) -> str | None:
        """领涨股涨幅 >20% 的大概率为北交所或新股，不具参考性"""
        pct = _to_number(row.get("pct_change_stock"))
        if pct is not None and abs(pct) > 20:
            return None
        return row.get("lead_stock") or None

    signals["sectors"]["industry_moneyflow_rows"] = [
        {
            "name": _pick_row_name(row, "name", "industry", "ts_code"),
            "net_amount_yi": _to_number(row.get("net_amount_yi")) or _to_amount_yi(row.get("net_amount")),
            "pct_change": _to_number(row.get("pct_change")),
            "lead_stock": _clean_lead_stock(row),
        }
        for row in _sort_by_net_yi(ind_rows)[:5]
    ]

    concept_ths = _section_rows(market.get("concept_moneyflow_ths"))
    concept_dc = _section_rows(market.get("concept_moneyflow_dc"))
    concept_rows = concept_ths or concept_dc
    signals["sectors"]["concept_moneyflow_rows"] = [
        {
            "name": _pick_row_name(row, "name", "industry", "ts_code"),
            "net_amount_yi": _to_number(row.get("net_amount_yi")) or _to_amount_yi(row.get("net_amount")),
            "pct_change": _to_number(row.get("pct_change")),
            "lead_stock": _clean_lead_stock(row),
        }
        for row in _sort_by_net_yi(concept_rows)[:5]
    ]

    ladder_rows = _section_rows(market.get("limit_step"))
    filtered_ladder = [
        row for row in ladder_rows
        if not _stock_name_is_st(_pick_row_name(row, "name", "ts_name", "ts_code"))
    ]
    signals["emotion"]["ladder_rows"] = [
        {
            "name": _pick_row_name(row, "name", "ts_name", "ts_code"),
            "nums": row.get("nums"),
        }
        for row in sorted(filtered_ladder, key=lambda item: -(_to_number(item.get("nums")) or 0))[:10]
    ]
    return signals


def _normalize_sector_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text if text not in {"-", "—", "None"} else ""


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_sector_name(item) for item in value if _normalize_sector_name(item)]
    if not isinstance(value, str):
        return []
    stripped = value.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [_normalize_sector_name(item) for item in parsed if _normalize_sector_name(item)]
    normalized = (
        stripped.replace("；", "，")
        .replace(";", "，")
        .replace("、", "，")
        .replace("/", "，")
        .replace("|", "，")
    )
    return [part.strip() for part in normalized.split("，") if part.strip()]


def _normalize_stock_key(value: Any) -> str:
    text = _normalize_sector_name(value)
    if not text:
        return ""
    return re.sub(r"\s+", "", text).upper()


def _coerce_stock_names(value: Any) -> list[str]:
    return [item for item in _coerce_text_list(value) if item]


def _extract_post_market_source(envelope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    inner = envelope.get("raw_data")
    if isinstance(inner, dict):
        return inner
    return envelope


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return _section_rows(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return _coerce_dict_list(parsed)
    return []


def _safe_float(value: Any) -> float | None:
    parsed = _to_number(value)
    return float(parsed) if parsed is not None else None


def _stock_name_is_st(name: str) -> bool:
    from utils import is_st_stock
    return is_st_stock(name)


def _stock_code_is_bj(code: str) -> bool:
    normalized = str(code or "").strip().upper()
    if not normalized:
        return False
    if normalized.endswith(".BJ"):
        return True
    digits = normalized.split(".", 1)[0]
    return digits.startswith(("43", "82", "83", "87", "88", "89", "92"))


def _pick_sector_leaders(
    *,
    sector_name: str,
    market: dict[str, Any] | None,
    post_market_source: dict[str, Any] | None,
    main_themes: list[dict[str, Any]],
    sector_signals: dict[str, Any] | None,
) -> dict[str, str | None]:
    records: dict[str, dict[str, Any]] = {}
    seen_counter = 0

    def ensure_record(stock_name: str) -> dict[str, Any] | None:
        nonlocal seen_counter
        normalized_name = _normalize_sector_name(stock_name)
        stock_key = _normalize_stock_key(normalized_name)
        if not normalized_name or not stock_key:
            return None
        record = records.get(stock_key)
        if record is None:
            record = {
                "name": normalized_name,
                "key": stock_key,
                "strong_sources": set(),
                "weak_sources": set(),
                "limit_up": None,
                "top_volume_rank": None,
                "top_volume_amount_billion": None,
                "codes": set(),
                "first_seen_order": seen_counter,
            }
            records[stock_key] = record
            seen_counter += 1
        return record

    def add_candidate(stock_name: str, source: str, strength: str) -> None:
        record = ensure_record(stock_name)
        if record is None:
            return
        bucket = "strong_sources" if strength == "strong" else "weak_sources"
        record[bucket].add(source)

    normalized_sector_name = _normalize_sector_name(sector_name)
    if not normalized_sector_name:
        return {"emotion_leader": None, "capacity_leader": None, "lead_stock": None}

    for theme in main_themes:
        if not isinstance(theme, dict):
            continue
        if _normalize_sector_name(theme.get("theme_name")) != normalized_sector_name:
            continue
        for stock_name in _coerce_stock_names(theme.get("key_stocks")):
            add_candidate(stock_name, "main_theme", "strong")

    if market:
        for item in _section_rows(market.get("sector_industry")):
            if _normalize_sector_name(item.get("name") or item.get("sector_name")) != normalized_sector_name:
                continue
            for stock_name in _coerce_stock_names(item.get("top_stock")):
                add_candidate(stock_name, "sector_industry", "strong")

        sector_rhythm = market.get("sector_rhythm_industry")
        if isinstance(sector_rhythm, list):
            for item in sector_rhythm:
                if not isinstance(item, dict):
                    continue
                if _normalize_sector_name(item.get("name") or item.get("sector_name")) != normalized_sector_name:
                    continue
                for stock_name in _coerce_stock_names(item.get("top_stock_today") or item.get("top_stock")):
                    add_candidate(stock_name, "sector_rhythm", "strong")

    strongest_rows = (sector_signals or {}).get("strongest_rows") or []
    for row in strongest_rows:
        if not isinstance(row, dict):
            continue
        if _normalize_sector_name(row.get("name")) != normalized_sector_name:
            continue
        for stock_name in _coerce_stock_names(row.get("lead_stock")):
            add_candidate(stock_name, "strongest", "strong")

    for row in (sector_signals or {}).get("industry_moneyflow_rows") or []:
        if not isinstance(row, dict):
            continue
        if _normalize_sector_name(row.get("name")) != normalized_sector_name:
            continue
        for stock_name in _coerce_stock_names(row.get("lead_stock")):
            add_candidate(stock_name, "industry_moneyflow", "weak")

    for row in (sector_signals or {}).get("concept_moneyflow_rows") or []:
        if not isinstance(row, dict):
            continue
        if _normalize_sector_name(row.get("name")) != normalized_sector_name:
            continue
        for stock_name in _coerce_stock_names(row.get("lead_stock")):
            add_candidate(stock_name, "concept_moneyflow", "weak")

    source = post_market_source or {}
    limit_up = source.get("limit_up") if isinstance(source.get("limit_up"), dict) else {}
    for item in _coerce_dict_list(limit_up.get("stocks")):
        stock_key = _normalize_stock_key(item.get("name"))
        if not stock_key or stock_key not in records:
            continue
        record = records[stock_key]
        record["limit_up"] = item
        code = str(item.get("code") or "").strip().upper()
        if code:
            record["codes"].add(code)

    top_volume_rows = _coerce_dict_list((market or {}).get("top_volume_stocks"))
    if not top_volume_rows:
        top_volume_rows = _coerce_dict_list(source.get("top_volume_stocks"))
    for item in top_volume_rows:
        stock_key = _normalize_stock_key(item.get("name"))
        if not stock_key or stock_key not in records:
            continue
        record = records[stock_key]
        rank = item.get("rank")
        parsed_rank = int(rank) if isinstance(rank, (int, float)) or str(rank).isdigit() else None
        if parsed_rank is not None:
            current_rank = record.get("top_volume_rank")
            if current_rank is None or parsed_rank < current_rank:
                record["top_volume_rank"] = parsed_rank
        amount_billion = _safe_float(item.get("amount_billion"))
        if amount_billion is not None:
            current_amount = record.get("top_volume_amount_billion")
            if current_amount is None or amount_billion > current_amount:
                record["top_volume_amount_billion"] = amount_billion
        code = str(item.get("code") or "").strip().upper()
        if code:
            record["codes"].add(code)

    def is_valid_record(record: dict[str, Any]) -> bool:
        if _stock_name_is_st(record["name"]):
            return False
        return not any(_stock_code_is_bj(code) for code in record["codes"])

    def emotion_score(record: dict[str, Any]) -> float:
        score = 6.0 * len(record["strong_sources"]) + 1.5 * len(record["weak_sources"])
        limit_up_item = record.get("limit_up") or {}
        limit_times = _safe_float(limit_up_item.get("limit_times")) or 0.0
        amount_billion = _safe_float(limit_up_item.get("amount_billion")) or 0.0
        if limit_up_item:
            score += 10.0 + min(limit_times, 5.0) * 2.0 + min(amount_billion / 20.0, 4.0)
        rank = record.get("top_volume_rank")
        if isinstance(rank, int) and rank <= 20:
            score += max(0.0, 3.0 - (rank - 1) * 0.1)
        if len(record["strong_sources"]) + len(record["weak_sources"]) > 1:
            score += 2.0
        return round(score, 2)

    def capacity_score(record: dict[str, Any]) -> float:
        score = 4.0 * len(record["strong_sources"]) + 1.5 * len(record["weak_sources"])
        rank = record.get("top_volume_rank")
        if isinstance(rank, int) and rank <= 20:
            score += max(0.0, 14.0 - (rank - 1) * 0.5)
        amount_billion = record.get("top_volume_amount_billion")
        if amount_billion is not None:
            score += min(float(amount_billion) / 50.0, 4.0)
        limit_up_item = record.get("limit_up") or {}
        limit_up_amount = _safe_float(limit_up_item.get("amount_billion")) or 0.0
        if limit_up_item:
            score += 2.5 + min(limit_up_amount / 25.0, 4.0)
        if len(record["strong_sources"]) + len(record["weak_sources"]) > 1:
            score += 2.0
        return round(score, 2)

    def emotion_gate(record: dict[str, Any]) -> bool:
        return bool(record["strong_sources"]) or (bool(record["weak_sources"]) and bool(record.get("limit_up")))

    def capacity_gate(record: dict[str, Any]) -> bool:
        return bool(record["strong_sources"]) or (
            bool(record["weak_sources"]) and (
                isinstance(record.get("top_volume_rank"), int) or bool(record.get("limit_up"))
            )
        )

    def pick_best(gate) -> str | None:
        ranked: list[tuple[float, int, str]] = []
        for record in records.values():
            if not is_valid_record(record) or not gate(record):
                continue
            score = emotion_score(record) if gate is emotion_gate else capacity_score(record)
            ranked.append((score, int(record["first_seen_order"]), record["name"]))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        top_score, _, top_name = ranked[0]
        return top_name if top_score > 0 else None

    emotion_leader = pick_best(emotion_gate)
    capacity_leader = pick_best(capacity_gate)
    return {
        "emotion_leader": emotion_leader,
        "capacity_leader": capacity_leader,
        "lead_stock": capacity_leader or emotion_leader,
    }


def _build_sector_projection_candidates(
    *,
    market: dict[str, Any] | None,
    post_market_env: dict[str, Any] | None,
    main_themes: list[dict[str, Any]],
    teacher_notes: list[dict[str, Any]],
    industry_info: list[dict[str, Any]],
    sector_signals: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    known_names: set[str] = set()
    if isinstance(main_themes, list):
        for theme in main_themes:
            if isinstance(theme, dict):
                name = _normalize_sector_name(theme.get("theme_name"))
                if name:
                    known_names.add(name)

    strongest_rows = (sector_signals or {}).get("strongest_rows") or []
    industry_moneyflow_rows = (sector_signals or {}).get("industry_moneyflow_rows") or []
    concept_moneyflow_rows = (sector_signals or {}).get("concept_moneyflow_rows") or []
    for rows in (strongest_rows, industry_moneyflow_rows, concept_moneyflow_rows):
        for row in rows:
            if isinstance(row, dict):
                name = _normalize_sector_name(row.get("name"))
                if name:
                    known_names.add(name)

    if market:
        for key in ("sector_industry", "sector_rhythm_industry"):
            section = market.get(key)
            rows = _section_rows(section) if key == "sector_industry" else section
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        name = _normalize_sector_name(row.get("name") or row.get("sector_name"))
                        if name:
                            known_names.add(name)

    for item in industry_info:
        if isinstance(item, dict):
            name = _normalize_sector_name(item.get("sector_name"))
            if name:
                known_names.add(name)
    post_market_source = _extract_post_market_source(post_market_env)

    candidates: dict[str, dict[str, Any]] = {}

    def ensure_candidate(sector_name: str) -> dict[str, Any]:
        name = _normalize_sector_name(sector_name)
        if not name:
            return {}
        if name not in candidates:
            candidates[name] = {
                "sector_name": name,
                "source_tags": [],
                "facts": {
                    "phase_hint": None,
                    "duration_days": None,
                    "pct_chg": None,
                    "limit_up_count": None,
                    "emotion_leader": None,
                    "capacity_leader": None,
                    "lead_stock": None,
                    "net_amount_yi": None,
                    "teacher_note_refs": [],
                },
                "key_stocks": [],
                "evidence_lines": [],
                "score": 0,
            }
        return candidates[name]

    def add_tag(candidate: dict[str, Any], tag: str, score: int) -> None:
        if tag not in candidate["source_tags"]:
            candidate["source_tags"].append(tag)
            candidate["score"] += score

    def add_evidence(candidate: dict[str, Any], text: str) -> None:
        normalized = text.strip()
        if normalized and normalized not in candidate["evidence_lines"]:
            candidate["evidence_lines"].append(normalized)

    def add_key_stocks(candidate: dict[str, Any], stocks: Any) -> None:
        for stock in _coerce_text_list(stocks):
            if stock not in candidate["key_stocks"]:
                candidate["key_stocks"].append(stock)

    for theme in main_themes:
        if not isinstance(theme, dict):
            continue
        sector_name = _normalize_sector_name(theme.get("theme_name"))
        if not sector_name:
            continue
        candidate = ensure_candidate(sector_name)
        add_tag(candidate, "main_theme", 6)
        phase = _normalize_sector_name(theme.get("phase"))
        if phase and not candidate["facts"]["phase_hint"]:
            candidate["facts"]["phase_hint"] = phase
        duration_days = theme.get("duration_days")
        if duration_days is not None:
            candidate["facts"]["duration_days"] = duration_days
        add_key_stocks(candidate, theme.get("key_stocks"))
        add_evidence(
            candidate,
            f"活跃主线，阶段 {phase or '待判断'}，持续 {duration_days or '-'} 天",
        )

    sector_rhythm = market.get("sector_rhythm_industry") if market else None
    if isinstance(sector_rhythm, list):
        for item in sector_rhythm:
            if not isinstance(item, dict):
                continue
            sector_name = _normalize_sector_name(item.get("name"))
            if not sector_name:
                continue
            candidate = ensure_candidate(sector_name)
            add_tag(candidate, "rhythm", 5)
            phase = _normalize_sector_name(item.get("phase"))
            if phase and not candidate["facts"]["phase_hint"]:
                candidate["facts"]["phase_hint"] = phase
            if item.get("change_today") is not None:
                candidate["facts"]["pct_chg"] = item.get("change_today")
            if item.get("cumulative_pct_5d") is not None:
                candidate["facts"]["cumulative_pct_5d"] = item.get("cumulative_pct_5d")
            if item.get("cumulative_pct_10d") is not None:
                candidate["facts"]["cumulative_pct_10d"] = item.get("cumulative_pct_10d")
            add_evidence(
                candidate,
                f"节奏信号 {phase or '待判断'}，当日涨跌幅 {item.get('change_today') if item.get('change_today') is not None else '-'}%",
            )

    for row in strongest_rows:
        if not isinstance(row, dict):
            continue
        sector_name = _normalize_sector_name(row.get("name"))
        if not sector_name:
            continue
        candidate = ensure_candidate(sector_name)
        add_tag(candidate, "strongest", 4)
        if row.get("pct_chg") is not None:
            candidate["facts"]["pct_chg"] = row.get("pct_chg")
        if row.get("up_nums") is not None:
            candidate["facts"]["limit_up_count"] = row.get("up_nums")
        add_evidence(
            candidate,
            f"最强板块榜单，涨停 {row.get('up_nums') or 0} 家，涨跌幅 {row.get('pct_chg') if row.get('pct_chg') is not None else '-'}%",
        )

    for row in industry_moneyflow_rows:
        if not isinstance(row, dict):
            continue
        sector_name = _normalize_sector_name(row.get("name"))
        if not sector_name:
            continue
        candidate = ensure_candidate(sector_name)
        add_tag(candidate, "moneyflow", 3)
        if row.get("net_amount_yi") is not None:
            candidate["facts"]["net_amount_yi"] = row.get("net_amount_yi")
        add_evidence(
            candidate,
            f"行业资金流入 {row.get('net_amount_yi') if row.get('net_amount_yi') is not None else '-'} 亿",
        )

    for row in concept_moneyflow_rows:
        if not isinstance(row, dict):
            continue
        sector_name = _normalize_sector_name(row.get("name"))
        if not sector_name:
            continue
        candidate = ensure_candidate(sector_name)
        add_tag(candidate, "moneyflow", 3)
        if row.get("net_amount_yi") is not None and candidate["facts"]["net_amount_yi"] is None:
            candidate["facts"]["net_amount_yi"] = row.get("net_amount_yi")
        add_evidence(
            candidate,
            f"概念资金流入 {row.get('net_amount_yi') if row.get('net_amount_yi') is not None else '-'} 亿",
        )

    for info in industry_info:
        if not isinstance(info, dict):
            continue
        sector_name = _normalize_sector_name(info.get("sector_name"))
        if not sector_name:
            continue
        candidate = ensure_candidate(sector_name)
        add_tag(candidate, "industry_info", 2)
        add_evidence(candidate, f"行业信息：{str(info.get('content') or '').strip()[:80]}")

    for note in teacher_notes:
        if not isinstance(note, dict):
            continue
        raw_parts = [
            note.get("sectors"),
            note.get("key_points"),
            note.get("core_view"),
            note.get("raw_content"),
        ]
        note_text = "\n".join(str(part) for part in raw_parts if part)
        explicit_names = _coerce_text_list(note.get("sectors"))
        matched_names = [name for name in known_names if name and name in note_text]
        explicit_known_names = [name for name in explicit_names if name in known_names]
        targets = explicit_known_names or matched_names
        for sector_name in targets:
            candidate = ensure_candidate(sector_name)
            if not candidate:
                continue
            add_tag(candidate, "teacher_note", 2)
            ref = {
                "note_id": note.get("id"),
                "teacher_name": note.get("teacher_name"),
                "title": note.get("title"),
            }
            if ref not in candidate["facts"]["teacher_note_refs"]:
                candidate["facts"]["teacher_note_refs"].append(ref)
            add_evidence(
                candidate,
                f"老师观点 {note.get('teacher_name') or '-'}：{str(note.get('key_points') or note.get('core_view') or note.get('title') or '').strip()[:80]}",
            )

    for sector_name, candidate in candidates.items():
        leader_info = _pick_sector_leaders(
            sector_name=sector_name,
            market=market,
            post_market_source=post_market_source,
            main_themes=main_themes,
            sector_signals=sector_signals,
        )
        candidate["facts"]["emotion_leader"] = leader_info["emotion_leader"]
        candidate["facts"]["capacity_leader"] = leader_info["capacity_leader"]
        candidate["facts"]["lead_stock"] = leader_info["lead_stock"]
        if leader_info["emotion_leader"] or leader_info["capacity_leader"]:
            add_evidence(
                candidate,
                (
                    f"自动识别 情绪龙头 {leader_info['emotion_leader'] or '-'}，"
                    f"容量中军 {leader_info['capacity_leader'] or '-'}"
                ),
            )

    rows: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates.values(),
        key=lambda item: (-item["score"], item["sector_name"]),
    ):
        rows.append(
            {
                "sector_name": candidate["sector_name"],
                "source_tags": candidate["source_tags"],
                "facts": candidate["facts"],
                "key_stocks": candidate["key_stocks"],
                "evidence_text": "；".join(candidate["evidence_lines"][:4]),
            }
        )
    return rows


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
    market = _apply_market_ma5w_fallback(conn, market)
    enrich_daily_market_row(market)  # 展开 raw_data 中的扩展字段（style_factors/sector_*/rhythm_* 等）
    prev_market = Q.get_prev_daily_market(conn, date)
    prev_market = _apply_market_ma5w_fallback(conn, prev_market)
    avg_5d = Q.get_avg_amount(conn, date, 5)
    avg_20d = Q.get_avg_amount(conn, date, 20)
    emotion = Q.get_latest_emotion(conn)
    themes = Q.get_active_themes(conn)
    holdings = _enrich_holdings_prefill_from_post_envelope(
        Q.get_holdings(conn, status="active"),
        holdings_quote_map,
    )
    calendar = Q.get_calendar_range(conn, date, date)

    next_td = Q.get_next_trade_date(conn, date)
    if next_td:
        note_end = next_td
    else:
        note_end = (_date.fromisoformat(date) + timedelta(days=3)).isoformat()
    notes = conn.execute(
        "SELECT n.*, t.name as teacher_name FROM teacher_notes n "
        "JOIN teachers t ON n.teacher_id = t.id "
        "WHERE n.date >= ? AND n.date < ? ORDER BY n.created_at DESC",
        (date, note_end),
    ).fetchall()
    prev_review = Q.get_prev_daily_review(conn, date)

    # 近 N 天行业信息/行业笔记（来自 industry_info 表）
    industry_info = Q.get_recent_industry_info(
        conn,
        date_from=_industry_info_date_from(date),
        date_to=date,
    )
    review_signals = _build_review_signals(market)

    post_src = _extract_post_market_source(env)
    rct = post_src.get("research_coverage_top")
    review_signals["market"]["research_coverage_top"] = rct if isinstance(rct, list) else []

    review_signals["sectors"]["projection_candidates"] = _build_sector_projection_candidates(
        market=market,
        post_market_env=env,
        main_themes=themes,
        teacher_notes=[dict(n) for n in notes],
        industry_info=industry_info,
        sector_signals=review_signals.get("sectors"),
    )
    holding_signals = build_holding_signals(conn, date, market_row=market_for_signals)

    from utils.trade_date import is_trade_day as _is_trade_day
    is_trading_day_calendar = _is_trade_day(date, conn=conn)
    is_trading_day = is_trading_day_calendar
    if is_trading_day and market is None and _date.fromisoformat(date) < _date.today():
        is_trading_day = False

    prev_trade_date = Q.get_prev_trade_date_from_db(conn, date) if not is_trading_day_calendar else None

    return {
        "date": date,
        "is_trading_day": is_trading_day,
        "prev_trade_date": prev_trade_date,
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


@router.post("/{date}/to-draft")
def review_to_draft(date: str, body: Optional[dict] = None, registry=Depends(get_provider_registry)):
    from services.planning_service import PlanningService

    date = _validate_date(date)
    payload = body or {}
    trade_date = payload.get("trade_date")
    if trade_date:
        trade_date = _validate_date(trade_date)
    service = PlanningService(registry=registry)
    try:
        return service.draft_from_review(
            review_date=date,
            trade_date=trade_date,
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "review not found")


@router.put("/{date}")
def save_review(date: str, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    Q.upsert_daily_review(conn, date, body)
    if "step7_positions" in body:
        tasks = _extract_holding_tasks_from_step7(body.get("step7_positions"))
        Q.replace_holding_tasks(conn, trade_date=date, tasks=tasks, source="review_step7")
    conn.commit()
    return {"ok": True, "date": date}
