"""按盘中最后一次回执统计覆盖；盘后量价归档不参与。"""
from collections import Counter
from datetime import date, datetime, timedelta
import json
import re

from services.intraday_monitor.guards import shanghai_now
from .bar_archive import ENDS
from .core import MAX_DELAY, MIN_DELAY, calendar_basis


FIRST_5M_DAY = date(2026, 11, 18)  # 11月17日收盘后从15分钟切换到5分钟。
RECEIPT_STATES = ("complete", "partial", "source_failed")
STATES = (*RECEIPT_STATES, "no_receipt", "invalid_receipt", "in_progress")


def _counts(values):
    counts = Counter(values)
    return {key: counts[key] for key in STATES}


def _full_coverage(counts):
    measured = sum(counts.values()) - counts["in_progress"]
    return round(100 * counts["complete"] / measured, 2) if measured else None


def _day(root, day, previous, now):
    times = {end.replace(":", ""): shanghai_now(datetime.fromisoformat(f"{day}T{end}"))
             for end in ENDS}
    attempts = {}
    invalid, unknown = set(), []
    folder = root / day
    # 目录不存在代表没有归档；无权限/IO失败由调用方显式报告。
    for path in (folder.iterdir() if folder.exists() else ()):
        if path.suffix != ".json":
            continue
        slot = path.name[:4]
        if slot not in times or not re.fullmatch(r"[0-9]{4}-[0-9a-f]{32}\.json", path.name):
            unknown.append(path.name)
            continue
        end = times[slot]
        try:
            item = json.loads(path.read_bytes())
            stamp = datetime.fromisoformat(item["captured_at"])
            if (stamp.tzinfo is None or stamp.utcoffset() != timedelta(hours=8)
                    or not MIN_DELAY <= stamp-end <= MAX_DELAY):
                raise ValueError("回执时间或槽位不符")
            if stamp > now:
                continue
            status = item.get("status")
            if (status not in RECEIPT_STATES or item.get("previous_trade_date") != previous
                    or not isinstance(item.get("evaluations"), list) or not isinstance(item.get("errors"), list)):
                status = "invalid_receipt"
            attempts.setdefault(slot, []).append((stamp, status))
        except (OSError, KeyError, ValueError, TypeError, OverflowError):
            invalid.add(slot)
    slots = {}
    for slot, end in times.items():
        if end + MIN_DELAY > now:
            continue
        if slot in invalid:
            slots[slot] = "invalid_receipt"
        elif slot in attempts:
            latest = max(v[0] for v in attempts[slot])
            states = {status for stamp, status in attempts[slot] if stamp == latest}
            slots[slot] = states.pop() if len(states) == 1 else "invalid_receipt"
        else:
            slots[slot] = "in_progress" if now <= end + MAX_DELAY else "no_receipt"
    counts = _counts(slots.values())
    return {"date": day, "status": "invalid_receipt" if counts["invalid_receipt"] or unknown else "ok",
            "counts": counts, "complete_pct": _full_coverage(counts),
            "morning": _counts(v for s, v in slots.items() if s < "1200"),
            "afternoon": _counts(v for s, v in slots.items() if s > "1200"),
            "slots": slots, "unrecognized_files": sorted(unknown)}


def receipt_coverage(root, start, end, *, now=None, db_path=None):
    now = shanghai_now(now)
    if start > end or end > now.date():
        raise ValueError("日期范围倒置或含未来日期")
    if start < FIRST_5M_DAY:
        raise ValueError("5分钟监控自2026-11-18开盘生效，旧版频率不同")
    days, totals = [], Counter()
    for offset in range((end-start).days+1):
        day = str(start + timedelta(days=offset))
        status, previous = calendar_basis(day, db_path)
        if status != "complete":
            days.append({"date": day, "status": status})
            continue
        try:
            report = _day(root, day, previous, now)
        except OSError:
            report = {"date": day, "status": "archive_unreadable"}
        days.append(report)
        totals.update(report.get("counts", {}))
    excluded = [d["date"] for d in days if d["status"] in ("blocked_calendar", "archive_unreadable")]
    return {"start_date": str(start), "end_date": str(end), "as_of": now.isoformat(),
            "status": "partial" if any(d["status"] not in ("ok", "non_trade_day") for d in days) else "ok",
            "counts": {key: totals[key] for key in STATES}, "complete_pct": _full_coverage(totals),
            "denominator_slots": sum(totals.values()) - totals["in_progress"], "excluded_dates": excluded,
            "denominator_scope": "日历已核验且可读日期的已检查或已过期槽位；无回执和无效回执计入，仍在等待首次回执的槽位另列",
            "days": days}
