"""全市场七天横盘不创新低事实扫描。

口径来自 ``teacher_notes#548``：先出现一个低点，随后阳线、阴线都不再创新低，
横住至少七天。程序把它机械化为：截至 T 日最近 8 根有效个股 K 线中，第 1 根
为基准，后续 7 根前复权最低价均不低于基准最低价；同时用总振幅、收盘最大
偏离和期末净漂移三道硬门确认横盘，排除台阶式上涨。停牌日没有 K 线，不占天数。

扫描只读取行情、复权因子、证券身份与当前申万二级成分快照，不写业务库、关注池
或计划层。申万二级只作展示，不参与筛选、预截断或 PK；板块源失败会单独降级，
不影响已经由行情硬门确认的扫描状态。全市场硬源或覆盖门失败时返回
``source_failed``，不能把未完成扫描写成空候选。
"""
from __future__ import annotations

import math
import datetime
import re
import sqlite3
from collections import Counter

from services.monthly_pattern.market import is_b_share_code
from services.no_new_low import constants as C
from utils import is_st_stock
from utils.qfq import OHLC_PRICE_KEYS, apply_qfq


def _bare_code(value) -> str:
    return str(value or "").strip().upper().split(".", 1)[0]


def _normalize_date(value) -> str:
    raw = str(value or "").replace("-", "")[:8]
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _recent_trade_dates(conn: sqlite3.Connection, date: str, count: int) -> list[str]:
    rows = conn.execute(
        "SELECT date FROM trade_calendar WHERE is_open=1 AND date<=? "
        "ORDER BY date DESC LIMIT ?",
        (date, count),
    ).fetchall()
    dates = [str(row[0]) for row in rows]
    if len(dates) != count or not dates or dates[0] != date:
        return []
    # 开放日数量足够不代表中间没有漏日；缺失日不能当作休市跳过。
    calendar = conn.execute(
        "SELECT date, is_open FROM trade_calendar WHERE date BETWEEN ? AND ? ORDER BY date",
        (dates[-1], date),
    ).fetchall()
    try:
        start = datetime.date.fromisoformat(dates[-1])
        end = datetime.date.fromisoformat(date)
    except ValueError:
        return []
    expected = [(start + datetime.timedelta(days=i)).isoformat()
                for i in range((end - start).days + 1)]
    if ([str(row[0]) for row in calendar] != expected
            or any(row[1] not in (0, 1) for row in calendar)):
        return []
    return list(reversed(dates))


def _call_with_retry(registry, capability: str, *args):
    last = None
    for attempt in range(C.GLOBAL_SOURCE_ATTEMPTS):
        last = registry.call(capability, *args)
        if getattr(last, "success", False) and isinstance(last.data, list) and last.data:
            return last, attempt
    return last, C.GLOBAL_SOURCE_ATTEMPTS - 1


def _source_error(result) -> str:
    """保留来源失败，避免被空列表数量校验覆盖；告警不携带上游账户链接。"""
    error = str(getattr(result, "error", "") or "")
    if error:
        return re.sub(r"https?://\S+", "[链接已隐藏]", error)
    if not getattr(result, "success", False):
        return "source_failed_without_error"
    if not isinstance(result.data, list):
        return "invalid_source_payload: expected list"
    return ""


