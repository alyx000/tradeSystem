"""月线模式全市场扫描与观察池编排。"""
from __future__ import annotations

import calendar
import math
import sqlite3
from collections import defaultdict
from datetime import date as date_type

from services.monthly_pattern import detectors, financials, mainline, market, pool, repository
from services.monthly_pattern.models import MonthlyBar
from services.volume_concentration import repo as concentration_repo


STRATEGIES = (
    "fundamental_monthly_trend",
    "theme_monthly_attack",
    "monthly_reacceleration",
)

_STRATEGY_REQUIRED_MONTHS = {
    "fundamental_monthly_trend": detectors.FUNDAMENTAL_MIN_MONTHS,
    "theme_monthly_attack": detectors.MACD_MIN_MONTHS,
    "monthly_reacceleration": detectors.MACD_MIN_MONTHS,
}


class MonthlyPatternSourceError(RuntimeError):
    pass


class MonthlyPatternTemporalOrderError(ValueError):
    """扫描日早于现有运行/池状态水位，禁止把未来状态带入历史结果。"""


def ensure_scan_not_before_pool_tail(
    conn: sqlite3.Connection,
    scan_date: str,
) -> None:
    """历史扫描必须从不晚于现有池水位的可信检查点另行重建。"""
    row = conn.execute(
        """
        SELECT MAX(activity_date)
        FROM (
            SELECT entered_date AS activity_date FROM monthly_pattern_pool
            UNION ALL
            SELECT last_seen_date AS activity_date FROM monthly_pattern_pool
            UNION ALL
            SELECT exited_date AS activity_date
            FROM monthly_pattern_pool
            WHERE exited_date IS NOT NULL
            UNION ALL
            SELECT scan_date AS activity_date FROM monthly_pattern_runs
        )
        """
    ).fetchone()
    state_tail = str(row[0] or "") if row else ""
    if state_tail and scan_date < state_tail:
        raise MonthlyPatternTemporalOrderError(
            f"scan_date={scan_date} 早于月线状态水位 {state_tail}；"
            "禁止在 live pool 上历史回放。请从可信检查点重建后缀。"
        )


def _result_ok(result) -> bool:
    return bool(getattr(result, "success", False))


def _calendar_month_ends(registry, scan_date: str, months: int) -> list[str]:
    try:
        as_of = date_type.fromisoformat(scan_date)
    except ValueError as exc:
        raise ValueError("scan_date 必须为 YYYY-MM-DD") from exc
    years_back = math.ceil(months / 12) + 1
    rows: list[dict] = []
    for year in range(as_of.year - years_back, as_of.year + 1):
        result = registry.call("get_trade_calendar", f"{year}-06-30")
        if not _result_ok(result) or not isinstance(result.data, list):
            raise MonthlyPatternSourceError(
                f"trade_calendar {year} source_failed: "
                f"{getattr(result, 'error', 'unknown error')}"
            )
        rows.extend(result.data)
    month_ends = market.select_completed_month_ends(rows, scan_date, months=months)
    if len(month_ends) != months:
        raise MonthlyPatternSourceError(
            f"完成月交易日历仅 {len(month_ends)} 个月，要求 {months} 个月"
        )
    month_keys = [item[:7] for item in month_ends]
    for previous, current in zip(month_keys, month_keys[1:]):
        year, month = (int(part) for part in previous.split("-"))
        if month == 12:
            expected = f"{year + 1:04d}-01"
        else:
            expected = f"{year:04d}-{month + 1:02d}"
        if current != expected:
            raise MonthlyPatternSourceError(
                f"完成月交易日历不连续: {previous} 后为 {current}，应为 {expected}"
            )
    if as_of.day == calendar.monthrange(as_of.year, as_of.month)[1]:
        expected_tail = f"{as_of.year:04d}-{as_of.month:02d}"
    elif as_of.month == 1:
        expected_tail = f"{as_of.year - 1:04d}-12"
    else:
        expected_tail = f"{as_of.year:04d}-{as_of.month - 1:02d}"
    if month_keys[-1] != expected_tail:
        raise MonthlyPatternSourceError(
            f"完成月交易日历尾月为 {month_keys[-1]}，"
            f"扫描日 {scan_date} 应为 {expected_tail}"
        )
    return month_ends


