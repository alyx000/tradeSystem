"""tail-scan 纯指标函数（[事实]）：近N日涨幅 / MA / 连涨 / 距前高。

输入历史日线（不复权，末根=T-1）+ 实时现价；样本不足一律返回 None，不硬算。
"""
from __future__ import annotations


def gain_nd(hist_closes: list[float], live_price: float, n: int) -> float | None:
    if not hist_closes or len(hist_closes) < n or live_price is None:
        return None
    base = hist_closes[-n]
    if not base:
        return None
    return (live_price / base - 1) * 100


def ma(closes: list[float], n: int) -> float | None:
    if not closes or len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def above_all_ma(live_price: float, closes: list[float], windows=(5, 10, 20)) -> bool | None:
    if live_price is None:
        return None
    vals = [ma(closes, w) for w in windows]
    if any(v is None for v in vals):
        return None
    return all(live_price > v for v in vals)


def up_days(bars: list[dict]) -> int:
    count = 0
    for bar in reversed(bars or []):
        pct = bar.get("pct_chg")
        if pct is not None and pct > 0:
            count += 1
        else:
            break
    return count


def dist_to_high(live_price: float, highs: list[float], lookback: int) -> float | None:
    if not highs or live_price is None:
        return None
    window = highs[-lookback:]
    peak = max(window) if window else None
    if not peak:
        return None
    return (live_price / peak - 1) * 100


def broke_prior_high(live_price: float, highs: list[float], lookback: int) -> bool | None:
    if not highs or live_price is None:
        return None
    window = highs[-lookback:]
    return live_price > max(window) if window else None
