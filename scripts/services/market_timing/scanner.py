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


def _time_cycle(bars: list[dict]) -> tuple[dict | None, dict]:
    """swing 拐点 + 斐波那契变盘点判定。返回 (pivot, turning_point)。"""
    pivot = D.find_swing_pivot(bars)
    if not pivot:
        return None, {"day_count": None, "hit": None, "near": None}
    dc = D.fib_day_count(bars, pivot["date"])
    return pivot, D.fib_turning_point(dc)


def _advance_fractal(prior: dict | None, bars: list[dict]) -> dict:
    """据上一交易日同指数行 prior + 今日 bars 推进底分型状态。

    none/invalid → 检测新成型(forming)；forming → 尝试确认(confirmed)或维持；
    forming/confirmed 跌破结构低点 → invalid；confirmed 持有 → 维持。
    返回 {status, low_date, low_price, confirm_date, json}。
    """
    today = bars[-1] if bars else {}
    close = today.get("close")
    prior_status = (prior or {}).get("fractal_status", "none")
    prior_json = (prior or {}).get("fractal_json")
    stored = json.loads(prior_json) if prior_json else None
    prior_confirm = (prior or {}).get("fractal_confirm_date")

    def _pack(status, info, confirm_date):
        return {
            "status": status,
            "low_date": (info or {}).get("low_date"),
            "low_price": (info or {}).get("low_price"),
            "confirm_date": confirm_date,
            "json": json.dumps(info) if info else None,
        }

    # 结构跌破 → invalid
    if (prior_status in ("forming", "confirmed") and stored and close is not None
            and stored.get("low_price") is not None and close < stored["low_price"]):
        return _pack("invalid", stored, prior_confirm)

    # forming → 尝试确认
    if prior_status == "forming" and stored:
        ok, _ = D.is_fractal_confirmed(bars, stored)
        if ok:
            return _pack("confirmed", stored, today.get("trade_date"))
        return _pack("forming", stored, None)

    # confirmed 持有（结构未破）→ 维持
    if prior_status == "confirmed" and stored:
        return _pack("confirmed", stored, prior_confirm)

    # none/invalid → 检测新成型
    formed, info = D.is_bottom_fractal(bars)
    if formed:
        return _pack("forming", info, None)
    return _pack("none", None, None)


def _market_amount(bars_by_code: dict, date: str) -> tuple[float | None, float | None]:
    """两市成交额(亿) + 近 N 交易日分位（地量识别）。amount 单位千元→亿。"""
    series: dict[str, float] = {}
    for code in C.MARKET_AMOUNT_INDICES:
        for b in bars_by_code.get(code, []):
            amt = b.get("amount")
            if amt is None:
                continue
            series[b["trade_date"]] = series.get(b["trade_date"], 0.0) + amt
    if date not in series:
        return None, None
    today_total = series[date]
    window_dates = sorted(d for d in series if d <= date)[-C.AMOUNT_PCTILE_WINDOW:]
    totals = [series[d] for d in window_dates]
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


def run_daily(conn: sqlite3.Connection, registry, date: str, *, dry_run: bool = False, indices=None) -> dict:
    """盘后扫描一日。dry_run=True 时不落库（内存副本，历史校准用）。返回结构化结果。"""
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
        pivot, tp = _time_cycle(bars)
        turning_points.append(tp)
        prior = repo.get_prior_signal(conn, code, date)
        fractal = _advance_fractal(prior, bars)
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
        row = {
            "trade_date": date, "index_code": item["code"], "index_name": item["name"],
            "swing_pivot_date": pivot["date"] if pivot else None,
            "swing_pivot_type": pivot["type"] if pivot else None,
            "swing_pivot_price": pivot["price"] if pivot else None,
            "fib_day_count": tp.get("day_count"), "fib_hit": tp.get("hit"), "fib_near": tp.get("near"),
            "fractal_status": fr["status"], "fractal_low_date": fr["low_date"],
            "fractal_low_price": fr["low_price"], "fractal_confirm_date": fr["confirm_date"],
            "fractal_json": fr["json"],
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