def _ensure_month_bars(
    conn: sqlite3.Connection,
    registry,
    month_ends: list[str],
    *,
    min_market_rows: int,
    min_factor_coverage: float,
    min_universe_coverage: float = 0.95,
    min_adjacent_coverage_ratio: float = 0.98,
    max_adjacent_coverage_ratio: float = 1.02,
) -> tuple[int, int]:
    existing = repository.existing_month_ends(
        conn,
        month_ends,
        min_rows=min_market_rows,
        min_universe_coverage=min_universe_coverage,
        min_factor_coverage=min_factor_coverage,
    )
    manifests_by_month = {
        item["month_end"]: item
        for item in repository.load_month_bar_manifests(conn, sorted(existing))
    }
    fetched = 0
    for month_end in month_ends:
        if month_end in existing:
            continue
        universe_result = registry.call("get_stock_universe_as_of", month_end)
        if not _result_ok(universe_result) or not isinstance(universe_result.data, list):
            raise MonthlyPatternSourceError(
                f"get_stock_universe_as_of {month_end}: "
                f"{getattr(universe_result, 'error', 'source_failed')}"
            )
        quotes_result = registry.call("get_market_monthly_quotes", month_end)
        if not _result_ok(quotes_result) or not isinstance(quotes_result.data, list):
            raise MonthlyPatternSourceError(
                f"get_market_monthly_quotes {month_end}: "
                f"{getattr(quotes_result, 'error', 'source_failed')}"
            )
        factors_result = registry.call("get_adj_factor", month_end)
        if not _result_ok(factors_result) or not isinstance(factors_result.data, list):
            raise MonthlyPatternSourceError(
                f"get_adj_factor {month_end}: "
                f"{getattr(factors_result, 'error', 'source_failed')}"
            )
        rows, manifest = market.join_month_quotes_and_factors(
            quotes_result.data,
            factors_result.data,
            month_end=month_end,
            min_rows=min_market_rows,
            min_factor_coverage=min_factor_coverage,
            universe_rows=universe_result.data,
            min_universe_coverage=min_universe_coverage,
            universe_source=getattr(universe_result, "source", None)
            or "get_stock_universe_as_of",
            return_manifest=True,
        )
        manifest["source_meta"].update(
            {
                "quote_source": getattr(quotes_result, "source", None),
                "factor_source": getattr(factors_result, "source", None),
                "min_adjacent_coverage_ratio": min_adjacent_coverage_ratio,
                "max_adjacent_coverage_ratio": max_adjacent_coverage_ratio,
                "adjacent_guard": "passed_for_available_neighbors",
            }
        )
        market.validate_month_manifest_sequence(
            [*manifests_by_month.values(), manifest],
            min_adjacent_coverage_ratio=min_adjacent_coverage_ratio,
            max_adjacent_coverage_ratio=max_adjacent_coverage_ratio,
        )
        repository.replace_month_bars(conn, rows)
        repository.save_month_bar_manifest(conn, manifest)
        # 合法月份按成功前缀持久化，后续月份失败时可断点续跑；池状态另走原子事务。
        conn.commit()
        manifests_by_month[month_end] = manifest
        fetched += 1
    market.validate_month_manifest_sequence(
        [manifests_by_month[item] for item in month_ends],
        min_adjacent_coverage_ratio=min_adjacent_coverage_ratio,
        max_adjacent_coverage_ratio=max_adjacent_coverage_ratio,
    )
    return fetched, len(existing)


def _stock_series(rows: list[dict]) -> dict[str, list[MonthlyBar]]:
    adjusted = market.apply_month_end_qfq(rows)
    grouped: dict[str, list[MonthlyBar]] = defaultdict(list)
    for row in adjusted:
        grouped[row["stock_code"]].append(
            MonthlyBar(
                month=row["month_end"][:7],
                end_date=row["month_end"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row["amount"]),
                is_complete=True,
                trading_days=1,
                price_shape_valid=bool(row.get("price_shape_valid", True)),
            )
        )
    return {
        code: sorted(bars, key=lambda bar: (bar.end_date, bar.month))
        for code, bars in grouped.items()
    }


def _has_required_month_suffix(
    bars: list[MonthlyBar],
    expected_months: list[str],
    *,
    required_months: int,
) -> bool:
    """个股必须拥有截至信号月的连续交易月后缀，不能拿陈旧末根冒充本月。"""
    if required_months <= 0 or len(expected_months) < required_months:
        return False
    actual = {bar.month for bar in bars if bar.is_complete}
    required = expected_months[-required_months:]
    return all(month in actual for month in required)


def _two_latest_months_below_ma5(
    bars: list[MonthlyBar],
    expected_months: list[str],
) -> bool:
    """只认最近两个严格相邻完成交易月均收于各自 MA5 下方。"""
    # 前一月 MA5 至少需要再往前 4 根，因此要有连续 6 个交易月。
    if not _has_required_month_suffix(
        bars,
        expected_months,
        required_months=6,
    ):
        return False
    by_month = {bar.month: bar for bar in bars if bar.is_complete}
    tail = [by_month[month] for month in expected_months[-6:]]
    indicators = detectors.compute_monthly_indicators(tail)
    previous, latest = tail[-2:]
    previous_ma5 = indicators[-2].ma5
    latest_ma5 = indicators[-1].ma5
    return bool(
        previous_ma5 is not None
        and latest_ma5 is not None
        and previous.close < previous_ma5
        and latest.close < latest_ma5
    )


def _minimum_financial_period(scan_date: str) -> str:
    as_of = date_type.fromisoformat(scan_date)
    if as_of.month <= 4:
        return f"{as_of.year - 1:04d}-09-30"
    if as_of.month <= 8:
        return f"{as_of.year:04d}-03-31"
    if as_of.month <= 10:
        return f"{as_of.year:04d}-06-30"
    return f"{as_of.year:04d}-09-30"


def _minimum_annual_period(scan_date: str) -> str:
    """返回在该扫描月之前已经法定到期的最近年报期。"""
    as_of = date_type.fromisoformat(scan_date)
    annual_year = as_of.year - 2 if as_of.month <= 4 else as_of.year - 1
    return f"{annual_year:04d}-12-31"


