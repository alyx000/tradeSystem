"""情绪核心前复权区间统计与波段证据。"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from services.emotion_leader import constants as C
from utils.qfq import OHLC_PRICE_KEYS, apply_qfq


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def infer_wave_evidence(bars: list[dict]) -> dict:
    """给出机械波段证据；标签始终属于 [判断]，不参与候选晋级。"""
    if not bars:
        return {"wave_label": "未计算", "confirmed_restarts": 0, "candidate_restart": False}

    running_peak = _finite_float(bars[0].get("high"))
    if running_peak is None or running_peak <= 0:
        return {"wave_label": "未计算", "confirmed_restarts": 0, "candidate_restart": False}
    in_pullback = False
    prior_peak = running_peak
    pullback_low: float | None = None
    confirmed = 0

    for bar in bars:
        high = _finite_float(bar.get("high"))
        close = _finite_float(bar.get("close"))
        if high is None or close is None or high <= 0 or close <= 0:
            return {"wave_label": "未计算", "confirmed_restarts": 0, "candidate_restart": False}
        if not in_pullback:
            running_peak = max(running_peak, high)
            drawdown_pct = (close / running_peak - 1.0) * 100.0
            if drawdown_pct <= -C.WAVE_PULLBACK_PCT:
                in_pullback = True
                prior_peak = running_peak
                pullback_low = close
        else:
            pullback_low = min(pullback_low or close, close)
            if high >= prior_peak:
                confirmed += 1
                in_pullback = False
                running_peak = max(prior_peak, high)
                pullback_low = None

    current_close = _finite_float(bars[-1].get("close"))
    recovery_pct = None
    candidate = False
    if in_pullback and pullback_low and current_close:
        recovery_pct = (current_close / pullback_low - 1.0) * 100.0
        candidate = recovery_pct >= C.WAVE_RECOVERY_PCT

    if confirmed >= 2:
        label = "多波"
    elif confirmed == 1:
        label = "二波"
    elif candidate:
        label = "二波候选"
    else:
        label = "单波"
    return {
        "wave_label": label,
        "confirmed_restarts": confirmed,
        "candidate_restart": candidate,
        "recovery_pct": round(recovery_pct, 2) if recovery_pct is not None else None,
    }


def calculate_metrics(bars: list[dict], factors: list[dict], launch_date: str, target_date: str) -> dict:
    ordered = sorted((dict(row) for row in bars), key=lambda row: str(row.get("trade_date") or ""))
    if not ordered or str(ordered[-1].get("trade_date")) != target_date:
        return {"metric_status": "source_failed", "metric_error": "目标日行情缺失或陈旧"}
    adjusted = apply_qfq(ordered, factors, keys=OHLC_PRICE_KEYS)
    if adjusted is None:
        return {"metric_status": "source_failed", "metric_error": "复权因子缺失或与行情错位"}

    base_rows = [row for row in adjusted if str(row.get("trade_date")) < launch_date]
    life_bars = [row for row in adjusted if launch_date <= str(row.get("trade_date")) <= target_date]
    if not base_rows or not life_bars:
        return {"metric_status": "source_failed", "metric_error": "启动日前基准或生命周期行情不足"}
    base_close = _finite_float(base_rows[-1].get("close"))
    current_close = _finite_float(life_bars[-1].get("close"))
    highs = [(_finite_float(row.get("high")), str(row.get("trade_date"))) for row in life_bars]
    if base_close is None or current_close is None or base_close <= 0 or current_close <= 0:
        return {"metric_status": "source_failed", "metric_error": "基准价或目标日收盘价非法"}
    if any(value is None or value <= 0 for value, _ in highs):
        return {"metric_status": "source_failed", "metric_error": "生命周期最高价存在缺失"}
    peak_high, peak_date = max(highs, key=lambda item: (item[0], item[1]))  # type: ignore[arg-type]
    max_gain_pct = (peak_high / base_close - 1.0) * 100.0  # type: ignore[operator]
    interval_gain_pct = (current_close / base_close - 1.0) * 100.0
    distance_from_peak_pct = (current_close / peak_high - 1.0) * 100.0  # type: ignore[operator]
    today_pct = _finite_float(life_bars[-1].get("pct_chg"))
    return {
        "metric_status": "ok",
        "base_date": str(base_rows[-1].get("trade_date")),
        "base_close_qfq": round(base_close, 4),
        "current_close_qfq": round(current_close, 4),
        "max_gain_pct": round(max_gain_pct, 2),
        "interval_gain_pct": round(interval_gain_pct, 2),
        "distance_from_peak_pct": round(distance_from_peak_pct, 2),
        "peak_date": peak_date,
        "today_pct_chg": round(today_pct, 2) if today_pct is not None else None,
        "new_peak_today": peak_date == target_date,
        **infer_wave_evidence(life_bars),
    }


def fetch_metrics(registry, item: dict, target_date: str) -> dict:
    start = (date.fromisoformat(item["launch_date"]) - timedelta(days=20)).isoformat()
    code = item["code"]
    try:
        quote = registry.call("get_stock_daily_range", code, start, target_date)
    except Exception as exc:  # noqa: BLE001 - provider exception becomes per-stock partial
        return {"metric_status": "source_failed", "metric_error": f"行情调用异常:{exc}"}
    bars = getattr(quote, "data", None)
    if not getattr(quote, "success", False) or not isinstance(bars, list) or not bars:
        return {
            "metric_status": "source_failed",
            "metric_error": str(getattr(quote, "error", "") or "区间行情不可得"),
        }
    try:
        factor = registry.call("get_stock_adj_factor_range", code, start, target_date)
    except Exception as exc:  # noqa: BLE001
        return {"metric_status": "source_failed", "metric_error": f"复权因子调用异常:{exc}"}
    factors = getattr(factor, "data", None)
    if not getattr(factor, "success", False) or not isinstance(factors, list) or not factors:
        return {
            "metric_status": "source_failed",
            "metric_error": str(getattr(factor, "error", "") or "复权因子不可得"),
        }
    result = calculate_metrics(bars, factors, item["launch_date"], target_date)
    result["quote_source"] = str(getattr(quote, "source", "") or "")
    result["factor_source"] = str(getattr(factor, "source", "") or "")
    return result
