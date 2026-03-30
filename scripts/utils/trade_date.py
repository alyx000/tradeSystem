"""交易日工具（供盘前/晚间任务复用，避免 main 与 collectors 循环导入）"""
from __future__ import annotations

from datetime import datetime, timedelta


def get_prev_trade_date(registry, today: str) -> str:
    """
    向前最多查找 7 天，找到最近一个交易日（不含 today）。
    若 provider 不可用则简单回退到昨天。
    """
    for delta in range(1, 8):
        candidate = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=delta)).strftime("%Y-%m-%d")
        r = registry.call("is_trade_day", candidate)
        if r.success and r.data:
            return candidate
    return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