def _financial_report_periods_for_scan(scan_date: str) -> list[str]:
    """显式请求最新期间、法定到期期间及其同比期间，避免 provider 默认截尾。"""
    as_of = date_type.fromisoformat(scan_date)
    candidates = [
        f"{year:04d}-{month_day}"
        for year in range(as_of.year, as_of.year - 4, -1)
        for month_day in ("03-31", "06-30", "09-30", "12-31")
        if f"{year:04d}-{month_day}" <= scan_date
    ]
    periods = set(sorted(candidates, reverse=True)[:5])
    required = {
        _minimum_financial_period(scan_date),
        _minimum_annual_period(scan_date),
    }
    periods.update(required)
    for period in required:
        periods.add(f"{int(period[:4]) - 1:04d}{period[4:]}")
    return sorted(periods, reverse=True)


def _latest_financial_views(
    rows: list[dict],
    *,
    scan_date: str | None = None,
) -> dict[str, dict]:
    by_code_period: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        code = str(row.get("stock_code") or row.get("ts_code") or "").split(".")[0]
        period = str(row.get("report_period") or "")
        if not code or not period:
            continue
        previous = by_code_period[code].get(period)
        row_version = (
            str(
                row.get("version_visible_date")
                or row.get("financial_ann_date")
                or ""
            ),
            str(row.get("version_observed_at") or ""),
            str(row.get("financial_ann_date") or ""),
            str(row.get("created_at") or ""),
            str(row.get("snapshot_hash") or ""),
        )
        previous_version = (
            str(
                (previous or {}).get("version_visible_date")
                or (previous or {}).get("financial_ann_date")
                or ""
            ),
            str((previous or {}).get("version_observed_at") or ""),
            str((previous or {}).get("financial_ann_date") or ""),
            str((previous or {}).get("created_at") or ""),
            str((previous or {}).get("snapshot_hash") or ""),
        )
        if previous is None or row_version > previous_version:
            by_code_period[code][period] = row

    output: dict[str, dict] = {}
    for code, periods in by_code_period.items():
        ordered = sorted(periods)
        latest = periods[ordered[-1]]
        annual_periods = [period for period in ordered if period.endswith("-12-31")]
        annual = periods[annual_periods[-1]] if annual_periods else None

        def prior_for(snapshot: dict | None) -> dict | None:
            if snapshot is None:
                return None
            period = str(snapshot.get("report_period") or "")
            try:
                prior_period = f"{int(period[:4]) - 1}{period[4:]}"
            except (TypeError, ValueError):
                return None
            return periods.get(prior_period)

        def previous_period_for(snapshot: dict | None) -> dict | None:
            if snapshot is None:
                return None
            period = str(snapshot.get("report_period") or "")
            try:
                year = int(period[:4])
            except (TypeError, ValueError):
                return None
            previous_period = {
                "03-31": f"{year - 1:04d}-12-31",
                "06-30": f"{year:04d}-03-31",
                "09-30": f"{year:04d}-06-30",
                "12-31": f"{year:04d}-09-30",
            }.get(period[5:])
            return periods.get(previous_period) if previous_period else None

        latest_assessment = financials.evaluate_financial_snapshot(
            latest,
            prior_same_period=prior_for(latest),
            prior_period=previous_period_for(latest),
        )
        annual_assessment = (
            financials.evaluate_financial_snapshot(
                annual,
                prior_same_period=prior_for(annual),
                prior_period=previous_period_for(annual),
            )
            if annual is not None
            else None
        )
        verified = bool(
            annual_assessment
            and annual_assessment["status"] == "verified"
            and latest_assessment["core_passed"]
        )
        stale = bool(
            scan_date
            and str(latest.get("report_period") or "")
            < _minimum_financial_period(scan_date)
        )
        if stale:
            financial_status = "stale"
            verified = False
        elif verified:
            financial_status = "verified"
        elif any(
            assessment
            and assessment.get("status") == "failed"
            for assessment in (latest_assessment, annual_assessment)
        ):
            financial_status = "failed"
        elif (
            annual_assessment is None
            and latest_assessment.get("status") == "pre_screen"
        ):
            financial_status = "pre_screen"
        else:
            financial_status = "insufficient"
        output[code] = {
            "status": financial_status,
            "verified": verified,
            "latest": latest_assessment,
            "annual": annual_assessment,
            "report_period": latest.get("report_period"),
            "financial_ann_date": latest.get("financial_ann_date"),
        }
    return output