def _enrich_industries(registry, candidates: list[dict]) -> dict:
    """用当前申万二级成分快照给候选打展示标签，不改变任何筛选或排序字段。"""
    if not candidates:
        return {
            "status": "skipped", "basis": "current_membership_snapshot",
            "source": "", "candidate_rows": 0, "matched_rows": 0,
            "coverage": 1.0, "retry_count": 0, "error": "no_candidates",
        }

    last = None
    last_error = ""
    retry_count = 0
    for attempt in range(C.GLOBAL_SOURCE_ATTEMPTS):
        retry_count = attempt
        try:
            last = registry.call("get_stock_sw_industry_map")
        except Exception as exc:  # 辅助展示源失败不得打掉核心扫描。
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if getattr(last, "success", False) and isinstance(last.data, dict) and last.data:
            normalized: dict[str, str] = {}
            for raw_code, entry in last.data.items():
                code = _bare_code(raw_code)
                industry = entry.get("sw_l2") if isinstance(entry, dict) else entry
                label = str(industry or "").strip()
                if re.fullmatch(r"\d{6}", code) and label not in {"", "未分类", "UNCLASSIFIED"}:
                    normalized[code] = label
            if not normalized:
                last_error = "industry_map_has_no_valid_sw_l2"
                continue
            matched = 0
            for candidate in candidates:
                industry = normalized.get(candidate["code"])
                candidate["industry"] = industry or "未分类"
                matched += int(bool(industry))
            coverage = matched / len(candidates)
            return {
                "status": "ok" if matched == len(candidates) else "partial",
                "basis": "current_membership_snapshot",
                "source": str(getattr(last, "source", "") or "unknown"),
                "candidate_rows": len(candidates), "matched_rows": matched,
                "coverage": coverage, "retry_count": retry_count,
                "error": "" if matched == len(candidates) else f"unclassified_candidates:{len(candidates) - matched}",
            }
        last_error = str(getattr(last, "error", "") or "invalid_or_empty_industry_map")

    for candidate in candidates:
        candidate["industry"] = "未分类"
    return {
        "status": "source_failed", "basis": "current_membership_snapshot",
        "source": str(getattr(last, "source", "") or "unknown") if last is not None else "unknown",
        "candidate_rows": len(candidates), "matched_rows": 0,
        "coverage": 0.0, "retry_count": retry_count,
        "error": last_error or "industry_map_unavailable",
    }


def _daily_map(rows: list[dict], date: str, min_market_rows: int) -> tuple[dict | None, str | None]:
    if len(rows) < min_market_rows:
        return None, f"market_daily_rows:{date}:{len(rows)}<{min_market_rows}"
    mapped: dict[str, dict] = {}
    seen: set[str] = set()
    for row in rows:
        code = _bare_code(row.get("ts_code") or row.get("code"))
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            return None, f"market_daily_duplicate_or_invalid_code:{date}:{code or '?'}"
        seen.add(code)
        if _normalize_date(row.get("trade_date")) != date:
            return None, f"market_daily_stale_date:{date}:{row.get('trade_date')}"
        values = {key: _number(row.get(key)) for key in (*OHLC_PRICE_KEYS, "vol", "amount")}
        if any(values[key] is None or values[key] <= 0 for key in OHLC_PRICE_KEYS):
            continue
        if values["vol"] is None or values["vol"] < 0 or values["amount"] is None or values["amount"] < 0:
            continue
        mapped[code] = {
            **row,
            **values,
            "code": code,
            "trade_date": date,
        }
    if len(mapped) < min_market_rows:
        return None, f"market_daily_valid_rows:{date}:{len(mapped)}<{min_market_rows}"
    return mapped, None


def _factor_map(rows: list[dict], date: str, min_market_rows: int) -> tuple[dict | None, str | None]:
    if len(rows) < min_market_rows:
        return None, f"adj_factor_rows:{date}:{len(rows)}<{min_market_rows}"
    mapped: dict[str, dict] = {}
    seen: set[str] = set()
    for row in rows:
        code = _bare_code(row.get("ts_code") or row.get("code"))
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            return None, f"adj_factor_duplicate_or_invalid_code:{date}:{code or '?'}"
        seen.add(code)
        if _normalize_date(row.get("trade_date")) != date:
            return None, f"adj_factor_stale_date:{date}:{row.get('trade_date')}"
        factor = _number(row.get("adj_factor"))
        if factor is None or factor <= 0:
            continue
        mapped[code] = {"trade_date": date, "adj_factor": factor}
    if len(mapped) < min_market_rows:
        return None, f"adj_factor_valid_rows:{date}:{len(mapped)}<{min_market_rows}"
    return mapped, None


