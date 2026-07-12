from __future__ import annotations

from .constants import UNCLASSIFIED


def _amount_key(item: dict) -> float:
    try:
        return float(item.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_by_sector(stocks: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for stock in stocks:
        industry = stock.get("industry") or UNCLASSIFIED
        grouped.setdefault(industry, []).append(stock)

    out = []
    for industry, rows in grouped.items():
        ordered = sorted(rows, key=lambda x: (-_amount_key(x), x.get("code") or ""))
        out.append({
            "industry": industry,
            "count": len(ordered),
            "stocks": ordered,
        })
    return sorted(out, key=lambda x: (-x["count"], x["industry"] == UNCLASSIFIED, x["industry"]))
