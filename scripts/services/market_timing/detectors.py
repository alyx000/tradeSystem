"""market-timing 检测器（纯函数，无副作用、不触网络/DB）。

约定：bars 为某指数的日线列表，按 trade_date **升序**（最旧在前、最新在末尾，
closes[-1] = 今日）；每根含 {trade_date, open, high, low, close, vol, ...}。

两组检测：
  ① 时间周期：find_swing_pivot → fib_day_count → fib_turning_point → count_resonance
  ② 底分型：is_bottom_fractal（成型）+ is_breakout_confirm（放量中阳确认）

全部返回结构化 dict（含判断依据），方便渲染层标 [判断] 并解释，守红线不出价位/不给方向。
"""
from __future__ import annotations

from . import constants as C


def ma(values: list[float], n: int) -> float | None:
    """最近 n 个值的均值；不足 n 返回 None。可用于 close 或 vol。"""
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def _body_pct(bar: dict) -> float | None:
    """K 线实体涨幅 (close - open) / open。"""
    o = bar.get("open")
    c = bar.get("close")
    if not o:
        return None
    return (c - o) / o


# ── ① 时间周期 ──

def find_swing_pivot(
    bars: list[dict], *, window: int = C.SWING_WINDOW, min_reversal: float = C.SWING_MIN_REVERSAL_PCT
) -> dict | None:
    """最近一个有效 swing 拐点（高点或低点，双向）。无则 None。

    拐点须满足：① 是 [i-window, i+window] 内的最高(高点)/最低(低点)；
    ② 其后反向运行幅度 ≥ min_reversal（过滤毛刺）。
    从最新往回扫，返回最近(index 最大)的合格拐点。
    返回 {index, date, price, type}，type ∈ {'high', 'low'}。
    """
    n = len(bars)
    if n < 2 * window + 1:
        return None
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    for i in range(n - 1 - window, window - 1, -1):
        # swing high：窗口内最高 + 其后回落 ≥ min_reversal
        if highs[i] == max(highs[i - window:i + window + 1]):
            min_after = min(lows[i + 1:])
            if highs[i] > 0 and (highs[i] - min_after) / highs[i] >= min_reversal:
                return {"index": i, "date": bars[i]["trade_date"], "price": highs[i], "type": "high"}
        # swing low：窗口内最低 + 其后反弹 ≥ min_reversal
        if lows[i] == min(lows[i - window:i + window + 1]):
            max_after = max(highs[i + 1:])
            if lows[i] > 0 and (max_after - lows[i]) / lows[i] >= min_reversal:
                return {"index": i, "date": bars[i]["trade_date"], "price": lows[i], "type": "low"}
    return None


def fib_day_count(bars: list[dict], pivot_date: str) -> int | None:
    """pivot_date 到最后一根的交易日数（pivot 当日 = 0）。pivot 不在 bars 返回 None。"""
    for i, b in enumerate(bars):
        if b["trade_date"] == pivot_date:
            return (len(bars) - 1) - i
    return None


def fib_turning_point(day_count: int | None, *, tolerance: int = C.FIB_NEAR_TOLERANCE) -> dict:
    """day_count 是否命中/临近斐波那契变盘点。

    返回 {day_count, hit, near, distance}：
      - hit：精确命中的斐波那契数（否则 None）
      - near：在 ±tolerance 内临近的斐波那契数（精确命中时为 None）
      - distance：到最近斐波那契数的距离
    """
    if day_count is None or day_count <= 0:
        return {"day_count": day_count, "hit": None, "near": None, "distance": None}
    nearest = min(C.FIB_SEQUENCE, key=lambda f: abs(f - day_count))
    dist = abs(nearest - day_count)
    if dist == 0:
        return {"day_count": day_count, "hit": nearest, "near": None, "distance": 0}
    if dist <= tolerance:
        return {"day_count": day_count, "hit": None, "near": nearest, "distance": dist}
    return {"day_count": day_count, "hit": None, "near": None, "distance": dist}


