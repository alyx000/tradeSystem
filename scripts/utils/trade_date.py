"""交易日工具（供盘前/晚间任务复用，避免 main 与 collectors 循环导入）"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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


def is_trade_day(date: str, *, conn: sqlite3.Connection | None = None, registry=None) -> bool | None:
    """统一的交易日判断：DB 优先 → provider 在线查 → 周末降级。

    返回 True/False 表示确定，None 表示无法判断（纯周末/工作日降级时仍返回 bool）。
    """
    from db import queries as Q

    if conn is not None:
        cached = Q.is_trade_day_from_db(conn, date)
        if cached is not None:
            return cached

    if registry is not None:
        try:
            r = registry.call("is_trade_day", date)
            if r.success and r.data is not None:
                if conn is not None:
                    try:
                        Q.upsert_trade_calendar(conn, [{"date": date, "is_open": int(r.data)}])
                    except Exception:
                        pass
                return bool(r.data)
        except Exception:
            pass

    dt = datetime.strptime(date, "%Y-%m-%d")
    return dt.weekday() < 5


def ensure_trade_calendar(conn: sqlite3.Connection, registry, year: int | None = None) -> int:
    """确保指定年份的交易日历已导入 DB。已存在则跳过，返回新增条数。"""
    from db import queries as Q

    if year is None:
        year = datetime.now().year

    if Q.trade_calendar_year_covered(conn, year):
        return 0

    date_str = f"{year}-06-15"
    try:
        r = registry.call("get_trade_calendar", date_str)
    except Exception as e:
        logger.warning("获取 %d 年交易日历失败: %s", year, e)
        return 0

    if not r.success or not r.data:
        logger.warning("获取 %d 年交易日历无数据: %s", year, r.error)
        return 0

    rows = []
    for item in r.data:
        cal_date_raw = str(item.get("cal_date", item.get("trade_date", "")))
        if len(cal_date_raw) == 8:
            cal_date = f"{cal_date_raw[:4]}-{cal_date_raw[4:6]}-{cal_date_raw[6:8]}"
        else:
            cal_date = cal_date_raw
        is_open = int(item.get("is_open", 0))
        rows.append({"date": cal_date, "is_open": is_open})

    if not rows:
        return 0

    count = Q.upsert_trade_calendar(conn, rows)
    logger.info("导入 %d 年交易日历 %d 条", year, count)
    return count
