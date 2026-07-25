"""月线模式纯函数：完成月聚合、指标、三类检测与观察状态。

边界约定：
- 输入日线 OHLC 必须已经统一为前复权口径；本模块只做同口径聚合，不混用 raw 价格。
- 最后一组日线默认视为未完成月。只有调用方用交易日历确认后，才可传
  ``last_month_complete=True`` 纳入正式月 K。
- 三类正式检测和状态评估都会过滤 ``MonthlyBar.is_complete=False``。
- 所有输出仅为结构化事实与规则命中，不包含买卖建议。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .models import DetectionResult, MonthlyBar, MonthlyIndicator, PoolStateResult


FUNDAMENTAL_MIN_MONTHS = 20
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9
MACD_MIN_MONTHS = MACD_SLOW_PERIOD + MACD_SIGNAL_PERIOD

# 课程没有给出这些定量阈值；集中命名，便于后续用历史样本回测而不改检测结构。
CLOSE_NEAR_HIGH_MAX_RANGE_RATIO = 0.10
REACCEL_SETUP_LOOKBACK_MONTHS = 12
PRIOR_HIGH_VOLUME_YIN_RATIO = 1.20
REACCEL_VOLUME_RATIO = 1.20
MA_CONVERGENCE_MAX_SPREAD_RATIO = 0.03
SETUP_VOLUME_MA_MONTHS = 5


def _finite_float(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _parse_trade_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid trade_date: {value!r}")


def _daily_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    trade_date = _parse_trade_date(raw.get("trade_date"))
    prices = {
        key: _finite_float(raw.get(key), field=key)
        for key in ("open", "high", "low", "close")
    }
    if any(value <= 0 for value in prices.values()):
        raise ValueError("OHLC prices must be positive")
    if prices["high"] < max(prices["open"], prices["close"]):
        raise ValueError("high must cover open and close")
    if prices["low"] > min(prices["open"], prices["close"]):
        raise ValueError("low must cover open and close")
    if prices["high"] < prices["low"]:
        raise ValueError("high must be at or above low")

    raw_volume = raw.get("volume")
    if raw_volume is None:
        raw_volume = raw.get("vol")
    volume = _finite_float(raw_volume, field="volume")
    amount = _finite_float(raw.get("amount"), field="amount")
    if volume < 0 or amount < 0:
        raise ValueError("volume and amount must be non-negative")
    return {
        "trade_date": trade_date,
        "open": prices["open"],
        "high": prices["high"],
        "low": prices["low"],
        "close": prices["close"],
        "volume": volume,
        "amount": amount,
    }


def aggregate_completed_monthly_bars(
    daily_bars: Iterable[Mapping[str, Any]],
    *,
    last_month_complete: bool = False,
) -> list[MonthlyBar]:
    """把前复权日 OHLCV 聚合为正式可用的完成月 K。

    只要数据中出现了更晚月份，之前的月份即可由时序证明已经完成；最后月份无法仅凭
    日线判断是否走到交易月末，因此默认排除。调用方必须用交易日历确认后显式传
    ``last_month_complete=True``，才能纳入最后月份。
    """
    rows = sorted((_daily_row(raw) for raw in daily_bars), key=lambda row: row["trade_date"])
    seen_dates: set[date] = set()
    for row in rows:
        trade_date = row["trade_date"]
        if trade_date in seen_dates:
            raise ValueError(f"duplicate trade_date: {trade_date.isoformat()}")
        seen_dates.add(trade_date)
    if not rows:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        month = row["trade_date"].strftime("%Y-%m")
        grouped.setdefault(month, []).append(row)

    months = sorted(grouped)
    completed_months = months if last_month_complete else months[:-1]
    result: list[MonthlyBar] = []
    for month in completed_months:
        month_rows = grouped[month]
        result.append(
            MonthlyBar(
                month=month,
                end_date=month_rows[-1]["trade_date"].isoformat(),
                open=month_rows[0]["open"],
                high=max(row["high"] for row in month_rows),
                low=min(row["low"] for row in month_rows),
                close=month_rows[-1]["close"],
                volume=sum(row["volume"] for row in month_rows),
                amount=sum(row["amount"] for row in month_rows),
                is_complete=True,
                trading_days=len(month_rows),
            )
        )
    return result


def _validate_monthly_bar(bar: MonthlyBar) -> None:
    for field in ("open", "high", "low", "close", "volume", "amount"):
        _finite_float(getattr(bar, field), field=field)
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError("monthly OHLC prices must be positive")
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise ValueError("monthly high/low must cover open and close")
    if bar.volume < 0 or bar.amount < 0:
        raise ValueError("monthly volume and amount must be non-negative")
    if bar.trading_days <= 0:
        raise ValueError("monthly trading_days must be positive")
    if not isinstance(bar.price_shape_valid, bool):
        raise ValueError("price_shape_valid must be boolean")


def _sorted_monthly_bars(bars: Sequence[MonthlyBar]) -> list[MonthlyBar]:
    ordered = sorted(bars, key=lambda bar: (bar.end_date, bar.month))
    seen_months: set[str] = set()
    for bar in ordered:
        _validate_monthly_bar(bar)
        if bar.month in seen_months:
            raise ValueError(f"duplicate month: {bar.month}")
        seen_months.add(bar.month)
    return ordered


def _sma(values: Sequence[float], period: int, end_index: int) -> float | None:
    start = end_index + 1 - period
    if period <= 0 or start < 0:
        return None
    window = values[start:end_index + 1]
    return sum(window) / period


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    ema = values[0]
    result = [ema]
    for value in values[1:]:
        ema = ema * (1.0 - alpha) + value * alpha
        result.append(ema)
    return result


def compute_monthly_indicators(bars: Sequence[MonthlyBar]) -> list[MonthlyIndicator]:
    """计算 SMA5/10/20、5/10 月均量和 MACD(12/26/9) 完整序列。"""
    ordered = _sorted_monthly_bars(bars)
    if not ordered:
        return []
    closes = [bar.close for bar in ordered]
    volumes = [bar.volume for bar in ordered]
    ema_fast = _ema_series(closes, MACD_FAST_PERIOD)
    ema_slow = _ema_series(closes, MACD_SLOW_PERIOD)
    dif_values = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    dea_values = _ema_series(dif_values, MACD_SIGNAL_PERIOD)

    result: list[MonthlyIndicator] = []
    for index, bar in enumerate(ordered):
        dif = dif_values[index]
        dea = dea_values[index]
        previous_dif = dif_values[index - 1] if index else None
        previous_dea = dea_values[index - 1] if index else None
        enough_macd_history = index + 1 >= MACD_MIN_MONTHS
        golden_cross = bool(
            enough_macd_history
            and previous_dif is not None
            and previous_dea is not None
            and previous_dif <= previous_dea
            and dif > dea
        )
        result.append(
            MonthlyIndicator(
                month=bar.month,
                ma5=_sma(closes, 5, index),
                ma10=_sma(closes, 10, index),
                ma20=_sma(closes, 20, index),
                volume_ma5=_sma(volumes, 5, index),
                volume_ma10=_sma(volumes, 10, index),
                macd_dif=dif,
                macd_dea=dea,
                macd_histogram=2.0 * (dif - dea),
                macd_golden_cross=golden_cross,
                macd_zero_axis_golden_cross=bool(golden_cross and dif > 0 and dea > 0),
            )
        )
    return result


def _formal_bars(
    bars: Sequence[MonthlyBar],
    *,
    required_months: int,
) -> tuple[list[MonthlyBar], dict[str, int]]:
    ordered = _sorted_monthly_bars(bars)
    completed = [bar for bar in ordered if bar.is_complete]
    return completed, {
        "available_months": len(completed),
        "required_months": required_months,
        "excluded_incomplete_months": len(ordered) - len(completed),
    }


def _base_evidence(completed: Sequence[MonthlyBar], sample: dict[str, int]) -> dict[str, Any]:
    return {
        "sample": sample,
        "as_of_month": completed[-1].month if completed else None,
    }


def _insufficient(
    pattern: str,
    completed: Sequence[MonthlyBar],
    sample: dict[str, int],
) -> DetectionResult:
    return DetectionResult(
        pattern=pattern,
        matched=False,
        status="insufficient_history",
        evidence=_base_evidence(completed, sample),
    )


def detect_fundamental_monthly_trend(
    bars: Sequence[MonthlyBar],
) -> DetectionResult:
    """技术确认层：MA5 > MA10 > MA20，且完成月收盘不低于 MA5。"""
    pattern = "fundamental_monthly_trend"
    completed, sample = _formal_bars(bars, required_months=FUNDAMENTAL_MIN_MONTHS)
    if len(completed) < FUNDAMENTAL_MIN_MONTHS:
        return _insufficient(pattern, completed, sample)
    latest_bar = completed[-1]
    latest = compute_monthly_indicators(completed)[-1]
    alignment = bool(
        latest.ma5 is not None
        and latest.ma10 is not None
        and latest.ma20 is not None
        and latest.ma5 > latest.ma10 > latest.ma20
    )
    close_held = bool(latest.ma5 is not None and latest_bar.close >= latest.ma5)
    matched = alignment and close_held
    evidence = {
        **_base_evidence(completed, sample),
        "conditions": {
            "ma_bullish_alignment": {
                "met": alignment,
                "operator": "MA5 > MA10 > MA20",
                "ma5": latest.ma5,
                "ma10": latest.ma10,
                "ma20": latest.ma20,
            },
            "close_at_or_above_ma5": {
                "met": close_held,
                "operator": "close >= MA5",
                "close": latest_bar.close,
                "ma5": latest.ma5,
            },
        },
    }
    return DetectionResult(
        pattern=pattern,
        matched=matched,
        status="matched" if matched else "not_matched",
        evidence=evidence,
    )


def _close_near_high(bar: MonthlyBar) -> tuple[bool, float | None]:
    if not bar.price_shape_valid:
        return False, None
    price_range = bar.high - bar.low
    distance_ratio = 0.0 if price_range == 0 else (bar.high - bar.close) / price_range
    return distance_ratio <= CLOSE_NEAR_HIGH_MAX_RANGE_RATIO, distance_ratio


def detect_theme_monthly_attack(bars: Sequence[MonthlyBar]) -> DetectionResult:
    """题材月线进攻：阳线实体穿三线 + 零轴上金叉 + 量过 5/10 月均量之一。"""
    pattern = "theme_monthly_attack"
    required = max(FUNDAMENTAL_MIN_MONTHS, MACD_MIN_MONTHS)
    completed, sample = _formal_bars(bars, required_months=required)
    if len(completed) < required:
        return _insufficient(pattern, completed, sample)
    latest_bar = completed[-1]
    latest = compute_monthly_indicators(completed)[-1]
    mas = [latest.ma5, latest.ma10, latest.ma20]
    has_mas = all(value is not None for value in mas)
    shape_valid = latest_bar.price_shape_valid
    crosses = bool(
        shape_valid
        and has_mas
        and latest_bar.close > latest_bar.open
        and latest_bar.open <= min(mas)
        and latest_bar.close >= max(mas)
    )
    volume_above = bool(
        latest.volume_ma5 is not None
        and latest.volume_ma10 is not None
        and (
            latest_bar.volume > latest.volume_ma5
            or latest_bar.volume > latest.volume_ma10
        )
    )
    near_high, distance_ratio = _close_near_high(latest_bar)
    zero_axis_cross = latest.macd_zero_axis_golden_cross
    matched = crosses and zero_axis_cross and volume_above
    evidence = {
        **_base_evidence(completed, sample),
        "conditions": {
            "price_shape_valid": {
                "met": shape_valid,
                "operator": "adj_factor == previous_month_end_adj_factor",
                "hard_gate": True,
            },
            "bullish_body_crosses_three_mas": {
                "met": crosses,
                "operator": "close > open; open <= min(MA5,MA10,MA20); close >= max(...)",
                "price_shape_valid": shape_valid,
                "open": latest_bar.open,
                "close": latest_bar.close,
                "ma5": latest.ma5,
                "ma10": latest.ma10,
                "ma20": latest.ma20,
            },
            "zero_axis_golden_cross": {
                "met": zero_axis_cross,
                "macd_dif": latest.macd_dif,
                "macd_dea": latest.macd_dea,
                "operator": "previous DIF <= DEA; current DIF > DEA; DIF > 0; DEA > 0",
            },
            "volume_above_ma5_or_ma10": {
                "met": volume_above,
                "operator": "volume > volume_MA5 OR volume > volume_MA10",
                "volume": latest_bar.volume,
                "volume_ma5": latest.volume_ma5,
                "volume_ma10": latest.volume_ma10,
            },
            "close_near_month_high": {
                "met": near_high,
                "hard_gate": False,
                "distance_ratio": distance_ratio,
                "max_distance_ratio": CLOSE_NEAR_HIGH_MAX_RANGE_RATIO,
            },
        },
    }
    return DetectionResult(
        pattern=pattern,
        matched=matched,
        status="matched" if matched else "not_matched",
        evidence=evidence,
    )


def _prior_setup_evidence(
    bars: Sequence[MonthlyBar],
    indicators: Sequence[MonthlyIndicator],
) -> dict[str, Any]:
    latest_index = len(bars) - 1
    start = max(0, latest_index - REACCEL_SETUP_LOOKBACK_MONTHS)
    setup_kinds: list[str] = []
    high_volume_yin: list[dict[str, Any]] = []
    convergence: list[dict[str, Any]] = []
    shape_invalid_months: list[str] = []
    for index in range(start, latest_index):
        bar = bars[index]
        indicator = indicators[index]
        if not bar.price_shape_valid:
            shape_invalid_months.append(bar.month)
        if index >= SETUP_VOLUME_MA_MONTHS:
            prior_volume_ma = sum(
                item.volume for item in bars[index - SETUP_VOLUME_MA_MONTHS:index]
            ) / SETUP_VOLUME_MA_MONTHS
            threshold = prior_volume_ma * PRIOR_HIGH_VOLUME_YIN_RATIO
            if (
                bar.price_shape_valid
                and bar.close < bar.open
                and bar.volume >= threshold
            ):
                high_volume_yin.append(
                    {
                        "month": bar.month,
                        "volume": bar.volume,
                        "prior_volume_ma5": prior_volume_ma,
                        "threshold": threshold,
                    }
                )
        mas = [indicator.ma5, indicator.ma10, indicator.ma20]
        if all(value is not None for value in mas):
            average = sum(mas) / len(mas)
            spread_ratio = (max(mas) - min(mas)) / average
            if spread_ratio <= MA_CONVERGENCE_MAX_SPREAD_RATIO:
                convergence.append(
                    {
                        "month": bar.month,
                        "spread_ratio": spread_ratio,
                        "max_spread_ratio": MA_CONVERGENCE_MAX_SPREAD_RATIO,
                        "ma5": indicator.ma5,
                        "ma10": indicator.ma10,
                        "ma20": indicator.ma20,
                    }
                )
    if high_volume_yin:
        setup_kinds.append("high_volume_bearish_month")
    if convergence:
        setup_kinds.append("ma_convergence")
    return {
        "met": bool(setup_kinds),
        "operator": "prior high-volume bearish month OR prior MA5/10/20 convergence",
        "setup_kinds": setup_kinds,
        "lookback_months": REACCEL_SETUP_LOOKBACK_MONTHS,
        "high_volume_bearish_months": high_volume_yin,
        "shape_invalid_months": shape_invalid_months,
        "ma_convergence_months": convergence,
    }


def detect_monthly_reacceleration(bars: Sequence[MonthlyBar]) -> DetectionResult:
    """二次启动：既往放量阴线/均线粘合 + 当前零轴上再金叉与放量阳线。"""
    pattern = "monthly_reacceleration"
    required = max(MACD_MIN_MONTHS, FUNDAMENTAL_MIN_MONTHS)
    completed, sample = _formal_bars(bars, required_months=required)
    if len(completed) < required:
        return _insufficient(pattern, completed, sample)
    indicators = compute_monthly_indicators(completed)
    latest_bar = completed[-1]
    latest = indicators[-1]
    prior_setup = _prior_setup_evidence(completed, indicators)
    prior_volume_window = completed[-(SETUP_VOLUME_MA_MONTHS + 1):-1]
    prior_volume_ma = (
        sum(bar.volume for bar in prior_volume_window) / SETUP_VOLUME_MA_MONTHS
        if len(prior_volume_window) == SETUP_VOLUME_MA_MONTHS
        else None
    )
    volume_threshold = (
        prior_volume_ma * REACCEL_VOLUME_RATIO
        if prior_volume_ma is not None
        else None
    )
    high_volume_bullish = bool(
        latest_bar.price_shape_valid
        and prior_volume_ma is not None
        and latest_bar.close > latest_bar.open
        and latest_bar.volume >= volume_threshold
    )
    zero_axis_cross = latest.macd_zero_axis_golden_cross
    matched = bool(prior_setup["met"] and zero_axis_cross and high_volume_bullish)
    evidence = {
        **_base_evidence(completed, sample),
        "conditions": {
            "prior_setup": prior_setup,
            "zero_axis_golden_cross": {
                "met": zero_axis_cross,
                "macd_dif": latest.macd_dif,
                "macd_dea": latest.macd_dea,
            },
            "high_volume_bullish_month": {
                "met": high_volume_bullish,
                "price_shape_valid": latest_bar.price_shape_valid,
                "operator": (
                    f"close > open; volume >= prior_volume_MA5 * {REACCEL_VOLUME_RATIO}"
                ),
                "open": latest_bar.open,
                "close": latest_bar.close,
                "volume": latest_bar.volume,
                "prior_volume_ma5": prior_volume_ma,
                "volume_threshold": volume_threshold,
            },
        },
        "parameters": {
            "setup_lookback_months": REACCEL_SETUP_LOOKBACK_MONTHS,
            "prior_high_volume_yin_ratio": PRIOR_HIGH_VOLUME_YIN_RATIO,
            "reacceleration_volume_ratio": REACCEL_VOLUME_RATIO,
            "ma_convergence_max_spread_ratio": MA_CONVERGENCE_MAX_SPREAD_RATIO,
        },
    }
    return DetectionResult(
        pattern=pattern,
        matched=matched,
        status="matched" if matched else "not_matched",
        evidence=evidence,
    )


def evaluate_pool_state(bars: Sequence[MonthlyBar]) -> PoolStateResult:
    """用完成月维护观察状态：active / risk / reentry / insufficient_history。"""
    required = 5
    completed, sample = _formal_bars(bars, required_months=required)
    base = _base_evidence(completed, sample)
    if len(completed) < required:
        return PoolStateResult(state="insufficient_history", evidence=base)

    indicators = compute_monthly_indicators(completed)
    latest_bar = completed[-1]
    latest = indicators[-1]
    close_held = bool(latest.ma5 is not None and latest_bar.close >= latest.ma5)

    stood_back = False
    previous_ma5 = None
    previous_bar = completed[-2] if len(completed) >= 2 else None
    if len(completed) >= 6 and previous_bar is not None:
        previous_ma5 = indicators[-2].ma5
        stood_back = bool(
            previous_ma5 is not None
            and previous_bar.close < previous_ma5
            and latest_bar.close >= latest.ma5
        )

    half_body_price = None
    half_reclaim = False
    half_body_shape_valid = bool(
        previous_bar is not None
        and previous_bar.price_shape_valid
        and latest_bar.price_shape_valid
    )
    if (
        previous_bar is not None
        and half_body_shape_valid
        and previous_bar.close < previous_bar.open
    ):
        half_body_price = (previous_bar.open + previous_bar.close) / 2.0
        half_reclaim = bool(
            latest_bar.close > latest_bar.open
            and latest_bar.volume > previous_bar.volume
            and latest_bar.close >= half_body_price
        )

    if stood_back or half_reclaim:
        state = "reentry"
    elif not close_held:
        state = "risk"
    else:
        state = "active"

    evidence = {
        **base,
        "conditions": {
            "close_at_or_above_ma5": {
                "met": close_held,
                "operator": "close >= MA5",
                "close": latest_bar.close,
                "ma5": latest.ma5,
            },
            "stood_back_above_ma5": {
                "met": stood_back,
                "operator": "previous close < previous MA5; current close >= current MA5",
                "previous_close": previous_bar.close if previous_bar else None,
                "previous_ma5": previous_ma5,
                "current_close": latest_bar.close,
                "current_ma5": latest.ma5,
            },
            "high_volume_half_body_reclaim": {
                "met": half_reclaim,
                "price_shape_valid": half_body_shape_valid,
                "current_price_shape_valid": latest_bar.price_shape_valid,
                "previous_price_shape_valid": (
                    previous_bar.price_shape_valid if previous_bar else None
                ),
                "operator": (
                    "previous close < open; current bullish; current volume > previous volume; "
                    "current close >= previous bearish body midpoint"
                ),
                "half_body_price": half_body_price,
                "current_close": latest_bar.close,
                "current_volume": latest_bar.volume,
                "previous_volume": previous_bar.volume if previous_bar else None,
            },
        },
    }
    return PoolStateResult(state=state, evidence=evidence)


def detect_pattern(pattern: str, bars: Sequence[MonthlyBar]) -> DetectionResult:
    """按稳定策略名分派纯检测，便于上层扫描器复用同一规则。"""
    detectors = {
        "fundamental_monthly_trend": detect_fundamental_monthly_trend,
        "theme_monthly_attack": detect_theme_monthly_attack,
        "monthly_reacceleration": detect_monthly_reacceleration,
    }
    try:
        detector = detectors[pattern]
    except KeyError as exc:
        raise ValueError(f"unknown monthly pattern: {pattern}") from exc
    return detector(bars)
