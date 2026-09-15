"""日线预警与60分钟确认。所有阈值是工程代理，不是老师原话或胜率。

MACD(12,26,9)，EMA 从序列首值起算；120根日线/至少80根分钟线预热。
固定历史窗口只使用截至目标日的完成K线，无未来行情、无拟合画图角度。
"""
from __future__ import annotations

import math
from datetime import datetime
from statistics import mean

SCHEMA = "sector-adjustment-risk-v1"
DAILY_BARS = 120
MINUTE_DAYS = 30
PARAMETERS = {
    "macd": [12, 26, 9], "daily_bars": DAILY_BARS,
    "rise_window": 60, "rise_min_pct": 20.0, "peak_max_age": 20,
    "hist_shrink_ratio": 0.9, "pivot_radius": 2, "pivot_min_gap": 4,
    "price_peak_tolerance": 0.001, "minute_days": MINUTE_DAYS,
    "market_increment_ratio": 1.1, "market_increment_days": 3,
}
LABELS = {
    "confirmed": "多周期调整信号",
    "daily_break": "日线风险加强",
    "warning": "日线动能预警",
    "not_triggered": "完整口径未触发",
    "daily_only": "日线未触发／60分钟待核验",
    "not_applicable": "未满足明显上涨前提",
    "missing_data": "数据不足，未判断",
}