def count_resonance(turning_points: list[dict]) -> int:
    """共振 = 同日多指数**精确命中**变盘点的指数数。"""
    return sum(1 for tp in turning_points if tp and tp.get("hit") is not None)


# ── ② 底分型 + 放量中阳确认 ──

def is_bottom_fractal(bars: list[dict]) -> tuple[bool, dict]:
    """底分型成型：最近三根 K 中间一根(bars[-2])的低点严格最低（缠论底分型口径）。

    返回 (bool, {low_date, low_price, left_high, right_high})；
    left_high/right_high 供确认层做「突破前高」判断。
    """
    if len(bars) < 3:
        return False, {"reason": "insufficient_history"}
    left, mid, right = bars[-3], bars[-2], bars[-1]
    ok = mid["low"] < left["low"] and mid["low"] < right["low"]
    return ok, {
        "low_date": mid["trade_date"],
        "low_price": mid["low"],
        "left_high": left["high"],
        "right_high": right["high"],
    }


def is_breakout_confirm(bars: list[dict]) -> tuple[bool, dict]:
    """放量中阳突破确认：当日同时满足 中阳 + 放量 + 收盘破 MA5 + MA5 拐头。

    - 中阳：实体涨幅 ≥ MID_YANG_MIN_PCT
    - 放量：当日量 ≥ 前 5 日均量 × FRACTAL_VOLUME_RATIO
    - 破 MA5：收盘 > MA5
    - MA5 拐头：今日 MA5 > 昨日 MA5
    返回 (bool, detail)。
    """
    if len(bars) < C.MIN_BARS_FOR_SIGNAL:
        return False, {"reason": "insufficient_history"}
    today = bars[-1]
    closes = [b["close"] for b in bars]
    vols = [b.get("vol") or 0.0 for b in bars]
    ma5_today = ma(closes, 5)
    ma5_prev = ma(closes[:-1], 5)
    ma5_vol_prev = ma(vols[:-1], 5)

    body = _body_pct(today)
    is_mid_yang = body is not None and body >= C.MID_YANG_MIN_PCT
    is_volume_up = bool(ma5_vol_prev and ma5_vol_prev > 0
                        and (today.get("vol") or 0.0) >= ma5_vol_prev * C.FRACTAL_VOLUME_RATIO)
    above_ma5 = ma5_today is not None and today["close"] > ma5_today
    ma5_turning = ma5_today is not None and ma5_prev is not None and ma5_today > ma5_prev

    ok = bool(is_mid_yang and is_volume_up and above_ma5 and ma5_turning)
    return ok, {
        "mid_yang": is_mid_yang,
        "body_pct": body,
        "volume_up": is_volume_up,
        "above_ma5": above_ma5,
        "ma5": ma5_today,
        "ma5_turning": ma5_turning,
    }


def is_fractal_confirmed(bars: list[dict], fractal: dict) -> tuple[bool, dict]:
    """底分型确认：在已成型底分型(fractal)基础上，今日同时满足
      ① 放量中阳突破 MA5 + MA5 拐头（is_breakout_confirm）
      ② 突破前高：收盘 > max(left_high, right_high)（消费 is_bottom_fractal 返回的前高，
         否则放量中阳日可能在没破前高时被误判确认）
      ③ 结构未破：收盘 > 底分型低点（跌破则 invalid 而非确认）
    返回 (bool, detail)。
    """
    ok_breakout, det = is_breakout_confirm(bars)
    today = bars[-1] if bars else {}
    close = today.get("close")
    highs = [h for h in (fractal.get("left_high"), fractal.get("right_high")) if h is not None]
    prior_high = max(highs) if highs else None
    low = fractal.get("low_price")
    broke_prior_high = bool(prior_high is not None and close is not None and close > prior_high)
    structure_held = bool(low is not None and close is not None and close > low)
    ok = bool(ok_breakout and broke_prior_high and structure_held)
    return ok, {
        **det,
        "broke_prior_high": broke_prior_high,
        "prior_high": prior_high,
        "structure_held": structure_held,
    }
