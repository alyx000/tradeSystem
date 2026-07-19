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


def run_backfill(conn: sqlite3.Connection, registry, provider, start: str, end: str) -> dict:
    """两阶段回填:①逐码分片采集,内存按日期聚合;②逐日守卫总额+share_pct,整日一次写。

    已有快照的日期跳过(daily 采的行含 proxy,回填行不含,不可覆盖)。
    截断异常向上抛不吞(疑似截断的数据宁可整体失败也不落半截)。"""
    l1 = provider._ensure_sw_l1_codes() or set()
    l2 = provider._ensure_sw_l2_codes() or set()
    code_meta = [(c, "L1") for c in sorted(l1)] + [(c, "L2") for c in sorted(l2)]
    by_date: dict = {}
    codes_failed: list[str] = []
    for code, level in code_meta:
        try:
            bars = collector.fetch_code_history(provider, code, start, end)
        except collector.BackfillTruncationError:
            raise
        except Exception as e:  # 单码失败记账继续,不拖垮全量
            logger.warning("[sector-crowding backfill] %s 失败: %s", code, e)
            codes_failed.append(code)
            continue
        for bar in bars:
            by_date.setdefault(bar["date"], []).append(
                {"code": code, "name": code, "level": level,
                 "close": bar["close"], "amount_billion": bar["amount_billion"]})
    # L1 合成与 daily 同一分支逻辑(Explore review 中1):回填若无 L1 行而 parent_map 可靠,
    # 逐日合成 L1,否则合成 L1 永无历史序列 → 分位/双高对 L1 长期失效
    parent_map = {} if l1 else (provider._ensure_sw_l1_parent_map() or {})
    written = skipped = 0
    for d in sorted(by_date):
        if repo.get_snapshot(conn, d) is not None:
            skipped += 1
            continue
        total, _src = collector.fetch_market_total(conn, registry, d)
        sectors = by_date[d]
        has_l1 = any(s["level"] == "L1" for s in sectors)
        if not has_l1 and parent_map:
            sectors = sectors + collector.synthesize_l1(sectors, parent_map)
            l1_status = "synthesized"
        else:
            l1_status = "native" if has_l1 else "missing"
        for s in sectors:
            s["share_pct"] = analyzer.compute_share_pct(s.get("amount_billion"), total)
        repo.save_snapshot(conn, {
            "date": d, "market_total_billion": total, "sectors": sectors,
            "proxy": None, "meta": {"backfilled": True, "l1_status": l1_status}})
        written += 1
    return {"dates_written": written, "dates_skipped": skipped, "codes_failed": codes_failed}
