"""逐规则最近一个观测日的检查回执；绝不参与是否发送的决策。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime


RESULTS = {"not_triggered": "已检查、未触发", "triggered": "已触发",
           "matched_no_event": "条件成立、本轮未产生新提醒", "quote_gap": "行情缺失、无法判断",
           "calculation_gap": "计算依据不足、无法判断"}
DELIVERY = {"sent": "已发送", "failed": "发送失败、待重试", "pending": "待发送",
            "retired": "已停止重试，未发送"}


def _valid(row):
    return (isinstance(row, dict) and isinstance(row.get("day"), str)
            and all(type(row.get(key)) is int and row[key] >= 0
                    for key in ("checks", "quote_gaps", "calculation_gaps", "triggers", "sent")))


def _rows(state):
    rows = state.setdefault("rule_health", {})
    return rows if isinstance(rows, dict) else {}


def observe(state, key, now, result, *, reason=None, quote_at=None):
    rows = _rows(state)
    row = rows.get(key)
    if row is not None and not _valid(row):
        return  # 损坏的统计不从零起算，也不阻断原有提醒。
    day = now.date().isoformat()
    if row is None or row["day"] != day:
        previous_quote = (row or {}).get("last_valid_quote_at")
        row = rows[key] = dict(day=day, first_check_at=now.isoformat(), checks=0, quote_gaps=0,
                               calculation_gaps=0, triggers=0, sent=0, last_delivery=None,
                               last_valid_quote_at=previous_quote)
    row["checks"] += 1
    row.update(last_check_at=now.isoformat(), last_result=result, last_reason=reason)
    if quote_at is not None:
        row["last_valid_quote_at"] = quote_at.isoformat()
    if result in {"quote_gap", "calculation_gap"}:
        row["quote_gaps" if result == "quote_gap" else "calculation_gaps"] += 1
        row.update(last_gap_at=now.isoformat(), last_gap_reason=reason)
    if result == "triggered":
        row["triggers"] += 1
        row.update(last_trigger_at=now.isoformat(), last_delivery="pending")


def record_delivery(state, pending, now, ok):
    rows = _rows(state)
    for key, count in Counter(event.get("rule_id") for event in pending).items():
        row = rows.get(key)
        if not _valid(row) or row["day"] != now.date().isoformat():
            continue
        row["last_delivery"] = "sent" if ok else "failed"
        if ok:
            row["sent"] += count
            row["last_sent_at"] = now.isoformat()


def _stored_row(state, key):
    rows = state.get("rule_health")
    row = rows.get(key) if isinstance(rows, dict) else None
    return row if _valid(row) else None


def last_valid_quote(state, key, now):
    row = _stored_row(state, key)
    try:
        stamp = datetime.fromisoformat((row or {}).get("last_valid_quote_at") or "")
    except (ValueError, TypeError):
        return None
    return stamp if stamp.tzinfo is not None and stamp <= now else None


def for_status(state, key, now):
    row = _stored_row(state, key)
    if row is None or row["day"] != now.date().isoformat():
        return None  # 旧账本、当日未检查或损坏：未知，不是零次缺失。
    return row