def number(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不是行情数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("非有限行情数值")
    return result


def normalize(rows, code, expected, *, minute=False):
    """严格核对身份、唯一性、OHLC 与完整日期脊柱；拒绝静默删脏行。"""
    key = "trade_time" if minute else "trade_date"
    output = {}
    for raw in rows:
        if str(raw.get("ts_code")) != code:
            raise ValueError("行情代码不匹配")
        stamp = str(raw.get(key, ""))
        if minute:
            stamp = datetime.fromisoformat(stamp).strftime("%Y-%m-%d %H:%M:%S")
        else:
            stamp = datetime.strptime(stamp.replace("-", ""), "%Y%m%d").date().isoformat()
        # 上游多返回边界外行情也不允许泄入计算。
        if stamp not in expected:
            raise ValueError("行情日期越界或分钟时间标签异常")
        if stamp in output:
            raise ValueError("行情时间重复")
        row = {k: number(raw.get(k)) for k in ("open", "high", "low", "close", "vol")}
        if not (0 < row["low"] <= min(row["open"], row["close"]) <=
                max(row["open"], row["close"]) <= row["high"]) or row["vol"] < 0:
            raise ValueError("OHLC或成交量非法")
        row["time"] = stamp
        output[stamp] = row
    if set(output) != set(expected):
        raise ValueError(f"行情缺口：{len(output)}/{len(expected)}")
    return [output[t] for t in expected]


def ema(values, period):
    result = [values[0]]
    weight = 2 / (period + 1)
    for value in values[1:]:
        result.append(weight * value + (1 - weight) * result[-1])
    return result


def macd(bars):
    closes = [b["close"] for b in bars]
    dif = [a-b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    dea = ema(dif, 9)
    return dif, [2*(a-b) for a, b in zip(dif, dea)]


def divergence(bars, *, window):
    """已完成双侧2根确认的相邻高点；相隔>=4根且中间有>=1%回落。

    红柱须正且缩短>=10%，DIF同时降低，避免把单纯柱缩短当完整顶背离。
    第二峰超过第一峰0.1%记创新高；±0.1%记双头代理。
    """
    dif, hist = macd(bars)
    start = max(60, len(bars)-window)
    peaks = [i for i in range(start, len(bars)-2)
             if bars[i]["high"] > max(b["high"] for b in bars[i-2:i])
             and bars[i]["high"] >= max(b["high"] for b in bars[i+1:i+3])]
    result = None
    for left, right in zip(peaks, peaks[1:]):
        if right-left < 4 or min(b["low"] for b in bars[left+1:right]) > bars[left]["high"]*.99:
            continue
        ratio = bars[right]["high"]/bars[left]["high"]
        if ratio < .999 or hist[left] <= 0 or hist[right] < 0 or dif[right] >= dif[left]:
            continue
        if hist[right] > hist[left]*PARAMETERS["hist_shrink_ratio"]:
            continue
        # 后续又出现更高峰时，旧背离不得继续冒充当前确认。
        if any(b["high"] > bars[right]["high"]*1.001 for b in bars[right+1:]):
            continue
        result = {"first": left, "second": right,
                  "first_time": bars[left]["time"], "second_time": bars[right]["time"],
                  "first_high": bars[left]["high"], "second_high": bars[right]["high"],
                  "first_hist": round(hist[left], 6), "second_hist": round(hist[right], 6),
                  "first_dif": round(dif[left], 6), "second_dif": round(dif[right], 6),
                  "pattern": "new_high" if ratio > 1.001 else "double_top_proxy"}
    return result


def analyze(daily, minutes=None):
    if len(daily) != DAILY_BARS:
        raise ValueError("日线预热窗口不完整")
    tail = daily[-60:]
    peak = max(range(1, len(tail)), key=lambda i: (tail[i]["high"], i))
    trough = min(range(peak), key=lambda i: tail[i]["low"])
    rise = (tail[peak]["high"]/tail[trough]["low"]-1)*100
    eligible = rise >= PARAMETERS["rise_min_pct"] and len(tail)-1-peak <= 20
    daily_div = divergence(daily, window=60)
    _, hist = macd(daily)
    # 尚未被右侧K线确认的近期高点，只能产生柱体衰减预警。
    recent = max(range(len(daily)-5, len(daily)), key=lambda i: daily[i]["high"])
    previous = range(len(daily)-25, len(daily)-5)
    max_hist = max(hist[i] for i in previous)
    weakening = (daily[recent]["high"] >= max(daily[i]["high"] for i in previous)
                 and max_hist > 0 and hist[recent] <= max_hist*.9)
    engulf = False
    signal_peak = daily_div["second"] if daily_div else recent
    for i in range(len(daily)-5, len(daily)):
        a, b = daily[i-1], daily[i]
        if (i > signal_peak and a["close"] > a["open"] and b["close"] < b["open"]
                and b["open"] >= a["close"] and b["close"] < a["open"]
                and b["vol"] > mean(x["vol"] for x in daily[i-5:i])
                and daily[-1]["close"] < a["open"]):
            engulf = True
    minute_div = divergence(minutes, window=60) if minutes and len(minutes) >= 80 else None
    anchor = None
    broken = False
    if minute_div:
        l, r = minute_div["first"], minute_div["second"]
        rising = [b for b in minutes[l:r+1] if b["close"] > b["open"] and b["vol"] > 0]
        if rising:
            bar = max(rising, key=lambda b: b["vol"])
            anchor = {"time": bar["time"], "low": bar["low"], "vol": bar["vol"]}
            # 用连续两根完成60分钟收盘低于锚点代理“有效跌破”，非老师精确定义。
            broken = len(minutes)-r-1 >= 2 and all(b["close"] < bar["low"] for b in minutes[-2:])
    warning = bool(daily_div or weakening)
    if not eligible:
        level = "not_applicable"
    elif warning and minute_div and broken:
        level = "confirmed"
    elif warning and engulf:
        level = "daily_break"
    elif warning:
        level = "warning"
    else:
        level = "not_triggered" if minutes else "daily_only"
    vol_mean = mean(b["vol"] for b in daily[-6:-1])
    return {"level": level, "eligible": eligible, "rise_pct": round(rise, 2),
            "rise_start": tail[trough]["time"], "peak_date": tail[peak]["time"],
            "peak_age": len(tail)-1-peak, "daily_divergence": daily_div,
            "hist_weakening": weakening, "bearish_engulfing": engulf,
            "minute_divergence": minute_div, "minute_anchor": anchor,
            "minute_break": broken if minutes else None,
            "volume_ratio_5": round(daily[-1]["vol"]/vol_mean, 3) if vol_mean > 0 else None,
            "close": daily[-1]["close"], "as_of": daily[-1]["time"]}
