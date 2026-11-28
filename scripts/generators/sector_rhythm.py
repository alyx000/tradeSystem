"""板块节奏报告的五日收益、上榜强度和榜单覆盖展示。"""
from __future__ import annotations

import math


def signed_pct(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "待核"
    return f"{value:+.2f}%" if value else "0.00%"


def rhythm_streak(row):
    count = row.get("consecutive_in_top30")
    if count is None:
        return "待核"
    lower_bound = (row.get("signals") or {}).get("consecutive_is_lower_bound")
    return f"{'至少' if lower_bound else ''}{count}天"


def rhythm_window_cells(row: dict, days: int = 5) -> tuple[str, str, str]:
    key = f"{days}d"
    evidence = (row.get("rhythm_window_evidence") or {}).get(key) or {}
    period = signed_pct(row.get(f"period_return_{key}_pct"))
    if period == "待核" and evidence.get("reason"):
        period += f"（{evidence['reason']}）"
    listed_sum = signed_pct(row.get(f"top30_sum_{key}_pct"))
    covered = row.get(f"ranking_covered_days_{key}")
    listed = row.get(f"top30_days_{key}")
    days_text = f"{listed}/{days}" if listed is not None else "待核"
    if covered is not None and covered < days:
        days_text += f"（榜单覆盖 {covered}/{days}）"
        if listed_sum != "待核":
            listed_sum += "（已核部分）"
    if row.get(f"top30_sum_{key}_pct") is None:
        legacy = signed_pct(row.get(f"cumulative_pct_{key}"))
        if legacy != "待核":
            listed_sum = f"待核（旧归档窗口上榜日合计 {legacy}）"
    return period, listed_sum, days_text
