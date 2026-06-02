"""窗口定义与区间换算（日历日回溯，闭区间含 anchor）。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowSpec:
    key: str
    lookback_days: int
    label: str
    top_n: int


WINDOWS: dict[str, WindowSpec] = {
    "recent3d": WindowSpec("recent3d", 3, "近3日", 5),
    "weekly": WindowSpec("weekly", 7, "近1周", 6),
    "monthly": WindowSpec("monthly", 30, "近1月", 8),
}


def resolve_window(key: str) -> WindowSpec:
    spec = WINDOWS.get(key)
    if spec is None:
        raise ValueError(f"未知窗口 {key!r}，需为 {sorted(WINDOWS)} 之一")
    return spec


def window_bounds(spec: WindowSpec, anchor_date: str) -> tuple[str, str]:
    """[start, anchor] 闭区间，start = anchor - (lookback_days - 1) 天。"""
    anchor = datetime.date.fromisoformat(anchor_date)
    start = anchor - datetime.timedelta(days=spec.lookback_days - 1)
    return start.isoformat(), anchor.isoformat()
