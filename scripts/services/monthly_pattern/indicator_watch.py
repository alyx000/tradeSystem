"""课程指标篇的月线种子与日/周观察纯函数。

本模块只计算可复核的行情事实，不做 I/O、不写观察池，也不输出买卖建议。

口径：

- 月线种子只使用完成月；最新完成月是回踩观察月，其前紧邻至少 5 根阳月。
- 日频监控用截至目标日的日线重新聚合当前月；当前收盘必须不低于动态
  5 月均线，未满足时只保留历史月线种子，不允许晋级日线重启/周线共振。
- MACD 固定为 12/26/9，``above_zero`` 与 ``bullish_on_zero`` 分开表达：
  前者只要求 DIF、DEA 同时大于零，后者另要求 DIF >= DEA。
- 周线为目标日 as-of 聚合，当前周只聚合到目标日，绝不读取目标日之后的数据。
- 5/13 日均量仅沿用现有系统作为辅助事实，不参与状态硬门。
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Any

from services.monthly_pattern.models import DetectionResult


MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_MIN_OBSERVATIONS = MACD_SLOW + MACD_SIGNAL
MIN_BULLISH_MONTHS = 5
MONTHLY_MA_WINDOWS = (5, 10, 20)
DEFAULT_VOLUME_WINDOWS = (5, 13)
DYNAMIC_MONTHLY_MA_WINDOW = 5

UNRESOLVED_RULES = (
    {
        "rule": "转写中的 RSR 是否实际为 RSI(9/9/9)",
        "reason": "尚未用原视频指标栏核对，首版不计算该指标或双指标背离",
    },
    {
        "rule": "课程原始均量线周期",
        "reason": "转写数字存在歧义；5/13 仅标为现有系统辅助口径，不冒充课程参数",
    },
    {
        "rule": "60 分钟底背离与突破锚点",
        "reason": "缺少可信 60 分钟事实源，且阴线锚点价位未明确，首版不自动化",
    },
    {
        "rule": "顶底背离的 pivot、柱体峰值或面积与有效突破",
        "reason": "原文没有给出可复现定义，首版不把背离转成自动告警",
    },
    {
        "rule": "牛市、熊市与未知环境的 canonical 分类",
        "reason": "系统尚无经验证的统一 regime 定义，首版不设牛熊硬门",
    },
    {
        "rule": "月线相对低位",
        "reason": "原文为定性偏好，未给出区间、均线或分位阈值，首版不作硬筛选",
    },
    {
        "rule": "跨月等待种子的保留与失效期限",
        "reason": (
            "原文允许下个月跌破后再等收回，但未给保留期限；"
            "首版等待桶只在同一目标月内无状态重算，月度翻页后按新完成月重建种子"
        ),
    },
)

_NO_DEFAULT = object()
_MISSING = object()


def _value(item: Any, key: str, default: Any = _NO_DEFAULT) -> Any:
    if isinstance(item, Mapping):
        value = item.get(key, _MISSING)
    else:
        value = getattr(item, key, _MISSING)
    if value is _MISSING:
        if default is _NO_DEFAULT:
            raise ValueError(f"missing field: {key}")
        return default
    return value


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _iso_date(value: Any, field: str) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def _month_ordinal(month: str) -> int:
    try:
        parsed = dt.datetime.strptime(month, "%Y-%m")
    except (TypeError, ValueError) as exc:
        raise ValueError("month must be YYYY-MM") from exc
    return parsed.year * 12 + parsed.month - 1


def _valid_ohlc(open_: float, high: float, low: float, close: float) -> bool:
    return (
        min(open_, high, low, close) > 0
        and high >= max(open_, close)
        and low <= min(open_, close)
        and high >= low
    )


def _seed_result(
    *,
    status: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> DetectionResult:
    return DetectionResult(
        pattern="monthly_indicator_seed",
        matched=status == "matched",
        status=status,
        evidence={"reason": reason, **(evidence or {})},
    )


def detect_monthly_seed(bars: Sequence[Any]) -> DetectionResult:
    """检测“五阳后完成月回踩 MA5”的月线种子。

    ``bars`` 可传 :class:`MonthlyBar` 或具备同名字段的 mapping。未完成月会被
    忽略；最近 20 个完成月必须按自然月连续。最近回踩月与其前 5 个月的
    shape 采用三态判定：已知硬门失败可直接返回 ``not_matched``；只有其余
    硬门均成立、最终结论仍依赖无效 shape 时才返回 ``blocked``。更早月份
    只提供上下文，不因旧形态无效阻断当前后缀。

    最新完成月为回踩月。该月收阴只写入 ``preferred_pullback`` 辅助证据，不是
    硬门；硬门只有：

    1. 其前紧邻连续阳月不少于 5；
    2. 最新月 MA5 > MA10 > MA20；
    3. 最新月 ``low <= MA5 <= close``。
    """
    normalized: list[dict[str, Any]] = []
    seen_months: set[str] = set()
    excluded_incomplete = 0

    try:
        for raw in bars or []:
            month = str(_value(raw, "month"))
            month_ord = _month_ordinal(month)
            if month in seen_months:
                raise ValueError(f"duplicate month: {month}")
            seen_months.add(month)

            end_date = _iso_date(_value(raw, "end_date"), "end_date")
            if end_date[:7] != month:
                raise ValueError(f"end_date outside month: {month}")

            complete = _value(raw, "is_complete")
            if not isinstance(complete, bool):
                raise ValueError(f"is_complete must be bool: {month}")
            if not complete:
                excluded_incomplete += 1
                continue

            normalized.append(
                {
                    "month": month,
                    "month_ord": month_ord,
                    "end_date": end_date,
                    "price_shape_valid": _value(raw, "price_shape_valid"),
                    "raw": raw,
                }
            )
    except ValueError as exc:
        return _seed_result(status="blocked", reason=str(exc))

    normalized.sort(key=lambda item: item["month_ord"])
    if not normalized:
        return _seed_result(
            status="insufficient_history",
            reason="no_completed_months",
            evidence={"excluded_incomplete_months": excluded_incomplete},
        )

    required = max(MONTHLY_MA_WINDOWS)
    available_suffix = normalized[-min(required, len(normalized)):]
    for previous, current in zip(available_suffix, available_suffix[1:]):
        if current["month_ord"] != previous["month_ord"] + 1:
            return _seed_result(
                status="blocked",
                reason=(
                    "non_consecutive_completed_months: "
                    f"{previous['month']}->{current['month']}"
                ),
                evidence={
                    "completed_months": len(normalized),
                    "required_suffix_months": required,
                },
            )
    if len(normalized) < required:
        return _seed_result(
            status="insufficient_history",
            reason="monthly_ma_history_insufficient",
            evidence={
                "available_completed_months": len(normalized),
                "required_completed_months": required,
                "excluded_incomplete_months": excluded_incomplete,
            },
        )

    # 月均线只使用最近 20 个完成月的 close。更早的历史缺口或月内形态无效
    # 不影响本次种子判定，避免 48 月窗口中任意旧问题误阻断当前可判定后缀。
    suffix = normalized[-required:]

    shape_window = suffix[-(MIN_BULLISH_MONTHS + 1):]
    shape_invalid_months: list[str] = []
    try:
        for item in suffix:
            close = _finite_number(
                _value(item["raw"], "close"),
                f"{item['month']}.close",
            )
            if close <= 0:
                raise ValueError(f"close must be positive: {item['month']}")
            item["close"] = close

        # ``price_shape_valid=False`` 是月末因子不足以认证月内 OHLC 形态，
        # 不是源事实损坏。它仍须通过字段存在、有限且为正的基础完整性门，
        # 但不做跨字段形态比较；若其他可信硬门已明确失败，三态 AND 仍可
        # 安全返回 not_matched。声明形态有效的行若 OHLC 自身非法则继续
        # 优先 blocked。
        for item in shape_window:
            shape_valid = item["price_shape_valid"]
            if not isinstance(shape_valid, bool):
                raise ValueError(
                    f"price_shape_valid must be bool: {item['month']}"
                )
            raw = item["raw"]
            item["open"] = _finite_number(
                _value(raw, "open"),
                f"{item['month']}.open",
            )
            item["high"] = _finite_number(
                _value(raw, "high"),
                f"{item['month']}.high",
            )
            item["low"] = _finite_number(
                _value(raw, "low"),
                f"{item['month']}.low",
            )
            if min(item["open"], item["high"], item["low"]) <= 0:
                raise ValueError(
                    f"open/high/low must be positive: {item['month']}"
                )
            if not shape_valid:
                shape_invalid_months.append(item["month"])
                continue
            if not _valid_ohlc(
                item["open"],
                item["high"],
                item["low"],
                item["close"],
            ):
                raise ValueError(f"invalid OHLC: {item['month']}")
    except ValueError as exc:
        return _seed_result(
            status="blocked",
            reason=str(exc),
            evidence={"required_suffix_months": required},
        )

    latest = suffix[-1]
    closes = [item["close"] for item in suffix]
    moving_averages = {
        window: sum(closes[-window:]) / window for window in MONTHLY_MA_WINDOWS
    }
    ma5 = moving_averages[5]
    aligned = moving_averages[5] > moving_averages[10] > moving_averages[20]

    mandatory_bullish_months = shape_window[:-1]
    known_bearish_months = [
        item["month"]
        for item in mandatory_bullish_months
        if item["price_shape_valid"] is True
        and item["close"] <= item["open"]
    ]
    unknown_bullish_months = [
        item["month"]
        for item in mandatory_bullish_months
        if item["price_shape_valid"] is not True
    ]
    if known_bearish_months:
        streak_met: bool | None = False
    elif unknown_bullish_months:
        streak_met = None
    else:
        streak_met = True

    close_at_or_above_ma5 = latest["close"] >= ma5
    if not close_at_or_above_ma5:
        support_held: bool | None = False
    elif latest["price_shape_valid"] is not True:
        support_held = None
    else:
        support_held = latest["low"] <= ma5

    bullish_streak = 0
    streak_capped_by_invalid_shape = False
    for item in reversed(suffix[:-1]):
        if item["price_shape_valid"] is not True:
            streak_capped_by_invalid_shape = True
            break
        if "open" not in item:
            raw = item["raw"]
            try:
                item["open"] = _finite_number(
                    _value(raw, "open"),
                    f"{item['month']}.open",
                )
                item["high"] = _finite_number(
                    _value(raw, "high"),
                    f"{item['month']}.high",
                )
                item["low"] = _finite_number(
                    _value(raw, "low"),
                    f"{item['month']}.low",
                )
            except ValueError:
                streak_capped_by_invalid_shape = True
                break
            if not _valid_ohlc(
                item["open"],
                item["high"],
                item["low"],
                item["close"],
            ):
                streak_capped_by_invalid_shape = True
                break
        if item["close"] > item["open"]:
            bullish_streak += 1
        else:
            break

    preferred_pullback = (
        latest["close"] < latest["open"]
        if latest["price_shape_valid"] is True
        else None
    )

    conditions = {
        "prior_consecutive_bullish_months": {
            "met": streak_met,
            "count": bullish_streak,
            "minimum": MIN_BULLISH_MONTHS,
            "known_bearish_months": known_bearish_months,
            "unknown_shape_months": unknown_bullish_months,
        },
        "monthly_ma_alignment": {
            "met": aligned,
            "operator": "MA5 > MA10 > MA20",
            "ma5": moving_averages[5],
            "ma10": moving_averages[10],
            "ma20": moving_averages[20],
        },
        "ma5_support": {
            "met": support_held,
            "operator": "low <= MA5 <= close",
            "close_at_or_above_ma5": close_at_or_above_ma5,
            "low": latest.get("low"),
            "ma5": ma5,
            "close": latest["close"],
        },
        "preferred_pullback": {
            "met": preferred_pullback,
            "operator": "close < open",
            "hard_gate": False,
            "open": latest.get("open"),
            "close": latest["close"],
        },
    }
    evidence = {
        "seed_month": latest["month"],
        "seed_month_end": latest["end_date"],
        "completed_months": len(normalized),
        "required_suffix_months": required,
        "excluded_incomplete_months": excluded_incomplete,
        "bullish_streak_before_pullback": bullish_streak,
        "positive_month_streak": bullish_streak,
        "streak_capped_by_invalid_shape": streak_capped_by_invalid_shape,
        "shape_invalid_months": shape_invalid_months,
        "preferred_pullback": preferred_pullback,
        "open": latest.get("open"),
        "low": latest.get("low"),
        "close": latest["close"],
        "ma5": moving_averages[5],
        "ma10": moving_averages[10],
        "ma20": moving_averages[20],
        "conditions": conditions,
    }

    # ``preferred_pullback`` has hard_gate=False and must never enter ``failed``.
    failed = [
        name
        for name in (
            "prior_consecutive_bullish_months",
            "monthly_ma_alignment",
            "ma5_support",
        )
        if conditions[name]["met"] is False
    ]
    indeterminate = [
        name
        for name in (
            "prior_consecutive_bullish_months",
            "monthly_ma_alignment",
            "ma5_support",
        )
        if conditions[name]["met"] is None
    ]
    if failed:
        return _seed_result(
            status="not_matched",
            reason="conditions_not_met",
            evidence={
                **evidence,
                "failed_conditions": failed,
                "indeterminate_conditions": indeterminate,
            },
        )
    if indeterminate:
        return _seed_result(
            status="blocked",
            reason=f"price_shape_invalid: {shape_invalid_months[0]}",
            evidence={
                **evidence,
                "failed_conditions": [],
                "indeterminate_conditions": indeterminate,
            },
        )
    return _seed_result(status="matched", reason="monthly_seed_matched", evidence=evidence)


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    ema = values[0]
    result = [ema]
    for value in values[1:]:
        ema = ema + alpha * (value - ema)
        result.append(ema)
    return result


def macd_state_series(
    closes: Sequence[Any],
    *,
    dates: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """返回 MACD(12,26,9) 状态序列。

    前 ``MACD_MIN_OBSERVATIONS - 1`` 个点保留数值但 ``ready=False``，两个布尔
    状态均为 ``None``，避免把 EMA seed 初期当成已确认信号。
    """
    values = [_finite_number(value, "close") for value in closes or []]
    if not values:
        return []
    if any(value <= 0 for value in values):
        raise ValueError("close must be positive")
    if dates is not None and len(dates) != len(values):
        raise ValueError("dates length must equal closes length")

    ema_fast = _ema_series(values, MACD_FAST)
    ema_slow = _ema_series(values, MACD_SLOW)
    dif_values = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    dea_values = _ema_series(dif_values, MACD_SIGNAL)

    result: list[dict[str, Any]] = []
    for index, (dif, dea) in enumerate(zip(dif_values, dea_values)):
        ready = index + 1 >= MACD_MIN_OBSERVATIONS
        above_zero = (dif > 0 and dea > 0) if ready else None
        bullish_on_zero = (above_zero and dif >= dea) if ready else None
        golden_cross = (
            dif > dea
            and dif_values[index - 1] <= dea_values[index - 1]
            if ready and index > 0
            else None
        )
        result.append(
            {
                "index": index,
                "date": dates[index] if dates is not None else None,
                "dif": dif,
                "dea": dea,
                "hist": 2.0 * (dif - dea),
                "ready": ready,
                "above_zero": above_zero,
                "bullish_on_zero": bullish_on_zero,
                "golden_cross": golden_cross,
            }
        )
    return result


def _bar_volume(raw: Any, date: str) -> float:
    volume = _value(raw, "volume", _MISSING)
    vol = _value(raw, "vol", _MISSING)
    if volume is _MISSING and vol is _MISSING:
        raise ValueError(f"missing field: {date}.volume")
    if volume is not _MISSING and vol is not _MISSING:
        volume_number = _finite_number(volume, f"{date}.volume")
        vol_number = _finite_number(vol, f"{date}.vol")
        if not math.isclose(volume_number, vol_number, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"conflicting volume fields: {date}")
        return volume_number
    return _finite_number(
        volume if volume is not _MISSING else vol,
        f"{date}.volume",
    )


def _normalize_daily_bars(
    adjusted_bars: Sequence[Any],
    target_date: str,
) -> tuple[list[dict[str, Any]], int]:
    target = dt.date.fromisoformat(target_date)
    seen_dates: set[str] = set()
    normalized: list[dict[str, Any]] = []
    excluded_after_target = 0

    for raw in adjusted_bars or []:
        raw_date = _value(raw, "trade_date", _value(raw, "date", _MISSING))
        date = _iso_date(raw_date, "trade_date")
        if dt.date.fromisoformat(date) > target:
            excluded_after_target += 1
            continue
        if date in seen_dates:
            raise ValueError(f"duplicate trade_date: {date}")
        seen_dates.add(date)

        open_ = _finite_number(_value(raw, "open"), f"{date}.open")
        high = _finite_number(_value(raw, "high"), f"{date}.high")
        low = _finite_number(_value(raw, "low"), f"{date}.low")
        close = _finite_number(_value(raw, "close"), f"{date}.close")
        volume = _bar_volume(raw, date)
        if not _valid_ohlc(open_, high, low, close):
            raise ValueError(f"invalid OHLC: {date}")
        if volume < 0:
            raise ValueError(f"volume must be non-negative: {date}")

        normalized.append(
            {
                "trade_date": date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    normalized.sort(key=lambda item: item["trade_date"])
    return normalized, excluded_after_target


def _aggregate_weekly_normalized(
    bars: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    weeks: dict[tuple[int, int], dict[str, Any]] = {}
    for bar in bars:
        date = dt.date.fromisoformat(bar["trade_date"])
        iso = date.isocalendar()
        key = (iso.year, iso.week)
        if key not in weeks:
            weeks[key] = {
                "week": f"{iso.year}-W{iso.week:02d}",
                "trade_date": bar["trade_date"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                "trading_days": 1,
            }
            continue
        current = weeks[key]
        current["trade_date"] = bar["trade_date"]
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
        current["volume"] += bar["volume"]
        current["trading_days"] += 1
    return [weeks[key] for key in sorted(weeks)]


def _dynamic_monthly_ma5_state(
    bars: Sequence[dict[str, Any]],
    target_date: str,
) -> dict[str, Any]:
    """计算目标日所在月的 as-of 动态 MA5 支撑状态。

    每个月只取截至目标日最后一根日线收盘，最近 5 个自然月必须连续且最后一个月
    必须是目标月。所有输入已由调用方统一到目标日前复权坐标。
    """
    target_month = target_date[:7]
    by_month: dict[str, dict[str, Any]] = {}
    for bar in bars:
        month = bar["trade_date"][:7]
        current = by_month.get(month)
        if current is None:
            by_month[month] = {
                "close": bar["close"],
                "low": bar["low"],
                "last_date": bar["trade_date"],
            }
        else:
            current["close"] = bar["close"]
            current["low"] = min(current["low"], bar["low"])
            current["last_date"] = bar["trade_date"]

    available_months = sorted(by_month)
    suffix = available_months[-DYNAMIC_MONTHLY_MA_WINDOW:]
    evidence: dict[str, Any] = {
        "status": "insufficient_history",
        "reason": "dynamic_month_ma5_history_insufficient",
        "hard_gate": True,
        "operator": "current_close >= dynamic_month_ma5",
        "as_of_date": target_date,
        "current_month": target_month,
        "scope": "target_date_as_of",
        "used_for_monthly_seed": False,
        "required_months": DYNAMIC_MONTHLY_MA_WINDOW,
        "available_months": len(available_months),
        "months": suffix,
        "month_closes": [],
        "month_last_dates": [],
        "current_close": None,
        "current_month_low": None,
        "ma5": None,
        "support_held": None,
        "current_month_low_below_target_asof_ma5": None,
        "distance_pct": None,
    }
    if len(suffix) < DYNAMIC_MONTHLY_MA_WINDOW or suffix[-1] != target_month:
        return evidence

    for previous, current in zip(suffix, suffix[1:]):
        if _month_ordinal(current) != _month_ordinal(previous) + 1:
            evidence["status"] = "blocked"
            evidence["reason"] = (
                f"dynamic_month_ma5_month_gap: {previous}->{current}"
            )
            return evidence

    closes = [float(by_month[month]["close"]) for month in suffix]
    last_dates = [str(by_month[month]["last_date"]) for month in suffix]
    ma5 = sum(closes) / DYNAMIC_MONTHLY_MA_WINDOW
    current_close = closes[-1]
    current_month_low = float(by_month[target_month]["low"])
    support_held = current_close >= ma5
    return {
        **evidence,
        "status": "complete",
        "reason": (
            "current_close_at_or_above_dynamic_month_ma5"
            if support_held
            else "current_close_below_dynamic_month_ma5"
        ),
        "month_closes": closes,
        "month_last_dates": last_dates,
        "current_close": current_close,
        "current_month_low": current_month_low,
        "ma5": ma5,
        "support_held": support_held,
        "current_month_low_below_target_asof_ma5": current_month_low < ma5,
        "distance_pct": (current_close / ma5 - 1.0) * 100.0,
    }


def aggregate_weekly_bars_as_of(
    adjusted_bars: Sequence[Any],
    target_date: str,
) -> list[dict[str, Any]]:
    """把日线聚合为目标日 as-of 周线，目标日之后的 bar 只计数排除、不参与聚合。"""
    target = _iso_date(target_date, "target_date")
    normalized, _ = _normalize_daily_bars(adjusted_bars, target)
    return _aggregate_weekly_normalized(normalized)


def daily_weekly_macd_states(
    adjusted_bars: Sequence[Any],
    target_date: str,
) -> dict[str, Any]:
    """返回截至目标日的日线/周线 MACD 状态序列。"""
    target = _iso_date(target_date, "target_date")
    normalized, excluded = _normalize_daily_bars(adjusted_bars, target)
    daily = macd_state_series(
        [bar["close"] for bar in normalized],
        dates=[bar["trade_date"] for bar in normalized],
    )
    weekly_bars = _aggregate_weekly_normalized(normalized)
    weekly = macd_state_series(
        [bar["close"] for bar in weekly_bars],
        dates=[bar["trade_date"] for bar in weekly_bars],
    )
    return {
        "daily": daily,
        "weekly": weekly,
        "weekly_bars": weekly_bars,
        "as_of_bar_count": len(normalized),
        "excluded_after_target_count": excluded,
    }


def _attach_monitor_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """同时保留顶层细分字段与 orchestrator 使用的 ``stage/evidence`` 形状。"""
    daily = result["daily"]
    weekly = result["weekly"]
    volume = result["volume_auxiliary"]
    dynamic_monthly_ma5 = result["dynamic_monthly_ma5"]
    current_daily = daily.get("current") or {}
    current_weekly = weekly.get("current") or {}
    result["stage"] = result["status"]
    result["evidence"] = {
        "reason": result["reason"],
        "error": result["reason"] if result["status"] == "blocked" else None,
        "target_date": result["target_date"],
        "seed_month_end": result["seed_month_end"],
        "data_quality": result["data_quality"],
        "dynamic_monthly_ma5": dynamic_monthly_ma5,
        "daily_macd": current_daily,
        "weekly_macd": current_weekly,
        "current_daily": current_daily,
        "current_weekly": current_weekly,
        "reentry_date": daily.get("first_above_zero_flip_after_seed"),
        "first_reentry_date": daily.get("first_above_zero_flip_after_seed"),
        "current_above_zero": current_daily.get("above_zero"),
        "volume": volume,
        "volume_confirmation": volume,
    }
    return result


def _empty_monitor(
    *,
    status: str,
    reason: str,
    target_date: str | None,
    seed_month_end: str | None,
    input_bar_count: int,
    excluded_after_target_count: int = 0,
) -> dict[str, Any]:
    return _attach_monitor_evidence({
        "status": status,
        "reason": reason,
        "target_date": target_date,
        "seed_month_end": seed_month_end,
        "data_quality": {
            "input_bar_count": input_bar_count,
            "as_of_bar_count": None,
            "excluded_after_target_count": excluded_after_target_count,
        },
        "daily": {
            "current": None,
            "first_above_zero_flip_after_seed": None,
            "reactivated": None,
        },
        "weekly": {"current": None, "bar_count": None},
        "dynamic_monthly_ma5": {
            "status": "unavailable",
            "reason": reason,
            "hard_gate": True,
            "operator": "current_close >= dynamic_month_ma5",
            "as_of_date": target_date,
            "current_month": target_date[:7] if target_date else None,
            "scope": "target_date_as_of",
            "used_for_monthly_seed": False,
            "required_months": DYNAMIC_MONTHLY_MA_WINDOW,
            "available_months": None,
            "months": [],
            "month_closes": [],
            "month_last_dates": [],
            "current_close": None,
            "current_month_low": None,
            "ma5": None,
            "support_held": None,
            "current_month_low_below_target_asof_ma5": None,
            "distance_pct": None,
        },
        "volume_auxiliary": {
            "classification": "existing_system_auxiliary",
            "hard_gate": False,
            "available": False,
            "confirmed": None,
        },
    })


def _validate_volume_windows(volume_windows: Sequence[int]) -> tuple[int, ...]:
    windows = tuple(volume_windows or ())
    if not windows:
        raise ValueError("volume_windows must not be empty")
    if any(
        isinstance(window, bool) or not isinstance(window, int) or window <= 0
        for window in windows
    ):
        raise ValueError("volume_windows must contain positive integers")
    if len(set(windows)) != len(windows):
        raise ValueError("volume_windows must not contain duplicates")
    return windows


def evaluate_daily_monitor(
    adjusted_bars: Sequence[Any],
    target_date: str,
    seed_month_end: str,
    volume_windows: Sequence[int] = DEFAULT_VOLUME_WINDOWS,
) -> dict[str, Any]:
    """评估月线种子后的日/周观察状态。

    状态优先级为 ``blocked`` → 日线历史不足 → 动态月 MA5 历史不足 →
    ``monthly_seeded``（动态月 MA5 未守住或等待日线）→
    ``daily_reactivated`` → ``resonance_observed``。
    ``resonance_observed`` 要求 seed 后已发生首次日线 ``above_zero`` 翻转，
    当前收盘不低于动态月 MA5，且当前日、周均为 ``bullish_on_zero``。
    量能仅作辅助证据，不参与状态门。
    """
    input_count = len(adjusted_bars or [])
    try:
        target = _iso_date(target_date, "target_date")
        seed = _iso_date(seed_month_end, "seed_month_end")
        windows = _validate_volume_windows(volume_windows)
        if seed > target:
            raise ValueError("seed_month_end must not be after target_date")
        normalized, excluded = _normalize_daily_bars(adjusted_bars, target)
    except ValueError as exc:
        return _empty_monitor(
            status="blocked",
            reason=str(exc),
            target_date=str(target_date) if target_date is not None else None,
            seed_month_end=str(seed_month_end) if seed_month_end is not None else None,
            input_bar_count=input_count,
        )

    if not normalized:
        return _empty_monitor(
            status="blocked",
            reason="no_bar_on_or_before_target",
            target_date=target,
            seed_month_end=seed,
            input_bar_count=input_count,
            excluded_after_target_count=excluded,
        )
    if normalized[-1]["trade_date"] != target:
        result = _empty_monitor(
            status="blocked",
            reason=(
                "target_bar_missing: "
                f"last_as_of_bar={normalized[-1]['trade_date']} target={target}"
            ),
            target_date=target,
            seed_month_end=seed,
            input_bar_count=input_count,
            excluded_after_target_count=excluded,
        )
        result["data_quality"]["as_of_bar_count"] = len(normalized)
        return result

    dynamic_monthly_ma5 = _dynamic_monthly_ma5_state(normalized, target)
    daily = macd_state_series(
        [bar["close"] for bar in normalized],
        dates=[bar["trade_date"] for bar in normalized],
    )
    weekly_bars = _aggregate_weekly_normalized(normalized)
    weekly = macd_state_series(
        [bar["close"] for bar in weekly_bars],
        dates=[bar["trade_date"] for bar in weekly_bars],
    )
    current_daily = daily[-1]
    current_weekly = weekly[-1]

    first_flip: str | None = None
    for previous, current in zip(daily, daily[1:]):
        if (
            current["date"] > seed
            and previous["above_zero"] is False
            and current["above_zero"] is True
        ):
            first_flip = current["date"]
            break

    prior_mas: dict[str, float] = {}
    max_window = max(windows)
    volume_available = len(normalized) >= max_window + 1
    if volume_available:
        for window in windows:
            prior = normalized[-(window + 1):-1]
            prior_mas[str(window)] = sum(bar["volume"] for bar in prior) / window
    current_bar = normalized[-1]
    bullish_bar = current_bar["close"] > current_bar["open"]
    volume_above_all = (
        all(current_bar["volume"] > average for average in prior_mas.values())
        if volume_available
        else None
    )
    volume_confirmed = (
        bullish_bar and bool(volume_above_all) if volume_available else None
    )
    volume_auxiliary = {
        "classification": "existing_system_auxiliary",
        "hard_gate": False,
        "windows": list(windows),
        "available": volume_available,
        "bullish_bar": bullish_bar,
        "volume": current_bar["volume"],
        "prior_volume_mas": prior_mas,
        "volume_above_all_prior_mas": volume_above_all,
        "confirmed": volume_confirmed,
    }

    data_quality = {
        "input_bar_count": input_count,
        "as_of_bar_count": len(normalized),
        "excluded_after_target_count": excluded,
        "dynamic_month_count": dynamic_monthly_ma5["available_months"],
    }
    daily_evidence = {
        "current": current_daily,
        "first_above_zero_flip_after_seed": first_flip,
        "reactivated": bool(first_flip and current_daily["above_zero"] is True),
    }
    weekly_evidence = {
        "current": current_weekly,
        "bar_count": len(weekly_bars),
        "as_of_week_end": weekly_bars[-1]["trade_date"],
    }

    ready_on_or_before_seed = any(
        point["ready"] and point["date"] <= seed for point in daily
    )
    if dynamic_monthly_ma5["status"] == "blocked":
        return _attach_monitor_evidence({
            "status": "blocked",
            "reason": dynamic_monthly_ma5["reason"],
            "target_date": target,
            "seed_month_end": seed,
            "data_quality": data_quality,
            "daily": daily_evidence,
            "weekly": weekly_evidence,
            "dynamic_monthly_ma5": dynamic_monthly_ma5,
            "volume_auxiliary": volume_auxiliary,
        })

    if current_daily["ready"] is not True or not ready_on_or_before_seed:
        reason = (
            "daily_macd_history_insufficient"
            if current_daily["ready"] is not True
            else "pre_seed_macd_history_insufficient"
        )
        return _attach_monitor_evidence({
            "status": "insufficient_history",
            "reason": reason,
            "target_date": target,
            "seed_month_end": seed,
            "data_quality": data_quality,
            "daily": daily_evidence,
            "weekly": weekly_evidence,
            "dynamic_monthly_ma5": dynamic_monthly_ma5,
            "volume_auxiliary": volume_auxiliary,
        })

    if dynamic_monthly_ma5["status"] != "complete":
        return _attach_monitor_evidence({
            "status": "insufficient_history",
            "reason": dynamic_monthly_ma5["reason"],
            "target_date": target,
            "seed_month_end": seed,
            "data_quality": data_quality,
            "daily": daily_evidence,
            "weekly": weekly_evidence,
            "dynamic_monthly_ma5": dynamic_monthly_ma5,
            "volume_auxiliary": volume_auxiliary,
        })

    if dynamic_monthly_ma5["support_held"] is not True:
        return _attach_monitor_evidence({
            "status": "monthly_seeded",
            "reason": "current_close_below_dynamic_month_ma5",
            "target_date": target,
            "seed_month_end": seed,
            "data_quality": data_quality,
            "daily": daily_evidence,
            "weekly": weekly_evidence,
            "dynamic_monthly_ma5": dynamic_monthly_ma5,
            "volume_auxiliary": volume_auxiliary,
        })

    daily_reactivated = daily_evidence["reactivated"]
    resonance = bool(
        daily_reactivated
        and current_daily["bullish_on_zero"] is True
        and current_weekly["bullish_on_zero"] is True
    )
    if resonance:
        status = "resonance_observed"
        reason = "daily_and_weekly_bullish_on_zero"
    elif daily_reactivated:
        status = "daily_reactivated"
        reason = "post_seed_daily_above_zero_flip_is_current"
    else:
        status = "monthly_seeded"
        reason = "waiting_for_post_seed_daily_above_zero_flip"

    return _attach_monitor_evidence({
        "status": status,
        "reason": reason,
        "target_date": target,
        "seed_month_end": seed,
        "data_quality": data_quality,
        "daily": daily_evidence,
        "weekly": weekly_evidence,
        "dynamic_monthly_ma5": dynamic_monthly_ma5,
        "volume_auxiliary": volume_auxiliary,
    })


__all__ = [
    "MACD_MIN_OBSERVATIONS",
    "UNRESOLVED_RULES",
    "aggregate_weekly_bars_as_of",
    "daily_weekly_macd_states",
    "detect_monthly_seed",
    "evaluate_daily_monitor",
    "macd_state_series",
]
