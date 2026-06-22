"""margin_index_correlation 复盘网站 payload（只读，供复盘「1.大盘」section）。

build_daily_payload：某交易日两融×指数联动快照（背离/水位趋势/领先滞后/同步相关四维）。
读 margin_index_correlation_daily；无记录 → available=False（前端优雅显示「暂无」）。
"""
from __future__ import annotations

import sqlite3

from . import repo


def build_daily_payload(conn: sqlite3.Connection, date: str) -> dict:
    """某交易日 payload。无数据 → available=False。"""
    record = repo.get(conn, date)
    if not record:
        return {"date": date, "available": False}
    return {**record, "available": True}
