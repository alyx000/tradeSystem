from __future__ import annotations

import json
import math
import re
from typing import Any

from services.daily_leaders.models import (
    CLARITY_MEDIUM,
    TEACHER_CONFLICT,
    TEACHER_SUPPORT,
    TEACHER_UNMENTIONED,
)
from services.daily_leaders.selection import normalize_stock_display
from utils import is_st_stock
from utils.price_limit import limit_pct_for


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _rows_from_section(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_num(value: float | None) -> str | None:
    if value is None:
        return None
    if float(value).is_integer():
        return f"{value:.1f}"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalize_key(value: Any) -> str:
    return "".join(_text(value).split()).upper()


_CANONICAL_A_SHARE_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)


def _canonical_stock_code(value: Any) -> str:
    """Return a bare canonical A-share code, rejecting names and malformed values."""
    normalized = _normalize_key(value)
    match = _CANONICAL_A_SHARE_CODE_RE.fullmatch(normalized)
    return match.group(1) if match else ""


def _candidate_codes(item: dict[str, Any]) -> set[str]:
    codes = {
        code
        for code in (
            _canonical_stock_code(item.get("stock_code")),
            _canonical_stock_code(item.get("code")),
        )
        if code
    }
    stock_tokens = normalize_stock_display(item.get("stock")).split()
    if stock_tokens:
        stock_code = _canonical_stock_code(stock_tokens[0])
        if stock_code:
            codes.add(stock_code)
    return codes


