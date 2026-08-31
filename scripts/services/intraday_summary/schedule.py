"""上海交易时段内的半小时扫描槽位。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from services.intraday_monitor.guards import shanghai_now


SLOT_GRACE = timedelta(minutes=5)


@dataclass(frozen=True)
class ScanSlot:
    label: str
    kind: str
    previous_label: str | None = None

    def slot_id(self, day: str) -> str:
        return f"{day}T{self.label}"


SLOTS = (
    ScanSlot("09:30", "baseline"),
    ScanSlot("10:00", "summary", "09:30"),
    ScanSlot("10:30", "summary", "10:00"),
    ScanSlot("11:00", "summary", "10:30"),
    ScanSlot("11:30", "summary", "11:00"),
    ScanSlot("13:00", "baseline"),
    ScanSlot("13:30", "summary", "13:00"),
    ScanSlot("14:00", "summary", "13:30"),
    ScanSlot("14:30", "summary", "14:00"),
    ScanSlot("15:00", "summary", "14:30"),
)


def slot_for_time(now: datetime) -> ScanSlot | None:
    """返回当前应执行的槽位；允许 launchd 在槽位后 5 分钟内补触发。"""
    local = shanghai_now(now)
    for slot in SLOTS:
        hour, minute = (int(part) for part in slot.label.split(":"))
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay = local - scheduled
        if timedelta(0) <= delay <= SLOT_GRACE:
            return slot
    return None
