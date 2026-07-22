"""外汇行情的纯数值与来源合同校验，供 provider 和报告层共同复用。"""
from __future__ import annotations

import math


CHINAMONEY_SPOT_URL = (
    "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/rfx-sp-quot.json"
)
CHINAMONEY_C_SWAP_URL = (
    "https://www.chinamoney.org.cn/r/cms/www/chinamoney/data/fx/"
    "fx-c-sw-curv-USD.CNY.json"
)
CHINAMONEY_C_SWAP_SOURCES = {"报价数据", "交易数据"}
USD_CNY_RATE_MIN = 1.0
USD_CNY_RATE_MAX = 20.0
USD_CNY_SPREAD_MAX = 0.10
USD_CNY_SWAP_POINT_ABS_MAX = 100_000.0


def _finite_float(value, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 不是有效数值")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 不是有效数值") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} 不是有限数值")
    return number


def validate_usd_cny_spot_values(
    bid_value,
    ask_value,
    mid_value=None,
) -> tuple[float, float, float]:
    """校验 USD/CNY 买卖价、最大点差及算术中值。"""
    bid = _finite_float(bid_value, "bid")
    ask = _finite_float(ask_value, "ask")
    if not USD_CNY_RATE_MIN <= bid <= USD_CNY_RATE_MAX:
        raise ValueError("bid 超出 USD/CNY 合理区间")
    if not USD_CNY_RATE_MIN <= ask <= USD_CNY_RATE_MAX:
        raise ValueError("ask 超出 USD/CNY 合理区间")
    if bid > ask or ask - bid > USD_CNY_SPREAD_MAX:
        raise ValueError("USD/CNY 买卖价倒挂或点差异常")
    computed_mid = (bid + ask) / 2
    if mid_value is not None:
        supplied_mid = _finite_float(mid_value, "mid")
        if not math.isclose(supplied_mid, computed_mid, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("USD/CNY 中值与买卖价不一致")
    return bid, ask, computed_mid


def validate_usd_cny_swap_values(
    swap_point_value,
    forward_rate_value,
) -> tuple[float, float, float]:
    """校验 USD/CNY 掉期点、全价汇率及反推即期的数值不变量。"""
    swap_point = _finite_float(swap_point_value, "swap_point_pips")
    forward_rate = _finite_float(forward_rate_value, "forward_rate")
    if abs(swap_point) > USD_CNY_SWAP_POINT_ABS_MAX:
        raise ValueError("USD/CNY 掉期点超出合理区间")
    if not USD_CNY_RATE_MIN <= forward_rate <= USD_CNY_RATE_MAX:
        raise ValueError("USD/CNY 全价汇率超出合理区间")
    implied_spot = forward_rate - swap_point / 10_000
    if (
        not math.isfinite(implied_spot)
        or not USD_CNY_RATE_MIN <= implied_spot <= USD_CNY_RATE_MAX
    ):
        raise ValueError("USD/CNY 掉期反推即期超出合理区间")
    return swap_point, forward_rate, implied_spot