def _candidate_name_keys(item: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for value in (item.get("stock_name"), item.get("name"), item.get("stock")):
        text = normalize_stock_display(value)
        if not text:
            continue
        tokens = text.split()
        if tokens and _canonical_stock_code(tokens[0]) and len(tokens) > 1:
            text = normalize_stock_display(" ".join(tokens[1:]))
        if not _canonical_stock_code(text):
            key = _normalize_key(text)
            if key:
                names.add(key)
    return names


def _stock_name_text(item: dict[str, Any]) -> str:
    stock_name = normalize_stock_display(
        item.get("stock_name") or item.get("name") or item.get("stock")
    )
    tokens = stock_name.split()
    if tokens and _canonical_stock_code(tokens[0]):
        return normalize_stock_display(" ".join(tokens[1:]))
    return stock_name


def _canonicalize_item_codes(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out.pop("stock_code", None)
    out.pop("code", None)
    codes = _candidate_codes(item)
    if len(codes) == 1:
        code = next(iter(codes))
        out["stock_code"] = code
        out["code"] = code
    return out


class _StockQuoteIndex:
    """One-pass indexes for stock quote matching by canonical code or unique name."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.by_code: dict[str, dict[str, Any]] = {}
        by_name: dict[str, dict[str, Any] | None] = {}
        for row in rows:
            code = _canonical_stock_code(row.get("code") or row.get("ts_code"))
            if code:
                self.by_code.setdefault(code, row)
            for key in _candidate_name_keys(row):
                existing = by_name.get(key)
                if key not in by_name:
                    by_name[key] = row
                    continue
                existing_code = _canonical_stock_code(
                    (existing or {}).get("code") or (existing or {}).get("ts_code")
                )
                if existing is not row and (not code or not existing_code or code != existing_code):
                    by_name[key] = None
        self.by_name = {key: row for key, row in by_name.items() if row is not None}

    @classmethod
    def from_prefill(cls, prefill: dict[str, Any]) -> _StockQuoteIndex:
        market = prefill.get("market") or {}
        rows = (
            _rows_from_section(market.get("stock_quotes"))
            if isinstance(market, dict)
            else []
        )
        return cls(rows)

    def find(self, item: dict[str, Any]) -> dict[str, Any] | None:
        codes = _candidate_codes(item)
        if codes:
            if len(codes) != 1:
                return None
            return self.by_code.get(next(iter(codes)))
        matched = {
            id(row): row
            for key in _candidate_name_keys(item)
            if (row := self.by_name.get(key)) is not None
        }
        return next(iter(matched.values())) if len(matched) == 1 else None


def _stock_matches_row(item: dict[str, Any], row: dict[str, Any]) -> bool:
    aliases = {_normalize_key(alias) for alias in _stock_aliases(item)}
    aliases.discard("")
    if not aliases:
        return False
    for key in ("stock", "name", "stock_name", "ts_name", "lead_stock", "buy_sm_amount_stock", "code", "ts_code"):
        value = _normalize_key(row.get(key))
        if value and value in aliases:
            return True
    return False


def _to_optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    number = _to_float(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _to_nonnegative_int(value: Any) -> int:
    number = _to_optional_nonnegative_int(value)
    return number if number is not None else 0


def _quote_facts(quote_index: _StockQuoteIndex, item: dict[str, Any]) -> dict[str, Any]:
    row = quote_index.find(item)
    if row is None:
        return {
            "code": "",
            "pct_chg": None,
            "amount_yi": None,
            "limit_height": None,
            "board_type": None,
            "sw_l2": "",
            "sector_source": "",
        }
    raw_code = _text(row.get("code") or row.get("ts_code")).strip().upper()
    code = _canonical_stock_code(raw_code)
    pct_chg = _to_float(
        row.get("pct_chg") if row.get("pct_chg") is not None else row.get("pct_change")
    )
    amount_yi = _to_float(
        row.get("amount_yi")
        if row.get("amount_yi") is not None
        else row.get("amount_billion")
    )
    amount_yi = _normalize_quote_amount_yi(amount_yi)
    limit_height = _to_optional_nonnegative_int(row.get("limit_height"))
    return {
        "code": code,
        "pct_chg": pct_chg,
        "amount_yi": amount_yi,
        "limit_height": limit_height,
        "board_type": _board_attribute(raw_code, pct_chg) if pct_chg is not None else None,
        "sw_l2": _text(row.get("sw_l2")).strip(),
        "sector_source": _text(row.get("sector_source")).strip(),
    }


def _with_quote_facts(
    quote_index: _StockQuoteIndex,
    item: dict[str, Any],
    *,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = facts or _quote_facts(quote_index, item)
    out = _canonicalize_item_codes(item)
    if facts["code"]:
        out["stock_code"] = facts["code"]
        out["code"] = facts["code"]
    if facts["sw_l2"]:
        out["sector"] = facts["sw_l2"]
        out["sector_source"] = (
            facts["sector_source"] or "tushare:index_member_all"
        )
    if facts["board_type"] is not None:
        out["board_type"] = facts["board_type"]
    if facts["limit_height"] is not None:
        out["limit_height"] = facts["limit_height"]
    if facts["pct_chg"] is not None:
        out["pct_chg"] = facts["pct_chg"]
    if facts["amount_yi"] is not None:
        out["amount_yi"] = facts["amount_yi"]
    return out


def _row_name_matches_sector(sector: str, row: dict[str, Any]) -> bool:
    target = _normalize_key(sector)
    if not target:
        return False
    for key in ("name", "industry", "sector_name", "ts_name", "ts_code"):
        if _normalize_key(row.get(key)) == target:
            return True
    return False


def _strength_from_row(row: dict[str, Any]) -> dict[str, float | None]:
    pct_keys = ("pct_chg", "pct_change", "change_pct", "pct_change_stock")
    amount_keys = ("amount_yi", "amount_billion", "top_volume_amount_billion", "成交额亿")
    raw_amount_keys = ("amount", "成交额")
    pct = next(
        (value for key in pct_keys if (value := _to_float(row.get(key))) is not None),
        None,
    )
    amount_yi = next(
        (value for key in amount_keys if (value := _to_float(row.get(key))) is not None),
        None,
    )
    if amount_yi is None:
        raw_amount = next(
            (value for key in raw_amount_keys if (value := _to_float(row.get(key))) is not None),
            None,
        )
        if raw_amount is not None and abs(raw_amount) >= 1_000_000:
            amount_yi = round(raw_amount / 1e8, 2)
    if amount_yi is not None:
        amount_yi = _normalize_quote_amount_yi(amount_yi)
    return {"pct": pct, "amount_yi": amount_yi}


def _find_stock_strength(
    prefill: dict[str, Any],
    item: dict[str, Any],
    quote_index: _StockQuoteIndex,
) -> dict[str, float | None]:
    market = prefill.get("market") or {}
    if not isinstance(market, dict):
        return {"pct": None, "amount_yi": None}

    quote = quote_index.find(item)
    if quote is not None:
        return _strength_from_row(quote)
    for section in ("market_daily_changes", "top_volume_stocks"):
        for row in _rows_from_section(market.get(section)):
            if not _stock_matches_row(item, row):
                continue
            return _strength_from_row(row)
    return {"pct": None, "amount_yi": None}


def _find_sector_strength(prefill: dict[str, Any], sector: str) -> dict[str, float | None]:
    market = prefill.get("market") or {}
    if not isinstance(market, dict):
        return {"net_amount_yi": None, "pct": None}

    for section in (
        "sector_moneyflow_ths",
        "concept_moneyflow_ths",
        "sector_moneyflow_dc",
        "concept_moneyflow_dc",
        "limit_cpt_list",
    ):
        for row in _rows_from_section(market.get(section)):
            if not _row_name_matches_sector(sector, row):
                continue
            net_amount = _to_float(row.get("net_amount_yi") if row.get("net_amount_yi") is not None else row.get("net_amount"))
            if net_amount is not None and abs(net_amount) >= 1_000_000:
                net_amount = round(net_amount / 1e8, 2)
            pct = _to_float(row.get("pct_change") if row.get("pct_change") is not None else row.get("pct_chg"))
            return {"net_amount_yi": net_amount, "pct": pct}
    return {"net_amount_yi": None, "pct": None}


def _teacher_supported_prefill_strength_evidence(
    prefill: dict[str, Any],
    item: dict[str, Any],
    quote_index: _StockQuoteIndex,
) -> str | None:
    stock_strength = _find_stock_strength(prefill, item, quote_index)
    sector_strength = _find_sector_strength(prefill, _text(item.get("sector")).strip())
    parts = ["老师明确支持主线预填票，补充当日强度"]
    stock_pct = _fmt_num(stock_strength.get("pct"))
    amount_yi = _fmt_num(stock_strength.get("amount_yi"))
    net_amount = _fmt_num(sector_strength.get("net_amount_yi"))
    sector_pct = _fmt_num(sector_strength.get("pct"))
    if stock_pct is not None:
        parts.append(f"个股涨幅 {stock_pct}%")
    if amount_yi is not None:
        parts.append(f"成交额 {amount_yi} 亿")
    if net_amount is not None:
        parts.append(f"板块资金净流入 {net_amount} 亿")
    if sector_pct is not None:
        parts.append(f"板块涨幅 {sector_pct}%")
    if len(parts) == 1:
        return None
    return "，".join(parts) + "，需 LLM 与人工复核"


def teacher_alignment(stock: str, sector: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
    stock_text = _text(stock)
    sector_text = _text(sector)
    conflict_words = ("退潮", "走弱", "回避")

    for note in notes or []:
        haystack = " ".join(
            _text(note.get(key)) for key in ("title", "core_view", "key_points", "raw_content")
        )
        sectors = [_text(item) for item in _loads_list(note.get("sectors"))]
        sector_hit = bool(sector_text and (sector_text in haystack or sector_text in sectors))
        stock_hit = bool(stock_text and stock_text in haystack)

        matched_terms = [term for term in (stock_text, sector_text) if term and term in haystack]
        if sector_hit and _has_nearby_conflict(haystack, matched_terms, conflict_words):
            return {"status": TEACHER_CONFLICT, "note": note}
        if sector_hit or stock_hit:
            return {"status": TEACHER_SUPPORT, "note": note}

    return {"status": TEACHER_UNMENTIONED, "note": None}


def _has_nearby_conflict(haystack: str, terms: list[str], conflict_words: tuple[str, ...]) -> bool:
    window = 4
    for term in terms:
        start = haystack.find(term)
        while start != -1:
            end = start + len(term)
            left = max(0, start - window)
            right = min(len(haystack), end + window)
            nearby = haystack[left:right]
            if any(word in nearby for word in conflict_words):
                return True
            start = haystack.find(term, start + 1)
    return False


def _note_ref(note: dict[str, Any] | None) -> dict[str, Any] | None:
    if not note:
        return None
    snippet = ""
    for key in ("core_view", "key_points", "raw_content"):
        snippet = _text(note.get(key)).strip()
        if snippet:
            break
    return {
        "id": note.get("id"),
        "date": _text(note.get("date")),
        "teacher_name": _text(note.get("teacher_name")),
        "title": _text(note.get("title")),
        "snippet": snippet[:80],
    }


def _history_keys(history: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for item in history or []:
        keys.update(_candidate_keys(_canonicalize_item_codes(item)))
    return keys


def _trusted_stock_identities(
    history: list[dict[str, Any]],
    prefill_items: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    identities: dict[str, tuple[str, str]] = {}
    conflicts: set[str] = set()
    for item in [*(history or []), *(prefill_items or [])]:
        name_keys = _candidate_name_keys(item)
        codes = _candidate_codes(item)
        if len(name_keys) != 1 or len(codes) != 1:
            continue
        stock_name = _stock_name_text(item)
        stock_code = next(iter(codes))
        name_key = _normalize_key(stock_name)
        if not name_key:
            continue
        existing = identities.get(name_key)
        if existing and existing[0] != stock_code:
            conflicts.add(name_key)
            continue
        identities[name_key] = (stock_code, stock_name)
    for name_key in conflicts:
        identities.pop(name_key, None)
    return identities


def _with_history_stock_identity(
    item: dict[str, Any],
    identities: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    codes = _candidate_codes(item)
    if codes:
        return _canonicalize_item_codes(item)
    cleaned = _canonicalize_item_codes(item)
    for name_key in _candidate_name_keys(cleaned):
        identity = identities.get(name_key)
        if not identity:
            continue
        stock_code, stock_name = identity
        return {
            **cleaned,
            "stock_code": stock_code,
            "code": stock_code,
            "stock_name": stock_name,
        }
    return cleaned


def _stock_aliases(item: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    stock = normalize_stock_display(item.get("stock"))
    stock_name = normalize_stock_display(item.get("stock_name") or item.get("name"))
    stock_code = next(iter(_candidate_codes(item)), "")
    for value in (stock, stock_name, stock_code, f"{stock_code} {stock_name}".strip()):
        if value:
            aliases.add(value)
    if " " in stock:
        first, rest = stock.split(" ", 1)
        if first:
            aliases.add(first.strip())
        if rest:
            aliases.add(rest.strip())
    return aliases


def _candidate_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    sector = _text(item.get("sector")).strip()
    codes = _candidate_codes(item)
    if codes:
        return {(f"code:{code}", sector) for code in codes}
    return {(f"name:{name}", sector) for name in _candidate_name_keys(item)}


def _is_st_candidate(item: dict[str, Any]) -> bool:
    return any(is_st_stock(alias) for alias in _stock_aliases(item))


def _seen_candidate(item: dict[str, Any], seen: set[tuple[str, str]]) -> bool:
    return bool(_candidate_keys(item) & seen)


def _mark_seen(item: dict[str, Any], seen: set[tuple[str, str]]) -> None:
    seen.update(_candidate_keys(item))


def _stock_already_candidate(item: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    return _find_existing_candidate(item, candidates) is not None


def _find_existing_candidate(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    identity_keys = {key for key, _ in _candidate_keys({**item, "sector": ""})}
    if not identity_keys:
        return None
    for candidate in candidates:
        candidate_keys = {
            key for key, _ in _candidate_keys({**candidate, "sector": ""})
        }
        if identity_keys & candidate_keys:
            return candidate
    return None


def _market_flow_items(
    prefill: dict[str, Any],
    quote_index: _StockQuoteIndex,
    limit: int = 120,
) -> list[dict[str, Any]]:
    market = prefill.get("market") or {}
    if not isinstance(market, dict):
        return []

    source_specs = [
        ("sector_moneyflow_ths", "lead_stock", "行业资金流"),
        ("concept_moneyflow_ths", "lead_stock", "同花顺概念资金流"),
        ("sector_moneyflow_dc", "buy_sm_amount_stock", "东方财富行业资金流"),
        ("concept_moneyflow_dc", "buy_sm_amount_stock", "东方财富概念资金流"),
    ]
    raw_items: list[tuple[float, dict[str, Any]]] = []
    for section, stock_field, source_label in source_specs:
        for row in _rows_from_section(market.get(section)):
            stock = normalize_stock_display(row.get(stock_field))
            sector = _text(row.get("name") or row.get("industry") or row.get("sector_name")).strip()
            net_amount = _to_float(row.get("net_amount_yi") if row.get("net_amount_yi") is not None else row.get("net_amount"))
            pct_stock = _to_float(row.get("pct_change_stock"))
            pct_sector = _to_float(row.get("pct_change"))
            rank = _to_float(row.get("rank"))
            if not stock or not sector:
                continue
            if net_amount is None or net_amount <= 0:
                continue
            rank_bonus = max(0.0, 80.0 - rank * 3.0) if rank is not None and rank <= 20 else 0.0
            score = net_amount + max(pct_stock or 0.0, 0.0) * 5.0 + max(pct_sector or 0.0, 0.0) + rank_bonus
            base_item = {
                "stock": stock,
                "sector": "未分类",
                "sector_source": "",
                "source_sector": sector,
                "attribute_type": "走势引领",
                "attribute": f"{source_label}：净流入 {net_amount} 亿",
                "clarity": CLARITY_MEDIUM,
                "position": None,
                "_flow_source": source_label,
                "_net_amount_yi": net_amount,
                "_pct_change_stock": pct_stock,
                "_pct_change": pct_sector,
                "_rank": int(rank) if rank is not None else None,
            }
            base_item = _with_quote_facts(quote_index, base_item)
            raw_items.append((
                score,
                base_item,
            ))

    raw_items.sort(key=lambda pair: -pair[0])
    return [item for _, item in raw_items[:limit]]


def _board_attribute(code: str, pct_chg: float) -> str | None:
    if pct_chg < 9.7:
        return "非涨停"
    normalized = (code or "").strip().upper()
    if not _canonical_stock_code(normalized):
        return None
    board_limit_pct = limit_pct_for(normalized)
    if board_limit_pct == 30.0:
        return "30cm"
    if board_limit_pct == 20.0:
        return "20cm"
    if board_limit_pct == 10.0:
        return "10cm"
    return None


def _normalize_quote_amount_yi(value: float | None) -> float | None:
    if value is None:
        return None
    if value > 10_000:
        return round(value / 100_000, 2)
    return value


def _candidate_amount_yi(
    prefill: dict[str, Any],
    item: dict[str, Any],
    quote_index: _StockQuoteIndex,
) -> float | None:
    amount_yi = _to_float(item.get("amount_yi") if item.get("amount_yi") is not None else item.get("_amount_yi"))
    if amount_yi is not None:
        return _normalize_quote_amount_yi(amount_yi)
    return _find_stock_strength(prefill, item, quote_index).get("amount_yi")


def _passes_min_amount_yi(
    prefill: dict[str, Any],
    item: dict[str, Any],
    min_amount_yi: float | None,
    quote_index: _StockQuoteIndex,
) -> bool:
    if min_amount_yi is None:
        return True
    amount_yi = _candidate_amount_yi(prefill, item, quote_index)
    return amount_yi is not None and amount_yi >= min_amount_yi


def _quote_strength_score(
    attribute_type: str | None,
    pct_chg: float,
    amount_yi: float | None,
) -> float:
    rounded_pct_bucket = round(pct_chg, 1)
    amount_component = min(amount_yi or 0.0, 500.0)
    board_component = 500.0 if attribute_type in {"20cm", "30cm"} else 0.0
    return board_component + rounded_pct_bucket * 10_000.0 + amount_component


def _fact_selection_score(base: float, item: dict[str, Any]) -> float:
    limit_height = _to_nonnegative_int(item.get("limit_height"))
    pct_chg = _to_float(item.get("pct_chg")) or 0.0
    amount_yi = _to_float(item.get("amount_yi")) or 0.0
    return float(base + limit_height * 1_000_000.0 + max(pct_chg, 0.0) * 10_000.0 + amount_yi)


def _quote_strength_items(
    quote_index: _StockQuoteIndex,
    limit: int = 80,
) -> list[dict[str, Any]]:
    raw_items: list[tuple[float, dict[str, Any]]] = []
    for row in quote_index.rows:
        stock = normalize_stock_display(row.get("name") or row.get("stock_name"))
        raw_code = _text(row.get("code") or row.get("ts_code")).strip().upper()
        code = _canonical_stock_code(raw_code)
        pct_chg = _to_float(row.get("pct_chg") if row.get("pct_chg") is not None else row.get("pct_change"))
        amount_yi = _to_float(row.get("amount_yi") if row.get("amount_yi") is not None else row.get("amount_billion"))
        if amount_yi is None:
            raw_amount = _to_float(row.get("amount"))
            if raw_amount is not None and abs(raw_amount) >= 1_000_000:
                amount_yi = round(raw_amount / 1e5, 2)
        amount_yi = _normalize_quote_amount_yi(amount_yi)
        if not stock or pct_chg is None:
            continue
        if pct_chg < 9.7:
            continue
        attribute_type = _board_attribute(raw_code, pct_chg)
        score = _quote_strength_score(attribute_type, pct_chg, amount_yi)
        sw_l2 = _text(row.get("sw_l2")).strip()
        item = {
            "stock": stock,
            "code": code,
            "stock_code": code,
            "sector": sw_l2 or "未分类",
            "sector_source": _text(row.get("sector_source")).strip() if sw_l2 else "",
            "source_sector": "日内强势",
            "attribute_type": "",
            "attribute": f"日内涨幅 {pct_chg}% / 成交额 {_fmt_num(amount_yi) or '-'} 亿",
            "clarity": CLARITY_MEDIUM,
            "position": None,
            "pct_chg": pct_chg,
            "amount_yi": amount_yi,
            "_pct_chg": pct_chg,
            "_amount_yi": amount_yi,
            "_quote_score": score,
        }
        if attribute_type is not None:
            item["board_type"] = attribute_type
        limit_height = _to_optional_nonnegative_int(row.get("limit_height"))
        if limit_height is not None:
            item["limit_height"] = limit_height
        raw_items.append((score, item))

    raw_items.sort(key=lambda pair: -pair[0])
    return [item for _, item in raw_items[:limit]]


def _with_common_fields(
    item: dict[str, Any],
    *,
    score: float,
    history_keys: set[tuple[str, str]],
    notes: list[dict[str, Any]],
    evidence_text: str,
    extra_evidence: list[str] | None = None,
) -> dict[str, Any]:
    stock = _text(item.get("stock")).strip()
    sector = _text(item.get("sector")).strip()
    alignment = teacher_alignment(stock, sector, notes)
    out = dict(item)
    out["stock"] = normalize_stock_display(out.get("stock"))
    if out.get("stock_name"):
        out["stock_name"] = normalize_stock_display(out.get("stock_name"))
    out.setdefault("clarity", CLARITY_MEDIUM)
    out["is_new"] = not bool(_candidate_keys(item) & history_keys)
    out["teacher_alignment"] = alignment["status"]
    note_ref = _note_ref(alignment["note"])
    if note_ref:
        out["teacher_note_ref"] = note_ref
    evidence = [{"label": "[判断]", "text": evidence_text}]
    for text in extra_evidence or []:
        if text:
            evidence.append({"label": "[判断]", "text": text})
    out["evidence"] = evidence
    out["_selection_score"] = float(score)
    return out


def build_candidates(
    *,
    prefill: dict[str, Any],
    trend_pool: list[dict[str, Any]],
    history: list[dict[str, Any]],
    date: str = "",
    min_amount_yi: float | None = None,
) -> dict[str, Any]:
    notes = prefill.get("teacher_notes") or []
    history_keys = _history_keys(history)
    quote_index = _StockQuoteIndex.from_prefill(prefill)
    step5 = prefill.get("step5_leaders") or {}
    step5_items = step5.get("top_leaders") or []
    trusted_stock_identities = _trusted_stock_identities(history, step5_items)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for raw_item in step5_items:
        item = {
            "stock": normalize_stock_display(raw_item.get("stock")),
            "sector": _text(raw_item.get("sector")).strip(),
            "attribute_type": _text(raw_item.get("attribute_type")).strip(),
            "attribute": raw_item.get("attribute"),
            "clarity": raw_item.get("clarity") or CLARITY_MEDIUM,
            "position": raw_item.get("position"),
        }
        for key in ("stock_code", "code", "stock_name"):
            value = _text(raw_item.get(key)).strip()
            if value:
                item[key] = value
        item = _with_history_stock_identity(item, trusted_stock_identities)
        item = _with_quote_facts(quote_index, item)
        if not item["stock"] or not item["sector"] or _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi, quote_index):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        alignment = teacher_alignment(item["stock"], item["sector"], notes)
        extra_evidence = []
        if alignment["status"] == TEACHER_SUPPORT:
            extra = _teacher_supported_prefill_strength_evidence(
                prefill,
                item,
                quote_index,
            )
            if extra:
                extra_evidence.append(extra)
        candidates.append(
            _with_common_fields(
                item,
                score=_fact_selection_score(80.0, item),
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自复盘预填候选，需用户确认",
                extra_evidence=extra_evidence,
            )
        )

    for history_item in history or []:
        stock_name = _stock_name_text(history_item)
        stock_codes = _candidate_codes(history_item)
        stock_code = next(iter(stock_codes)) if len(stock_codes) == 1 else ""
        stock = f"{stock_code} {stock_name}".strip() if stock_code else stock_name
        sector = _text(history_item.get("sector")).strip()
        item = {
            "stock": stock,
            "stock_name": stock_name,
            "sector": sector,
            "attribute_type": _text(history_item.get("attribute_type") or "历史最票跟踪").strip(),
            "attribute": history_item.get("attribute"),
            "clarity": history_item.get("clarity") or CLARITY_MEDIUM,
            "position": history_item.get("position"),
        }
        if stock_code:
            item["stock_code"] = stock_code
        explicit_code = _text(history_item.get("code")).strip()
        if explicit_code:
            item["code"] = explicit_code
        item = _with_quote_facts(quote_index, item)
        if not item["stock"] or not item["sector"] or _is_st_candidate({
            **item,
            "stock_name": history_item.get("stock_name"),
            "stock_code": history_item.get("stock_code"),
        }):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi, quote_index):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        candidates.append(
            _with_common_fields(
                item,
                score=_fact_selection_score(60.0, item),
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自历史最票跟踪，需用户确认是否仍属当日最票",
            )
        )

    for item in _quote_strength_items(quote_index):
        item = _with_history_stock_identity(item, trusted_stock_identities)
        if _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi, quote_index):
            continue
        if _stock_already_candidate(item, candidates):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        pct_chg = item.pop("_pct_chg", None)
        amount_yi = item.pop("_amount_yi", None)
        quote_score = item.pop("_quote_score", 0.0)
        detail = f"来自日内涨幅强度候选，个股涨幅 {_fmt_num(pct_chg)}%"
        if amount_yi is not None:
            detail += f"，成交额 {_fmt_num(amount_yi)} 亿"
        detail += "，涨停/涨幅强度与成交额优先于单一概念资金流，需 LLM 与人工复核"
        candidates.append(
            _with_common_fields(
                item,
                score=(
                    90.0
                    + quote_score
                    + _to_nonnegative_int(item.get("limit_height")) * 1_000_000.0
                ),
                history_keys=history_keys,
                notes=notes,
                evidence_text=detail,
            )
        )

    for item in _market_flow_items(prefill, quote_index):
        item = _with_history_stock_identity(item, trusted_stock_identities)
        if _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi, quote_index):
            continue
        pct_stock = item.pop("_pct_change_stock", None)
        pct_sector = item.pop("_pct_change", None)
        rank = item.pop("_rank", None)
        source_label = item.pop("_flow_source", "盘后资金流")
        net_amount = item.pop("_net_amount_yi", None)
        detail = f"来自{source_label}候选，{item.get('source_sector') or item['sector']} 净流入 {net_amount} 亿"
        if rank is not None:
            detail += f"，榜单排名 {rank}"
        if pct_stock is not None:
            detail += f"，领涨股涨幅 {pct_stock}%"
        elif pct_sector is not None:
            detail += f"，板块涨幅 {pct_sector}%"
        detail += "，需 LLM 与人工复核"
        flow_score = _fact_selection_score(
            65.0 + max(_to_float(net_amount) or 0.0, 0.0),
            item,
        )
        existing = _find_existing_candidate(item, candidates)
        if existing is not None:
            source_sector = _text(item.get("source_sector")).strip()
            sources = [
                _text(source).strip()
                for source in existing.get("source_sectors") or []
                if _text(source).strip()
            ]
            existing_source = _text(existing.get("source_sector")).strip()
            if existing_source and existing_source not in sources:
                sources.append(existing_source)
            if source_sector and source_sector not in sources:
                sources.append(source_sector)
            if source_sector:
                existing["source_sector"] = source_sector
            if sources:
                existing["source_sectors"] = sources
            existing.setdefault("evidence", []).append(
                {"label": "[判断]", "text": detail}
            )
            existing["_selection_score"] = max(
                float(existing.get("_selection_score") or 0.0),
                flow_score,
            )
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        candidates.append(
            _with_common_fields(
                item,
                score=flow_score,
                history_keys=history_keys,
                notes=notes,
                evidence_text=detail,
            )
        )

    candidates.sort(
        key=lambda item: (-item["_selection_score"], item["sector"], item["stock"])
    )
    return {"date": date, "top_leaders": candidates}
