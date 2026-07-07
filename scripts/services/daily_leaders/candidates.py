from __future__ import annotations

import json
from typing import Any

from services.daily_leaders.models import (
    CLARITY_MEDIUM,
    TEACHER_CONFLICT,
    TEACHER_SUPPORT,
    TEACHER_UNMENTIONED,
)
from utils import is_st_stock


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
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: float | None) -> str | None:
    if value is None:
        return None
    if float(value).is_integer():
        return f"{value:.1f}"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalize_key(value: Any) -> str:
    return "".join(_text(value).split()).upper()


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


def _row_name_matches_sector(sector: str, row: dict[str, Any]) -> bool:
    target = _normalize_key(sector)
    if not target:
        return False
    for key in ("name", "industry", "sector_name", "ts_name", "ts_code"):
        if _normalize_key(row.get(key)) == target:
            return True
    return False


def _find_stock_strength(prefill: dict[str, Any], item: dict[str, Any]) -> dict[str, float | None]:
    market = prefill.get("market") or {}
    if not isinstance(market, dict):
        return {"pct": None, "amount_yi": None}

    pct_keys = ("pct_chg", "pct_change", "change_pct", "pct_change_stock")
    amount_keys = ("amount_yi", "amount_billion", "top_volume_amount_billion", "成交额亿")
    raw_amount_keys = ("amount", "成交额")
    for section in ("stock_quotes", "market_daily_changes", "top_volume_stocks"):
        for row in _rows_from_section(market.get(section)):
            if not _stock_matches_row(item, row):
                continue
            pct = next((_to_float(row.get(key)) for key in pct_keys if _to_float(row.get(key)) is not None), None)
            amount_yi = next((_to_float(row.get(key)) for key in amount_keys if _to_float(row.get(key)) is not None), None)
            if amount_yi is None:
                raw_amount = next((_to_float(row.get(key)) for key in raw_amount_keys if _to_float(row.get(key)) is not None), None)
                if raw_amount is not None and abs(raw_amount) >= 1_000_000:
                    amount_yi = round(raw_amount / 1e8, 2)
            if amount_yi is not None:
                amount_yi = _normalize_quote_amount_yi(amount_yi)
            return {"pct": pct, "amount_yi": amount_yi}
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


def _teacher_supported_prefill_strength_evidence(prefill: dict[str, Any], item: dict[str, Any]) -> str | None:
    stock_strength = _find_stock_strength(prefill, item)
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
        sector = _text(item.get("sector")).strip()
        for stock in _stock_aliases(item):
            keys.add((stock, sector))
    return keys


