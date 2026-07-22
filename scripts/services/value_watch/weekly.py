"""完成周聚合 + 周均线 + 周 MACD（纯函数，不依赖 pandas）。

口径（spec v8）：
- ISO 自然周分组（isocalendar 年+周号），week_end = 周内最后一个有 bar 的交易日
  （节假日周不一定是周五）。
- 目标周是否"完成"由调用方用交易日历判定（target_week_has_remaining_open_days），
  本模块不查日历。
- MACD 用 ewm(adjust=False) 等价递推：ema[i] = ema[i-1] + 2/(n+1)*(x[i]-ema[i-1])，
  seed = x[0]；DIF = EMA12 − EMA26，DEA = EMA9(DIF)。
"""
from __future__ import annotations

import datetime


def aggregate_completed_weeks(daily: list[dict], target_date: str,
                              target_week_has_remaining_open_days: bool) -> list[dict]:
    """日线（升序，元素 {"date","close","volume"|None}）→ 完成周列表
    {"week_end","close","volume"}。目标周尚有剩余 open 日则剔除该周。"""
    target_key = _week_key(target_date)
    weeks: dict[tuple[int, int], dict] = {}
    for bar in daily:
        key = _week_key(bar["date"])
        w = weeks.setdefault(key, {"week_end": bar["date"], "close": bar["close"],
                                   "volume": 0.0, "_vol_ok": True})
        # daily 升序：同周后来者覆盖 week_end/close
        w["week_end"] = bar["date"]
        w["close"] = bar["close"]
        vol = bar.get("volume")
        if vol is None:
            w["_vol_ok"] = False
        elif w["_vol_ok"]:
            w["volume"] += vol
    out = []
    for key in sorted(weeks):
        if key == target_key and target_week_has_remaining_open_days:
            continue  # 未完成周剔除：周中运行不产生周线事件
        w = weeks[key]
        out.append({"week_end": w["week_end"], "close": w["close"],
                    "volume": w["volume"] if w["_vol_ok"] else None})
    return out


def _week_key(date_str: str) -> tuple[int, int]:
    iso = datetime.date.fromisoformat(date_str).isocalendar()
    return (iso[0], iso[1])


def weekly_ma(closes: list[float], n: int) -> list["float | None"]:
    out: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(sum(closes[i + 1 - n:i + 1]) / n)
    return out


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1)
    out = []
    ema = values[0]
    for v in values:
        ema = ema + alpha * (v - ema)
        out.append(ema)
    # 注:首值按递推公式 ema = x0 + alpha*(x0-x0) = x0,与 pandas adjust=False 一致
    return out


def weekly_macd(closes: list[float]) -> tuple[list[float], list[float]]:
    """标准 12/26/9。返回 (DIF, DEA)，与输入等长。"""
    if not closes:
        return [], []
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    return dif, dea
