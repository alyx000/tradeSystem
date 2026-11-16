"""Bounded same-day retry for missing inputs only; never retry a notification."""
from __future__ import annotations

import sys
import time

from services.daily_leaders.readiness import DataNotReady, now_shanghai

RETRY_SECONDS = 300


def check_window(date, now=None):
    now = now or now_shanghai()
    if now.date().isoformat() != date or now.weekday() >= 5 or not 21 <= now.hour < 23:
        raise TimeoutError('定时推送只允许上海工作日21:00至23:00，已停止且不推送')


def run_when_ready(attempt, date, *, wait=False, now=now_shanghai, sleep=time.sleep):
    while True:
        if wait:
            check_window(date, now())
        try:
            return attempt()
        except DataNotReady as exc:
            print(f'[daily-leaders] 未就绪：{exc}；本次不生成、不推送', file=sys.stderr)
            if not wait:
                raise
            current = now()
            check_window(date, current)
            deadline = current.replace(hour=23, minute=0, second=0, microsecond=0)
            seconds = min(RETRY_SECONDS, (deadline - current).total_seconds())
            print(f'[daily-leaders] {int(seconds)}秒后重新检查，截止当晚23:00', file=sys.stderr)
            sleep(seconds)
