"""断板反包打分层指标纯函数：前复权 / MACD DIF / 近10日涨幅 / 250日区间分位。

口径写死保证可复现（spec 打分层设计，v4 D12/D13 增量）：
- 前复权：以候选 bars 末根（T 日）为基准归一，`price * factor / factor_T`；
  factors 缺 T 日、或存在 bars 日期在 factors 中找不到对应因子（对不齐）→ 返回 None，
  不得用未复权价硬算（spec「复权因子获取失败→该票涨幅/MACD/位置三维度整体标缺失」）。
- MACD：EMA12/EMA26 手写循环，seed=首根 close（对齐 pandas `adjust=False` 语义），
  不引入 pandas 依赖；`len < MIN_BARS_INDICATOR`（120）标缺失。
- 近10日涨幅：含断板日 `close[T]/close[T-10]-1`，`len < 11` 标缺失。
- 250日区间分位：末 250 根 `min(low)/max(high)`，`range_high<=range_low` 或任一关键值
  非有限数 → missing；样本 120-249 根为降级样本（`state="degraded"`），<120 为缺失。
"""
from __future__ import annotations

import math

from services.board_break import constants as C


def apply_qfq(bars: list[dict], factors: list[dict]) -> list[dict] | None:
    """按 `trade_date` 对齐前复权 close/high/low，归一化到 T 日（bars 末根）。

    factors 缺 T 日因子，或 bars 中任一交易日在 factors 里找不到对应因子（对不齐），
    或**任一历史日因子 <=0 / 非有限数（NaN/inf）**，一律返回 None（不得用未复权价硬算）。
    历史日 0 因子若不挡住，会把该日 OHLC 整体乘 0 清零，进而污染 250 日区间分位
    （range_low 被拉到 0）与减持位置极性判断（恒判"低位"，方向打反）。
    """
    if not bars or not factors:
        return None

    factor_map = {f.get("trade_date"): f.get("adj_factor") for f in factors}
    t_date = bars[-1].get("trade_date")
    factor_t = _to_float(factor_map.get(t_date))
    if factor_t is None or factor_t <= 0 or not math.isfinite(factor_t):
        return None

    out = []
    for bar in bars:
        raw_factor = factor_map.get(bar.get("trade_date"))
        factor = _to_float(raw_factor)
        if factor is None or factor <= 0 or not math.isfinite(factor):
            return None  # 对不齐，或历史日因子非正/非有限（脏值污染）
        ratio = factor / factor_t
        if not math.isfinite(ratio):
            return None  # 极端因子比值溢出（门2 S2 R2）
        adjusted = dict(bar)
        for key in ("close", "high", "low"):
            value = _to_float(bar.get(key))
            if value is None or not math.isfinite(value):
                # 历史 bar 任一价格缺失/非有限 → 整体返 None（三维度缺失），
                # 不得静默跳过：close=None 会让 _ema 抛 TypeError 打崩整批；
                # high/low 缺失会让 position_250d 用残缺区间产出伪 full 分位（门2 S2 R1）。
                return None
            adjusted_value = value * ratio
            if not math.isfinite(adjusted_value):
                return None  # 复权后溢出为 inf/nan（门2 S2 R2）：宁整体缺失不出伪 ok
            adjusted[key] = adjusted_value
        out.append(adjusted)
    return out


def macd_dif(closes: list[float]) -> float | None:
    """DIF = EMA12 - EMA26，EMA seed=首根 close（`adjust=False` 语义）。"""
    if len(closes) < C.MIN_BARS_INDICATOR:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    return ema12 - ema26


def gain_10d(closes: list[float]) -> float | None:
    """(close[T] / close[T-10] - 1) * 100，含断板日，需 >= 11 根。"""
    if len(closes) < 11:
        return None
    base = closes[-11]
    if base is None or base == 0:
        return None
    result = (closes[-1] / base - 1) * 100
    # 派生除法溢出守卫（门2 S2 R3）：有限输入也可能溢出为 inf，宁缺失不出伪 ok
    return result if math.isfinite(result) else None


def position_250d(bars: list[dict]) -> dict:
    """250日区间分位：`(close_T - min(low)) / (max(high) - min(low))`（末250根）。"""
    n = len(bars)
    if n < C.MIN_BARS_INDICATOR:
        return {"value": None, "state": "missing", "bar_count": n}

    window = bars[-C.POSITION_BARS:]
    lows = [b.get("low") for b in window if b.get("low") is not None]
    highs = [b.get("high") for b in window if b.get("high") is not None]
    close_t = bars[-1].get("close")
    if not lows or not highs or close_t is None:
        return {"value": None, "state": "missing", "bar_count": n}

    range_low = min(lows)
    range_high = max(highs)
    if not all(math.isfinite(v) for v in (range_low, range_high, close_t)):
        return {"value": None, "state": "missing", "bar_count": n}
    if range_high <= range_low:
        return {"value": None, "state": "missing", "bar_count": n}

    value = (close_t - range_low) / (range_high - range_low)
    if not math.isfinite(value):
        return {"value": None, "state": "missing", "bar_count": n}  # 派生除法溢出守卫（门2 S2 R3）
    state = "full" if n >= C.POSITION_BARS else "degraded"
    return {"value": value, "state": state, "bar_count": n}


def _ema(values: list[float], n: int) -> float:
    k = 2.0 / (n + 1)
    ema = values[0]
    for px in values[1:]:
        ema = ema * (1 - k) + px * k
    return ema


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
