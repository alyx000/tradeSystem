"""趋势主升漏斗编排（盘后 EOD 只读扫描 → 持久化观察池）。

数据流：涨停列表(get_limit_up_list) → 映射申万二级(get_stock_sw_industry_map)
→ ∩ 主线池(daily_volume_concentration Top-K ∪ --sectors) → 拉区间 OHLCV(get_stock_daily_range)
→ 检测器 → 入池/维护/退池(pool)。

两遍：
- Pass 1 发现：主线∩涨停 且 首次涨停加速 + 缓涨 → 入池（**不含贴MA5**：涨停日必远离 MA5）。
- Pass 2 维护：在池 active 股 → 趋势破坏则退池；否则 touch 并记回踩/见顶信号（贴MA5/缩量阴线买点/远离MA5）。

红线：只读观察清单，标 [判断]，不出价位、不写计划层。registry.call 走 capability，不耦合具体 provider。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from services.trend_leader import constants as C
from services.trend_leader import detectors as D
from services.trend_leader import pool
from services.volume_concentration import repo as vc_repo
from services.volume_concentration.aggregator import UNCLASSIFIED


def _lookback_start(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=C.RANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def _main_sectors(conn: sqlite3.Connection, date: str, top_k: int, sectors) -> tuple[set, bool]:
    """主线池 = 当日成交额集中度 Top-K 申万二级 ∪ 手工 --sectors；当日缺失回退最近一日（走 vc_repo）。"""
    rec = vc_repo.get_concentration(conn, date)
    degraded = False
    if rec is None:
        degraded = True
        recent = vc_repo.get_recent_concentration(conn, date, 1)  # <= date 的最近一条
        rec = recent[-1] if recent else None
    auto = []
    if rec and rec.get("sector_summary"):
        auto = [s["industry"] for s in rec["sector_summary"] if s.get("industry") != UNCLASSIFIED][:top_k]
    return set(auto) | set(sectors or []), degraded


def _bars(registry, code: str, start: str, date: str) -> list[dict]:
    r = registry.call("get_stock_daily_range", code, start, date)
    return r.data if getattr(r, "success", False) and isinstance(r.data, list) else []


def _break_reason(trend_detail: dict) -> str:
    if trend_detail.get("below_ma10"):
        return "收盘跌破MA10"
    if trend_detail.get("two_day_break"):
        return "连续2日跌破MA5且跌幅扩大"
    return "趋势破坏"


def run_daily(conn: sqlite3.Connection, registry, date: str, *,
              sectors=None, top_k: int = C.DEFAULT_TOP_K_SECTORS, range_start: str | None = None) -> dict:
    main_sectors, degraded = _main_sectors(conn, date, top_k, sectors)
    start = range_start or _lookback_start(date)

    lu = registry.call("get_limit_up_list", date)
    limit_stocks = (lu.data or {}).get("stocks", []) if getattr(lu, "success", False) else []
    sw = registry.call("get_stock_sw_industry_map")
    sw_map = sw.data if (getattr(sw, "success", False) and isinstance(sw.data, dict)) else {}
    # AkShare 降级回裸码(600552)、Tushare sw_map 键是 ts_code(600552.SH)；建裸码副索引兜底匹配，
    # 否则降级源的涨停股会因 sw_map miss → 未分类 → 被主线交集静默漏掉。
    sw_by_bare = {k.split(".")[0]: v for k, v in sw_map.items()}

    summary = {
        "date": date, "limit_up": len(limit_stocks), "main_sectors": sorted(main_sectors),
        "degraded_main": degraded, "candidates": 0,
        "entered": [], "refreshed": [], "exited": [], "in_pool_signals": [], "data_errors": [],
    }

    entered_codes = set()
    # Pass 1 — 发现：主线∩涨停 + 首次涨停 + 缓涨
    for st in limit_stocks:
        code = st.get("code")
        if not code:
            continue
        sw_entry = sw_map.get(code) or sw_by_bare.get(code.split(".")[0]) or {}
        sw_l2 = sw_entry.get("sw_l2", UNCLASSIFIED)
        if sw_l2 not in main_sectors:          # 涨停 ∩ 主线
            continue
        # candidates = 主线∩涨停（进入检测阶段的数量），与 entered（过检入池）故意分开：
        # 二者之差 = 漏斗的检测器过滤层，是设计意图而非统计错误。
        summary["candidates"] += 1
        bars = _bars(registry, code, start, date)
        if not bars:                            # 行情拉取失败/空：记错误，不误判为"无信号"
            summary["data_errors"].append(code)
            continue
        first, fd = D.is_first_limit_up_acceleration(bars, code, today_limit_up=True)
        gentle, gd = D.is_gentle_rise(bars, code)
        if not (first and gentle):
            continue
        name = st.get("name") or sw_entry.get("name", "")
        res = pool.record(conn, code=code, name=name, sw_l2=sw_l2,
                          first_limit_date=date, date=date,
                          signal_json={"first_limit": fd, "gentle": gd})
        entered_codes.add(code)
        summary["entered" if res == "entered" else "refreshed"].append(code)

    # Pass 2 — 维护：在池 active（未在 Pass1 处理）→ 退池 / 记信号
    for r in pool.list_pool(conn, status="active"):
        code = r["code"]
        if code in entered_codes:
            continue
        bars = _bars(registry, code, start, date)
        if not bars:                            # 行情缺失：不 touch 推进 last_seen/days、不退池，记错误
            summary["data_errors"].append(code)
            continue
        broken, bd = D.is_trend_broken(bars)
        if broken:
            pool.mark_exited(conn, code, date=date, reason=_break_reason(bd))
            summary["exited"].append(code)
            continue
        shrink, sd = D.is_volume_shrink_pullback(bars)
        near, nd = D.is_near_ma5(bars)
        far, fdd = D.is_far_from_ma5(bars)
        pool.touch(conn, code, date=date,
                   signal_json={"shrink_pullback": sd, "near_ma5": nd, "overheat": fdd, "trend": bd})
        summary["in_pool_signals"].append({
            "code": code, "shrink_pullback_buy": shrink, "near_ma5": near, "overheat": far,
        })

    return summary
