"""当期/历史展示的唯一规则；不修改人工确认事实，不用行情样本数代替交易日历。"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

from services.emotion_leader import constants as C

POLICY_VERSION = "current-history-v1"


def load_tracking_calendar(conn, target: str, lookback_days: int = 120) -> list[dict]:
    start = (date.fromisoformat(target) - timedelta(days=max(120, lookback_days))).isoformat()
    try:
        rows = conn.execute(
            "SELECT date,is_open FROM trade_calendar WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, target),
        ).fetchall()
        return [{"date": r[0], "is_open": r[1]} for r in rows]
    except (sqlite3.Error, AttributeError):
        return []


def open_dates(calendar: object, target: str) -> list[str]:
    """只有逐自然日完整且目标日开放的日历可用于计算时效。"""
    if not isinstance(calendar, list) or not calendar:
        return []
    try:
        days = [date.fromisoformat(r["date"]) for r in calendar]
        if (any(type(r["is_open"]) is not int or r["is_open"] not in (0, 1) for r in calendar)
                or any(b - a != timedelta(days=1) for a, b in zip(days, days[1:]))
                or days[-1].isoformat() != target or calendar[-1]["is_open"] != 1):
            return []
        return [r["date"] for r in calendar if r["is_open"] == 1]
    except (KeyError, TypeError, ValueError):
        return []


def trade_age(dates: list[str], since: object, target: str) -> int | None:
    if not isinstance(since, str) or since not in dates or target not in dates or since > target:
        return None
    return dates.index(target) - dates.index(since)


def annotate(item: dict, target: str, dates: list[str]) -> dict:
    row = dict(item)
    manual = row.get("manual_confirmed") is True
    age = trade_age(dates, row.get("manual_last_seen_date"), target) if manual else None
    manual_status = ("unknown" if age is None else "fresh" if age < C.MANUAL_PRIORITY_OPEN_DAYS else "expired") if manual else "none"
    limit_age = trade_age(dates, row.get("last_limit_up_date"), target)
    row.update(manual_priority_status=manual_status, manual_age_open_days=age,
               manual_priority_active=manual_status == "fresh", days_since_limit_up=limit_age)
    # 旧快照没有 metric_as_of 时以其报告日期为准；显式陈旧日期不可覆盖。
    if row.get("metric_as_of", target) != target:
        row.update(metric_status="source_failed", today_pct_chg=None, new_peak_today=None,
                   current_state="未计算", feedback_type="未计算")
    comparable = row.get("metric_status", "ok") == "ok" and row.get("metric_as_of", target) == target
    recent_limit = limit_age is not None and limit_age < C.CURRENT_CORE_OPEN_DAYS
    if not comparable:
        group, reason = "unverified", "当日行情待核验或停牌"
    elif row.get("new_peak_today") is True or row.get("current_state") == "涨停":
        group, reason = "current", "当日创新高或涨停"
    elif recent_limit:
        group, reason = "current", f"最近{C.CURRENT_CORE_OPEN_DAYS}个交易日内涨停"
    elif manual_status == "fresh":
        group, reason = "current", "人工确认仍在有效期"
    elif not dates or manual_status == "unknown" or (row.get("last_limit_up_date") and limit_age is None):
        group, reason = "unverified", "日历或确认日期不足，时效待核验"
    elif limit_age is not None or manual_status == "expired":
        group, reason = "history", "近期无涨停且人工优先权未生效"
    else:
        group, reason = "unverified", "缺少可核验的活跃日期"
    row.update(tracking_group=group, tracking_reason=reason)
    return row


def _number(value: object, default: float = -10000.0) -> float:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else default


def rank_key(row: dict) -> tuple:
    """高度先于人工优先权；当日正负不决定入选资格。"""
    return (row.get("metric_status", "ok") == "ok", _number(row.get("max_height"), 0),
            row.get("manual_priority_active") is True,
            str(row.get("last_limit_up_date") or row.get("launch_date") or ""),
            _number(row.get("max_gain_pct")), row.get("new_peak_today") is True,
            abs(_number(row.get("today_pct_chg"), 0)), str(row.get("code") or ""))


def tracking_view(payload: dict) -> dict:
    target = payload.get("date", "")
    dates = open_dates(payload.get("tracking_calendar"), target)
    groups = {"current": [], "history": [], "unverified": []}
    cached = 0
    for item in payload.get("active", []) + payload.get("archived", []):
        if item.get("metric_status") == "cached_archived" or (item.get("archived") and item.get("metric_as_of") != target):
            cached += 1
            continue
        row = annotate(item, target, dates)
        groups[row["tracking_group"]].append(row)
    for rows in groups.values():
        rows.sort(key=rank_key, reverse=True)
    return {**groups, "cached_count": cached, "calendar_status": "complete" if dates else "missing_data",
            "policy_version": POLICY_VERSION}


def should_archive(row: dict) -> bool:
    """仅对可判定的历史核心归档；时效未知不等于已过期。"""
    age = row.get("days_since_limit_up") if row.get("last_limit_up_date") else row.get("manual_age_open_days")
    drawdown = row.get("distance_from_peak_pct")
    return (row.get("tracking_group") == "history"
            and row.get("manual_priority_status") in {"none", "expired"}
            and type(age) is int and age > C.ARCHIVE_AFTER_TRADE_DAYS
            and type(drawdown) in (int, float) and math.isfinite(drawdown)
            and drawdown <= C.ARCHIVE_DRAWDOWN_PCT)