def _universe_map(rows: list[dict], date: str, min_market_rows: int) -> tuple[dict | None, str | None]:
    mapped: dict[str, dict] = {}
    target = date.replace("-", "")
    for row in rows:
        code = _bare_code(row.get("ts_code") or row.get("code") or row.get("symbol"))
        name = str(row.get("name") or "").strip()
        list_date = str(row.get("list_date") or "").replace("-", "")
        delist_date = str(row.get("delist_date") or "").replace("-", "")
        if not re.fullmatch(r"\d{6}", code) or not name or code in mapped:
            return None, f"stock_universe_duplicate_or_invalid:{code or '?'}"
        if len(list_date) != 8 or not list_date.isdigit():
            return None, f"stock_universe_invalid_list_date:{code}"
        if delist_date and (len(delist_date) != 8 or not delist_date.isdigit()):
            return None, f"stock_universe_invalid_delist_date:{code}"
        if list_date <= target and (not delist_date or delist_date >= target):
            mapped[code] = dict(row)
    if len(mapped) < min_market_rows:
        return None, f"stock_universe_rows:{len(mapped)}<{min_market_rows}"
    return mapped, None


def _competition_ranks(items: list[dict], key_fn) -> dict[str, int]:
    ranks: dict[str, int] = {}
    previous = object()
    rank = 0
    for position, item in enumerate(items, start=1):
        key = key_fn(item)
        if key != previous:
            rank = position
            previous = key
        ranks[item["code"]] = rank
    return ranks


def _pool_business_key(item: dict) -> tuple:
    """PK 强池按横盘质量排序；成交额只作最后破平，代码不参与业务名次。"""
    volume_ratio = _number(item.get("volume_ratio"))
    volume_stability = -abs(volume_ratio - 1.0) if volume_ratio is not None else float("-inf")
    return (
        -round(float(item.get("base_range_pct") or 0.0), 6),
        -round(float(item.get("max_close_deviation_pct") or 0.0), 6),
        -round(float(item.get("abs_window_return_pct") or 0.0), 6),
        round(volume_stability, 6),
        round(float(item.get("today_amount_yi") or 0.0), 6),
    )


def _evaluate_candidate(identity: dict, bars: list[dict], factors: list[dict]) -> tuple[dict | None, str]:
    required = C.REQUIRED_STOCK_BARS
    if len(bars) < required:
        return None, "insufficient_history"
    bars = bars[-required:]
    factors = factors[-required:]
    adjusted = apply_qfq(bars, factors, keys=OHLC_PRICE_KEYS)
    if adjusted is None:
        return None, "qfq_failed"

    anchor = adjusted[0]
    following = adjusted[1:]
    anchor_low = _number(anchor.get("low"))
    if anchor_low is None or anchor_low <= 0:
        return None, "qfq_failed"
    # 创新低是严格小于；触及同一低点不算新低。极小浮点误差用相对容差吸收。
    tolerance = max(abs(anchor_low) * 1e-10, 1e-12)
    if any(float(row["low"]) < anchor_low - tolerance for row in following):
        return None, "new_low_within_seven_bars"

    latest = adjusted[-1]
    base_high = max(float(row["high"]) for row in adjusted)
    base_low = min(float(row["low"]) for row in adjusted)
    anchor_close = float(anchor["close"])
    base_range_pct = (base_high / base_low - 1.0) * 100.0
    close_deviations = [abs(float(row["close"]) / anchor_close - 1.0) * 100.0 for row in adjusted]
    max_close_deviation_pct = max(close_deviations)
    window_return_pct = (float(latest["close"]) / anchor_close - 1.0) * 100.0
    abs_window_return_pct = abs(window_return_pct)
    threshold_tolerance = 1e-9
    if base_range_pct > C.MAX_BASE_RANGE_PCT + threshold_tolerance:
        return None, "range_too_wide"
    if max_close_deviation_pct > C.MAX_CLOSE_DEVIATION_PCT + threshold_tolerance:
        return None, "close_band_broken"
    if abs_window_return_pct > C.MAX_ABS_WINDOW_RETURN_PCT + threshold_tolerance:
        return None, "net_drift_too_large"

    later_floor = min(float(row["low"]) for row in following)
    previous_volumes = [float(row["vol"]) for row in adjusted[:-1] if _number(row.get("vol")) is not None]
    mean_previous_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0.0
    latest_volume = float(latest.get("vol") or 0.0)

    return {
        "code": _bare_code(identity.get("ts_code") or identity.get("code") or identity.get("symbol")),
        "name": str(identity.get("name") or "").strip(),
        "anchor_date": anchor["trade_date"],
        "latest_date": latest["trade_date"],
        "anchor_low": anchor_low,
        "latest_low": float(latest["low"]),
        "latest_close": float(latest["close"]),
        "low_floor_margin_pct": (later_floor / anchor_low - 1.0) * 100.0,
        "latest_low_lift_pct": (float(latest["low"]) / anchor_low - 1.0) * 100.0,
        "close_vs_anchor_pct": (float(latest["close"]) / anchor_low - 1.0) * 100.0,
        "window_return_pct": window_return_pct,
        "abs_window_return_pct": abs_window_return_pct,
        "max_close_deviation_pct": max_close_deviation_pct,
        "base_range_pct": base_range_pct,
        "volume_ratio": latest_volume / mean_previous_volume if mean_previous_volume > 0 else None,
        # Tushare daily.amount 单位为千元；1 亿元 = 100000 千元。
        "today_amount_yi": float(latest.get("amount") or 0.0) / 100_000.0,
        "bar_count": required,
    }, "matched"