def _financial_views(
    conn: sqlite3.Connection,
    registry,
    scan_date: str,
    *,
    include_financial: bool,
    market_codes: set[str],
    min_coverage: float = 0.90,
) -> tuple[dict[str, dict], str]:
    if not include_financial:
        return {}, "disabled"
    report_periods = _financial_report_periods_for_scan(scan_date)
    result = registry.call(
        "get_financial_snapshots",
        scan_date,
        report_periods,
    )
    if not _result_ok(result) or not isinstance(result.data, list):
        return {}, "source_failed"
    source_meta = {
        "source": getattr(result, "source", None),
        "note": getattr(result, "note", None),
    }
    snapshots = [{**row, "source_meta": source_meta} for row in result.data]
    if not snapshots:
        # 成功空批次不能回读历史缓存，否则新候选会被陈旧财务误升为 active。
        return {}, "source_ok_empty"
    repository.save_financial_snapshots(
        conn,
        snapshots,
        observed_date=(scan_date if not _is_historical_scan(scan_date) else None),
    )
    conn.commit()
    batch_codes = {
        str(row.get("stock_code") or row.get("ts_code") or "").split(".")[0]
        for row in snapshots
        if row.get("stock_code") or row.get("ts_code")
    }
    covered_codes = batch_codes & market_codes
    coverage = len(covered_codes) / len(market_codes) if market_codes else 0.0
    if coverage < min_coverage:
        return {}, "coverage_failed"
    unavailable_component_states = {
        "missing",
        "source_failed",
        "coverage_failed",
        "failed",
    }
    required_periods = {
        _minimum_financial_period(scan_date),
        _minimum_annual_period(scan_date),
    }
    for required_period in required_periods:
        for component in ("fina_indicator", "balancesheet", "income"):
            component_codes = {
                str(row.get("stock_code") or row.get("ts_code") or "").split(".")[0]
                for row in snapshots
                if str(row.get("report_period") or "") == required_period
                if isinstance(row.get(component), dict)
                and str(row[component].get("status") or "").lower()
                not in unavailable_component_states
            }
            component_coverage = (
                len(component_codes & market_codes) / len(market_codes)
                if market_codes
                else 0.0
            )
            if component_coverage < min_coverage:
                return {}, "coverage_failed"
    stored = repository.load_financial_snapshots(conn, as_of_date=scan_date)
    # 即使整批覆盖过门，批次中漏掉的个股也不得回退到该股陈旧缓存。
    batch_periods = {
        (
            str(row.get("stock_code") or row.get("ts_code") or "").split(".")[0],
            str(row.get("report_period") or ""),
        )
        for row in snapshots
    }
    current_batch_stored = [
        row
        for row in stored
        if (
            str(row.get("stock_code") or "").split(".")[0],
            str(row.get("report_period") or ""),
        )
        in batch_periods
    ]
    eligible_codes = {
        str(row.get("stock_code") or "").split(".")[0]
        for row in current_batch_stored
    }
    eligible_coverage = (
        len(eligible_codes & market_codes) / len(market_codes)
        if market_codes
        else 0.0
    )
    if eligible_coverage < min_coverage:
        return {}, "as_of_coverage_failed"
    for required_period in required_periods:
        for component in ("fina_indicator", "balancesheet", "income"):
            eligible_component_codes = {
                str(row.get("stock_code") or "").split(".")[0]
                for row in current_batch_stored
                if str(row.get("report_period") or "") == required_period
                if isinstance(row.get(component), dict)
                and str(row[component].get("status") or "").lower()
                not in unavailable_component_states
            }
            component_coverage = (
                len(eligible_component_codes & market_codes) / len(market_codes)
                if market_codes
                else 0.0
            )
            if component_coverage < min_coverage:
                return {}, "as_of_coverage_failed"
    return _latest_financial_views(
        current_batch_stored,
        scan_date=scan_date,
    ), "success"


def _is_historical_scan(scan_date: str) -> bool:
    return date_type.fromisoformat(scan_date) < date_type.today()


def _industry_context(
    conn: sqlite3.Connection,
    registry,
    scan_date: str,
    *,
    market_codes: set[str],
    min_coverage: float = 0.90,
) -> tuple[dict[str, dict], list[str], dict, str]:
    if _is_historical_scan(scan_date):
        industry_map: dict[str, dict] = {}
        industry_status = "not_as_of"
    else:
        result = registry.call("get_stock_sw_industry_map")
        if not _result_ok(result) or not isinstance(result.data, dict):
            industry_map = {}
            industry_status = "source_failed"
        else:
            industry_map = {
                str(code).split(".")[0]: item
                for code, item in result.data.items()
                if isinstance(item, dict)
            }
            coverage = (
                len(set(industry_map) & market_codes) / len(market_codes)
                if market_codes
                else 0.0
            )
            industry_status = (
                "success"
                if industry_map and coverage >= min_coverage
                else "coverage_failed"
            )
            if industry_status != "success":
                industry_map = {}
    records = concentration_repo.get_recent_concentration(conn, scan_date, 3)
    sectors, meta = mainline.stable_main_sectors(records, top_k=8)
    return industry_map, sectors, meta, industry_status


def _candidate_status(
    strategy: str,
    *,
    open_row: dict | None,
    financial_view: dict | None,
    mainline_match: bool | None,
    signal_month: str,
    expected_months: list[str],
) -> str:
    if strategy == "fundamental_monthly_trend":
        if not (financial_view and financial_view.get("verified")):
            return "technical_candidate"
        if open_row is None:
            return "fundamental_verified"
        current = open_row["status"]
        if current == "active":
            return "active"
        if current == "fundamental_verified":
            try:
                current_index = expected_months.index(signal_month)
            except ValueError:
                return "fundamental_verified"
            previous_signal = (
                expected_months[current_index - 1] if current_index > 0 else None
            )
            verified_month = (
                (open_row.get("technical_evidence") or {}).get(
                    "fundamental_verified_month"
                )
                or open_row.get("signal_month")
            )
            return (
                "active"
                if verified_month == previous_signal
                else "fundamental_verified"
            )
        if current == "risk":
            risk_origin = str(
                ((open_row.get("source_meta") or {}).get("risk_from_status"))
                or ""
            )
            return (
                "active"
                if risk_origin == "active"
                else "fundamental_verified"
            )
        return "fundamental_verified"
    if strategy == "theme_monthly_attack":
        return "active" if mainline_match is True else "technical_candidate"
    return "active"


