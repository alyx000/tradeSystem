"""sector_crowding 编排层（只编排，不实现）。

run_daily：采集→share_pct→落库→读回历史现算分位→渲染（None=无数据，调用方不推送）。
run_report/run_trend：只读。分位/双高永不落库（spec v2 关键设计 1）。
"""
from __future__ import annotations

import logging
import re
import sqlite3

from utils.trade_date import is_non_trading_day

from . import analyzer, collector, formatter, repo

logger = logging.getLogger(__name__)

HISTORY_DAYS = 1900  # 分位回看窗口（≈2019 起全量交易日数）
DEFAULT_BACKFILL_START = "2019-01-01"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        # 读回渲染(而非直接用内存 record):顺带校验落库序列化,渲染的即落库的。
        # 调用方契约:返回 None 仅表示"未落库未推送"(非交易日/无数据两分支);
        # save 成功后本路径恒返回 str,若未来 _render 新增可返 None 的分支须重审此契约
        return _render(conn, date, repo.get_snapshot(conn, date))
    return _render(conn, date, record)


def _render(conn: sqlite3.Connection, date: str, today_record: dict | None) -> str | None:
    """「精简历史 + 当日全量覆盖」拼接契约的单点实现。

    get_recent 是精简列(历史行 proxy/meta 恒 None,见 repo docstring),当日行一律用
    today_record(fresh 采集或 get_snapshot 全量行)顶替,否则代理段/meta 标注静默消失。"""
    history = [h for h in repo.get_recent(conn, date, HISTORY_DAYS) if h["date"] != date]
    if today_record is not None:
        history.append(today_record)
    view = analyzer.build_view(history, date) if history else None
    if view is None:
        return None
    view["proxy"] = today_record.get("proxy") if today_record else None
    return formatter.format_report(view)


def run_report(conn: sqlite3.Connection, date: str) -> str:
    md = _render(conn, date, repo.get_snapshot(conn, date))
    return md if md is not None else f"{date} 无拥挤度快照(先跑 sector-crowding daily)。"


def run_trend(conn: sqlite3.Connection, date: str, sector: str, days: int = 60) -> str:
    return formatter.format_trend(repo.get_recent(conn, date, days), sector)


def run_backfill(conn: sqlite3.Connection, registry, provider, start: str, end: str) -> dict:
    """两阶段回填:①采集层聚合(collector.fetch_history_by_date);②逐日守卫总额+
    share_pct+L1 状态机,整日一次写。

    已有快照的日期跳过(daily 采的行含 proxy,回填行不含,不可覆盖);
    跳过判定用日期集合(免 ~1800 次单行 JSON 反序列化)。L1 合成与 daily 走同一
    collector.resolve_l1 真源(回填不合成会导致合成 L1 永无历史分位)。
    注意:回填勿与盘后 daily 任务同时段运行——existing 集合是开跑时快照,期间 daily
    新落的行会被覆盖(并发窗口,操作约束规避)。"""
    if not (_ISO_DATE.match(start) and _ISO_DATE.match(end)):
        # get_existing_dates 的 BETWEEN 与存储列做字符串比较,YYYYMMDD 入参会让
        # 跳过判定恒空集 → 已有 daily 行被回填静默覆盖(门1 review 角度B)
        raise ValueError(f"run_backfill: start/end 须为 YYYY-MM-DD 格式,得到 {start!r}/{end!r}")
    by_date, codes_failed = collector.fetch_history_by_date(provider, start, end)
    if codes_failed:
        # fail-closed(codex 门2 高):部分码缺失仍落库会留下断链/低偏历史,且日期被
        # existing 集合锁死重跑不自愈。整体中止 → 重跑即重试,自愈语义单一。
        raise RuntimeError(
            f"sector-crowding backfill: {len(codes_failed)} 个码采集失败,中止不落库"
            f"(重跑即重试): {','.join(codes_failed[:10])}")
    existing = repo.get_existing_dates(conn, start, end)
    written = skipped = null_total = 0
    for d in sorted(by_date):
        if d in existing:
            skipped += 1
            continue
        total, _src = collector.fetch_market_total(conn, registry, d)
        if total is None:
            # NULL 是设计语义(spec 严重3:坏源落 NULL 不落假值),该日 share 缺席分位样本
            # 不产生假值;显式计数消除"覆盖率假信心",完成后由 CLI 呈现给用户抽查
            null_total += 1
        sectors, l1_status = collector.resolve_l1(
            by_date[d], provider._ensure_sw_l1_parent_map)
        for s in sectors:
            s["share_pct"] = analyzer.compute_share_pct(s.get("amount_billion"), total)
        repo.save_snapshot(conn, {
            "date": d, "market_total_billion": total, "sectors": sectors,
            "proxy": None, "meta": {"backfilled": True, "l1_status": l1_status}})
        written += 1
    return {"dates_written": written, "dates_skipped": skipped,
            "dates_null_total": null_total, "codes_failed": []}
