"""全市场两点快照的确定性半小时统计。"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import fmean, median


FLAT_EPSILON_PCT = 0.01
MIN_INTERVAL_COVERAGE = 0.95
MIN_AMOUNT_COVERAGE = 0.95
MIN_INDUSTRY_MEMBERS = 8
MIN_INDUSTRY_COVERAGE = 0.90


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _counts(values: list[float]) -> dict:
    return {
        "up": sum(value > FLAT_EPSILON_PCT for value in values),
        "down": sum(value < -FLAT_EPSILON_PCT for value in values),
        "flat": sum(abs(value) <= FLAT_EPSILON_PCT for value in values),
    }


def current_breadth(snapshot: dict) -> dict:
    values = []
    for quote in (snapshot.get("stocks") or {}).values():
        value = _finite(quote.get("pct_chg"))
        if value is not None:
            values.append(value)
    counts = _counts(values)
    total = len(values)
    return {
        **counts,
        "valid": total,
        "up_ratio_pct": round(counts["up"] / total * 100, 2) if total else None,
        "median_pct": round(median(values), 3) if values else None,
        "mean_pct": round(fmean(values), 3) if values else None,
        "strong_5pct": sum(value >= 5 for value in values),
        "weak_5pct": sum(value <= -5 for value in values),
    }


def _index_changes(previous: dict, current: dict) -> tuple[list[dict], str | None]:
    out = []
    previous_indices = previous.get("indices") or {}
    current_indices = current.get("indices") or {}
    expected_codes = set(previous_indices) | set(current_indices)
    common_codes = set(previous_indices) & set(current_indices)
    coverage = len(common_codes) / len(expected_codes) if expected_codes else 0.0
    for code, quote in current_indices.items():
        old = previous_indices.get(code) or {}
        old_price = _finite(old.get("price"))
        price = _finite(quote.get("price"))
        if old_price is None or price is None or old_price <= 0:
            continue
        out.append({
            "code": code,
            "name": quote.get("name") or code,
            "interval_pct": round((price / old_price - 1) * 100, 3),
            "daily_pct": _finite(quote.get("pct_chg")),
        })
    error = None
    if not expected_codes:
        error = "宽基指数两点行情缺失"
    elif coverage < 1.0:
        error = f"宽基指数两点覆盖不足（{len(common_codes)}/{len(expected_codes)}）"
    return out, error


def _industry_rankings(rows: list[dict], industry_map: dict[str, str]) -> tuple[dict, str | None]:
    mapped = [row for row in rows if industry_map.get(row["code"])]
    coverage = len(mapped) / len(rows) if rows else 0.0
    if coverage < MIN_INDUSTRY_COVERAGE:
        return {}, f"申万二级覆盖不足（{coverage:.1%} < {MIN_INDUSTRY_COVERAGE:.0%}）"
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in mapped:
        grouped[industry_map[row["code"]]].append(row)
    sectors = []
    for name, members in grouped.items():
        if len(members) < MIN_INDUSTRY_MEMBERS:
            continue
        changes = [member["interval_pct"] for member in members]
        amounts = [
            member["amount_delta"] for member in members
            if member.get("amount_delta") is not None
        ]
        sectors.append({
            "name": name,
            "members": len(members),
            "median_pct": round(median(changes), 3),
            "up_ratio_pct": round(sum(value > FLAT_EPSILON_PCT for value in changes) / len(changes) * 100, 1),
            "amount_yi": round(sum(amounts) / 1e8, 2) if amounts else None,
        })
    ordered = sorted(sectors, key=lambda item: (item["median_pct"], item["up_ratio_pct"]), reverse=True)
    return {
        "coverage_pct": round(coverage * 100, 2),
        "strongest": ordered[:5],
        "weakest": list(reversed(ordered[-5:])),
    }, None


def analyze_interval(previous: dict, current: dict, industry_map: dict[str, str]) -> dict:
    previous_stocks = previous.get("stocks") or {}
    current_stocks = current.get("stocks") or {}
    common_codes = sorted(set(previous_stocks) & set(current_stocks))
    denominator = max(len(previous_stocks), len(current_stocks), 1)
    common_coverage = len(common_codes) / denominator
    if common_coverage < MIN_INTERVAL_COVERAGE:
        return {
            "status": "coverage_failed",
            "error": f"两点共同股票覆盖不足（{common_coverage:.1%} < {MIN_INTERVAL_COVERAGE:.0%}）",
            "common": len(common_codes),
            "coverage_pct": round(common_coverage * 100, 2),
        }

    rows = []
    for code in common_codes:
        old = previous_stocks[code]
        quote = current_stocks[code]
        old_price = _finite(old.get("price"))
        price = _finite(quote.get("price"))
        if old_price is None or price is None or old_price <= 0:
            continue
        old_amount = _finite(old.get("amount"))
        amount = _finite(quote.get("amount"))
        amount_delta = None
        if old_amount is not None and amount is not None and amount >= old_amount >= 0:
            amount_delta = amount - old_amount
        rows.append({
            "code": code,
            "name": quote.get("name") or old.get("name") or code,
            "interval_pct": round((price / old_price - 1) * 100, 4),
            "daily_pct": _finite(quote.get("pct_chg")),
            "amount_delta": amount_delta,
        })
    values = [row["interval_pct"] for row in rows]
    counts = _counts(values)
    amount_rows = [row for row in rows if row["amount_delta"] is not None]
    sectors, sector_error = _industry_rankings(rows, industry_map) if industry_map else ({}, "申万二级映射不可用")
    indices, index_error = _index_changes(previous, current)
    rising = sorted(rows, key=lambda item: item["interval_pct"], reverse=True)[:5]
    falling = sorted(rows, key=lambda item: item["interval_pct"])[:5]
    total = len(values)
    amount_coverage = len(amount_rows) / total if total else 0.0
    amount_error = None
    amount_yi = None
    if amount_coverage >= MIN_AMOUNT_COVERAGE:
        amount_yi = round(sum(row["amount_delta"] for row in amount_rows) / 1e8, 2)
    else:
        amount_error = (
            f"区间成交额差分覆盖不足（{amount_coverage:.1%} < "
            f"{MIN_AMOUNT_COVERAGE:.0%}）"
        )
    result = {
        "status": "partial" if sector_error or index_error or amount_error else "complete",
        "error": sector_error,
        "index_error": index_error,
        "amount_error": amount_error,
        "common": len(common_codes),
        "coverage_pct": round(common_coverage * 100, 2),
        "valid": total,
        **counts,
        "up_ratio_pct": round(counts["up"] / total * 100, 2) if total else None,
        "median_pct": round(median(values), 3) if values else None,
        "mean_pct": round(fmean(values), 3) if values else None,
        "up_1pct": sum(value >= 1 for value in values),
        "down_1pct": sum(value <= -1 for value in values),
        "amount_yi": amount_yi,
        "amount_coverage_pct": round(amount_coverage * 100, 2) if total else None,
        "indices": indices,
        "sectors": sectors,
        "rising": rising,
        "falling": falling,
    }
    return result


def market_tone(interval: dict) -> str:
    """机械化盘面归纳；只用于压缩阅读，不替代原始事实。"""
    median_pct = _finite(interval.get("median_pct"))
    up_ratio = _finite(interval.get("up_ratio_pct"))
    if median_pct is None or up_ratio is None:
        return "半小时方向未计算"
    if median_pct >= 0.15 and up_ratio >= 60:
        return "半小时内多数个股走强，扩散度偏强"
    if median_pct <= -0.15 and up_ratio <= 40:
        return "半小时内多数个股走弱，扩散度偏弱"
    return "半小时内涨跌分化，未形成单边扩散"