def _financial_explicitly_failed(financial_view: dict | None) -> bool:
    """只有字段完整且明确未过硬门的快照才算反证。"""
    if not financial_view:
        return False
    assessments = (
        financial_view.get("latest"),
        financial_view.get("annual"),
    )
    return any(
        isinstance(assessment, dict) and assessment.get("status") == "failed"
        for assessment in assessments
    )


def _resolve_candidate_status(
    strategy: str,
    *,
    open_row: dict | None,
    financial_view: dict | None,
    mainline_match: bool | None,
    industry_status: str,
    mainline_status: str,
    signal_month: str,
    expected_months: list[str],
) -> str:
    proposed = _candidate_status(
        strategy,
        open_row=open_row,
        financial_view=financial_view,
        mainline_match=mainline_match,
        signal_month=signal_month,
        expected_months=expected_months,
    )
    if open_row is None or proposed != "technical_candidate":
        return proposed

    current = open_row["status"]
    if current not in {"active", "risk", "fundamental_verified"}:
        return proposed
    if strategy == "fundamental_monthly_trend":
        return "risk" if _financial_explicitly_failed(financial_view) else current
    if strategy == "theme_monthly_attack":
        reliable_mismatch = (
            mainline_match is False
            and industry_status == "success"
            and mainline_status in {"ok", "limited_history"}
        )
        return "risk" if reliable_mismatch else current
    return proposed


def _financial_payload(
    open_row: dict | None,
    financial_view: dict | None,
    finance_status: str,
) -> tuple[dict, str | None, str | None]:
    """来源不可用或新快照资料不足时，不销毁池内最后一份有效财务证据。"""
    keep_existing = bool(
        open_row
        and (
            financial_view is None
            or
            finance_status in {
                "disabled",
                "source_failed",
                "source_ok_empty",
                "coverage_failed",
                "as_of_coverage_failed",
            }
            or (financial_view or {}).get("status") in {"insufficient", "stale"}
        )
        and (open_row.get("financial_evidence") or {})
    )
    if keep_existing:
        return (
            open_row.get("financial_evidence") or {},
            open_row.get("report_period"),
            open_row.get("financial_ann_date"),
        )
    view = financial_view or {}
    return (
        view,
        view.get("report_period"),
        view.get("financial_ann_date"),
    )


def _source_meta_for_status(
    base: dict | None,
    *,
    open_row: dict | None,
    target_status: str,
) -> dict:
    """风险态记录来源状态，避免未完成两阶段确认的 episode 恢复时直升。"""
    source_meta = dict(base or {})
    if target_status != "risk" or open_row is None:
        return source_meta
    current_status = str(open_row.get("status") or "")
    previous_origin = str(
        ((open_row.get("source_meta") or {}).get("risk_from_status"))
        or ""
    )
    risk_origin = (
        previous_origin if current_status == "risk" else current_status
    )
    if risk_origin:
        source_meta["risk_from_status"] = risk_origin
    return source_meta


def _transition(
    *,
    stock_code: str,
    strategy_type: str,
    action: str,
    from_status: str | None,
    to_status: str,
    reason: str | None = None,
    pool_state: str | None = None,
) -> dict | None:
    """统一状态变化契约；刷新、陈旧请求与同状态写入不进入变化清单。"""
    if action not in {"entered", "transitioned", "exited"}:
        return None
    if from_status == to_status:
        return None
    item = {
        "stock_code": stock_code,
        "strategy_type": strategy_type,
        "action": action,
        "from_status": from_status,
        "to_status": to_status,
    }
    if reason:
        item["reason"] = reason
    if pool_state:
        item["pool_state"] = pool_state
    return item


