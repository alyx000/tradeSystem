"""“五阳回踩 + 日线重启”只读影子监控编排。

本模块刻意不复用 ``monthly_pattern_pool`` 的月频状态机，也不写运行收据：

- 月线种子只读取已经带 certified manifest 的完成月事实；
- 只有月线种子才拉取截至目标日的日线和复权因子；
- 日/周指标统一使用目标日为锚的前复权日线，历史扫描不读取目标日之后的数据；
- 主线板块只作可选的 ``[判断]`` 背景，不是硬筛选门；
- 不写 TradeDraft / TradePlan / 关注池，也不提供买卖建议。

首版是校准用影子任务。自动推送、定时和事件去重均不在本模块范围内。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from services.monthly_pattern import (
    detectors,
    indicator_watch,
    mainline,
    market,
    repository,
    service,
)
from services.monthly_pattern.models import MonthlyBar
from services.volume_concentration import repo as concentration_repo
from utils.qfq import OHLC_PRICE_KEYS, apply_qfq
from utils.trade_date import CLOSE_CUTOFF

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_MONTHS = 48
DEFAULT_DAILY_LOOKBACK_DAYS = 560
DEFAULT_TOP_K_SECTORS = 8
MIN_LATEST_UNIVERSE_CODES = 4000
_STOCK_CODE_RE = re.compile(r"^(?P<code>\d{6})(?:\.(?:SH|SZ|BJ))?$")
_MONTHLY_PRIMARY_CLASSIFICATIONS = (
    "matched",
    "not_matched",
    "blocked",
    "insufficient_history",
    "evidenced_not_applicable",
)


class IndicatorWatchSourceError(RuntimeError):
    """关键事实源不完整，不能把结果渲染成正常空候选。"""


def _result_ok(result: Any) -> bool:
    return bool(getattr(result, "success", False))


def _bare_stock_code(raw: Any, *, label: str) -> str:
    text = str(raw or "").strip().upper()
    match = _STOCK_CODE_RE.fullmatch(text)
    if match is None:
        raise IndicatorWatchSourceError(f"{label} 股票代码非法: {raw!r}")
    return match.group("code")


def _normalized_calendar_rows(registry, year: int) -> list[dict]:
    result = registry.call("get_trade_calendar", f"{year:04d}-06-30")
    if not _result_ok(result) or not isinstance(result.data, list):
        raise IndicatorWatchSourceError(
            f"trade_calendar {year} source_failed: "
            f"{getattr(result, 'error', 'unknown error')}"
        )
    rows: list[dict] = []
    for raw in result.data:
        raw_day = raw.get("cal_date") or raw.get("date")
        text = str(raw_day or "").replace("-", "")[:8]
        if len(text) != 8 or not text.isdigit():
            continue
        normalized = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            is_open = int(raw.get("is_open", 0)) == 1
        except (TypeError, ValueError):
            is_open = False
        rows.append({"date": normalized, "is_open": is_open})
    if not rows:
        raise IndicatorWatchSourceError(f"trade_calendar {year} 返回空或非法数据")
    return rows


def _closed_open_days(rows: list[dict], now: datetime) -> list[str]:
    """返回截至上海时间 ``now`` 已经完成收盘的开放日。"""
    today = now.date().isoformat()
    eligible = [
        row["date"]
        for row in rows
        if row["is_open"] and row["date"] <= today
    ]
    if (now.hour, now.minute) < CLOSE_CUTOFF:
        eligible = [day for day in eligible if day != today]
    return eligible


def resolve_target_date(
    registry,
    requested: str | None,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """返回目标开放日及截至今天的最近开放日。

    显式 ``--date`` 必须本身是开放日；默认日期才允许自动回退到最近已完成交易日。
    """
    shanghai_tz = ZoneInfo("Asia/Shanghai")
    if now is None:
        now = datetime.now(shanghai_tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=shanghai_tz)
    else:
        now = now.astimezone(shanghai_tz)
    today = now.date()
    if requested is not None:
        try:
            target = date.fromisoformat(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError("date 必须为 YYYY-MM-DD") from exc
        if target > today:
            raise ValueError(f"{target.isoformat()} 晚于今天，目标日尚未完成")
        if (
            target == today
            and (now.hour, now.minute) < CLOSE_CUTOFF
        ):
            raise ValueError(
                f"{target.isoformat()} 尚未越过 15:30 收盘安全线"
            )
        rows = _normalized_calendar_rows(registry, target.year)
        open_days = {row["date"] for row in rows if row["is_open"]}
        if target.isoformat() not in open_days:
            raise ValueError(f"{target.isoformat()} 不是交易日")
    else:
        target = today
        rows = _normalized_calendar_rows(registry, today.year)
        eligible = _closed_open_days(rows, now)
        if not eligible:
            previous_rows = _normalized_calendar_rows(registry, today.year - 1)
            eligible = _closed_open_days(previous_rows, now)
        if not eligible:
            raise IndicatorWatchSourceError("交易日历中没有可用的已完成开放日")
        target = date.fromisoformat(max(eligible))

    try:
        current_rows = _normalized_calendar_rows(registry, today.year)
    except IndicatorWatchSourceError:
        if requested is None:
            raise
        current_rows = []
    latest_candidates = _closed_open_days(current_rows, now)
    if not latest_candidates:
        try:
            previous_rows = _normalized_calendar_rows(registry, today.year - 1)
        except IndicatorWatchSourceError:
            previous_rows = []
        latest_candidates = _closed_open_days(previous_rows, now)
    # 当前日历只是“能否安全使用当前行业快照”的可选证据；显式历史扫描不能
    # 因当前年份日历故障而失败，也不能因此误把历史日当作最近开放日。
    latest_open = max(latest_candidates) if latest_candidates else ""
    return target.isoformat(), latest_open


def _seed_cutoff(target_date: str) -> str:
    target = date.fromisoformat(target_date)
    first_of_month = target.replace(day=1)
    return (first_of_month - timedelta(days=1)).isoformat()


def _certified_month_ends(
    conn: sqlite3.Connection,
    registry,
    target_date: str,
    *,
    months: int,
) -> list[str]:
    """选择目标月之前的完成月，并要求每个月都有仍然有效的 certified 收据。"""
    cutoff = _seed_cutoff(target_date)
    month_ends = service._calendar_month_ends(registry, cutoff, months)  # noqa: SLF001
    reusable = repository.existing_month_ends(conn, month_ends)
    missing = sorted(set(month_ends) - reusable)
    if missing:
        preview = ", ".join(missing[-5:])
        raise IndicatorWatchSourceError(
            f"blocked_monthly_seed: {len(missing)} 个完成月缺少有效 certified 收据"
            f"（最近：{preview}）；只读监控不会自动补采或回退旧月"
        )
    manifests = repository.load_month_bar_manifests(conn, month_ends)
    market.validate_month_manifest_sequence(manifests)
    return month_ends


def _certified_universe_count(
    conn: sqlite3.Connection,
    month_end: str,
) -> int:
    manifests = repository.load_month_bar_manifests(conn, [month_end])
    if len(manifests) != 1 or manifests[0].get("month_end") != month_end:
        raise IndicatorWatchSourceError(
            f"{month_end} certified manifest 收据缺失"
        )
    raw_count = manifests[0].get("universe_count")
    if isinstance(raw_count, bool):
        raise IndicatorWatchSourceError(
            f"{month_end} certified universe_count 非法"
        )
    try:
        count = int(raw_count)
    except (TypeError, ValueError) as exc:
        raise IndicatorWatchSourceError(
            f"{month_end} certified universe_count 非法"
        ) from exc
    if count < MIN_LATEST_UNIVERSE_CODES:
        raise IndicatorWatchSourceError(
            f"{month_end} certified universe_count 覆盖异常: {count}"
        )
    return count


def _latest_universe_codes(
    registry,
    month_end: str,
    *,
    expected_count: int,
) -> set[str]:
    """取得最新完成月 as-of 宇宙，用于区分合法退出与缺月线事实。"""
    result = registry.call("get_stock_universe_as_of", month_end)
    if not _result_ok(result) or not isinstance(result.data, list):
        raise IndicatorWatchSourceError(
            "stock_universe_as_of source_failed: "
            f"{getattr(result, 'error', 'unknown error')}"
        )
    if not result.data:
        raise IndicatorWatchSourceError(
            f"stock_universe_as_of {month_end} 返回空列表"
        )
    codes: set[str] = set()
    for row in result.data:
        if not isinstance(row, dict):
            raise IndicatorWatchSourceError(
                f"stock_universe_as_of {month_end} 存在非法行"
            )
        raw_code = row.get("ts_code") or row.get("stock_code") or row.get("code")
        codes.add(
            _bare_stock_code(
                raw_code,
                label=f"stock_universe_as_of {month_end}",
            )
        )
    if len(codes) != expected_count:
        raise IndicatorWatchSourceError(
            f"stock_universe_as_of {month_end} 与 certified 分母不一致: "
            f"live={len(codes)} certified={expected_count}"
        )
    return codes


def _stock_series(rows: list[dict]) -> tuple[dict[str, list[MonthlyBar]], dict[str, str]]:
    names: dict[str, str] = {}
    for row in rows:
        code = str(row.get("stock_code") or "").split(".")[0]
        if code and row.get("stock_name"):
            names[code] = str(row["stock_name"])

    adjusted = market.apply_month_end_qfq(rows)
    grouped: dict[str, list[MonthlyBar]] = defaultdict(list)
    for row in adjusted:
        code = str(row["stock_code"]).split(".")[0]
        grouped[code].append(
            MonthlyBar(
                month=str(row["month_end"])[:7],
                end_date=str(row["month_end"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
                amount=float(row.get("amount") or 0.0),
                is_complete=True,
                trading_days=1,
                price_shape_valid=bool(row.get("price_shape_valid", True)),
            )
        )
    return {
        code: sorted(bars, key=lambda bar: (bar.end_date, bar.month))
        for code, bars in grouped.items()
    }, names


def _as_mapping(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "to_dict"):
        return result.to_dict()
    raise TypeError(f"indicator result must be mapping-like, got {type(result)!r}")


def _seed_sort_key(item: dict) -> tuple[int, int, float, str]:
    evidence = item.get("monthly_evidence") or {}
    ma5 = float(evidence.get("ma5") or 0.0)
    close = float(evidence.get("close") or 0.0)
    support_distance = (close / ma5 - 1.0) if ma5 > 0 else 999.0
    return (
        0 if evidence.get("preferred_pullback") else 1,
        -int(evidence.get("positive_month_streak") or 0),
        support_distance,
        item["stock_code"],
    )


def _month_index(month: str) -> int:
    year, month_number = (int(part) for part in month.split("-"))
    return year * 12 + month_number - 1


def _month_from_index(value: int) -> str:
    year, zero_based_month = divmod(value, 12)
    return f"{year:04d}-{zero_based_month + 1:02d}"


def _certified_no_trade_months(
    no_trade_facts: dict[str, dict[str, dict]] | None,
    stock_code: str,
) -> set[str]:
    """把仓储返回的 canonical month_end 映射为 YYYY-MM 月份键。"""
    by_month = (no_trade_facts or {}).get(stock_code) or {}
    result: set[str] = set()
    for month_end, fact in by_month.items():
        if not isinstance(fact, dict):
            continue
        if fact.get("fact_status") != "certified_no_trade":
            continue
        text = str(fact.get("month_end") or month_end or "")
        if len(text) >= 7:
            result.add(text[:7])
    return result


def _recent_suffix_missing_months(
    bars: list[MonthlyBar],
    *,
    required_months: int = 20,
) -> set[str]:
    """返回最近至多 ``required_months`` 根完成月 K 之间缺失的自然月。"""
    months = sorted(
        {
            str(getattr(bar, "month", "") or getattr(bar, "end_date", ""))[:7]
            for bar in bars
            if str(getattr(bar, "month", "") or getattr(bar, "end_date", ""))
        }
    )
    months = months[-required_months:]
    missing: set[str] = set()
    for previous, current in zip(months, months[1:]):
        previous_index = _month_index(previous)
        current_index = _month_index(current)
        for value in range(previous_index + 1, current_index):
            missing.add(_month_from_index(value))
    return missing


def _monthly_macd_context(bars: list[MonthlyBar]) -> dict:
    """只用截至种子月的连续后缀计算月 MACD 上下文。"""
    completed = sorted(
        (bar for bar in bars if bar.is_complete),
        key=lambda bar: (bar.end_date, bar.month),
    )
    suffix: list[MonthlyBar] = []
    for bar in reversed(completed):
        if suffix and _month_index(suffix[-1].month) != _month_index(bar.month) + 1:
            break
        suffix.append(bar)
    suffix.reverse()
    if len(suffix) < detectors.MACD_MIN_MONTHS:
        return {
            "status": "insufficient_history",
            "available_months": len(suffix),
            "required_months": detectors.MACD_MIN_MONTHS,
        }
    latest = detectors.compute_monthly_indicators(suffix)[-1]
    above_zero = latest.macd_dif > 0 and latest.macd_dea > 0
    bullish_on_zero = above_zero and latest.macd_dif >= latest.macd_dea
    if above_zero:
        label = "monthly_mainrise_context"
    elif latest.macd_dif <= 0 and latest.macd_dea <= 0:
        label = "monthly_reversal_context"
    else:
        label = "transition_unknown"
    return {
        "status": "complete",
        "context": label,
        "dif": latest.macd_dif,
        "dea": latest.macd_dea,
        "hist": latest.macd_histogram,
        "above_zero": above_zero,
        "bullish_on_zero": bullish_on_zero,
        "golden_cross": latest.macd_golden_cross,
        "as_of_month": latest.month,
        "available_months": len(suffix),
    }


def _monthly_seeds(
    rows: list[dict],
    *,
    max_seeds: int | None,
    expected_month_end: str | None = None,
    latest_universe_codes: set[str] | None = None,
    no_trade_facts: dict[str, dict[str, dict]] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    series, names = _stock_series(rows)
    seeds: list[dict] = []
    counts: dict[str, int] = defaultdict(int)
    if latest_universe_codes is not None:
        missing_entire_window = latest_universe_codes - set(series)
        for code in missing_entire_window:
            no_trade_months = _certified_no_trade_months(
                no_trade_facts,
                code,
            )
            if (
                expected_month_end is not None
                and expected_month_end[:7] in no_trade_months
            ):
                counts["evidenced_not_applicable"] += 1
                counts["evidenced_no_trade_latest"] += 1
                counts["evidenced_no_trade_entire_window"] += 1
            else:
                counts["blocked"] += 1
                counts["blocked_missing_latest_month"] += 1
                counts["blocked_missing_entire_window"] += 1
    for code, bars in series.items():
        name = names.get(code, "")
        if latest_universe_codes is not None and code not in latest_universe_codes:
            counts["out_of_scope_latest_universe"] += 1
            continue
        if (
            expected_month_end is not None
            and bars
            and bars[-1].end_date != expected_month_end
        ):
            no_trade_months = _certified_no_trade_months(
                no_trade_facts,
                code,
            )
            if expected_month_end[:7] in no_trade_months:
                counts["evidenced_not_applicable"] += 1
                counts["evidenced_no_trade_latest"] += 1
            else:
                counts["blocked"] += 1
                counts["blocked_missing_latest_month"] += 1
            continue
        raw_result = _as_mapping(indicator_watch.detect_monthly_seed(bars))
        status = str(raw_result.get("status") or "unknown")
        raw_evidence = raw_result.get("evidence")
        if not isinstance(raw_evidence, dict):
            raw_evidence = {}
        if status == "blocked" and str(raw_evidence.get("reason") or "").startswith(
            "non_consecutive_completed_months:"
        ):
            missing_months = _recent_suffix_missing_months(bars)
            no_trade_months = _certified_no_trade_months(
                no_trade_facts,
                code,
            )
            if missing_months and missing_months.issubset(no_trade_months):
                status = "evidenced_not_applicable"
                raw_evidence = {
                    **raw_evidence,
                    "evidenced_no_trade_months": sorted(missing_months),
                }
        counts[status] += 1
        if status == "evidenced_not_applicable":
            counts["evidenced_no_trade_gap"] += 1
        elif status == "blocked":
            reason = str(raw_evidence.get("reason") or "")
            if reason.startswith("price_shape_invalid:"):
                counts["blocked_price_shape"] += 1
            elif reason.startswith("non_consecutive_completed_months:"):
                counts["blocked_month_gap"] += 1
            else:
                counts["blocked_other"] += 1
        elif (
            status == "not_matched"
            and isinstance(raw_evidence.get("shape_invalid_months"), list)
            and raw_evidence["shape_invalid_months"]
        ):
            counts["shape_short_circuited_not_matched"] += 1
        if not raw_result.get("matched"):
            continue
        evidence = dict(raw_evidence)
        try:
            evidence["monthly_macd"] = _monthly_macd_context(bars)
        except (AttributeError, TypeError, ValueError) as exc:
            evidence["monthly_macd"] = {
                "status": "blocked",
                "error": str(exc),
            }
        seeds.append(
            {
                "stock_code": code,
                "stock_name": name,
                "monthly_status": status,
                "monthly_evidence": evidence,
            }
        )
    seeds.sort(key=_seed_sort_key)
    total = len(seeds)
    if max_seeds is not None:
        seeds = seeds[:max_seeds]
    counts["monthly_seed_total"] = total
    counts["monthly_seed_scanned"] = len(seeds)
    counts["monthly_seed_truncated"] = total - len(seeds)
    return seeds, dict(counts)


def _assert_monthly_classification_conservation(
    counts: dict[str, int],
    seeds: list[dict],
    *,
    eligible_a_share_universe: int,
) -> None:
    """Fail closed unless every eligible A share has one primary outcome."""
    primary = {
        key: counts.get(key, 0)
        for key in _MONTHLY_PRIMARY_CLASSIFICATIONS
    }
    invalid = {
        key: value
        for key, value in primary.items()
        if type(value) is not int or value < 0
    }
    classified = (
        -1 if invalid else sum(int(value) for value in primary.values())
    )
    seed_total = counts.get("monthly_seed_total", -1)
    seed_scanned = counts.get("monthly_seed_scanned", -1)
    seed_truncated = counts.get("monthly_seed_truncated", -1)
    matched = primary["matched"]
    valid_seed_counts = all(
        type(value) is int and value >= 0
        for value in (
            seed_total,
            seed_scanned,
            seed_truncated,
        )
    )
    if (
        type(eligible_a_share_universe) is not int
        or eligible_a_share_universe < 0
        or invalid
        or classified != eligible_a_share_universe
        or not valid_seed_counts
        or matched != seed_total
        or matched != seed_scanned
        or matched != len(seeds)
        or seed_truncated != 0
    ):
        raise IndicatorWatchSourceError(
            "A股月线主分类不守恒: "
            f"eligible={eligible_a_share_universe} "
            f"classified={classified} primary={primary} "
            f"seed_total={seed_total} seed_scanned={seed_scanned} "
            f"seed_truncated={seed_truncated} seeds={len(seeds)}"
        )


def _dedupe_exact_dates(
    rows: list[dict],
    *,
    fields: tuple[str, ...],
    label: str,
) -> tuple[list[dict], int]:
    """只抑制字段完全一致的同日重复；同日冲突必须 fail-closed。"""
    by_date: dict[str, dict] = {}
    duplicate_count = 0
    for row in rows:
        trade_date = str(row.get("trade_date") or "")
        if not trade_date:
            raise IndicatorWatchSourceError(f"{label} 存在缺失 trade_date 的行")
        previous = by_date.get(trade_date)
        if previous is None:
            by_date[trade_date] = row
            continue
        previous_payload = tuple(previous.get(field) for field in fields)
        current_payload = tuple(row.get(field) for field in fields)
        if previous_payload != current_payload:
            raise IndicatorWatchSourceError(
                f"{label} 同日事实冲突: {trade_date}"
            )
        duplicate_count += 1
    return [by_date[key] for key in sorted(by_date)], duplicate_count


def _fetch_adjusted_daily(
    registry,
    stock_code: str,
    start_date: str,
    target_date: str,
) -> tuple[list[dict] | None, dict]:
    bars_result = registry.call(
        "get_stock_daily_range",
        stock_code,
        start_date,
        target_date,
    )
    factors_result = registry.call(
        "get_stock_adj_factor_range",
        stock_code,
        start_date,
        target_date,
    )
    source = {
        "daily": getattr(bars_result, "source", None),
        "adj_factor": getattr(factors_result, "source", None),
    }
    if not _result_ok(bars_result) or not isinstance(bars_result.data, list):
        source["status"] = "daily_source_failed"
        source["error"] = getattr(bars_result, "error", None)
        return None, source
    if not _result_ok(factors_result) or not isinstance(factors_result.data, list):
        source["status"] = "adj_factor_source_failed"
        source["error"] = getattr(factors_result, "error", None)
        return None, source
    try:
        bars, duplicate_bars = _dedupe_exact_dates(
            [
                dict(row)
                for row in bars_result.data
                if str(row.get("trade_date") or "") <= target_date
            ],
            fields=(
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
                "amount",
                "pct_chg",
            ),
            label=f"{stock_code} daily",
        )
        factors, duplicate_factors = _dedupe_exact_dates(
            [
                dict(row)
                for row in factors_result.data
                if str(row.get("trade_date") or "") <= target_date
            ],
            fields=("adj_factor",),
            label=f"{stock_code} adj_factor",
        )
    except IndicatorWatchSourceError as exc:
        source["status"] = "duplicate_conflict"
        source["error"] = str(exc)
        return None, source
    source["deduplicated_daily_rows"] = duplicate_bars
    source["deduplicated_factor_rows"] = duplicate_factors
    adjusted = apply_qfq(bars, factors, keys=OHLC_PRICE_KEYS)
    if adjusted is None:
        source["status"] = "qfq_failed"
        source["error"] = "日线与复权因子缺失、错位或包含非法值"
        return None, source
    source["status"] = "success"
    source["bar_count"] = len(adjusted)
    return adjusted, source


def _mainline_context(
    conn: sqlite3.Connection,
    registry,
    target_date: str,
    latest_open: str,
) -> tuple[dict[str, dict], list[str], dict]:
    try:
        records = concentration_repo.get_recent_concentration(conn, target_date, 3)
        sectors, meta = mainline.stable_main_sectors(
            records,
            top_k=DEFAULT_TOP_K_SECTORS,
        )
    except sqlite3.OperationalError as exc:
        sectors = []
        meta = {
            "status": "missing",
            "source_dates": [],
            "error": str(exc),
        }
    context = {
        "status": meta.get("status"),
        "sectors": sectors,
        "source_dates": meta.get("source_dates") or [],
        "industry_status": "not_as_of",
        "industry_semantics": "historical_industry_mapping_unavailable",
    }
    if target_date != latest_open:
        return {}, sectors, context

    result = registry.call("get_stock_sw_industry_map")
    if not _result_ok(result) or not isinstance(result.data, dict) or not result.data:
        context["industry_status"] = "source_failed"
        return {}, sectors, context
    mapping = {
        str(code).split(".")[0]: dict(info)
        for code, info in result.data.items()
        if isinstance(info, dict)
    }
    context["industry_status"] = "current_snapshot"
    context["industry_semantics"] = "仅用于最近开放日的当前快照判断，不用于历史回放"
    return mapping, sectors, context


def _stock_identity_context(
    registry,
    target_date: str,
    seeds: list[dict],
) -> tuple[set[str], dict[str, str], dict]:
    """独立取得目标日 ST 身份；名称只用于展示，失败不阻断技术计算。"""
    st_result = registry.call("get_stock_st", target_date)
    if not _result_ok(st_result) or not isinstance(st_result.data, list):
        raise IndicatorWatchSourceError(
            "stock_st source_failed: "
            f"{getattr(st_result, 'error', 'unknown error')}"
        )
    if not st_result.data:
        raise IndicatorWatchSourceError(
            f"stock_st {target_date} 返回空列表，拒绝解释为全市场无 ST"
        )
    st_codes: set[str] = set()
    for row in st_result.data:
        if not isinstance(row, dict):
            raise IndicatorWatchSourceError(
                f"stock_st {target_date} 存在非法行"
            )
        raw_code = row.get("ts_code") or row.get("code")
        st_codes.add(
            _bare_stock_code(
                raw_code,
                label=f"stock_st {target_date}",
            )
        )

    seed_codes = [str(seed["stock_code"]).split(".")[0] for seed in seeds]
    names = {
        str(seed["stock_code"]).split(".")[0]: str(seed.get("stock_name") or "")
        for seed in seeds
        if seed.get("stock_name")
    }
    basic_result = registry.call("get_stock_basic_batch", seed_codes)
    if _result_ok(basic_result) and isinstance(basic_result.data, list):
        for row in basic_result.data:
            if not isinstance(row, dict):
                continue
            code = str(
                row.get("ts_code")
                or row.get("stock_code")
                or row.get("code")
                or ""
            ).split(".")[0]
            name = str(row.get("name") or row.get("stock_name") or "").strip()
            if code and name:
                names[code] = name
        name_status = (
            "success"
            if all(code in names for code in seed_codes)
            else "partial"
        )
        name_error = None
    else:
        name_status = "source_failed"
        name_error = getattr(basic_result, "error", None)

    return st_codes, names, {
        "st_status": "success",
        "st_source": getattr(st_result, "source", None),
        "name_status": name_status,
        "name_source": getattr(basic_result, "source", None),
        "name_error": name_error,
        "semantics": (
            "ST 身份按目标日 get_stock_st；简称仅作展示，失败不改变技术候选"
        ),
    }


def _stage_rank(stage: str) -> int:
    return {
        "resonance_observed": 0,
        "daily_reactivated": 1,
        "monthly_seeded": 2,
        "insufficient_history": 3,
        "blocked": 4,
    }.get(stage, 5)


def _candidate_sort_key(item: dict) -> tuple[int, int, int, str]:
    monthly = item.get("monthly_evidence") or {}
    return (
        _stage_rank(str(item.get("stage") or "")),
        0 if item.get("mainline_match") is True else 1,
        0 if monthly.get("preferred_pullback") else 1,
        item["stock_code"],
    )


def _dynamic_monthly_support(item: dict) -> bool | None:
    daily_evidence = item.get("daily_evidence")
    if not isinstance(daily_evidence, dict):
        return None
    payload = daily_evidence.get("dynamic_monthly_ma5")
    if not isinstance(payload, dict):
        return None
    support_held = payload.get("support_held")
    return support_held if isinstance(support_held, bool) else None


def _blocked_summary(
    *,
    requested_date: str | None,
    target_date: str | None,
    error: Exception,
) -> dict:
    return {
        "requested_date": requested_date,
        "target_date": target_date,
        "seed_month": None,
        "status": "blocked",
        "source_status": {"critical": "blocked"},
        "counts": {},
        "mainline_context": {},
        "candidates": [],
        "waiting_monthly_reclaim": [],
        "indeterminate_current_month_ma5": [],
        "st_excluded_items": [],
        "data_issues": [],
        "unresolved_rules": indicator_watch.UNRESOLVED_RULES,
        "error": str(error),
        "write_boundary": {
            "database": False,
            "pool": False,
            "watchlist": False,
            "plan": False,
            "push": False,
        },
    }


def build_blocked_summary(
    *,
    requested_date: str | None,
    target_date: str,
    error: Exception,
) -> dict:
    """为自动编排生成可持久化的关键源故障收据。"""
    return _blocked_summary(
        requested_date=requested_date,
        target_date=target_date,
        error=error,
    )


def run_monitor(
    conn: sqlite3.Connection,
    registry,
    requested_date: str | None = None,
    *,
    months: int = DEFAULT_HISTORY_MONTHS,
    max_seeds: int | None = None,
    daily_lookback_days: int = DEFAULT_DAILY_LOOKBACK_DAYS,
) -> dict:
    """运行只读影子监控；关键源失败返回 ``blocked``，不伪装成空候选。"""
    if months < 35:
        raise ValueError("months 至少为 35（MACD 12/26/9）")
    if max_seeds is not None and max_seeds <= 0:
        raise ValueError("max_seeds 必须为正整数")
    if daily_lookback_days < 420:
        raise ValueError("daily_lookback_days 至少为 420 个自然日")

    target_date: str | None = None
    try:
        target_date, latest_open = resolve_target_date(registry, requested_date)
        month_ends = _certified_month_ends(
            conn,
            registry,
            target_date,
            months=months,
        )
        latest_universe_count = _certified_universe_count(
            conn,
            month_ends[-1],
        )
        certified_universe_codes = _latest_universe_codes(
            registry,
            month_ends[-1],
            expected_count=latest_universe_count,
        )
        excluded_b_share_codes = {
            code
            for code in certified_universe_codes
            if market.is_b_share_code(code)
        }
        latest_universe_codes = (
            certified_universe_codes - excluded_b_share_codes
        )
        rows = repository.load_effective_month_bars(conn, month_ends)
        no_trade_rows = repository.load_effective_no_trade_facts(
            conn,
            month_ends,
        )
        no_trade_facts: dict[str, dict[str, dict]] = defaultdict(dict)
        for fact in no_trade_rows:
            code = _bare_stock_code(
                fact.get("stock_code"),
                label="certified_no_trade",
            )
            month_end = str(fact.get("month_end") or "")
            no_trade_facts[code][month_end] = fact
        if not rows:
            if not no_trade_facts:
                raise IndicatorWatchSourceError(
                    "certified 完成月存在但月线事实为空"
                )
        # 先保留全部月线命中，等最近日行业/名称快照到位后再执行 ST 硬排除和
        # ``max_seeds`` 校准截断；否则前 N 里出现 ST 会挤掉正常种子。
        seeds, counts = _monthly_seeds(
            rows,
            max_seeds=None,
            expected_month_end=month_ends[-1],
            latest_universe_codes=latest_universe_codes,
            no_trade_facts=dict(no_trade_facts),
        )
        counts.update(
            {
                "latest_certified_universe": len(certified_universe_codes),
                "eligible_a_share_universe": len(latest_universe_codes),
                "excluded_b_share_universe": len(excluded_b_share_codes),
            }
        )
        _assert_monthly_classification_conservation(
            counts,
            seeds,
            eligible_a_share_universe=len(latest_universe_codes),
        )
    except ValueError:
        raise
    except Exception as exc:
        return _blocked_summary(
            requested_date=requested_date,
            target_date=target_date,
            error=exc,
        )

    if seeds:
        try:
            st_codes, identity_names, identity_context = _stock_identity_context(
                registry,
                target_date,
                seeds,
            )
        except Exception as exc:
            return _blocked_summary(
                requested_date=requested_date,
                target_date=target_date,
                error=exc,
            )
    else:
        st_codes = set()
        identity_names = {}
        identity_context = {
            "st_status": "not_needed",
            "st_source": None,
            "name_status": "not_needed",
            "name_source": None,
            "name_error": None,
            "semantics": "无月线种子，不需要查询目标日 ST 身份",
        }

    try:
        industry_map, main_sectors, mainline_context = _mainline_context(
            conn,
            registry,
            target_date,
            latest_open,
        )
    except Exception as exc:  # 可选背景失败不能删除技术候选
        industry_map = {}
        main_sectors = []
        mainline_context = {
            "status": "source_failed",
            "sectors": [],
            "source_dates": [],
            "industry_status": "source_failed",
            "industry_semantics": "可选板块背景失败，不作为技术硬门",
            "error": str(exc),
        }
    data_issues: list[dict] = []
    st_excluded_items: list[dict] = []
    eligible_seeds: list[dict] = []
    for seed in seeds:
        code = seed["stock_code"]
        info = industry_map.get(code) or {}
        stock_name = (
            identity_names.get(code)
            or seed.get("stock_name")
            or info.get("name")
            or ""
        )
        if code in st_codes:
            counts["st_excluded"] = counts.get("st_excluded", 0) + 1
            st_excluded_items.append(
                {
                    **seed,
                    "stock_name": stock_name,
                    "stage": "excluded_st",
                    "reason": "target_date_st",
                }
            )
            continue
        if not stock_name:
            counts["stock_name_unknown"] = counts.get("stock_name_unknown", 0) + 1
        eligible_seeds.append({**seed, "stock_name": stock_name})
    counts["monthly_seed_eligible"] = len(eligible_seeds)
    if max_seeds is not None:
        seeds = eligible_seeds[:max_seeds]
    else:
        seeds = eligible_seeds
    counts["monthly_seed_scanned"] = len(seeds)
    counts["monthly_seed_truncated"] = len(eligible_seeds) - len(seeds)

    start_date = (
        date.fromisoformat(target_date) - timedelta(days=daily_lookback_days)
    ).isoformat()
    candidates: list[dict] = []
    waiting_monthly_reclaim: list[dict] = []
    indeterminate_current_month_ma5: list[dict] = []
    completed_daily = 0
    for index, seed in enumerate(seeds, start=1):
        code = seed["stock_code"]
        logger.info(
            "[monthly-pattern monitor] %s/%s %s %s",
            index,
            len(seeds),
            code,
            seed.get("stock_name", ""),
        )
        try:
            adjusted, source = _fetch_adjusted_daily(
                registry,
                code,
                start_date,
                target_date,
            )
        except Exception as exc:
            adjusted = None
            source = {
                "status": "daily_processing_failed",
                "error": str(exc),
            }
        if adjusted is None:
            data_issues.append(
                {
                    "stock_code": code,
                    "stock_name": seed.get("stock_name", ""),
                    "stage": "blocked",
                    "source": source,
                }
            )
            continue
        try:
            result = _as_mapping(
                indicator_watch.evaluate_daily_monitor(
                    adjusted,
                    target_date=target_date,
                    seed_month_end=str(
                        seed["monthly_evidence"]["seed_month_end"]
                    ),
                )
            )
        except Exception as exc:
            data_issues.append(
                {
                    "stock_code": code,
                    "stock_name": seed.get("stock_name", ""),
                    "stage": "blocked",
                    "source": source,
                    "error": f"indicator_processing_failed: {exc}",
                }
            )
            continue
        stage = str(result.get("stage") or result.get("status") or "blocked")
        if stage == "blocked":
            data_issues.append(
                {
                    "stock_code": code,
                    "stock_name": seed.get("stock_name", ""),
                    "stage": stage,
                    "source": source,
                    "error": (result.get("evidence") or {}).get("error"),
                }
            )
            continue
        completed_daily += 1
        industry = str((industry_map.get(code) or {}).get("sw_l2") or "")
        stock_name = (
            seed.get("stock_name")
            or (industry_map.get(code) or {}).get("name")
            or ""
        )
        candidate = {
            **seed,
            "stock_name": stock_name,
            "stage": stage,
            "daily_evidence": dict(result.get("evidence") or {}),
            "industry": industry or None,
            "mainline_match": (
                industry in main_sectors if industry and main_sectors else None
            ),
            "source": source,
        }
        dynamic_support = _dynamic_monthly_support(candidate)
        if dynamic_support is True:
            candidates.append(candidate)
        elif dynamic_support is False:
            waiting_monthly_reclaim.append(candidate)
        else:
            indeterminate_current_month_ma5.append(candidate)

    candidates.sort(key=_candidate_sort_key)
    waiting_monthly_reclaim.sort(key=_candidate_sort_key)
    indeterminate_current_month_ma5.sort(key=_candidate_sort_key)
    evaluated_candidates = (
        candidates
        + waiting_monthly_reclaim
        + indeterminate_current_month_ma5
    )
    counts.update(
        {
            "daily_complete": completed_daily,
            "daily_blocked": len(data_issues),
            "current_candidates": len(candidates),
            "waiting_monthly_reclaim": len(waiting_monthly_reclaim),
            "indeterminate_current_month_ma5": len(
                indeterminate_current_month_ma5
            ),
            "resonance_observed": sum(
                item["stage"] == "resonance_observed" for item in candidates
            ),
            "daily_reactivated": sum(
                item["stage"] == "daily_reactivated" for item in candidates
            ),
            "monthly_seeded": sum(
                item["stage"] == "monthly_seeded" for item in candidates
            ),
            "current_month_ma5_held": sum(
                _dynamic_monthly_support(item) is True
                for item in evaluated_candidates
            ),
            "current_month_ma5_not_held": sum(
                _dynamic_monthly_support(item) is False
                for item in evaluated_candidates
            ),
            "current_month_ma5_unknown": sum(
                _dynamic_monthly_support(item) is None
                for item in evaluated_candidates
            ),
            "daily_insufficient": sum(
                item["stage"] == "insufficient_history"
                for item in evaluated_candidates
            ),
        }
    )
    if seeds and not completed_daily:
        status = "blocked"
    elif (
        data_issues
        or counts.get("blocked", 0)
        or counts.get("monthly_seed_truncated", 0)
        or counts.get("daily_insufficient", 0)
        or indeterminate_current_month_ma5
    ):
        status = "partial"
    else:
        status = "complete"
    return {
        "requested_date": requested_date,
        "target_date": target_date,
        "seed_month": month_ends[-1][:7],
        "seed_month_end": month_ends[-1],
        "status": status,
        "source_status": {
            "calendar": "success",
            "latest_universe": "success",
            "st_status": identity_context["st_status"],
            "stock_name": identity_context["name_status"],
            "monthly_seed": (
                "partial" if counts.get("blocked", 0) else "certified"
            ),
            "daily": (
                "blocked"
                if status == "blocked"
                else "partial"
                if (
                    data_issues
                    or counts.get("daily_insufficient", 0)
                    or indeterminate_current_month_ma5
                )
                else "truncated"
                if counts.get("monthly_seed_truncated", 0)
                else "success"
            ),
        },
        "counts": counts,
        "identity_context": identity_context,
        "mainline_context": mainline_context,
        "candidates": candidates,
        "waiting_monthly_reclaim": waiting_monthly_reclaim,
        "indeterminate_current_month_ma5": indeterminate_current_month_ma5,
        "st_excluded_items": st_excluded_items,
        "data_issues": data_issues,
        "unresolved_rules": indicator_watch.UNRESOLVED_RULES,
        "error": (
            "所有月线种子均未取得完整目标日日线/复权事实"
            if status == "blocked" and seeds
            else None
        ),
        "write_boundary": {
            "database": False,
            "pool": False,
            "watchlist": False,
            "plan": False,
            "push": False,
        },
    }
