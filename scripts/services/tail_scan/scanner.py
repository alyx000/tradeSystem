"""tail-scan 筛选层（[事实]）：实时全市场快照 → 涨幅>7% ∩ 非ST ∩ 成交额>20亿。

无状态：不建池不落库。仅依赖实时快照（唯一盘中源），全部字段为 T 日实时。
"""
from __future__ import annotations

from utils import is_st_stock
from utils.price_limit import limit_pct_for


def _amount_yi(amount) -> float | None:
    try:
        return float(amount) / 1e8
    except (TypeError, ValueError):
        return None


def _is_limit_up(code: str, name: str, price: float, pre_close: float) -> bool:
    """涨停判定：price 触及 pre_close×(1+涨幅比例)。基金类(limit_pct_for=None)恒 False。"""
    pct = limit_pct_for(code, name)
    if pct is None or not pre_close or pre_close <= 0:
        return False
    from services.tail_scan import constants as C
    up_price = pre_close * (1 + pct / 100)
    return price >= up_price * (1 - C.LIMIT_UP_EPSILON)


def _close_pos(price, high, low) -> float | None:
    try:
        span = high - low
        return round((price - low) / span, 4) if span > 0 else None
    except (TypeError, ValueError):
        return None


def _amplitude(high, low, pre_close) -> float | None:
    try:
        return round((high - low) / pre_close * 100, 2) if pre_close else None
    except (TypeError, ValueError):
        return None


def filter_quotes(quotes: list[dict], *, min_pct: float, min_amount_yi: float) -> list[dict]:
    """三条件筛选 + 尾盘强度快照。pct_chg/amount 缺失或 ST 一律剔除。"""
    out = []
    for q in quotes or []:
        pct = q.get("pct_chg")
        amt_yi = _amount_yi(q.get("amount"))
        name = q.get("name", "")
        if pct is None or amt_yi is None:
            continue
        if pct <= min_pct or amt_yi <= min_amount_yi:
            continue
        if is_st_stock(name):
            continue
        code, price = q.get("code", ""), q.get("price")
        high, low, pre = q.get("high"), q.get("low"), q.get("pre_close")
        out.append({
            "code": code, "name": name, "price": price, "pct_chg": pct,
            "amount_yi": round(amt_yi, 2), "open": q.get("open"),
            "high": high, "low": low, "pre_close": pre,
            "is_limit_up": _is_limit_up(code, name, price, pre),
            "close_pos": _close_pos(price, high, low),
            "amplitude": _amplitude(high, low, pre),
        })
    return out


def _all_codes(registry, date: str) -> list[str]:
    r = registry.call("get_stock_basic_list", date)
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        return []
    return [row.get("ts_code") for row in r.data if row.get("ts_code")]


def scan(registry, date: str, *, min_pct: float, min_amount_yi: float) -> dict:
    """编排：全市场码 → 实时快照 → 三条件筛选。码源或行情源失败 → source_failed。"""
    codes = _all_codes(registry, date)
    if not codes:
        return {"status": "source_failed", "quote_date": date, "quote_time": "",
                "candidates": [], "scanned": 0, "matched": 0,
                "error": "全市场代码清单获取失败（get_stock_basic_list）"}
    # sina _fetch_raw 任一分片失败即整批 error（~7 片，单点脆弱）。14:30 单次触发下补一次重试，
    # 仍失败才 source_failed（launchd 单次触发无二次机会，重试是最低成本兜底）。
    r = registry.call("get_realtime_quotes", codes)
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        r = registry.call("get_realtime_quotes", codes)  # 重试一次
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        return {"status": "source_failed", "quote_date": date, "quote_time": "",
                "candidates": [], "scanned": len(codes), "matched": 0,
                "error": f"实时行情获取失败（含重试）：{getattr(r, 'error', '未知')}"}
    quotes = r.data
    cands = filter_quotes(quotes, min_pct=min_pct, min_amount_yi=min_amount_yi)
    qd = quotes[0].get("quote_date", date) if quotes else date
    qt = quotes[0].get("quote_time", "") if quotes else ""
    return {"status": "ok", "quote_date": qd, "quote_time": qt,
            "candidates": cands, "scanned": len(quotes), "matched": len(cands),
            "error": None}
