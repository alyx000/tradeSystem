from __future__ import annotations

import json
from typing import Any

from services.daily_leaders.models import (
    CLARITY_MEDIUM,
    TEACHER_CONFLICT,
    TEACHER_SUPPORT,
    TEACHER_UNMENTIONED,
)


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
        stock = _text(item.get("stock_name") or item.get("stock")).strip()
        sector = _text(item.get("sector")).strip()
        if stock or sector:
            keys.add((stock, sector))
    return keys


def _candidate_key(item: dict[str, Any]) -> tuple[str, str]:
    return (_text(item.get("stock")).strip(), _text(item.get("sector")).strip())


def _with_common_fields(
    item: dict[str, Any],
    *,
    score: int,
    history_keys: set[tuple[str, str]],
    notes: list[dict[str, Any]],
    evidence_text: str,
) -> dict[str, Any]:
    stock = _text(item.get("stock")).strip()
    sector = _text(item.get("sector")).strip()
    alignment = teacher_alignment(stock, sector, notes)
    out = dict(item)
    out.setdefault("clarity", CLARITY_MEDIUM)
    out["is_new"] = (stock, sector) not in history_keys
    out["teacher_alignment"] = alignment["status"]
    note_ref = _note_ref(alignment["note"])
    if note_ref:
        out["teacher_note_ref"] = note_ref
    out["evidence"] = [{"label": "[判断]", "text": evidence_text}]
    out["_score"] = score
    return out


def build_candidates(
    *,
    prefill: dict[str, Any],
    trend_pool: list[dict[str, Any]],
    history: list[dict[str, Any]],
    date: str = "",
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
        if not item["stock"] or not item["sector"]:
            continue
        key = _candidate_key(item)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            _with_common_fields(
                item,
                score=80,
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自复盘预填候选，需用户确认",
            )
        )

    for pool_item in trend_pool or []:
        stock = f"{_text(pool_item.get('code')).strip()} {_text(pool_item.get('name')).strip()}".strip()
        sector = _text(pool_item.get("sw_l2") or "未分类").strip()
        signal = pool_item.get("last_signal") or {}
        entry_trigger = _text(signal.get("entry_trigger") or "趋势信号").strip()
        item = {
            "stock": stock,
            "sector": sector,
            "attribute_type": "走势引领",
            "attribute": f"趋势主升池触发：{entry_trigger}",
            "clarity": CLARITY_MEDIUM,
            "position": None,
        }
        if not item["stock"]:
            continue
        key = _candidate_key(item)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            _with_common_fields(
                item,
                score=70,
                history_keys=history_keys,
                notes=notes,
                evidence_text="来自趋势主升观察池，需用户确认",
            )
        )

    candidates.sort(key=lambda item: (-item["_score"], item["sector"], item["stock"]))
    out = [{key: value for key, value in item.items() if key != "_score"} for item in candidates]
    return {"date": date, "top_leaders": out}
