from __future__ import annotations

from .constants import EPSILON, UNCLASSIFIED


def _finite_positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _adjusted_high(raw_high: float, adj_factor: float) -> float:
    return round(raw_high * adj_factor, 10)


def detect_new_highs(rows: list[dict], watermarks: dict[str, dict], date: str) -> dict:
    new_highs: list[dict] = []
    updates: list[dict] = []
    skipped = 0
    initialized = 0
    market_count = 0

    for row in rows:
        code = row.get("code")
        high = row.get("high")
        adj = row.get("adj_factor")
        if not code or not _finite_positive(high) or not _finite_positive(adj):
            skipped += 1
            continue

        market_count += 1
        raw_high = float(high)
        adj_high = _adjusted_high(raw_high, float(adj))
        prev = watermarks.get(code)
        industry = row.get("industry") or UNCLASSIFIED
        base_update = {
            "code": code,
            "name": row.get("name") or "",
            "industry": industry,
            "last_seen_date": date,
        }

        if not prev:
            initialized += 1
            updates.append({
                **base_update,
                "max_adj_high": adj_high,
                "max_high_date": date,
                "max_raw_high": raw_high,
            })
            continue

        prev_high = float(prev["max_adj_high"])
        if adj_high > prev_high + EPSILON:
            item = {
                **row,
                "industry": industry,
                "raw_high": raw_high,
                "adj_high": adj_high,
                "prev_adj_high": prev_high,
                "prev_high_date": prev.get("max_high_date"),
            }
            new_highs.append(item)
            updates.append({
                **base_update,
                "max_adj_high": adj_high,
                "max_high_date": date,
                "max_raw_high": raw_high,
            })
        else:
            updates.append({
                **base_update,
                "max_adj_high": prev_high,
                "max_high_date": prev["max_high_date"],
                "max_raw_high": prev.get("max_raw_high"),
            })

    return {
        "market_count": market_count,
        "new_highs": new_highs,
        "watermark_updates": updates,
        "skipped_count": skipped,
        "initialized_count": initialized,
    }