def run_daily(
    conn: sqlite3.Connection,
    registry,
    date: str,
    *,
    snapshot_market_days: int = C.SNAPSHOT_MARKET_DAYS,
    min_market_rows: int = C.MIN_MARKET_ROWS,
    min_join_coverage: float = C.MIN_DAILY_FACTOR_JOIN_COVERAGE,
    min_target_coverage: float = C.MIN_TARGET_UNIVERSE_COVERAGE,
) -> dict:
    dates = _recent_trade_dates(conn, date, snapshot_market_days)
    if not dates:
        return {
            "status": "source_failed", "date": date, "failed_sources": {
                "trade_calendar": f"无法确认截至目标日的最近 {snapshot_market_days} 个开放日"
            },
        }

    universe_result, universe_retry = _call_with_retry(registry, "get_stock_universe_as_of", date)
    universe_rows = universe_result.data if (
        getattr(universe_result, "success", False) and isinstance(universe_result.data, list)
    ) else []
    universe, universe_error = _universe_map(universe_rows, date, min_market_rows)
    if universe is None:
        return {
            "status": "source_failed", "date": date,
            "failed_sources": {"stock_universe_as_of": _source_error(universe_result) or universe_error},
            "retries": {"stock_universe_as_of": universe_retry},
        }

    bars_by_code: dict[str, list[dict]] = {code: [] for code in universe}
    factors_by_code: dict[str, list[dict]] = {code: [] for code in universe}
    sources: dict[str, dict] = {}
    retries: dict[str, dict] = {}
    coverage_by_date: dict[str, dict] = {}

    for trade_date in dates:
        daily_result, daily_retry = _call_with_retry(registry, "get_market_daily_quotes", trade_date)
        factor_result, factor_retry = _call_with_retry(registry, "get_adj_factor", trade_date)
        retries[trade_date] = {"daily": daily_retry, "adj_factor": factor_retry}
        daily_rows = daily_result.data if (
            getattr(daily_result, "success", False) and isinstance(daily_result.data, list)
        ) else []
        factor_rows = factor_result.data if (
            getattr(factor_result, "success", False) and isinstance(factor_result.data, list)
        ) else []
        daily, daily_error = _daily_map(daily_rows, trade_date, min_market_rows)
        factors, factor_error = _factor_map(factor_rows, trade_date, min_market_rows)
        if daily is None or factors is None:
            return {
                "status": "source_failed", "date": date,
                "failed_sources": {
                    f"market_snapshot:{trade_date}": "; ".join(
                        error for error in (
                            _source_error(daily_result) or daily_error,
                            _source_error(factor_result) or factor_error,
                        ) if error
                    )
                },
                "retries": retries,
            }
        joint = set(daily) & set(factors)
        join_coverage = len(joint) / max(len(daily), len(factors), 1)
        target_coverage = len(joint & set(universe)) / max(len(universe), 1)
        coverage_by_date[trade_date] = {
            "daily_rows": len(daily), "factor_rows": len(factors), "joint_rows": len(joint),
            "join_coverage": join_coverage, "target_universe_coverage": target_coverage,
        }
        if join_coverage < min_join_coverage:
            return {
                "status": "source_failed", "date": date,
                "failed_sources": {
                    f"daily_adj_factor_coverage:{trade_date}":
                    f"{join_coverage:.2%}<{min_join_coverage:.2%}"
                },
                "coverage_by_date": coverage_by_date, "retries": retries,
            }
        # 仅目标日要求覆盖当前可交易宇宙；历史日包含尚未上市股票，不能用当前分母硬判。
        if trade_date == date and target_coverage < min_target_coverage:
            return {
                "status": "source_failed", "date": date,
                "failed_sources": {
                    "target_universe_coverage": f"{target_coverage:.2%}<{min_target_coverage:.2%}"
                },
                "coverage_by_date": coverage_by_date, "retries": retries,
            }
        sources[trade_date] = {
            "daily": str(getattr(daily_result, "source", "") or "unknown"),
            "adj_factor": str(getattr(factor_result, "source", "") or "unknown"),
        }
        for code in joint & set(universe):
            bars_by_code[code].append(daily[code])
            factors_by_code[code].append(factors[code])

    rejects = Counter()
    candidates: list[dict] = []
    for code, identity in sorted(universe.items()):
        if is_b_share_code(code):
            rejects["b_share"] += 1
            continue
        if is_st_stock(identity.get("name", "")):
            rejects["st_or_delist"] += 1
            continue
        # 历史停牌不占 8 根有效 K 线，但目标日必须有行情+复权联合事实；否则旧日
        # amount 会被误标为 T 日成交额，也不属于本交易日可做横截面 PK 的候选。
        if not bars_by_code[code] or bars_by_code[code][-1].get("trade_date") != date:
            rejects["target_day_no_joint_bar"] += 1
            continue
        candidate, reason = _evaluate_candidate(identity, bars_by_code[code], factors_by_code[code])
        if candidate is None:
            rejects[reason] += 1
            continue
        candidates.append(candidate)

    # 代码只保证序列化稳定；业务 rank 由不含代码的事实 key 计算，完全同值为并列。
    candidates.sort(key=lambda item: (_pool_business_key(item), item["code"]), reverse=True)
    horizontal_ranks = _competition_ranks(candidates, _pool_business_key)
    for item in candidates:
        item["horizontal_rank"] = horizontal_ranks[item["code"]]
    industry = _enrich_industries(registry, candidates)
    pk_pool = candidates[: C.PK_POOL_MAX]

    return {
        "status": "ok", "date": date,
        "definition": {
            "logic_version": "horizontal_v2",
            "anchor_bars": 1,
            "following_bars_without_new_low": C.NO_NEW_LOW_FOLLOWING_BARS,
            "price_basis": "qfq_low",
            "suspension_semantics": "no_bar_not_counted",
            "max_base_range_pct": C.MAX_BASE_RANGE_PCT,
            "max_close_deviation_pct": C.MAX_CLOSE_DEVIATION_PCT,
            "max_abs_window_return_pct": C.MAX_ABS_WINDOW_RETURN_PCT,
            "industry_basis": "current_membership_snapshot_display_only",
        },
        "snapshot_dates": dates,
        "universe_count": len(universe),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "pk_pool": pk_pool,
        "pk_pool_count": len(pk_pool),
        "pk_excluded_count": max(len(candidates) - len(pk_pool), 0),
        "rejects": dict(sorted(rejects.items())),
        "industry": industry,
        "sources": sources,
        "coverage_by_date": coverage_by_date,
        "retries": {"stock_universe_as_of": universe_retry, **retries},
    }
