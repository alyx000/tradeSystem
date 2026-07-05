"""MA4 拐头 + 成交额均线突破检测器（纯函数）。"""
from __future__ import annotations

import math
from typing import Iterable

from services.ma_breakout import constants as C


def _finite_float(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _series(bars: list[dict], key: str) -> list[float | None]:
    return [_finite_float(b.get(key)) for b in (bars or [])]


def _ma(values: list[float | None], n: int, end: int | None = None) -> float | None:
    if n <= 0:
        return None
    end = len(values) if end is None else end
    start = end - n
    if start < 0:
        return None
    window = values[start:end]
    if len(window) != n or any(v is None for v in window):
        return None
    return sum(window) / n


def _detail_key(prefix: str, window: int) -> str:
    return f"{prefix}{window}"


def is_ma_turning_up(bars: list[dict], period: int = C.MA_PERIOD) -> tuple[bool, dict]:
    """MA 今日上行，且上拐前至少两根 MA 连续下行。"""
    closes = _series(bars, "close")
    required = period + 3
    if len(closes) < required:
        return False, {"insufficient_history": True, "required_bars": required, "available_bars": len(closes)}
    ma_today = _ma(closes, period)
    ma_prev = _ma(closes, period, len(closes) - 1)
    ma_prev2 = _ma(closes, period, len(closes) - 2)
    ma_prev3 = _ma(closes, period, len(closes) - 3)
    detail = {
        "ma_today": ma_today,
        "ma_prev": ma_prev,
        "ma_prev2": ma_prev2,
        "ma_prev3": ma_prev3,
        "insufficient_history": ma_today is None or ma_prev is None or ma_prev2 is None or ma_prev3 is None,
    }
    if detail["insufficient_history"]:
        return False, detail
    return ma_today > ma_prev and ma_prev < ma_prev2 and ma_prev2 < ma_prev3, detail


def is_amount_breakout(
    bars: list[dict],
    windows: Iterable[int] = C.DEFAULT_AMOUNT_WINDOWS,
) -> tuple[bool, dict]:
    """今日成交额同时大于每条配置的成交额均线。"""
    windows = tuple(int(w) for w in windows)
    amounts = _series(bars, "amount")
    required = max(windows) if windows else 0
    if not windows or len(amounts) < required:
        return False, {"insufficient_history": True, "required_bars": required, "available_bars": len(amounts)}
    today = amounts[-1]
    detail = {"today_amount": today, "insufficient_history": today is None}
    if today is None:
        return False, detail
    matched = True
    for window in windows:
        avg = _ma(amounts, window)
        detail[_detail_key("amount_ma", window)] = avg
        if avg is None:
            detail["insufficient_history"] = True
            matched = False
        elif today <= avg:
            matched = False
    return matched, detail


def match_pattern(
    bars: list[dict],
    *,
    target_date: str,
    windows: Iterable[int] = C.DEFAULT_AMOUNT_WINDOWS,
    ma_period: int = C.MA_PERIOD,
) -> tuple[bool, dict]:
    """组合条件：最后一根必须是目标日，且 MA 拐头与成交额突破同时成立。"""
    if not bars:
        return False, {"insufficient_history": True}
    last_date = bars[-1].get("trade_date")
    if last_date != target_date:
        return False, {"stale_last_bar": True, "last_trade_date": last_date, "target_date": target_date}
    ma_ok, ma_detail = is_ma_turning_up(bars, ma_period)
    amount_ok, amount_detail = is_amount_breakout(bars, windows)
    return ma_ok and amount_ok, {
        "ma": ma_detail,
        "amount": amount_detail,
        "stale_last_bar": False,
    }