def _maintain_open_pool(
    conn: sqlite3.Connection,
    series: dict[str, list[MonthlyBar]],
    matched_identities: set[tuple[str, str]],
    *,
    scan_date: str,
    signal_month: str,
    expected_months: list[str],
    finance_by_code: dict[str, dict],
    finance_status: str,
    industry_map: dict[str, dict],
    main_sectors: list[str],
    industry_status: str,
    mainline_status: str,
) -> list[dict]:
    transitions: list[dict] = []
    for open_row in pool.list_pool(conn):
        if open_row["status"] == "exited":
            continue
        identity = (open_row["stock_code"], open_row["strategy_type"])
        if identity in matched_identities:
            continue
        bars = series.get(open_row["stock_code"])
        if not bars or not _has_required_month_suffix(
            bars,
            expected_months,
            required_months=5,
        ):
            # 缺本月或近期交易月断档时严格 no-op，不能用旧末根推进 risk/exit。
            continue
        state = detectors.evaluate_pool_state(bars)
        if state.state == "insufficient_history":
            continue
        current_status = open_row["status"]
        state_evidence = state.evidence
        technical_evidence = {
            **(open_row.get("technical_evidence") or {}),
            "pool_state": state_evidence,
        }
        if state.state == "risk" and _two_latest_months_below_ma5(
            bars,
            expected_months,
        ):
            reason = "最近两个严格相邻完成月收盘均低于各自月MA5"
            changed = pool.mark_exited(
                conn,
                identity[0],
                identity[1],
                date=scan_date,
                reason=reason,
            )
            transition = _transition(
                stock_code=identity[0],
                strategy_type=identity[1],
                action="exited" if changed else "stale",
                from_status=current_status,
                to_status="exited",
                reason=reason,
                pool_state=state.state,
            )
            if transition:
                transitions.append(transition)
            continue

        financial_view = finance_by_code.get(identity[0])
        financial_evidence, report_period, financial_ann_date = _financial_payload(
            open_row,
            financial_view,
            finance_status,
        )
        if (
            identity[1] == "fundamental_monthly_trend"
            and _financial_explicitly_failed(financial_view)
        ):
            risk_source_meta = _source_meta_for_status(
                open_row.get("source_meta"),
                open_row=open_row,
                target_status="risk",
            )
            action = pool.record(
                conn,
                stock_code=identity[0],
                stock_name=open_row["stock_name"],
                strategy_type=identity[1],
                status="risk",
                signal_month=open_row["signal_month"],
                date=scan_date,
                report_period=report_period,
                financial_ann_date=financial_ann_date,
                technical_evidence=technical_evidence,
                financial_evidence=financial_evidence,
                source_meta=risk_source_meta,
            )
            transition = _transition(
                stock_code=identity[0],
                strategy_type=identity[1],
                action=action,
                from_status=current_status,
                to_status="risk",
                reason="最新可见财务快照未通过基本面硬门",
                pool_state=state.state,
            )
            if transition:
                transitions.append(transition)
            continue

        industry = str(
            (industry_map.get(identity[0]) or {}).get("sw_l2") or ""
        )
        reliable_theme_mismatch = bool(
            identity[1] == "theme_monthly_attack"
            and industry_status == "success"
            and mainline_status in {"ok", "limited_history"}
            and industry
            and industry not in main_sectors
        )
        if reliable_theme_mismatch and current_status == "active":
            source_meta = _source_meta_for_status({
                **(open_row.get("source_meta") or {}),
                "industry": industry,
                "mainline_match": False,
            }, open_row=open_row, target_status="risk")
            action = pool.record(
                conn,
                stock_code=identity[0],
                stock_name=open_row["stock_name"],
                strategy_type=identity[1],
                status="risk",
                signal_month=open_row["signal_month"],
                date=scan_date,
                report_period=open_row.get("report_period"),
                financial_ann_date=open_row.get("financial_ann_date"),
                technical_evidence=technical_evidence,
                financial_evidence=open_row.get("financial_evidence"),
                source_meta=source_meta,
            )
            transition = _transition(
                stock_code=identity[0],
                strategy_type=identity[1],
                action=action,
                from_status=current_status,
                to_status="risk",
                reason="当前申万二级行业已不在可靠主线名单",
                pool_state=state.state,
            )
            if transition:
                transitions.append(transition)
            continue

        target_status = current_status
        if state.state == "risk":
            target_status = "risk"
            risk_source_meta = _source_meta_for_status(
                open_row.get("source_meta"),
                open_row=open_row,
                target_status="risk",
            )
            action = pool.record(
                conn,
                stock_code=identity[0],
                stock_name=open_row["stock_name"],
                strategy_type=identity[1],
                status="risk",
                signal_month=open_row["signal_month"],
                date=scan_date,
                report_period=open_row.get("report_period"),
                financial_ann_date=open_row.get("financial_ann_date"),
                technical_evidence=technical_evidence,
                financial_evidence=open_row.get("financial_evidence"),
                source_meta=risk_source_meta,
            )
        elif state.state == "reentry" and current_status == "risk":
            can_reenter = True
            if identity[1] == "fundamental_monthly_trend":
                can_reenter = bool(
                    finance_status == "success"
                    and financial_view
                    and financial_view.get("verified")
                )
                risk_origin = str(
                    ((open_row.get("source_meta") or {}).get("risk_from_status"))
                    or ""
                )
            elif identity[1] == "theme_monthly_attack":
                industry = str(
                    (industry_map.get(identity[0]) or {}).get("sw_l2") or ""
                )
                can_reenter = bool(
                    industry_status == "success"
                    and mainline_status in {"ok", "limited_history"}
                    and industry
                    and industry in main_sectors
                )
            if can_reenter:
                target_status = (
                    "fundamental_verified"
                    if (
                        identity[1] == "fundamental_monthly_trend"
                        and risk_origin != "active"
                    )
                    else "active"
                )
            else:
                target_status = "risk"
            if target_status == "fundamental_verified":
                technical_evidence["fundamental_verified_month"] = signal_month
            action = pool.record(
                conn,
                stock_code=identity[0],
                stock_name=open_row["stock_name"],
                strategy_type=identity[1],
                status=target_status,
                signal_month=signal_month,
                date=scan_date,
                report_period=report_period,
                financial_ann_date=financial_ann_date,
                technical_evidence=technical_evidence,
                financial_evidence=financial_evidence,
                source_meta=open_row.get("source_meta"),
            )
        else:
            keep_status = current_status
            if current_status == "risk":
                continue
            action = pool.record(
                conn,
                stock_code=identity[0],
                stock_name=open_row["stock_name"],
                strategy_type=identity[1],
                status=keep_status,
                signal_month=open_row["signal_month"],
                date=scan_date,
                report_period=open_row.get("report_period"),
                financial_ann_date=open_row.get("financial_ann_date"),
                technical_evidence=technical_evidence,
                financial_evidence=open_row.get("financial_evidence"),
                source_meta=open_row.get("source_meta"),
            )
        transition = _transition(
            stock_code=identity[0],
            strategy_type=identity[1],
            action=action,
            from_status=current_status,
            to_status=target_status,
            pool_state=state.state,
        )
        if transition:
            transitions.append(transition)
    return transitions


