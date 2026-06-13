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
from utils.price_limit import is_dual_board


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


def _dual_board_accelerators(registry, date: str) -> tuple[list[dict], bool]:
    """双创(20cm) 涨幅≥15% 的加速票（鞠磊「20cm 涨15%+」即加速，不必全 20% 涨停）。

    全市场日涨跌（get_market_daily_changes）→ 过滤 双创前缀 + pct_chg≥阈值。返回 ([{code,name}], ok)；
    含全涨停(20%)的票也会进，与涨停榜按裸码去重。provider 失败 → ([], False)，由上层记 source_errors。
    """
    r = registry.call("get_market_daily_changes", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return [], False
    out = []
    for row in r.data:
        # 行级防御：单条脏行（非 dict / 缺码 / pct 非数）跳过，不让全市场 feed 一条坏数据
        # 崩掉整个日跑（result 级失败已兜底，行级也必须容错；对齐监管采集的防御解析）。
        if not isinstance(row, dict):
            continue
        code = row.get("ts_code") or row.get("code")
        if not code:
            continue
        try:
            pct = float(row.get("pct_chg"))
        except (TypeError, ValueError):
            continue
        if is_dual_board(str(code)) and pct >= C.ACCEL_DUAL_BOARD_MIN_PCT:
            out.append({"code": str(code).strip(), "name": row.get("name", "")})
    return out, True


def run_daily(conn: sqlite3.Connection, registry, date: str, *,
              sectors=None, top_k: int = C.DEFAULT_TOP_K_SECTORS, range_start: str | None = None) -> dict:
    main_sectors, degraded = _main_sectors(conn, date, top_k, sectors)
    start = range_start or _lookback_start(date)

    lu = registry.call("get_limit_up_list", date)
    lu_ok = getattr(lu, "success", False)
    _raw_limit = (lu.data or {}).get("stocks", []) if lu_ok else []
    # 行级防御：涨停榜可能 stocks=None / 混入非 dict / 缺码脏行 → 规整为合法 dict 列表，
    # 否则后续 limit_bares/candidate_map 的 .split 会崩掉整个日跑（与 market_changes 同等容错）。
    limit_stocks = ([s for s in _raw_limit if isinstance(s, dict) and s.get("code")]
                    if isinstance(_raw_limit, list) else [])
    sw = registry.call("get_stock_sw_industry_map")
    sw_ok = getattr(sw, "success", False) and isinstance(sw.data, dict)
    sw_map = sw.data if sw_ok else {}
    # AkShare 降级回裸码(600552)、Tushare sw_map 键是 ts_code(600552.SH)；建裸码副索引兜底匹配，
    # 否则降级源的涨停股会因 sw_map miss → 未分类 → 被主线交集静默漏掉。
    sw_by_bare = {k.split(".")[0]: v for k, v in sw_map.items()}

    # 候选源扩并：双创(20cm) 涨幅≥15% 的加速票（鞠磊「20cm 涨15%+」），sw 可用时才取（要映射主线）。
    dual_accel, dual_ok = ([], True)
    if sw_ok:
        dual_accel, dual_ok = _dual_board_accelerators(registry, date)

    # 发现链路 provider 失败显式记账：否则「链路断了」会伪装成「今日无候选」，运营无法区分。
    source_errors = []
    if not lu_ok:
        source_errors.append("limit_up")
    if not sw_ok:
        source_errors.append("sw_map")
    if not dual_ok:
        source_errors.append("market_changes")

    summary = {
        "date": date, "limit_up": len(limit_stocks), "main_sectors": sorted(main_sectors),
        "degraded_main": degraded, "candidates": 0,
        "entered": [], "refreshed": [], "exited": [], "in_pool_signals": [],
        "data_errors": [], "source_errors": source_errors,
    }

    # 涨停裸码集：用于区分入池触发类型（涨停 vs 双创15%加速），写入 signal_json 供池/报告辨识。
    limit_bares = {(s.get("code") or "").split(".")[0] for s in (limit_stocks if sw_ok else [])}

    # 合并候选：涨停 ∪ 双创@15%，按裸码去重（涨停源字段更全，后写覆盖）。
    candidate_map: dict[str, dict] = {}
    for st in dual_accel:
        b = (st.get("code") or "").split(".")[0]
        if b:
            candidate_map[b] = st
    for st in (limit_stocks if sw_ok else []):
        b = (st.get("code") or "").split(".")[0]
        if b:
            candidate_map[b] = st

    entered_codes = set()
    # Pass 1 — 发现：主线∩(涨停∪双创加速) + 首次加速 + 缓涨。sw 映射不可用时跳过发现（无法可靠
    # 映射主线，不静默把全部当未分类过滤；失败已记 source_errors，Pass2 维护仍照常）。
    for st in candidate_map.values():
        code_raw = st.get("code")
        bare = code_raw.split(".")[0]          # 池内唯一身份（裸码），防 ts_code/裸码建重复 active
        sw_entry = sw_map.get(code_raw) or sw_by_bare.get(bare) or {}
        sw_l2 = sw_entry.get("sw_l2", UNCLASSIFIED)
        if sw_l2 not in main_sectors:          # 涨停 ∩ 主线
            continue
        # candidates = 主线∩涨停（进入检测阶段的数量），与 entered（过检入池）故意分开：
        # 二者之差 = 漏斗的检测器过滤层，是设计意图而非统计错误。
        summary["candidates"] += 1
        bars = _bars(registry, bare, start, date)
        if not bars:                            # 行情拉取失败/空：记错误，不误判为"无信号"
            summary["data_errors"].append(bare)
            continue
        first, fd = D.is_first_limit_up_acceleration(bars, bare, today_accelerated=True)
        gentle, gd = D.is_gentle_rise(bars, bare)
        if not (first and gentle):
            continue
        name = st.get("name") or sw_entry.get("name", "")
        # 入池触发：涨停（主板/双创全板）vs 双创15%加速（未到全涨停）。first_limit_date 列名沿用，
        # 语义已泛化为「首次加速日」（board-aware）；trigger 落 signal_json 让两类入池可辨识。
        trigger = "涨停" if bare in limit_bares else "双创15%加速"
        res = pool.record(conn, code=bare, name=name, sw_l2=sw_l2,
                          first_limit_date=date, date=date,
                          signal_json={"first_limit": fd, "gentle": gd, "entry_trigger": trigger})
        entered_codes.add(bare)
        if res in ("entered", "refreshed"):     # 据实汇报，stale(更早日期 no-op) 不报转换
            summary[res].append(bare)

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
            if pool.mark_exited(conn, code, date=date, reason=_break_reason(bd)):
                summary["exited"].append(code)   # 据实汇报：旧日期 no-op 不报退出
            continue
        shrink, sd = D.is_volume_shrink_pullback(bars)
        near, nd = D.is_near_ma5(bars)
        far, fdd = D.is_far_from_ma5(bars)
        # 维护会整体重写 last_signal_json → 显式带上入池触发，否则次日就丢失「涨停/双创15%」辨识。
        prev_trigger = (r.get("last_signal") or {}).get("entry_trigger")
        pool.touch(conn, code, date=date,
                   signal_json={"shrink_pullback": sd, "near_ma5": nd, "overheat": fdd,
                                "trend": bd, "entry_trigger": prev_trigger})
        summary["in_pool_signals"].append({
            "code": code, "shrink_pullback_buy": shrink, "near_ma5": near, "overheat": far,
        })

    return summary
