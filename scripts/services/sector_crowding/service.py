"""sector_crowding 编排层（只编排，不实现）。

run_daily：采集→share_pct→落库→读回历史现算分位→渲染（None=无数据，调用方不推送）。
run_report/run_trend：只读。分位/双高永不落库（spec v2 关键设计 1）。
"""
from __future__ import annotations

import logging
import sqlite3

from utils.trade_date import is_non_trading_day

from . import analyzer, collector, formatter, repo

logger = logging.getLogger(__name__)

HISTORY_DAYS = 1900  # 分位回看窗口（≈2019 起全量交易日数）
DEFAULT_BACKFILL_START = "2019-01-01"


def run_daily(conn: sqlite3.Connection, registry, provider, date: str, *,
              persist: bool = True) -> str | None:
    # 非交易日守卫下沉到 service(而非 CLI,Explore review 中3:CLI 层守卫无法单测)。
    # 与 sector_correlation 同语义:仅 persist 时守卫,dry-run 豁免(免守卫日历预取写真实库)。
    if persist and is_non_trading_day(conn, registry, date):
        logger.warning("⚠️ %s 为非交易日,跳过拥挤度采集(不落库、不推送)", date)
        return None
    fetched = collector.fetch_sector_daily(provider, date)
    if fetched is None:
        return None
    market_total, mt_source = collector.fetch_market_total(conn, registry, date)
    for s in fetched["sectors"]:
        s["share_pct"] = analyzer.compute_share_pct(s.get("amount_billion"), market_total)
    proxy = collector.fetch_proxy(registry, date)
    meta = dict(fetched["meta"])
    meta["market_total_source"] = mt_source
    if market_total is None:
        meta["missing_data"] = "market_total"
    record = {"date": date, "market_total_billion": market_total,
              "sectors": fetched["sectors"], "proxy": proxy, "meta": meta}
    if persist:
        repo.save_snapshot(conn, record)
        return run_report(conn, date)
    # dry-run：不落库。当日行一律用刚采集的 fresh record 顶替(库里可能有更早跑过的
    # 陈旧行;get_recent 是精简列,历史行无 proxy/meta——见 repo.get_recent docstring)
    history = [h for h in repo.get_recent(conn, date, HISTORY_DAYS) if h["date"] != date]
    history.append(record)
    view = analyzer.build_view(history, date)
    if view is None:
        return None
    view["proxy"] = record["proxy"]
    return formatter.format_report(view)


def run_report(conn: sqlite3.Connection, date: str) -> str:
    # 契约:get_recent 为精简列(历史行 proxy/meta 恒 None),当日全量必须 get_snapshot
    # 单行覆盖,否则报告的代理段/meta 标注段静默消失(门1 review 高优先级)
    history = repo.get_recent(conn, date, HISTORY_DAYS)
    snap = repo.get_snapshot(conn, date)
    if history and snap and history[-1]["date"] == date:
        history[-1] = snap
    view = analyzer.build_view(history, date) if history else None
    if view is None:
        return f"{date} 无拥挤度快照(先跑 sector-crowding daily)。"
    view["proxy"] = snap.get("proxy") if snap else None
    return formatter.format_report(view)


def run_trend(conn: sqlite3.Connection, date: str, sector: str, days: int = 60) -> str:
    return formatter.format_trend(repo.get_recent(conn, date, days), sector)