def _stock_aliases(item: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    stock = _text(item.get("stock")).strip()
    stock_name = _text(item.get("stock_name") or item.get("name")).strip()
    stock_code = _text(item.get("stock_code") or item.get("code")).strip()
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
    return {(stock, sector) for stock in _stock_aliases(item)}


def _is_st_candidate(item: dict[str, Any]) -> bool:
    return any(is_st_stock(alias) for alias in _stock_aliases(item))


def _seen_candidate(item: dict[str, Any], seen: set[tuple[str, str]]) -> bool:
    return bool(_candidate_keys(item) & seen)


def _mark_seen(item: dict[str, Any], seen: set[tuple[str, str]]) -> None:
    seen.update(_candidate_keys(item))


def _stock_already_candidate(item: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    aliases = {_normalize_key(alias) for alias in _stock_aliases(item)}
    aliases.discard("")
    if not aliases:
        return False
    for candidate in candidates:
        candidate_aliases = {_normalize_key(alias) for alias in _stock_aliases(candidate)}
        candidate_aliases.discard("")
        if aliases & candidate_aliases:
            return True
    return False


def _market_flow_items(prefill: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
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
            stock = _text(row.get(stock_field)).strip()
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
            raw_items.append((
                score,
                {
                    "stock": stock,
                    "sector": sector,
                    "attribute_type": "走势引领",
                    "attribute": f"{source_label}：净流入 {net_amount} 亿",
                    "clarity": CLARITY_MEDIUM,
                    "position": None,
                    "_flow_source": source_label,
                    "_net_amount_yi": net_amount,
                    "_pct_change_stock": pct_stock,
                    "_pct_change": pct_sector,
                    "_rank": int(rank) if rank is not None else None,
                },
            ))

    raw_items.sort(key=lambda pair: -pair[0])
    return [item for _, item in raw_items[:limit]]


def _board_attribute(code: str, pct_chg: float) -> str:
    normalized = (code or "").upper()
    if normalized.endswith(".BJ") or normalized.startswith(("8", "9")):
        return "30cm"
    if normalized.startswith(("300", "301", "688", "689")):
        return "20cm"
    if pct_chg >= 9.7:
        return "10cm"
    return "走势引领"


def _normalize_quote_amount_yi(value: float | None) -> float | None:
    if value is None:
        return None
    if value > 10_000:
        return round(value / 100_000, 2)
    return value


def _candidate_amount_yi(prefill: dict[str, Any], item: dict[str, Any]) -> float | None:
    amount_yi = _to_float(item.get("amount_yi") if item.get("amount_yi") is not None else item.get("_amount_yi"))
    if amount_yi is not None:
        return _normalize_quote_amount_yi(amount_yi)
    return _find_stock_strength(prefill, item).get("amount_yi")


def _passes_min_amount_yi(prefill: dict[str, Any], item: dict[str, Any], min_amount_yi: float | None) -> bool:
    if min_amount_yi is None:
        return True
    amount_yi = _candidate_amount_yi(prefill, item)
    return amount_yi is not None and amount_yi >= min_amount_yi


def _quote_strength_score(attribute_type: str, pct_chg: float, amount_yi: float | None) -> float:
    rounded_pct_bucket = round(pct_chg, 1)
    amount_component = min(amount_yi or 0.0, 500.0)
    board_component = 500.0 if attribute_type in {"20cm", "30cm"} else 0.0
    return board_component + rounded_pct_bucket * 10_000.0 + amount_component


def _quote_strength_items(prefill: dict[str, Any], limit: int = 80) -> list[dict[str, Any]]:
    market = prefill.get("market") or {}
    if not isinstance(market, dict):
        return []

    raw_items: list[tuple[float, dict[str, Any]]] = []
    for row in _rows_from_section(market.get("stock_quotes")):
        stock = _text(row.get("name") or row.get("stock_name")).strip()
        code = _text(row.get("code") or row.get("ts_code")).strip()
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
        attribute_type = _board_attribute(code, pct_chg)
        score = _quote_strength_score(attribute_type, pct_chg, amount_yi)
        raw_items.append((
            score,
            {
                "stock": stock,
                "sector": "日内强势",
                "attribute_type": attribute_type,
                "attribute": f"日内涨幅 {pct_chg}% / 成交额 {_fmt_num(amount_yi) or '-'} 亿",
                "clarity": CLARITY_MEDIUM,
                "position": None,
                "_pct_chg": pct_chg,
                "_amount_yi": amount_yi,
                "_quote_score": score,
            },
        ))

    raw_items.sort(key=lambda pair: -pair[0])
    return [item for _, item in raw_items[:limit]]


def _with_common_fields(
    item: dict[str, Any],
    *,
    score: int,
    history_keys: set[tuple[str, str]],
    notes: list[dict[str, Any]],
    evidence_text: str,
    extra_evidence: list[str] | None = None,
) -> dict[str, Any]:
    stock = _text(item.get("stock")).strip()
    sector = _text(item.get("sector")).strip()
    alignment = teacher_alignment(stock, sector, notes)
    out = dict(item)
    out.setdefault("clarity", CLARITY_MEDIUM)
    out["is_new"] = not bool(_candidate_keys({"stock": stock, "sector": sector}) & history_keys)
    out["teacher_alignment"] = alignment["status"]
    note_ref = _note_ref(alignment["note"])
    if note_ref:
        out["teacher_note_ref"] = note_ref
    evidence = [{"label": "[判断]", "text": evidence_text}]
    for text in extra_evidence or []:
        if text:
            evidence.append({"label": "[判断]", "text": text})
    out["evidence"] = evidence
    out["_score"] = score
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
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    step5 = prefill.get("step5_leaders") or {}
    for raw_item in step5.get("top_leaders") or []:
        item = {
            "stock": _text(raw_item.get("stock")).strip(),
            "sector": _text(raw_item.get("sector")).strip(),
            "attribute_type": _text(raw_item.get("attribute_type")).strip(),
            "attribute": raw_item.get("attribute"),
            "clarity": raw_item.get("clarity") or CLARITY_MEDIUM,
            "position": raw_item.get("position"),
        }
        if not item["stock"] or not item["sector"] or _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        alignment = teacher_alignment(item["stock"], item["sector"], notes)
        extra_evidence = []
        if alignment["status"] == TEACHER_SUPPORT:
            extra = _teacher_supported_prefill_strength_evidence(prefill, item)
            if extra:
                extra_evidence.append(extra)
        candidates.append(
            _with_common_fields(
                item,
                score=80,
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自复盘预填候选，需用户确认",
                extra_evidence=extra_evidence,
            )
        )

    for history_item in history or []:
        stock_name = _text(history_item.get("stock_name") or history_item.get("stock")).strip()
        stock_code = _text(history_item.get("stock_code") or history_item.get("code")).strip()
        stock = f"{stock_code} {stock_name}".strip() if stock_code else stock_name
        sector = _text(history_item.get("sector")).strip()
        item = {
            "stock": stock,
            "sector": sector,
            "attribute_type": _text(history_item.get("attribute_type") or "历史最票跟踪").strip(),
            "attribute": history_item.get("attribute"),
            "clarity": history_item.get("clarity") or CLARITY_MEDIUM,
            "position": history_item.get("position"),
        }
        if not item["stock"] or not item["sector"] or _is_st_candidate({
            **item,
            "stock_name": history_item.get("stock_name"),
            "stock_code": history_item.get("stock_code"),
        }):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        candidates.append(
            _with_common_fields(
                item,
                score=60,
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自历史最票跟踪，需用户确认是否仍属当日最票",
            )
        )

    for item in _quote_strength_items(prefill):
        if _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi):
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
                score=int(90 + quote_score),
                history_keys=history_keys,
                notes=notes,
                evidence_text=detail,
            )
        )

    for item in _market_flow_items(prefill):
        if _is_st_candidate(item):
            continue
        if not _passes_min_amount_yi(prefill, item, min_amount_yi):
            continue
        if _seen_candidate(item, seen):
            continue
        _mark_seen(item, seen)
        pct_stock = item.pop("_pct_change_stock", None)
        pct_sector = item.pop("_pct_change", None)
        rank = item.pop("_rank", None)
        source_label = item.pop("_flow_source", "盘后资金流")
        net_amount = item.pop("_net_amount_yi", None)
        detail = f"来自{source_label}候选，{item['sector']} 净流入 {net_amount} 亿"
        if rank is not None:
            detail += f"，榜单排名 {rank}"
        if pct_stock is not None:
            detail += f"，领涨股涨幅 {pct_stock}%"
        elif pct_sector is not None:
            detail += f"，板块涨幅 {pct_sector}%"
        detail += "，需 LLM 与人工复核"
        candidates.append(
            _with_common_fields(
                item,
                score=65,
                history_keys=history_keys,
                notes=notes,
                evidence_text=detail,
            )
        )

    candidates.sort(key=lambda item: (-item["_score"], item["sector"], item["stock"]))
    out = [{key: value for key, value in item.items() if key != "_score"} for item in candidates]
    return {"date": date, "top_leaders": out}
