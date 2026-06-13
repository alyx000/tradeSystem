"""market-timing 盘后扫描编排（EOD 只读派生信号）。

run_daily：逐指数拉日线 → 时间周期(swing+斐波那契变盘点) + 底分型生命周期推进
→ 叠加市场级客观上下文(共振数/成交额地量分位/跌停家数/涨跌家数) → upsert 落库。
全标 [判断] 由渲染层负责；本层只算事实与状态，不出方向/价位。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import constants as C
from . import detectors as D
from . import repo
from .fetch import fetch_index_daily


def _date_minus_days(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def _bars_through(registry, code: str, date: str):
    """拉该指数 [date-lookback, date] 升序日线（≤date）。返回 (bars, source)。"""
    start = _date_minus_days(date, C.RANGE_LOOKBACK_DAYS)
    res = fetch_index_daily(registry, code, start, date)
    if not res.success or not res.data:
        return [], (res.source if res else None)
    bars = [b for b in res.data if b.get("trade_date") and b["trade_date"] <= date]
    bars.sort(key=lambda b: b["trade_date"])
    return bars, res.source


def _resolve_pivot(bars: list[dict], code: str, pivot_overrides: dict | None) -> dict | None:
    """起算 swing 拐点：有手工覆盖(--pivot-date)用之，否则自动检测(D3 hybrid)。

    手工覆盖的 type 仅供显示（其后整体走低记 high、走高记 low）；指定日不在区间 → 无 pivot。
    """
    if pivot_overrides and code in pivot_overrides:
        pdate = pivot_overrides[code]
        for i, b in enumerate(bars):
            if b["trade_date"] == pdate:
                ptype = "high" if bars[-1]["close"] < b["close"] else "low"
                price = b["high"] if ptype == "high" else b["low"]
                return {"index": i, "date": pdate, "price": price, "type": ptype, "manual": True}
        return None
    return D.find_swing_pivot(bars)


def _time_cycle(bars: list[dict], code: str, pivot_overrides: dict | None) -> tuple[dict | None, dict]:
    """swing 拐点(自动/手工) + 斐波那契变盘点判定。返回 (pivot, turning_point)。"""
    pivot = _resolve_pivot(bars, code, pivot_overrides)
    if not pivot:
        return None, {"day_count": None, "hit": None, "near": None}
    dc = D.fib_day_count(bars, pivot["date"])
    return pivot, D.fib_turning_point(dc)


def _market_amount(bars_by_code: dict, date: str) -> tuple[float | None, float | None]:
    """两市成交额(亿) + 近 N 交易日分位（地量识别）。amount 单位千元→亿。

    仅统计「完整两市日」：某日须 MARKET_AMOUNT_INDICES 全部有 amount 才计入，
    避免单侧指数缺当日数据时把半截成交额当两市总额静默落库。目标日不完整 → 返回 None。
    """
    required = set(C.MARKET_AMOUNT_INDICES)
    per_date: dict[str, dict[str, float]] = {}
    for code in required:
        for b in bars_by_code.get(code, []):
            amt = b.get("amount")
            if amt is None:
                continue
            per_date.setdefault(b["trade_date"], {})[code] = amt
    complete = {d: sum(m.values()) for d, m in per_date.items() if required <= set(m)}
    if date not in complete:
        return None, None
    today_total = complete[date]
    window_dates = sorted(d for d in complete if d <= date)[-C.AMOUNT_PCTILE_WINDOW:]
    totals = [complete[d] for d in window_dates]
    pctile = sum(1 for t in totals if t <= today_total) / len(totals) if totals else None
    return round(today_total / C.QIANYUAN_PER_YI, 1), (round(pctile, 3) if pctile is not None else None)


def _advance_decline(registry, date: str) -> tuple[int | None, int | None]:
    res = registry.call("get_market_daily_changes", date)
    if res.success and isinstance(res.data, dict):
        return res.data.get("advance"), res.data.get("decline")
    return None, None


def _limit_down_count(registry, date: str) -> int | None:
    res = registry.call("get_limit_down_list", date)
    if res.success and isinstance(res.data, list):
        return len(res.data)
    return None


def run_daily(conn: sqlite3.Connection, registry, date: str, *, dry_run: bool = False,
              indices=None, pivot_overrides: dict | None = None) -> dict:
    """盘后扫描一日。dry_run=True 时不落库（内存副本，历史校准用）。

    pivot_overrides: {index_code: pivot_date} 手工指定 swing 起算点（D3 hybrid）。
    返回结构化结果。
    """
    index_list = indices if indices is not None else C.INDEX_LIST
    bars_by_code: dict[str, list] = {}
    per_index: list[dict] = []
    turning_points: list[dict] = []

    for idx in index_list:
        code, name = idx["code"], idx.get("name", idx["code"])
        bars, source = _bars_through(registry, code, date)
        bars_by_code[code] = bars
        # 当日无数据（数据未就绪/指数无该日）→ 跳过，不写空行
        if not bars or bars[-1].get("trade_date") != date:
            per_index.append({"code": code, "name": name, "skipped": True, "reason": "no_data_for_date", "source": source})
            turning_points.append({"hit": None, "near": None})
            continue
        pivot, tp = _time_cycle(bars, code, pivot_overrides)
        turning_points.append(tp)
        fractal = D.evaluate_fractal_status(bars)  # 无状态：从 bars 直接推导，不依赖历史行
        per_index.append({"code": code, "name": name, "skipped": False, "source": source,
                          "pivot": pivot, "tp": tp, "fractal": fractal})

    resonance = D.count_resonance(turning_points)
    amount_yi, amount_pctile = _market_amount(bars_by_code, date)
    advance, decline = _advance_decline(registry, date)
    limit_down = _limit_down_count(registry, date)

    written: list[dict] = []
    for item in per_index:
        if item.get("skipped"):
            continue
        pivot, tp, fr = item["pivot"], item["tp"], item["fractal"]
        fractal_json = (
            json.dumps({k: fr[k] for k in ("low_date", "low_price", "left_high", "right_high", "right_date")})
            if fr["status"] != "none" else None
        )
        row = {
            "trade_date": date, "index_code": item["code"], "index_name": item["name"],
            "swing_pivot_date": pivot["date"] if pivot else None,
            "swing_pivot_type": pivot["type"] if pivot else None,
            "swing_pivot_price": pivot["price"] if pivot else None,
            "fib_day_count": tp.get("day_count"), "fib_hit": tp.get("hit"), "fib_near": tp.get("near"),
            "fractal_status": fr["status"], "fractal_low_date": fr["low_date"],
            "fractal_low_price": fr["low_price"], "fractal_confirm_date": fr["confirm_date"],
            "fractal_json": fractal_json,
            "resonance_count": resonance, "market_amount_yi": amount_yi, "amount_pctile_20d": amount_pctile,
            "limit_down_count": limit_down, "advance": advance, "decline": decline,
            "data_source": item["source"],
        }
        if not dry_run:
            repo.upsert_signal(conn, row)
        written.append(row)
    if not dry_run:
        conn.commit()

    return {
        "date": date,
        "signals": written,
        "skipped": [i for i in per_index if i.get("skipped")],
        "resonance_count": resonance,
        "context": {"market_amount_yi": amount_yi, "amount_pctile_20d": amount_pctile,
                    "advance": advance, "decline": decline, "limit_down_count": limit_down},
    }
