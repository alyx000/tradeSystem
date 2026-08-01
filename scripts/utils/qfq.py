"""前复权（qfq）：按复权因子把历史 OHLC 归一到末根（T 日）价格坐标系。

从 `services/board_break/indicators.py` 下沉到 utils——**正确性关键**逻辑，凡是拿
跨月/跨年收盘序列算指标的服务都必须走它，不能各写一份或直接用未复权价。

为什么必须复权（2026-07-23 真实数据实证）：`get_stock_daily_range` 返回**未复权**价。
宁德时代 300750 一次 1.9% 的普通分红除权，就足以让 MA233 位置标注从「233↓」翻成
「233↑」——方向标反。窗口越长撞除权的概率越高，233 交易日≈一整年几乎必然跨一次；
送转（如比亚迪 002594 因子变化 3 倍）更是量级失真。

fail-closed 契约：因子对不齐 / 缺 T 日 / 脏值 → 返回 None，由调用方标缺失，
**绝不退回未复权价硬算**（错误的方向标注比「样本不足」危险得多）。
"""
from __future__ import annotations

import math


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


DEFAULT_PRICE_KEYS = ("close", "high", "low")
# 需要按 K 线实体判阴阳（close vs open）的调用方必须传这个，否则除权日 close 被缩放
# 而 open 未被缩放，阳线会被判成阴线——方向标反（pattern-scan 的阳放阴缩节奏依赖它）。
OHLC_PRICE_KEYS = ("open", "close", "high", "low")


def apply_qfq(
    bars: list[dict],
    factors: list[dict],
    keys: tuple[str, ...] = DEFAULT_PRICE_KEYS,
) -> list[dict] | None:
    """按 `trade_date` 对齐前复权 `keys` 指定的价格列，归一化到 T 日（bars 末根）。

    `keys` 默认只含 close/high/low（历史行为，board-break / ma_position 等消费方不读 open）；
    要用 `close > open` 判 K 线阴阳的调用方须显式传 `OHLC_PRICE_KEYS`——**混用未复权 open
    与复权 close 会把除权日的阴阳判反**，比样本缺失危险。

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
        for key in keys:
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