def _failed_summary(
    conn: sqlite3.Connection,
    *,
    scan_date: str,
    signal_month: str,
    input_by: str,
    source_status: dict,
    error: Exception,
    failure_key: str | None = None,
) -> dict:
    """回滚本轮状态写后，单独提交 failed 运行审计。"""
    conn.rollback()
    if failure_key is not None:
        source_status[failure_key] = "failed"
    summary = {
        "scan_date": scan_date,
        "signal_month": signal_month,
        "status": "failed",
        "source_status": source_status,
        "counts": {},
        "candidates": [],
        "transitions": [],
        "error": str(error),
    }
    repository.save_run(
        conn,
        scan_date=scan_date,
        signal_month=signal_month,
        status="failed",
        source_status=source_status,
        counts={},
        error=str(error),
        input_by=input_by,
    )
    conn.commit()
    return summary


def _scan_and_update_pool(
    conn: sqlite3.Connection,
    registry,
    scan_date: str,
    signal_month: str,
    series: dict[str, list[MonthlyBar]],
    expected_months: list[str],
    source_status: dict,
    *,
    include_financial: bool,
    input_by: str,
) -> dict:
    market_codes = {
        code
        for code, bars in series.items()
        if _has_required_month_suffix(
            bars,
            expected_months,
            required_months=1,
        )
    }
    finance_by_code, finance_status = _financial_views(
        conn,
        registry,
        scan_date,
        include_financial=include_financial,
        market_codes=market_codes,
    )
    source_status["financials"] = finance_status
    industry_map, main_sectors, mainline_meta, industry_status = _industry_context(
        conn,
        registry,
        scan_date,
        market_codes=market_codes,
    )
    source_status["industry_map"] = industry_status
    mainline_status = mainline_meta["status"]
    source_status["mainline"] = mainline_status
    mainline_reliable = (
        industry_status == "success"
        and mainline_status in {"ok", "limited_history"}
    )

    # 外部采集可能耗时；在任何 pool/run 写入前抢占写锁并复核状态水位，
    # 防止并发较新扫描在首次快速检查后推进水位，造成历史状态穿越。
    conn.execute("BEGIN IMMEDIATE")
    ensure_scan_not_before_pool_tail(conn, scan_date)

    candidates: list[dict] = []
    transitions: list[dict] = []
    matched_identities: set[tuple[str, str]] = set()
    for code, bars in series.items():
        for strategy in STRATEGIES:
            if not _has_required_month_suffix(
                bars,
                expected_months,
                required_months=_STRATEGY_REQUIRED_MONTHS[strategy],
            ):
                continue
            detection = detectors.detect_pattern(strategy, bars)
            if not detection.matched:
                continue
            industry_row = industry_map.get(code) or {}
            industry = str(industry_row.get("sw_l2") or "未分类")
            mainline_match = (
                industry in main_sectors
                if (
                    strategy == "theme_monthly_attack"
                    and industry != "未分类"
                    and mainline_reliable
                )
                else None
            )
            financial_view = finance_by_code.get(code)
            open_row = pool.get_open(conn, code, strategy)
            status = _resolve_candidate_status(
                strategy,
                open_row=open_row,
                financial_view=financial_view,
                mainline_match=mainline_match,
                industry_status=industry_status,
                mainline_status=mainline_status,
                signal_month=signal_month,
                expected_months=expected_months,
            )
            name = str(
                industry_row.get("name")
                or (open_row or {}).get("stock_name")
                or code
            )
            source_meta = {
                "monthly_bars": "tushare:monthly+adj_factor",
                "financials": finance_status,
                "industry_map": industry_status,
                "industry": industry,
                "mainline_match": mainline_match,
                "mainline": mainline_meta,
                "input_by": input_by,
            }
            source_meta = _source_meta_for_status(
                source_meta,
                open_row=open_row,
                target_status=status,
            )
            technical_evidence = detection.evidence
            if (
                strategy == "fundamental_monthly_trend"
                and status == "fundamental_verified"
            ):
                previous_verified_month = (
                    ((open_row or {}).get("technical_evidence") or {}).get(
                        "fundamental_verified_month"
                    )
                    or (open_row or {}).get("signal_month")
                )
                verified_month = (
                    signal_month
                    if financial_view and financial_view.get("verified")
                    else previous_verified_month
                )
                technical_evidence = {
                    **technical_evidence,
                    "fundamental_verified_month": verified_month,
                }
            (
                financial_evidence,
                report_period,
                financial_ann_date,
            ) = _financial_payload(open_row, financial_view, finance_status)
            action = pool.record(
                conn,
                stock_code=code,
                stock_name=name,
                strategy_type=strategy,
                status=status,
                signal_month=signal_month,
                date=scan_date,
                report_period=report_period,
                financial_ann_date=financial_ann_date,
                technical_evidence=technical_evidence,
                financial_evidence=financial_evidence,
                source_meta=source_meta,
            )
            matched_identities.add((code, strategy))
            candidate = {
                "stock_code": code,
                "stock_name": name,
                "strategy_type": strategy,
                "pool_status": status,
                "industry": industry,
                "mainline_match": mainline_match,
                "technical_evidence": technical_evidence,
                "financial_evidence": financial_evidence,
            }
            candidates.append(candidate)
            transition = _transition(
                stock_code=code,
                strategy_type=strategy,
                action=action,
                from_status=(open_row or {}).get("status"),
                to_status=status,
            )
            if transition:
                transitions.append(transition)

    transitions.extend(
        _maintain_open_pool(
            conn,
            series,
            matched_identities,
            scan_date=scan_date,
            signal_month=signal_month,
            expected_months=expected_months,
            finance_by_code=finance_by_code,
            finance_status=finance_status,
            industry_map=industry_map,
            main_sectors=main_sectors,
            industry_status=industry_status,
            mainline_status=mainline_status,
        )
    )
    open_pool_rows = [
        row
        for row in pool.list_pool(conn)
        if row.get("status") in pool.OPEN_STATUSES
    ]
    counts = {
        "market_stocks": len(market_codes),
        "matched_candidates": len(candidates),
        "matched_active": sum(
            item["pool_status"] == "active" for item in candidates
        ),
        "matched_fundamental_verified": sum(
            item["pool_status"] == "fundamental_verified" for item in candidates
        ),
        "matched_technical_candidate": sum(
            item["pool_status"] == "technical_candidate" for item in candidates
        ),
        "pool_active": sum(row["status"] == "active" for row in open_pool_rows),
        "pool_fundamental_verified": sum(
            row["status"] == "fundamental_verified" for row in open_pool_rows
        ),
        "pool_technical_candidate": sum(
            row["status"] == "technical_candidate" for row in open_pool_rows
        ),
        "pool_risk": sum(row["status"] == "risk" for row in open_pool_rows),
    }
    theme_in_scope = any(
        item["strategy_type"] == "theme_monthly_attack" for item in candidates
    ) or any(
        row["strategy_type"] == "theme_monthly_attack" for row in open_pool_rows
    )
    partial = (
        finance_status in {
            "source_failed",
            "source_ok_empty",
            "coverage_failed",
            "as_of_coverage_failed",
        }
        or industry_status in {"source_failed", "coverage_failed"}
        or (
            theme_in_scope
            and (
                mainline_meta["status"] == "missing"
                or industry_status == "not_as_of"
            )
        )
    )
    status = "partial" if partial else "complete"
    summary = {
        "scan_date": scan_date,
        "signal_month": signal_month,
        "status": status,
        "source_status": source_status,
        "counts": counts,
        "candidates": sorted(
            candidates,
            key=lambda item: (
                item["strategy_type"],
                item["stock_code"],
            ),
        ),
        "transitions": transitions,
        "error": None,
    }
    repository.save_run(
        conn,
        scan_date=scan_date,
        signal_month=signal_month,
        status=status,
        source_status=source_status,
        counts=counts,
        error=None,
        input_by=input_by,
    )
    conn.commit()
    return summary


