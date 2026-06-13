"""趋势主升检测器（纯函数）。

bar 约定：list[dict] 按 trade_date **升序**（最旧→最新），最后一根=今日；
字段至少含 close（部分检测器另需 open/vol/pre_close/pct_chg）。
每个检测器返回 `(matched: bool, detail: dict)`；历史不足时 matched=False 且 detail["insufficient_history"]=True。
"""
from __future__ import annotations

from services.trend_leader import constants as C
from utils.price_limit import is_dual_board, limit_pct_for


def ma(closes: list[float], n: int) -> float | None:
    """最近 n 个 close 的均值；不足 n 根或含 None 返回 None。"""
    if not closes or n <= 0 or len(closes) < n:
        return None
    window = closes[-n:]
    if any(c is None for c in window):
        return None
    return sum(window) / n


def _ma5_deviation(bars: list[dict]) -> tuple[float | None, float | None]:
    """返回 (ma5, deviation)；不足或 ma5=0 返回 (None, None)。deviation=(today_close-ma5)/ma5。"""
    closes = [b.get("close") for b in (bars or [])]
    m = ma(closes, 5)
    if m is None or m == 0:
        return None, None
    return m, (closes[-1] - m) / m


def is_near_ma5(bars: list[dict]) -> tuple[bool, dict]:
    """贴 MA5：|close - MA5| / MA5 <= 阈值（趋势延续特征）。"""
    m, dev = _ma5_deviation(bars)
    if m is None:
        return False, {"insufficient_history": True}
    return abs(dev) <= C.NEAR_MA5_MAX_DEVIATION, {
        "ma5": m, "deviation": dev, "insufficient_history": False,
    }


def is_far_from_ma5(bars: list[dict]) -> tuple[bool, dict]:
    """远离 MA5 见顶：(close - MA5) / MA5 >= 阈值（仅打了结提示，不退池）。"""
    m, dev = _ma5_deviation(bars)
    if m is None:
        return False, {"insufficient_history": True}
    return dev >= C.FAR_FROM_MA5_MIN_DEVIATION, {
        "ma5": m, "deviation": dev, "insufficient_history": False,
    }


def _limit_up_threshold(code: str, is_st: bool | None = None) -> float | None:
    """板块涨停的 pct_chg 检测阈值（板块比例×容差）；ETF 无固定涨停→None。在循环外算一次。"""
    pct = limit_pct_for(code, is_st=is_st)
    return pct * C.LIMIT_DETECT_FACTOR if pct is not None else None


def accel_threshold(code: str, is_st: bool | None = None) -> float | None:
    """加速达标的 pct_chg 阈值（board-aware）。鞠磊：主板涨停 / 双创涨15%+。
    双创(20cm) → ACCEL_DUAL_BOARD_MIN_PCT(15)；其余 → 涨停检测阈值（limit×容差）。
    板块判定复用 price_limit.is_dual_board（前缀单一真源，防口径漂移）。"""
    if is_dual_board(code):
        return C.ACCEL_DUAL_BOARD_MIN_PCT
    return _limit_up_threshold(code, is_st)


def _has_limit_up(bars: list[dict], threshold: float | None) -> bool:
    """窗口内是否存在涨停日（threshold=None 即无固定涨停品种 → False）。"""
    if threshold is None:
        return False
    for b in bars:
        p = b.get("pct_chg")
        if p is not None and p >= threshold:
            return True
    return False


