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
from services.review_leaders import sync_leader_tracking_from_step5
from services.trinity_factor.review_input import (
    normalize_review_payload_for_display,
    normalize_review_steps,
    validate_trade_date,
)
from services.trinity_factor.service import build_score_input_digest

router = APIRouter(prefix="/api/review", tags=["review"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOCK_LABEL_RE = re.compile(r"^(?P<name>.*?)(?:\((?P<code>[0-9]{6}(?:\.[A-Z]{2})?)\))?$", re.I)

# prefill 时回溯的行业信息天数
_INDUSTRY_INFO_LOOKBACK_DAYS = 7

# ──────────────────────────────────────────────────────────────
# 8 步 → trading_cognitions.category 映射
# 用于 prefill 中按步骤分组展示相关认知。修改映射时需与
# `.agents/skills/daily-review/references/eight-step-prompt-templates.md`
# 保持一致。此常量为 review 工作台专属；`config/cognition_taxonomy.yaml`
# 中的 `plan_mappings` 留给未来计划工作台消费，二者互不影响。
# ──────────────────────────────────────────────────────────────
_STEP_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "step1_market": ("structure", "macro", "cycle"),
    "step2_sectors": ("structure", "signal"),
    "step3_emotion": ("sentiment",),
    "step4_style": ("structure", "signal"),
    "step5_leaders": ("execution",),
    "step6_nodes": ("cycle", "position"),
    "step7_positions": ("sizing", "position", "execution", "fundamental"),
    "step8_plan": ("execution", "synthesis", "valuation"),
}

# prefill 响应中仅返回的白名单字段（裁剪掉 description / 时间戳等大字段）
_COGNITION_FIELD_WHITELIST: tuple[str, ...] = (
    "cognition_id",
    "title",
    "category",
    "sub_category",
    "evidence_level",
    "confidence",
    "instance_count",
    "validated_count",
    "invalidated_count",
    "pattern",
    "conflict_group",
    "tags",
)

# 白名单内需做 JSON 反序列化的字段（与 cognition.py `_parse_json_fields` 对齐）
_COGNITION_JSON_FIELDS: tuple[str, ...] = ("tags",)

# 每步展示上限（与 plan 「8 步 → category」章节一致）
_COGNITION_PER_STEP_LIMIT = 5


def _validate_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(422, f"Invalid date format: {date}")
    try:
        return validate_trade_date(date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ──────────────────────────────────────────────────────────────
# 认知 prefill：按 8 步 → category 聚合 active 认知
# ──────────────────────────────────────────────────────────────
def _fetch_cognitions_multi_category(
    conn: sqlite3.Connection,
    categories: tuple[str, ...],
) -> list[sqlite3.Row]:
    """单条 SQL 聚合查询多 category 的 active 认知，避免 N+1 连接。

    `CognitionService.list_cognitions` 只支持单 category 等值过滤，这里在
    review.py 内部直查 `trading_cognitions`，复用 `get_db_conn` 依赖提供的
    测试友好连接。
    """
    if not categories:
        return []
    placeholders = ",".join("?" * len(categories))
    sql = (
        "SELECT * FROM trading_cognitions "
        f"WHERE category IN ({placeholders}) AND status = 'active'"
    )
    return conn.execute(sql, tuple(categories)).fetchall()


def _parse_cognition_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把 `tags` 等 JSON 字段从 TEXT 反序列化为 Python 对象。

    复用 `scripts/api/routes/cognition.py::_parse_json_fields` 的解析语义：
    None 直接透传；已是 list/dict 的不动；字符串 `json.loads` 失败则置 None。
    """
    out = dict(row)
    for field in _COGNITION_JSON_FIELDS:
        v = out.get(field)
        if v is None or isinstance(v, (list, dict)):
            continue
        if isinstance(v, str):
            try:
                out[field] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                out[field] = None
    return out


def _project_cognition_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in _COGNITION_FIELD_WHITELIST}


def _sort_cognitions_for_prefill(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 `confidence DESC, instance_count DESC, updated_at DESC, cognition_id ASC`
    排序。利用 Python sorted 的稳定性，分 4 轮（从最低优先级到最高优先级）。
    """
    def _conf_key(r: dict[str, Any]) -> float:
        v = r.get("confidence")
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _count_key(r: dict[str, Any]) -> int:
        v = r.get("instance_count")
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    ordered = sorted(rows, key=lambda r: str(r.get("cognition_id") or ""))
    ordered = sorted(ordered, key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    ordered = sorted(ordered, key=_count_key, reverse=True)
    ordered = sorted(ordered, key=_conf_key, reverse=True)
    return ordered


def _build_cognitions_by_step(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """为 prefill 构造 `{step_key: [cognition_summary, ...]}`。

    流程：查询 → JSON 解析 → 二次排序 → top 5 截断 → 字段白名单裁剪。
    空 category 或无结果时返回 `[]`，保证前端无需处理 null/缺 key。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for step_key, categories in _STEP_CATEGORY_MAP.items():
        rows = _fetch_cognitions_multi_category(conn, categories)
        parsed = [_parse_cognition_json_fields(dict(r)) for r in rows]
        ordered = _sort_cognitions_for_prefill(parsed)
        top = ordered[:_COGNITION_PER_STEP_LIMIT]
        out[step_key] = [_project_cognition_fields(r) for r in top]
    return out


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


def _select_sector_moneyflow_rows(
    market: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按行业/概念复用相同的资金流源降级顺序。"""
    source = market or {}
    dc_concept_rows: list[dict[str, Any]] = []
    dc_industry_rows: list[dict[str, Any]] = []
    for row in _section_rows(source.get("sector_moneyflow_dc")):
        content_type = _normalize_sector_name(row.get("content_type"))
        if "概念" in content_type or content_type.lower() == "concept":
            dc_concept_rows.append(row)
        else:
            dc_industry_rows.append(row)

    industry_rows = (
        _section_rows(source.get("sector_moneyflow_ths")) or dc_industry_rows
    )
    concept_rows = (
        _section_rows(source.get("concept_moneyflow_ths"))
        or _section_rows(source.get("concept_moneyflow_dc"))
        or dc_concept_rows
    )
    return industry_rows, concept_rows


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
            "data_status": _sector_projection_source_state(market),
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

    ind_rows, concept_rows = _select_sector_moneyflow_rows(market)

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
    sector_type: str,
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
        market_section = "sector_industry" if sector_type == "industry" else "sector_concept"
        rhythm_section = (
            "sector_rhythm_industry" if sector_type == "industry" else "sector_rhythm_concept"
        )
        for item in _section_rows(market.get(market_section)):
            if _normalize_sector_name(item.get("name") or item.get("sector_name")) != normalized_sector_name:
                continue
            for stock_name in _coerce_stock_names(item.get("top_stock")):
                add_candidate(stock_name, market_section, "strong")

        sector_rhythm = market.get(rhythm_section)
        if isinstance(sector_rhythm, list):
            for item in sector_rhythm:
                if not isinstance(item, dict):
                    continue
                if _normalize_sector_name(item.get("name") or item.get("sector_name")) != normalized_sector_name:
                    continue
                for stock_name in _coerce_stock_names(item.get("top_stock_today") or item.get("top_stock")):
                    add_candidate(stock_name, rhythm_section, "strong")

    if sector_type == "concept":
        strongest_rows = (sector_signals or {}).get("strongest_rows") or []
        for row in strongest_rows:
            if not isinstance(row, dict):
                continue
            if _normalize_sector_name(row.get("name")) != normalized_sector_name:
                continue
            for stock_name in _coerce_stock_names(row.get("lead_stock")):
                add_candidate(stock_name, "strongest", "strong")

    moneyflow_key = (
        "industry_moneyflow_rows" if sector_type == "industry" else "concept_moneyflow_rows"
    )
    for row in (sector_signals or {}).get(moneyflow_key) or []:
        if not isinstance(row, dict):
            continue
        if _normalize_sector_name(row.get("name")) != normalized_sector_name:
            continue
        net_amount = _safe_float(row.get("net_amount_yi"))
        if net_amount is None or net_amount <= 0:
            continue
        for stock_name in _coerce_stock_names(row.get("lead_stock")):
            add_candidate(stock_name, moneyflow_key, "weak")

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
            bool(record["weak_sources"]) and isinstance(record.get("top_volume_rank"), int)
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


def _sector_projection_source_state(market: dict[str, Any] | None) -> str:
    source_keys = (
        "sector_industry",
        "sector_concept",
        "sector_rhythm_industry",
        "sector_rhythm_concept",
        "sector_moneyflow_ths",
        "sector_moneyflow_dc",
        "concept_moneyflow_ths",
        "concept_moneyflow_dc",
        "limit_cpt_list",
    )
    if not market:
        return "missing"
    sections = [market[key] for key in source_keys if key in market]
    if not sections:
        return "missing"

    statuses = {
        str(section.get("status") or "").strip().lower()
        for section in sections
        if isinstance(section, dict) and section.get("status")
    }

    def failed(section: Any) -> bool:
        if not isinstance(section, dict):
            return False
        status = str(section.get("status") or "").strip().lower()
        return status in {"source_failed", "failed", "error"} or bool(section.get("error"))

    if any(failed(section) for section in sections):
        return "source_failed"
    if any(_section_rows(section) for section in sections):
        return "ok"
    if "missing" in statuses:
        return "missing"
    if "rule_filtered_empty" in statuses:
        return "rule_filtered_empty"
    return "source_ok_empty"


def _build_sector_projection_candidates(
    *,
    trade_date: str,
    market: dict[str, Any] | None,
    post_market_env: dict[str, Any] | None,
    main_themes: list[dict[str, Any]],
    teacher_notes: list[dict[str, Any]],
    industry_info: list[dict[str, Any]],
    sector_signals: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    strongest_rows = (sector_signals or {}).get("strongest_rows") or []
    industry_moneyflow_rows = (sector_signals or {}).get("industry_moneyflow_rows") or []
    concept_moneyflow_rows = (sector_signals or {}).get("concept_moneyflow_rows") or []
    post_market_source = _extract_post_market_source(post_market_env)

    market_source_state = _sector_projection_source_state(market)

    candidates: dict[str, dict[str, Any]] = {}

    def ensure_candidate(sector_type: str, sector_name: str) -> dict[str, Any]:
        name = _normalize_sector_name(sector_name)
        if sector_type not in {"industry", "concept"} or not name:
            return {}
        sector_key = f"{sector_type}:{name}"
        if sector_key not in candidates:
            candidates[sector_key] = {
                "sector_key": sector_key,
                "sector_name": name,
                "sector_type": sector_type,
                "source_tags": [],
                "facts": {
                    "phase_hint": None,
                    "rhythm_confidence": None,
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
                "evidence_items": [],
                "support_categories": set(),
                "has_counter_evidence": False,
                "has_filtered_market_evidence": False,
                "has_main_theme": False,
                "rhythm_confidence_rank": None,
                "strongest_qualified": False,
                "strongest_rank": None,
            }
        return candidates[sector_key]

    def add_tag(candidate: dict[str, Any], tag: str) -> None:
        if tag not in candidate["source_tags"]:
            candidate["source_tags"].append(tag)

    def add_evidence(
        candidate: dict[str, Any],
        *,
        source: str,
        category: str,
        polarity: str,
        objective: bool,
        text: str,
    ) -> None:
        normalized = text.strip()
        if not normalized:
            return
        signature = (source, category, polarity, normalized)
        if any(
            (
                item["source"],
                item["category"],
                item["polarity"],
                item["text"],
            ) == signature
            for item in candidate["evidence_items"]
        ):
            return
        ordinal = len(candidate["evidence_items"]) + 1
        candidate["evidence_items"].append(
            {
                "evidence_id": (
                    f"{trade_date}:{candidate['sector_key']}:{source}:{ordinal}"
                ),
                "trade_date": trade_date,
                "source": source,
                "category": category,
                "polarity": polarity,
                "objective": objective,
                "text": normalized,
            }
        )
        if normalized not in candidate["evidence_lines"]:
            candidate["evidence_lines"].append(normalized)
        if not objective:
            return
        if polarity == "support":
            candidate["support_categories"].add(category)
        elif polarity == "counter":
            candidate["has_counter_evidence"] = True
        else:
            candidate["has_filtered_market_evidence"] = True

    def add_key_stocks(candidate: dict[str, Any], stocks: Any) -> None:
        for stock in _coerce_text_list(stocks):
            if stock not in candidate["key_stocks"]:
                candidate["key_stocks"].append(stock)

    for sector_type, section_key in (
        ("industry", "sector_industry"),
        ("concept", "sector_concept"),
    ):
        rows = _section_rows((market or {}).get(section_key))
        for item in sorted(rows, key=lambda row: _normalize_sector_name(row.get("name") or row.get("sector_name"))):
            sector_name = _normalize_sector_name(item.get("name") or item.get("sector_name"))
            if not sector_name:
                continue
            candidate = ensure_candidate(sector_type, sector_name)
            add_tag(candidate, "market_performance")
            pct_chg = _safe_float(
                item.get("change_pct")
                if item.get("change_pct") is not None
                else item.get("change_today")
            )
            if pct_chg is not None:
                candidate["facts"]["pct_chg"] = pct_chg
            add_key_stocks(candidate, item.get("top_stock"))
            polarity = "support" if pct_chg is not None and pct_chg > 0 else (
                "counter" if pct_chg is not None and pct_chg < 0 else "neutral"
            )
            add_evidence(
                candidate,
                source=section_key,
                category="market_performance",
                polarity=polarity,
                objective=True,
                text=f"当日涨跌幅 {pct_chg if pct_chg is not None else '-'}%",
            )

    for sector_type, section_key in (
        ("industry", "sector_rhythm_industry"),
        ("concept", "sector_rhythm_concept"),
    ):
        rhythm_rows = (market or {}).get(section_key)
        if not isinstance(rhythm_rows, list):
            continue
        for item in sorted(
            (row for row in rhythm_rows if isinstance(row, dict)),
            key=lambda row: _normalize_sector_name(row.get("name") or row.get("sector_name")),
        ):
            sector_name = _normalize_sector_name(item.get("name") or item.get("sector_name"))
            if not sector_name:
                continue
            candidate = ensure_candidate(sector_type, sector_name)
            add_tag(candidate, "rhythm")
            phase = _normalize_sector_name(item.get("phase"))
            confidence = _normalize_sector_name(item.get("confidence"))
            confidence_key = confidence.lower()
            confidence_rank = (
                0 if confidence_key in {"高", "high"}
                else 1 if confidence_key in {"中", "medium"}
                else 2
            )
            candidate["rhythm_confidence_rank"] = confidence_rank
            candidate["facts"]["rhythm_confidence"] = confidence or None
            if phase and not candidate["facts"]["phase_hint"]:
                candidate["facts"]["phase_hint"] = phase
            if item.get("change_today") is not None:
                candidate["facts"]["pct_chg"] = item.get("change_today")
            for field in ("cumulative_pct_5d", "cumulative_pct_10d"):
                if item.get(field) is not None:
                    candidate["facts"][field] = item.get(field)
            add_evidence(
                candidate,
                source=section_key,
                category="rhythm",
                polarity="support",
                objective=True,
                text=(
                    f"节奏信号 {phase or '待判断'}，置信度 {confidence or '-'}，"
                    f"当日涨跌幅 {item.get('change_today') if item.get('change_today') is not None else '-'}%"
                ),
            )

    for row in strongest_rows:
        if not isinstance(row, dict):
            continue
        sector_name = _normalize_sector_name(row.get("name"))
        if not sector_name:
            continue
        candidate = ensure_candidate("concept", sector_name)
        add_tag(candidate, "strongest")
        rank = _safe_float(row.get("rank"))
        pct_chg = _safe_float(row.get("pct_chg"))
        breadth = _safe_float(row.get("up_nums"))
        qualified = bool(
            rank is not None
            and rank.is_integer()
            and 1 <= rank <= 3
            and pct_chg is not None
            and pct_chg > 0
            and breadth is not None
            and breadth > 0
        )
        candidate["strongest_qualified"] = qualified
        candidate["strongest_rank"] = rank
        if row.get("pct_chg") is not None:
            candidate["facts"]["pct_chg"] = row.get("pct_chg")
        if row.get("up_nums") is not None:
            candidate["facts"]["limit_up_count"] = row.get("up_nums")
        add_evidence(
            candidate,
            source="limit_cpt_list",
            category="strongest",
            polarity="support" if qualified else "neutral",
            objective=True,
            text=(
                f"最强板块榜第 {int(rank) if rank is not None else '-'} 名，"
                f"涨停 {row.get('up_nums') or 0} 家，"
                f"涨跌幅 {row.get('pct_chg') if row.get('pct_chg') is not None else '-'}%"
            ),
        )

    for sector_type, source, rows in (
        ("industry", "industry_moneyflow", industry_moneyflow_rows),
        ("concept", "concept_moneyflow", concept_moneyflow_rows),
    ):
        for row in rows:
            if not isinstance(row, dict):
                continue
            sector_name = _normalize_sector_name(row.get("name"))
            if not sector_name:
                continue
            candidate = ensure_candidate(sector_type, sector_name)
            add_tag(candidate, "moneyflow")
            net_amount = _safe_float(row.get("net_amount_yi"))
            if net_amount is not None:
                candidate["facts"]["net_amount_yi"] = net_amount
            polarity = "support" if net_amount is not None and net_amount > 0 else (
                "counter" if net_amount is not None and net_amount < 0 else "neutral"
            )
            flow_label = "净流入" if polarity == "support" else (
                "净流出" if polarity == "counter" else "净额中性"
            )
            add_evidence(
                candidate,
                source=source,
                category="moneyflow",
                polarity=polarity,
                objective=True,
                text=f"{sector_type} 资金{flow_label} {abs(net_amount) if net_amount is not None else '-'} 亿",
            )

    # 行业资料先建立 industry 身份，后续无类型的主线名只挂到已知同名身份；
    # 仅在没有任何客观/资料类型线索时，才把主线作为 concept context 兜底。
    for info in industry_info:
        if not isinstance(info, dict):
            continue
        sector_name = _normalize_sector_name(info.get("sector_name"))
        if not sector_name:
            continue
        candidate = ensure_candidate("industry", sector_name)
        add_tag(candidate, "industry_info")
        add_evidence(
            candidate,
            source="industry_info",
            category="narrative",
            polarity="context",
            objective=False,
            text=f"行业信息：{str(info.get('content') or '').strip()[:80]}",
        )

    for theme in main_themes:
        if not isinstance(theme, dict):
            continue
        sector_name = _normalize_sector_name(theme.get("theme_name"))
        if not sector_name:
            continue
        matched = [
            candidate
            for candidate in candidates.values()
            if candidate["sector_name"] == sector_name
        ]
        if not matched:
            matched = [ensure_candidate("concept", sector_name)]
        has_unambiguous_type = len({candidate["sector_type"] for candidate in matched}) == 1
        for candidate in matched:
            # main_themes 无 sector_type；同名行业/概念并存时只作 context，
            # 不能同时将两个候选通过「主线+市场确认」硬门。
            candidate["has_main_theme"] = has_unambiguous_type
            add_tag(candidate, "main_theme")
            phase = _normalize_sector_name(theme.get("phase"))
            if phase and not candidate["facts"]["phase_hint"]:
                candidate["facts"]["phase_hint"] = phase
            duration_days = theme.get("duration_days")
            if duration_days is not None:
                candidate["facts"]["duration_days"] = duration_days
            add_key_stocks(candidate, theme.get("key_stocks"))
            add_evidence(
                candidate,
                source="main_theme",
                category="narrative",
                polarity="context",
                objective=False,
                text=f"活跃主线，阶段 {phase or '待判断'}，持续 {duration_days or '-'} 天",
            )

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
        known_names = {candidate["sector_name"] for candidate in candidates.values()}
        matched_names = [name for name in known_names if name and name in note_text]
        explicit_known_names = [name for name in explicit_names if name in known_names]
        targets = explicit_known_names or matched_names
        for sector_name in targets:
            for candidate in candidates.values():
                if candidate["sector_name"] != sector_name:
                    continue
                add_tag(candidate, "teacher_note")
                ref = {
                    "note_id": note.get("id"),
                    "teacher_name": note.get("teacher_name"),
                    "title": note.get("title"),
                }
                if ref not in candidate["facts"]["teacher_note_refs"]:
                    candidate["facts"]["teacher_note_refs"].append(ref)
                add_evidence(
                    candidate,
                    source="teacher_note",
                    category="narrative",
                    polarity="context",
                    objective=False,
                    text=(
                        f"老师观点 {note.get('teacher_name') or '-'}："
                        f"{str(note.get('key_points') or note.get('core_view') or note.get('title') or '').strip()[:80]}"
                    ),
                )

    # 展示层保留 Top5；仅对已由其它证据/上下文建立的候选，从完整原始源
    # 回查负资金流反证。正流入与未命中候选的行都不能借此创建支持候选。
    full_industry_rows, full_concept_rows = _select_sector_moneyflow_rows(market)
    for sector_type, source, rows in (
        ("industry", "industry_moneyflow", full_industry_rows),
        ("concept", "concept_moneyflow", full_concept_rows),
    ):
        for row in rows:
            sector_name = _normalize_sector_name(
                _pick_row_name(row, "name", "industry", "ts_code")
            )
            candidate = candidates.get(f"{sector_type}:{sector_name}")
            if not candidate:
                continue
            net_amount = _safe_float(row.get("net_amount_yi"))
            if net_amount is None:
                net_amount = _to_amount_yi(row.get("net_amount"))
            if net_amount is None or net_amount >= 0:
                continue
            add_tag(candidate, "moneyflow")
            candidate["facts"]["net_amount_yi"] = net_amount
            add_evidence(
                candidate,
                source=source,
                category="moneyflow",
                polarity="counter",
                objective=True,
                text=f"{sector_type} 资金净流出 {abs(net_amount)} 亿",
            )

    for candidate in candidates.values():
        same_name_count = sum(
            item["sector_name"] == candidate["sector_name"]
            for item in candidates.values()
        )
        leader_info = _pick_sector_leaders(
            sector_name=candidate["sector_name"],
            sector_type=candidate["sector_type"],
            market=market,
            post_market_source=post_market_source,
            # main_themes 没有 sector_type；同名行业/概念并存时不用其
            # key_stocks 做 leader 证据，避免一条无类型叙事串污两个候选。
            main_themes=main_themes if same_name_count == 1 else [],
            sector_signals=sector_signals,
        )
        candidate["facts"]["emotion_leader"] = leader_info["emotion_leader"]
        candidate["facts"]["capacity_leader"] = leader_info["capacity_leader"]
        candidate["facts"]["lead_stock"] = leader_info["lead_stock"]
        if leader_info["emotion_leader"] or leader_info["capacity_leader"]:
            add_evidence(
                candidate,
                source="leader_detection",
                category="leader",
                polarity="context",
                objective=False,
                text=(
                    f"自动识别 情绪龙头 {leader_info['emotion_leader'] or '-'}，"
                    f"容量中军 {leader_info['capacity_leader'] or '-'}"
                ),
            )

    for candidate in candidates.values():
        support_count = len(candidate["support_categories"])
        confidence_rank = candidate["rhythm_confidence_rank"]
        if confidence_rank == 0:
            candidate["candidate_tier"] = "core"
            candidate["rank_reason"] = "高置信节奏"
            candidate["rule_rank"] = 0
        elif confidence_rank == 1:
            candidate["candidate_tier"] = "core"
            candidate["rank_reason"] = "中置信节奏"
            candidate["rule_rank"] = 1
        elif candidate["strongest_qualified"]:
            candidate["candidate_tier"] = "core"
            candidate["rank_reason"] = "最强榜前三且涨幅与联动广度为正"
            candidate["rule_rank"] = 2
        elif candidate["has_main_theme"] and support_count >= 1:
            candidate["candidate_tier"] = "core"
            candidate["rank_reason"] = "活跃主线获当日市场确认"
            candidate["rule_rank"] = 3
        elif support_count >= 2:
            candidate["candidate_tier"] = "core"
            candidate["rank_reason"] = "至少2类独立客观市场证据"
            candidate["rule_rank"] = 4
        elif support_count == 1:
            candidate["candidate_tier"] = "watch"
            candidate["rank_reason"] = "仅1类当日客观市场证据"
            candidate["rule_rank"] = 5
        else:
            candidate["candidate_tier"] = "context"
            if candidate["has_counter_evidence"]:
                candidate["rank_reason"] = "仅反证，未通过客观硬门"
            elif candidate["has_filtered_market_evidence"]:
                candidate["rank_reason"] = "客观数据未通过硬门"
            else:
                candidate["rank_reason"] = "仅老师观点/行业资料/逻辑叙事"
            candidate["rule_rank"] = 6

        if support_count or candidate["has_counter_evidence"]:
            candidate["data_status"] = "ok"
        elif candidate["has_filtered_market_evidence"]:
            candidate["data_status"] = "rule_filtered_empty"
        elif market_source_state in {
            "missing",
            "source_ok_empty",
            "source_failed",
            "rule_filtered_empty",
        }:
            candidate["data_status"] = market_source_state
        else:
            candidate["data_status"] = "missing"

    tier_order = {"core": 0, "watch": 1, "context": 2}
    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            tier_order[item["candidate_tier"]],
            item["rule_rank"],
            -len(item["support_categories"]),
            item["strongest_rank"] if item["strongest_rank"] is not None else 9999,
            item["sector_key"],
        ),
    )

    rows: list[dict[str, Any]] = []
    for candidate in ranked:
        rows.append(
            {
                "sector_key": candidate["sector_key"],
                "sector_name": candidate["sector_name"],
                "sector_type": candidate["sector_type"],
                "candidate_tier": candidate["candidate_tier"],
                "data_status": candidate["data_status"],
                "rank_reason": candidate["rank_reason"],
                "source_tags": candidate["source_tags"],
                "facts": candidate["facts"],
                "key_stocks": candidate["key_stocks"],
                "evidence_items": candidate["evidence_items"],
                "evidence_text": "；".join(candidate["evidence_lines"][:4]),
            }
        )
    return rows


def _take_core_projection_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """取确定性排序后的 core 候选，供后续评分层直接消费。"""
    return [
        candidate
        for candidate in candidates
        if candidate.get("candidate_tier") == "core"
    ][:6]


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
    return build_review_prefill(conn, date)


def build_review_prefill(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """构造复盘预填载荷；调用方负责先校验日期，函数本身不依赖 FastAPI。"""
    market = Q.get_daily_market(conn, date)
    market_for_signals = dict(market) if market else None
    env = parse_post_market_envelope(market.get("raw_data") if market else None)
    holdings_quote_map = holdings_quote_details_from_envelope(env)
    market = _apply_market_ma5w_fallback(conn, market)
    enrich_daily_market_row(market)  # 展开 raw_data 中的扩展字段（style_factors/sector_*/rhythm_* 等）
    prev_market = Q.get_prev_daily_market(conn, date)
    prev_market = _apply_market_ma5w_fallback(conn, prev_market)
    # 与当日 market 一致地展开并移除 raw_data，避免前一日历史信封里的旧 northbound 块
    # （口径存疑，北向净额已下线）经 prefill 的 prev_market.raw_data 原样外泄。
    enrich_daily_market_row(prev_market)
    avg_5d = Q.get_avg_amount(conn, date, 5)
    avg_20d = Q.get_avg_amount(conn, date, 20)
    emotion = Q.get_latest_emotion(conn)
    themes = Q.get_active_themes_as_of(conn, date)
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
    rci = post_src.get("research_coverage_industry")
    review_signals["market"]["research_coverage_industry"] = rci if isinstance(rci, list) else []

    review_signals["sectors"]["projection_candidates"] = _build_sector_projection_candidates(
        trade_date=date,
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

    # ── step5_leaders 预填：从候选板块汇总最票 ──
    step5_prefill_leaders: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for cand in review_signals["sectors"]["projection_candidates"]:
        sector_name = cand.get("sector_name", "")
        facts = cand.get("facts") or {}
        for role, attr_type in [
            ("emotion_leader", "走势引领"),
            ("capacity_leader", "容量最大"),
        ]:
            stock = facts.get(role)
            if not stock:
                continue
            pair = (stock, sector_name)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            step5_prefill_leaders.append({
                "stock": stock,
                "sector": sector_name,
                "attribute_type": attr_type,
                "attribute": "",
                "clarity": "",
                "position": "",
                "is_new": False,
                "is_prefilled": True,
            })

    cognitions_by_step = _build_cognitions_by_step(conn)

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
        "step5_leaders": {"top_leaders": step5_prefill_leaders} if step5_prefill_leaders else None,
        "cognitions_by_step": cognitions_by_step,
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
    body = normalize_review_payload_for_display(body)
    if conn.in_transaction:
        raise HTTPException(409, "review save requires a clean transaction boundary")
    conn.execute("BEGIN IMMEDIATE")
    current_source = dict(Q.get_daily_review(conn, date) or {})
    current_source.update({
        key: body[key]
        for key in (
            "step1_market", "step2_sectors", "step3_emotion",
            "step4_style", "step5_leaders", "step6_nodes",
        )
        if key in body
    })
    current_input_digest = build_score_input_digest(
        trade_date=date,
        prefill=build_review_prefill(conn, date),
        review_steps=normalize_review_steps(current_source),
        strict_prev_trade_date=Q.get_prev_trade_date_from_db(conn, date),
    )
    step8 = body.get("step8_plan")
    if isinstance(step8, str):
        try:
            parsed_step8 = json.loads(step8)
        except json.JSONDecodeError:
            parsed_step8 = None
        if isinstance(parsed_step8, dict):
            step8 = parsed_step8
    if isinstance(step8, dict) and isinstance(step8.get("factor_decision"), dict):
        from services.trinity_factor.cycle import normalize_step8_factor_decision
        try:
            body["step8_plan"] = normalize_step8_factor_decision(
                conn,
                trade_date=date,
                step8=step8,
                current_input_digest=current_input_digest,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif "step8_plan" not in body:
        from services.trinity_factor.cycle import invalidate_stale_factor_decision

        cleared_step8, invalidated = invalidate_stale_factor_decision(
            conn,
            trade_date=date,
            step8=current_source.get("step8_plan"),
            current_input_digest=current_input_digest,
        )
        if invalidated:
            body["step8_plan"] = cleared_step8
    Q.upsert_daily_review(conn, date, body)
    if "step7_positions" in body:
        tasks = _extract_holding_tasks_from_step7(body.get("step7_positions"))
        Q.replace_holding_tasks(conn, trade_date=date, tasks=tasks, source="review_step7")
    sync_leader_tracking_from_step5(conn, date, body.get("step5_leaders"))
    conn.commit()
    return {"ok": True, "date": date}


def _sync_leader_tracking(
    conn: sqlite3.Connection,
    review_date: str,
    step5: Any,
) -> None:
    """Compatibility wrapper for tests/imports; real logic lives in services.review_leaders."""
    sync_leader_tracking_from_step5(conn, review_date, step5)