def run_daily(
    conn: sqlite3.Connection,
    registry,
    scan_date: str,
    *,
    input_by: str,
    months: int = 48,
    include_financial: bool = True,
    min_market_rows: int = 4000,
    min_factor_coverage: float = 0.95,
    min_universe_coverage: float = 0.95,
) -> dict:
    """采集完成月事实、运行三种检测并原子更新观察池。"""
    if not isinstance(input_by, str) or not input_by.strip():
        raise ValueError("input_by 不能为空")
    input_by = input_by.strip()
    try:
        date_type.fromisoformat(scan_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("scan_date 必须为 YYYY-MM-DD") from exc
    if months < detectors.MACD_MIN_MONTHS:
        raise ValueError(f"months 至少为 {detectors.MACD_MIN_MONTHS}")
    ensure_scan_not_before_pool_tail(conn, scan_date)
    source_status: dict = {}
    signal_month = scan_date[:7]
    try:
        month_ends = _calendar_month_ends(registry, scan_date, months)
        signal_month = month_ends[-1][:7]
        source_status["calendar"] = "success"
        fetched, cached = _ensure_month_bars(
            conn,
            registry,
            month_ends,
            min_market_rows=min_market_rows,
            min_factor_coverage=min_factor_coverage,
            min_universe_coverage=min_universe_coverage,
        )
        source_status["monthly_bars"] = {
            "status": "success",
            "fetched": fetched,
            "cached": cached,
        }
        rows = repository.load_effective_month_bars(conn, month_ends)
        series = _stock_series(rows)
    except Exception as exc:
        source_status.setdefault("monthly_bars", "source_failed")
        return _failed_summary(
            conn,
            scan_date=scan_date,
            signal_month=signal_month,
            input_by=input_by,
            source_status=source_status,
            error=exc,
        )

    try:
        return _scan_and_update_pool(
            conn,
            registry,
            scan_date,
            signal_month,
            series,
            [month_end[:7] for month_end in month_ends],
            source_status,
            include_financial=include_financial,
            input_by=input_by,
        )
    except MonthlyPatternTemporalOrderError:
        conn.rollback()
        raise
    except Exception as exc:
        return _failed_summary(
            conn,
            scan_date=scan_date,
            signal_month=signal_month,
            input_by=input_by,
            source_status=source_status,
            error=exc,
            failure_key="scan",
        )