def is_first_limit_up_acceleration(
    bars: list[dict], code: str, today_accelerated: bool, is_st: bool | None = None
) -> tuple[bool, dict]:
    """首次加速：今日加速达标（权威源传入；主板涨停 / 双创涨15%+）且近 60 交易日内除今日外无加速。

    「加速」board-aware（accel_threshold）：主板=涨停阈值，双创=15%。窗口内先前加速判定同口径。
    """
    if not today_accelerated:
        return False, {"today_accelerated": False, "insufficient_history": False}
    history = (bars or [])[:-1]  # 排除今日
    # 历史太短无法断言「近 60 日首次」（IPO/复牌/区间截断会把「未知」误判成「无涨停」事实）。
    # 部分反驳 codex「< 60 即 insufficient」：只要 ≥ MIN_BARS_FOR_SIGNAL 即可断言并标 partial_history，
    # 不强制满 60——否则会漏掉次新主升龙头（老师框架本就含新股），domain 上代价更大。
    if len(history) < C.MIN_BARS_FOR_SIGNAL:
        return False, {"today_accelerated": True, "lookback": len(history), "insufficient_history": True}
    window = history[-C.FIRST_LIMIT_LOOKBACK_DAYS:]
    prior = _has_limit_up(window, accel_threshold(code, is_st))
    return (not prior), {
        "today_accelerated": True, "prior_limit_in_window": prior,
        "lookback": len(window), "partial_history": len(window) < C.FIRST_LIMIT_LOOKBACK_DAYS,
        "insufficient_history": False,
    }


def is_gentle_rise(bars: list[dict], code: str, is_st: bool | None = None) -> tuple[bool, dict]:
    """缓涨：近 GENTLE_RISE_WINDOW 日累计涨幅 ∈[MIN,MAX]% 且窗口内（除今日）无涨停。"""
    bars = bars or []
    w = C.GENTLE_RISE_WINDOW
    if len(bars) < w:
        return False, {"insufficient_history": True}
    window = bars[-w:]
    closes = [b.get("close") for b in window]
    if any(c is None for c in closes) or not closes[0]:
        return False, {"insufficient_history": True}
    cum_pct = (closes[-1] - closes[0]) / closes[0] * 100
    has_limit = _has_limit_up(window[:-1], accel_threshold(code, is_st))
    matched = (C.GENTLE_RISE_MIN_PCT <= cum_pct <= C.GENTLE_RISE_MAX_PCT) and not has_limit
    return matched, {
        "cum_pct": cum_pct, "has_limit_in_window": has_limit, "insufficient_history": False,
    }


def is_volume_shrink_pullback(bars: list[dict]) -> tuple[bool, dict]:
    """缩量阴线买点：收阴（close<open）且 vol ≤ min(昨量, MA5量)×SHRINK_VOLUME_RATIO。"""
    bars = bars or []
    if len(bars) < 5:
        return False, {"insufficient_history": True}
    today = bars[-1]
    o, c, v = today.get("open"), today.get("close"), today.get("vol")
    prev_v = bars[-2].get("vol")
    ma5_vol = ma([b.get("vol") for b in bars], 5)
    if None in (o, c, v, prev_v, ma5_vol):
        return False, {"insufficient_history": True}
    is_yin = c < o
    shrink = v <= min(prev_v, ma5_vol) * C.SHRINK_VOLUME_RATIO
    return (is_yin and shrink), {
        "is_yin": is_yin, "shrink": shrink, "vol": v, "prev_vol": prev_v,
        "ma5_vol": ma5_vol, "insufficient_history": False,
    }


def is_trend_broken(bars: list[dict]) -> tuple[bool, dict]:
    """趋势破坏（退池）：收盘跌破 MA10 或 连续 2 日收盘<MA5 且第 2 日 pct_chg<第 1 日。"""
    bars = bars or []
    closes = [b.get("close") for b in bars]
    if len(closes) < C.MIN_BARS_FOR_SIGNAL or any(c is None for c in closes[-C.MIN_BARS_FOR_SIGNAL:]):
        return False, {"insufficient_history": True}
    ma10 = ma(closes, 10)
    ma5_today = ma(closes, 5)
    ma5_prev = ma(closes[:-1], 5)
    below_ma10 = closes[-1] < ma10
    pct_today = bars[-1].get("pct_chg") or 0.0
    pct_prev = bars[-2].get("pct_chg") or 0.0
    two_day_break = (closes[-1] < ma5_today) and (closes[-2] < ma5_prev) and (pct_today < pct_prev)
    return (below_ma10 or two_day_break), {
        "below_ma10": below_ma10, "two_day_break": two_day_break,
        "ma10": ma10, "insufficient_history": False,
    }
