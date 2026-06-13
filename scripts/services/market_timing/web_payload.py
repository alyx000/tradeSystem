"""market-timing 复盘网站 payload 构建（只读，供盘面概览面板）。

build_daily_payload：某交易日 6 指数信号 + 市场级上下文（表格/卡片）。
build_history_payload：市场级序列（共振数 / 成交额地量分位 随时间，趋势图）。
全部读 market_timing_signal；市场级列在 scanner 落库时冗余写各行（同日各指数一致）。
"""
from __future__ import annotations

import sqlite3

from . import repo

# 表格用的逐指数字段（市场级列单独放 context，不在每行重复）
_SIGNAL_FIELDS = (
    "index_code", "index_name",
    "swing_pivot_date", "swing_pivot_type", "swing_pivot_price",
    "fib_day_count", "fib_hit", "fib_near",
    "fractal_status", "fractal_low_date", "fractal_low_price", "fractal_confirm_date",
)


def build_daily_payload(conn: sqlite3.Connection, date: str) -> dict:
    """某交易日 payload。无数据 → available=False（前端优雅显示"暂无"）。"""
    rows = repo.list_signals(conn, date=date)
    if not rows:
        return {"date": date, "available": False, "resonance_count": 0,
                "context": {}, "signals": []}
    head = rows[0]  # 市场级列冗余写各行，取首行即可
    context = {
        "market_amount_yi": head.get("market_amount_yi"),
        "amount_pctile_20d": head.get("amount_pctile_20d"),
        "advance": head.get("advance"),
        "decline": head.get("decline"),
        "limit_down_count": head.get("limit_down_count"),
    }
    signals = [{k: r.get(k) for k in _SIGNAL_FIELDS} for r in rows]
    return {
        "date": date,
        "available": True,
        "resonance_count": head.get("resonance_count") or 0,
        "context": context,
        "signals": signals,
    }


def build_history_payload(conn: sqlite3.Connection, days: int) -> dict:
    """市场级序列（按日去重，升序）。共振数 + 成交额地量分位随时间。"""
    days = max(1, min(days, 120))
    # 市场级列同日各指数一致 → GROUP BY trade_date 取一份；DESC 取最近 days 天后升序
    cur = conn.execute(
        "SELECT trade_date, "
        "       MAX(resonance_count) AS resonance_count, "
        "       MAX(amount_pctile_20d) AS amount_pctile_20d "
        "FROM market_timing_signal GROUP BY trade_date "
        "ORDER BY trade_date DESC LIMIT ?",
        (days,),
    )
    rows = cur.fetchall()
    series = [
        {"date": d, "date_short": d[5:], "resonance_count": rc, "amount_pctile_20d": pct}
        for (d, rc, pct) in reversed(rows)  # 升序便于折线时间轴
    ]
    return {"requested_days": days, "series": series}
