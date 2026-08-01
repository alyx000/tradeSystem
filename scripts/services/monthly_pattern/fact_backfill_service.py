"""月线监控派生事实回补编排。

本模块只负责：

1. 从现有 effective 月线视图中锁定仍为 ``blocked`` 的最小事实缺口；
2. 每只股票合并成一次日线、一次复权因子区间请求；
3. 调用 :mod:`derived_facts` 形成可验签的完成月事实；
4. 生成 dry-run 收据，或在收据哈希一致时原子写入派生事实与运行审计。

原始 ``monthly_pattern_bars`` / manifest、观察池和计划层均不在写边界内。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Callable

from services.monthly_pattern import (
    indicator_watch,
    indicator_watch_service,
    market,
    repository,
)


FORMULA_VERSION = "daily_qfq_month_v1"
REQUIRED_SEED_MONTHS = 20
MIN_MONTHLY_QUOTE_UNIVERSE_COVERAGE = 0.95


class FactBackfillError(RuntimeError):
    """回补来源、计算或持久化失败。"""


class FactBackfillValidationError(ValueError):
    """命令参数或确认收据不合法。"""


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_ok(result: Any) -> bool:
    return bool(getattr(result, "success", False))


def _bare_code(value: Any) -> str:
    code = str(value or "").strip().upper().split(".")[0]
    if len(code) != 6 or not code.isdigit():
        raise FactBackfillError(f"股票代码非法: {value!r}")
    return code


def _month_index(month: str) -> int:
    year, number = (int(part) for part in month.split("-"))
    return year * 12 + number - 1


def _is_member(row: dict, month_end: str) -> bool:
    """以 latest universe 返回的 list/delist 字段判断历史月 as-of 身份。"""
    listed = str(row.get("list_date") or "").replace("-", "")[:8]
    delisted = str(row.get("delist_date") or "").replace("-", "")[:8]
    end = month_end.replace("-", "")
    month_start = end[:6] + "01"
    if len(listed) == 8 and listed.isdigit() and listed > end:
        return False
    if len(delisted) == 8 and delisted.isdigit() and delisted < month_start:
        return False
    return True


def _load_effective_rows(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> list[dict]:
    loader = getattr(repository, "load_effective_month_bars", None)
    if loader is None:
        return repository.load_month_bars(conn, month_ends)
    return list(loader(conn, month_ends))


def _load_no_trade_keys(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> set[tuple[str, str]]:
    loader = getattr(repository, "load_effective_no_trade_facts", None)
    if loader is None:
        loader = getattr(repository, "load_derived_no_trade_facts", None)
    if loader is None:
        return set()
    return {
        (
            _bare_code(row.get("stock_code")),
            str(row.get("month_end") or ""),
        )
        for row in loader(conn, month_ends)
    }


def _latest_universe_rows(
    registry,
    month_end: str,
    *,
    expected_count: int,
) -> dict[str, dict]:
    result = registry.call("get_stock_universe_as_of", month_end)
    if not _result_ok(result) or not isinstance(result.data, list):
        raise FactBackfillError(
            "stock_universe_as_of source_failed: "
            f"{getattr(result, 'error', 'unknown error')}"
        )
    rows: dict[str, dict] = {}
    for raw in result.data:
        if not isinstance(raw, dict):
            raise FactBackfillError("stock_universe_as_of 存在非法行")
        code = _bare_code(
            raw.get("ts_code") or raw.get("stock_code") or raw.get("code")
        )
        if code in rows:
            raise FactBackfillError(f"stock_universe_as_of 重复代码: {code}")
        rows[code] = dict(raw)
    if len(rows) != expected_count:
        raise FactBackfillError(
            "stock_universe_as_of 与 certified 分母不一致: "
            f"live={len(rows)} certified={expected_count}"
        )
    return rows


def _as_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "to_dict"):
        return result.to_dict()
    raise TypeError(f"检测结果必须可映射，实际为 {type(result)!r}")


def _plan_targets(
    *,
    effective_rows: list[dict],
    month_ends: list[str],
    universe_rows: dict[str, dict],
    no_trade_keys: set[tuple[str, str]],
) -> tuple[dict[str, dict[str, set[str]]], dict]:
    """只规划阶段一仍 blocked 的缺最新月、月缺口和真正 shape Unknown。"""
    series, names = indicator_watch_service._stock_series(effective_rows)  # noqa: SLF001
    expected_months = [item[:7] for item in month_ends]
    end_by_month = {item[:7]: item for item in month_ends}
    latest_end = month_ends[-1]
    required_tail = expected_months[-REQUIRED_SEED_MONTHS:]
    targets: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    skipped = Counter()

    for code, universe_row in sorted(universe_rows.items()):
        if market.is_b_share_code(code):
            skipped["excluded_b_share_universe"] += 1
            continue
        bars = series.get(code, [])
        months_present = {bar.month for bar in bars if bar.is_complete}
        recent_shape_invalid = {
            str(bar.month)
            for bar in bars[-6:]
            if bar.is_complete
            and getattr(bar, "price_shape_valid", True) is False
        }
        latest_no_trade = (code, latest_end) in no_trade_keys
        latest_present = expected_months[-1] in months_present

        if not latest_present and not latest_no_trade:
            for month in required_tail:
                month_end = end_by_month[month]
                if (
                    month not in months_present
                    and (code, month_end) not in no_trade_keys
                    and _is_member(universe_row, month_end)
                ):
                    targets[code][month_end].add("missing_latest")
            for month in sorted(recent_shape_invalid):
                month_end = end_by_month.get(month)
                if month_end is not None:
                    targets[code][month_end].add("shape_unverifiable")
            continue
        if latest_no_trade:
            skipped["evidenced_no_trade"] += 1
            continue

        detection = _as_dict(indicator_watch.detect_monthly_seed(bars))
        status = str(detection.get("status") or "unknown")
        evidence = detection.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        if status == "insufficient_history":
            skipped["insufficient_history"] += 1
            continue
        if status == "not_matched":
            skipped["not_matched"] += 1
            if evidence.get("shape_invalid_months"):
                skipped["shape_short_circuited_not_matched"] += 1
            continue
        if status != "blocked":
            skipped[status] += 1
            continue

        reason = str(evidence.get("reason") or "")
        if reason.startswith("price_shape_invalid:"):
            invalid_months = evidence.get("shape_invalid_months")
            if not isinstance(invalid_months, list):
                invalid_months = [reason.split(":", 1)[1].strip()]
            for month in invalid_months:
                month_end = end_by_month.get(str(month))
                if month_end is not None:
                    targets[code][month_end].add("shape_unverifiable")
            continue
        if reason.startswith("non_consecutive_completed_months:"):
            missing_months = (
                indicator_watch_service._recent_suffix_missing_months(  # noqa: SLF001
                    bars,
                    required_months=REQUIRED_SEED_MONTHS,
                )
            )
            for month in sorted(missing_months):
                month_end = end_by_month.get(month)
                if (
                    month_end is not None
                    and month not in months_present
                    and (code, month_end) not in no_trade_keys
                    and _is_member(universe_row, month_end)
                ):
                    targets[code][month_end].add("month_gap")
            for month in sorted(recent_shape_invalid):
                month_end = end_by_month.get(month)
                if month_end is not None:
                    targets[code][month_end].add("shape_unverifiable")
            continue
        skipped["blocked_other"] += 1

    plan = {
        code: {
            month_end: set(reasons)
            for month_end, reasons in sorted(months.items())
        }
        for code, months in sorted(targets.items())
        if months
    }
    return plan, {
        "planned_stocks": len(plan),
        "planned_months": sum(len(months) for months in plan.values()),
        "skipped": dict(sorted(skipped.items())),
        "known_names": sum(1 for code in plan if names.get(code)),
    }


def _dedupe(
    rows: list[dict],
    *,
    fields: tuple[str, ...],
    label: str,
) -> list[dict]:
    try:
        deduped, _count = indicator_watch_service._dedupe_exact_dates(  # noqa: SLF001
            rows,
            fields=fields,
            label=label,
        )
    except Exception as exc:
        raise FactBackfillError(str(exc)) from exc
    return deduped


def _monthly_quote_maps(
    registry,
    target_months: list[str],
) -> tuple[
    dict[str, dict[str, dict]],
    dict[str, str],
    dict[str, str],
]:
    maps: dict[str, dict[str, dict]] = {}
    sources: dict[str, str] = {}
    payload_hashes: dict[str, str] = {}
    for month_end in target_months:
        result = registry.call("get_market_monthly_quotes", month_end)
        if not _result_ok(result) or not isinstance(result.data, list):
            raise FactBackfillError(
                f"get_market_monthly_quotes {month_end}: "
                f"{getattr(result, 'error', 'source_failed')}"
            )
        rows: dict[str, dict] = {}
        for raw in result.data:
            if not isinstance(raw, dict):
                raise FactBackfillError(f"{month_end} monthly quote 存在非法行")
            code = _bare_code(
                raw.get("ts_code") or raw.get("stock_code") or raw.get("code")
            )
            if code in rows:
                raise FactBackfillError(
                    f"{month_end} monthly quote 重复代码: {code}"
                )
            rows[code] = dict(raw)
        maps[month_end] = rows
        sources[month_end] = str(getattr(result, "source", None) or "")
        payload_hashes[month_end] = _canonical_hash(
            [
                {"stock_code": code, "row": rows[code]}
                for code in sorted(rows)
            ]
        )
    return maps, sources, payload_hashes


def _universe_maps(
    registry,
    target_months: list[str],
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, str]]:
    memberships: dict[str, set[str]] = {}
    sources: dict[str, str] = {}
    payload_hashes: dict[str, str] = {}
    for month_end in target_months:
        result = registry.call("get_stock_universe_as_of", month_end)
        if not _result_ok(result) or not isinstance(result.data, list):
            raise FactBackfillError(
                f"get_stock_universe_as_of {month_end}: "
                f"{getattr(result, 'error', 'source_failed')}"
            )
        rows: dict[str, dict] = {}
        for row in result.data:
            if not isinstance(row, dict):
                raise FactBackfillError(
                    f"{month_end} as-of universe 存在非法行"
                )
            code = _bare_code(
                row.get("ts_code") or row.get("stock_code") or row.get("code")
            )
            if code in rows:
                raise FactBackfillError(
                    f"{month_end} as-of universe 重复代码: {code}"
                )
            rows[code] = dict(row)
        if not rows:
            raise FactBackfillError(f"{month_end} as-of universe 为空")
        memberships[month_end] = set(rows)
        sources[month_end] = str(getattr(result, "source", None) or "")
        payload_hashes[month_end] = _canonical_hash(
            [
                {"stock_code": code, "row": rows[code]}
                for code in sorted(rows)
            ]
        )
    return memberships, sources, payload_hashes


def _validate_monthly_quote_coverage(
    monthly_maps: dict[str, dict[str, dict]],
    universe_maps: dict[str, set[str]],
    *,
    monthly_payload_hashes: dict[str, str],
    universe_payload_hashes: dict[str, str],
) -> dict[str, dict[str, int | float | str]]:
    """证明全市场月线源足够完整，才允许把单票缺行解释为无交易。"""
    receipts: dict[str, dict[str, int | float]] = {}
    for month_end, quote_rows in monthly_maps.items():
        universe = universe_maps.get(month_end)
        if universe is None:
            raise FactBackfillError(f"{month_end} 缺少 as-of universe 覆盖证据")
        quote_codes = set(quote_rows)
        covered = len(quote_codes & universe)
        coverage = covered / max(len(universe), 1)
        if coverage < MIN_MONTHLY_QUOTE_UNIVERSE_COVERAGE:
            raise FactBackfillError(
                f"get_market_monthly_quotes {month_end} 覆盖不足: "
                f"quotes={len(quote_codes)} universe={len(universe)} "
                f"coverage={coverage:.2%}"
            )
        receipts[month_end] = {
            "quote_count": len(quote_codes),
            "universe_count": len(universe),
            "covered_count": covered,
            "coverage": round(coverage, 12),
            "monthly_payload_sha256": monthly_payload_hashes[month_end],
            "universe_payload_sha256": universe_payload_hashes[month_end],
        }
    return receipts


def _derive_fact(**kwargs) -> dict:
    """隔离派生算法 API，便于 schema/算法分支独立落地后只改这一层。"""
    try:
        from services.monthly_pattern import derived_facts
    except ImportError as exc:  # pragma: no cover - 合并期间的明确错误
        raise FactBackfillError("derived_facts 模块尚未就绪") from exc
    daily_rows = list(kwargs.pop("daily_rows"))
    factor_rows = list(kwargs.pop("factor_rows"))
    raw_monthly = kwargs.pop("raw_monthly")
    universe_member = kwargs.pop("universe_member")
    if daily_rows and raw_monthly is None:
        raise FactBackfillError("日线非空但原始月线为空，来源冲突")
    if not daily_rows and raw_monthly is not None:
        raise FactBackfillError("原始月线非空但日线为空，来源冲突")
    if not universe_member:
        raise FactBackfillError("目标月不在 as-of 股票宇宙，规划状态已漂移")
    if not daily_rows:
        builder = getattr(
            derived_facts,
            "build_certified_no_trade_fact",
            None,
        )
        if builder is None:
            raise FactBackfillError("derived_facts 缺少 no-trade 构建 API")
        return dict(
            builder(
                kwargs.pop("stock_code"),
                kwargs.pop("month_end"),
                universe_proven=True,
                raw_monthly_empty=True,
                daily_empty=True,
                **kwargs,
            )
        )
    builder = getattr(derived_facts, "build_month_fact", None)
    if builder is None:
        builder = getattr(derived_facts, "build_derived_month_fact", None)
    if builder is None:
        raise FactBackfillError("derived_facts 缺少完成月事实构建 API")
    return dict(
        builder(
            kwargs.pop("stock_code"),
            kwargs.pop("month_end"),
            daily_rows,
            factor_rows,
            raw_monthly=raw_monthly,
            **kwargs,
        )
    )


def _persist_receipt(
    conn: sqlite3.Connection,
    *,
    run: dict,
    facts: list[dict],
) -> None:
    saver = getattr(repository, "save_derived_fact_run_and_facts", None)
    if saver is not None:
        saver(conn, run=run, facts=facts)
        return
    run_saver = getattr(repository, "save_derived_fact_run", None)
    fact_saver = getattr(repository, "save_derived_month_facts", None)
    if run_saver is None or fact_saver is None:
        raise FactBackfillError("派生事实 repository 写入 API 尚未就绪")
    run_saver(conn, run)
    fact_saver(conn, facts)


def _database_state_hash(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> str:
    """绑定 raw、manifest 与既有派生事实，避免等待确认期间事实漂移。"""
    rows = repository.load_month_bars(conn, month_ends)
    manifests = repository.load_month_bar_manifests(conn, month_ends)
    derived = repository.load_derived_month_facts(conn, month_ends)
    return _canonical_hash(
        {
            "rows": sorted(
                [
                    {
                        key: row.get(key)
                        for key in (
                            "month_end",
                            "stock_code",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "amount",
                            "adj_factor",
                        )
                    }
                    for row in rows
                ],
                key=lambda item: (item["stock_code"], item["month_end"]),
            ),
            "manifests": sorted(
                manifests,
                key=lambda item: str(item.get("month_end") or ""),
            ),
            "derived_facts": sorted(
                [
                    {
                        "month_end": item.get("month_end"),
                        "stock_code": item.get("stock_code"),
                        "fact_status": item.get("fact_status"),
                        "fact_hash": item.get("fact_hash"),
                    }
                    for item in derived
                ],
                key=lambda item: (
                    str(item["stock_code"] or ""),
                    str(item["month_end"] or ""),
                ),
            ),
        }
    )


def _build_receipt(
    conn: sqlite3.Connection,
    registry,
    *,
    target_date: str,
    months: int,
    max_stocks: int | None,
) -> dict:
    resolved_date, _latest_open = indicator_watch_service.resolve_target_date(
        registry,
        target_date,
    )
    if resolved_date != target_date:
        raise FactBackfillValidationError("显式 date 不允许自动回退")
    month_ends = indicator_watch_service._certified_month_ends(  # noqa: SLF001
        conn,
        registry,
        target_date,
        months=months,
    )
    expected_count = indicator_watch_service._certified_universe_count(  # noqa: SLF001
        conn,
        month_ends[-1],
    )
    latest_universe = _latest_universe_rows(
        registry,
        month_ends[-1],
        expected_count=expected_count,
    )
    effective_rows = _load_effective_rows(conn, month_ends)
    no_trade_keys = _load_no_trade_keys(conn, month_ends)
    targets, plan_counts = _plan_targets(
        effective_rows=effective_rows,
        month_ends=month_ends,
        universe_rows=latest_universe,
        no_trade_keys=no_trade_keys,
    )
    total_planned_stocks = len(targets)
    if max_stocks is not None:
        targets = dict(list(targets.items())[:max_stocks])
    plan_counts["planned_stocks_before_limit"] = total_planned_stocks
    plan_counts["truncated_stocks"] = total_planned_stocks - len(targets)
    plan_counts["planned_stocks"] = len(targets)
    plan_counts["planned_months"] = sum(len(item) for item in targets.values())

    raw_by_key = {
        (_bare_code(row.get("stock_code")), str(row.get("month_end") or "")): row
        for row in repository.load_month_bars(conn, month_ends)
    }
    target_months = sorted(
        {
            month_end
            for stock_targets in targets.values()
            for month_end in stock_targets
        }
    )
    missing_raw_months = sorted(
        {
            month_end
            for code, stock_targets in targets.items()
            for month_end in stock_targets
            if (code, month_end) not in raw_by_key
        }
    )
    (
        universe_maps,
        universe_sources,
        universe_payload_hashes,
    ) = _universe_maps(registry, target_months)
    (
        monthly_maps,
        monthly_sources,
        monthly_payload_hashes,
    ) = _monthly_quote_maps(
        registry,
        missing_raw_months,
    )
    monthly_coverage = _validate_monthly_quote_coverage(
        monthly_maps,
        universe_maps,
        monthly_payload_hashes=monthly_payload_hashes,
        universe_payload_hashes=universe_payload_hashes,
    )

    facts: list[dict] = []
    items: list[dict] = []
    outcome_counts = Counter()
    for code, stock_targets in targets.items():
        ordered_months = sorted(stock_targets)
        start_date = ordered_months[0][:7] + "-01"
        end_date = ordered_months[-1]
        daily_result = registry.call(
            "get_stock_daily_range",
            code,
            start_date,
            end_date,
        )
        factor_result = registry.call(
            "get_stock_adj_factor_range",
            code,
            start_date,
            end_date,
        )
        if not _result_ok(daily_result) or not isinstance(daily_result.data, list):
            error = f"daily_source_failed: {getattr(daily_result, 'error', None)}"
            for month_end in ordered_months:
                items.append(
                    {
                        "stock_code": code,
                        "month_end": month_end,
                        "reasons": sorted(stock_targets[month_end]),
                        "outcome": "unresolved",
                        "error": error,
                    }
                )
                outcome_counts["unresolved"] += 1
            continue
        factor_source_error = None
        if not _result_ok(factor_result) or not isinstance(factor_result.data, list):
            # 整月无交易的认证不依赖复权因子；只在该月确有日线时阻断。
            factor_source_error = (
                "adj_factor_source_failed: "
                f"{getattr(factor_result, 'error', None)}"
            )
        try:
            daily_rows = _dedupe(
                [dict(row) for row in daily_result.data],
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
                label=f"{code} daily",
            )
            factor_rows = (
                []
                if factor_source_error is not None
                else _dedupe(
                    [dict(row) for row in factor_result.data],
                    fields=("adj_factor",),
                    label=f"{code} adj_factor",
                )
            )
        except FactBackfillError as exc:
            for month_end in ordered_months:
                items.append(
                    {
                        "stock_code": code,
                        "month_end": month_end,
                        "reasons": sorted(stock_targets[month_end]),
                        "outcome": "unresolved",
                        "error": str(exc),
                    }
                )
                outcome_counts["unresolved"] += 1
            continue

        stock_name = str(latest_universe[code].get("name") or "")
        for month_end in ordered_months:
            month_key = month_end[:7]
            month_daily = [
                row
                for row in daily_rows
                if str(row.get("trade_date") or "")[:7] == month_key
            ]
            month_factors = [
                row
                for row in factor_rows
                if str(row.get("trade_date") or "")[:7] == month_key
            ]
            raw_month = (
                raw_by_key.get((code, month_end))
                or monthly_maps.get(month_end, {}).get(code)
            )
            member = code in universe_maps[month_end]
            reasons = sorted(stock_targets[month_end])
            if month_daily and factor_source_error is not None:
                items.append(
                    {
                        "stock_code": code,
                        "month_end": month_end,
                        "reasons": reasons,
                        "outcome": "unresolved",
                        "error": factor_source_error,
                    }
                )
                outcome_counts["unresolved"] += 1
                continue
            source_meta = {
                "daily_source": getattr(daily_result, "source", None),
                "daily_error": getattr(daily_result, "error", None),
                "factor_source": getattr(factor_result, "source", None),
                "factor_error": getattr(factor_result, "error", None),
                "monthly_source": monthly_sources.get(month_end),
                "monthly_source_coverage": monthly_coverage.get(month_end),
                "monthly_payload_sha256": monthly_payload_hashes.get(month_end),
                "universe_source": universe_sources.get(month_end),
                "universe_payload_sha256": universe_payload_hashes[month_end],
                "query_start": start_date,
                "query_end": end_date,
                "all_reasons": reasons,
            }
            source_payload_hash = _canonical_hash(
                {
                    "stock_code": code,
                    "month_end": month_end,
                    "daily_rows": month_daily,
                    "factor_rows": month_factors,
                    "raw_monthly": raw_month,
                    "universe_member": member,
                    "source_meta": source_meta,
                }
            )
            try:
                fact = _derive_fact(
                    stock_code=code,
                    stock_name=stock_name,
                    month_end=month_end,
                    daily_rows=month_daily,
                    factor_rows=month_factors,
                    raw_monthly=raw_month,
                    universe_member=member,
                    replacement_reason=reasons[0],
                    source_meta=source_meta,
                    source_payload_hash=source_payload_hash,
                )
                fact_status = str(
                    fact.get("fact_status") or fact.get("status") or ""
                )
                fact_hash = str(fact.get("fact_hash") or "")
                if fact_status not in {"certified_bar", "certified_no_trade"}:
                    raise FactBackfillError(
                        str(fact.get("error") or "派生事实未通过认证")
                    )
                if len(fact_hash) != 64:
                    raise FactBackfillError("派生事实缺少合法 fact_hash")
            except Exception as exc:
                items.append(
                    {
                        "stock_code": code,
                        "month_end": month_end,
                        "reasons": reasons,
                        "outcome": "unresolved",
                        "error": str(exc),
                    }
                )
                outcome_counts["unresolved"] += 1
                continue
            facts.append(fact)
            items.append(
                {
                    "stock_code": code,
                    "month_end": month_end,
                    "reasons": reasons,
                    "outcome": fact_status,
                    "fact_hash": fact_hash,
                }
            )
            outcome_counts[fact_status] += 1

    items.sort(key=lambda item: (item["stock_code"], item["month_end"]))
    database_state_hash = _database_state_hash(conn, month_ends)
    public_receipt = {
        "target_date": target_date,
        "months": months,
        "formula_version": FORMULA_VERSION,
        "confirmation_scope": "derived_facts_and_input_state",
        "database_state_hash": database_state_hash,
        "plan_counts": plan_counts,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "items": items,
    }
    return {
        **public_receipt,
        "receipt_hash": _canonical_hash(public_receipt),
        "month_ends": month_ends,
        "_facts": facts,
    }


def _run_payload(
    receipt: dict,
    *,
    input_by: str,
    status: str = "complete",
) -> dict:
    run = {
        "run_id": uuid.uuid4().hex,
        "input_by": input_by,
        "request": {
            "target_date": receipt["target_date"],
            "months": receipt["months"],
            "formula_version": receipt["formula_version"],
            "confirmation_receipt_hash": receipt["receipt_hash"],
            "database_state_hash": receipt["database_state_hash"],
        },
        "counts": {
            **receipt["plan_counts"],
            **receipt["outcome_counts"],
        },
        "receipt": {
            "confirmation_receipt_hash": receipt["receipt_hash"],
            "database_state_hash": receipt["database_state_hash"],
            "items": receipt["items"],
        },
        "status": status,
    }
    hasher = getattr(
        repository,
        "compute_derived_fact_run_receipt_hash",
        None,
    )
    if hasher is None:
        run["receipt_hash"] = _canonical_hash(
            {
                key: run[key]
                for key in (
                    "run_id",
                    "input_by",
                    "status",
                    "request",
                    "counts",
                    "receipt",
                )
            }
        )
    else:
        run["receipt_hash"] = hasher(run)
    return run


def run_backfill(
    conn: sqlite3.Connection,
    registry,
    *,
    target_date: str,
    months: int,
    input_by: str,
    dry_run: bool,
    expected_receipt_hash: str | None = None,
    max_stocks: int | None = None,
    ensure_schema_before_persist: Callable[[], Any] | None = None,
) -> dict:
    """生成收据；dry-run 仅写内存连接，实际写入要求完整且哈希一致。"""
    try:
        date.fromisoformat(target_date)
    except (TypeError, ValueError) as exc:
        raise FactBackfillValidationError("date 必须为 YYYY-MM-DD") from exc
    requester = str(input_by or "").strip()
    if not requester:
        raise FactBackfillValidationError("input_by 不能为空")
    if months < 35:
        raise FactBackfillValidationError("months 至少为 35")
    if max_stocks is not None and max_stocks <= 0:
        raise FactBackfillValidationError("max_stocks 必须为正整数")
    if not dry_run and max_stocks is not None:
        raise FactBackfillValidationError("--max-stocks 只允许与 --dry-run 同用")
    if not dry_run and not str(expected_receipt_hash or "").strip():
        raise FactBackfillValidationError(
            "实际写入必须提供 --expect-receipt-hash"
        )

    receipt = _build_receipt(
        conn,
        registry,
        target_date=target_date,
        months=months,
        max_stocks=max_stocks,
    )
    unresolved = int(receipt["outcome_counts"].get("unresolved", 0))
    truncated = int(receipt["plan_counts"].get("truncated_stocks", 0))
    summary = {
        key: value
        for key, value in receipt.items()
        if not key.startswith("_") and key not in {"month_ends"}
    }
    summary["status"] = (
        "partial"
        if unresolved or truncated
        else ("ready_to_confirm" if dry_run else "complete")
    )
    summary["write_boundary"] = {
        "database": False,
        "derived_facts": False,
        "raw_monthly_bars": False,
        "monthly_pattern_pool": False,
        "watchlist": False,
        "trade_plan": False,
        "push": False,
    }
    summary["monitor_preview"] = None

    if not dry_run and (unresolved or truncated):
        return summary
    if not dry_run and receipt["receipt_hash"] != expected_receipt_hash:
        summary["status"] = "receipt_mismatch"
        summary["error"] = (
            "receipt_hash 与 dry-run 确认值不一致；数据库未写入"
        )
        return summary

    run = _run_payload(
        receipt,
        input_by=requester,
        status="partial" if unresolved or truncated else "complete",
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        if receipt["database_state_hash"] != _database_state_hash(
            conn,
            receipt["month_ends"],
        ):
            conn.rollback()
            summary["status"] = "state_drift"
            summary["error"] = (
                "等待确认期间月线/派生事实发生变化；未写入派生事实"
            )
            return summary
        if ensure_schema_before_persist is not None:
            ensure_schema_before_persist()
        _persist_receipt(
            conn,
            run=run,
            facts=receipt["_facts"],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    summary["write_boundary"]["database"] = not dry_run
    summary["write_boundary"]["derived_facts"] = not dry_run
    if dry_run:
        try:
            summary["monitor_preview"] = indicator_watch_service.run_monitor(
                conn,
                registry,
                target_date,
                months=months,
                max_seeds=None,
            )
        except Exception as exc:
            summary["monitor_preview"] = {
                "status": "blocked",
                "error": str(exc),
            }
    else:
        summary["run_id"] = run["run_id"]
    return summary
