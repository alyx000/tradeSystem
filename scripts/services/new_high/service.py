from __future__ import annotations

import datetime

from . import aggregator, collector, constants as C, detector, repo


_EXPECTED_TAIL_UNSET = object()


def _completed_record(record: dict) -> dict:
    return {**record, "status": "already_complete"}


def _failed_record(
    status: str,
    date: str,
    source: dict,
    *,
    market_count: int = 0,
) -> dict:
    return {
        "status": status,
        "date": date,
        "market_count": market_count,
        "new_high_count": 0,
        "sector_summary": [],
        "stocks": [],
        "source": dict(source),
    }


def _source_failed_record(date: str, source: dict) -> dict:
    return _failed_record("source_failed", date, source)


def _tail_failed_record(status: str, date: str, **detail) -> dict:
    return _failed_record(
        status,
        date,
        {
            "failed_source": "tail_guard",
            **detail,
        },
    )


def _tail_pair(conn) -> tuple[str | None, str | None]:
    return repo.get_latest_stats_date(conn), repo.get_latest_watermark_date(conn)


def _check_append_guard(conn, date: str, expected_tail) -> tuple[object, dict | None]:
    existing = repo.get_daily_stats(conn, date)
    if existing is not None:
        return expected_tail, _completed_record(existing)

    stats_end, watermark_end = _tail_pair(conn)
    if stats_end != watermark_end:
        return expected_tail, _tail_failed_record(
            "baseline_mismatch",
            date,
            stats_end=stats_end,
            watermark_end=watermark_end,
        )
    resolved_tail = stats_end if expected_tail is _EXPECTED_TAIL_UNSET else expected_tail
    if resolved_tail != stats_end:
        return resolved_tail, _tail_failed_record(
            "tail_changed",
            date,
            expected_tail=resolved_tail,
            stats_end=stats_end,
            watermark_end=watermark_end,
        )
    if stats_end is not None and date <= stats_end:
        return resolved_tail, _tail_failed_record(
            "historical_gap",
            date,
            expected_tail=resolved_tail,
            stats_end=stats_end,
        )
    return resolved_tail, None


def _detection_source(
    inputs: dict,
    detected: dict,
    *,
    top_n: int,
    previous_record: dict | None,
) -> tuple[dict, list[str]]:
    source = {
        **inputs["source"],
        "market_count": detected["market_count"],
        "valid_join_count": detected["market_count"],
        "skipped_count": detected["skipped_count"],
        "initialized_count": detected["initialized_count"],
        "top_n": top_n,
    }
    quote_unique_count = int(source.get("quote_unique_count") or 0)
    adj_unique_count = int(source.get("adj_factor_unique_count") or 0)
    adj_universe_coverage = (
        quote_unique_count / adj_unique_count if adj_unique_count else 0.0
    )
    industry_mapped_count = int(source.get("industry_mapped_count") or 0)
    industry_coverage = (
        industry_mapped_count / quote_unique_count if quote_unique_count else 0.0
    )
    source["adj_universe_coverage"] = adj_universe_coverage
    source["quote_adj_universe_coverage"] = adj_universe_coverage
    source["industry_coverage"] = industry_coverage
    source["minimum_market_count"] = C.MIN_MARKET_COUNT

    violations = []
    if int(source.get("duplicate_quote_count") or 0) != 0:
        violations.append("duplicate_quote_codes")
    if int(source.get("duplicate_adj_factor_count") or 0) != 0:
        violations.append("duplicate_adj_factor_codes")
    if int(source.get("invalid_quote_code_count") or 0) != 0:
        violations.append("invalid_quote_codes")
    if int(source.get("invalid_adj_factor_code_count") or 0) != 0:
        violations.append("invalid_adj_factor_codes")
    if detected["market_count"] != quote_unique_count:
        violations.append("incomplete_valid_join")
    if adj_universe_coverage < C.MIN_ADJ_UNIVERSE_COVERAGE:
        violations.append("adj_universe_coverage_below_minimum")
    if industry_coverage < C.MIN_INDUSTRY_COVERAGE:
        violations.append("industry_coverage_below_minimum")
    if detected["market_count"] < C.MIN_MARKET_COUNT:
        violations.append("market_count_below_absolute_minimum")

    if previous_record is not None:
        previous_market_count = int(previous_record.get("market_count") or 0)
        source["previous_market_count"] = previous_market_count
        if previous_market_count <= 0:
            source["previous_market_ratio"] = None
            violations.append("invalid_previous_market_count")
        else:
            ratio = detected["market_count"] / previous_market_count
            source["previous_market_ratio"] = ratio
            if ratio < C.MIN_PREVIOUS_MARKET_RATIO:
                violations.append("previous_market_ratio_below_minimum")
            if ratio > C.MAX_PREVIOUS_MARKET_RATIO:
                violations.append("previous_market_ratio_above_maximum")

    source["coverage_violations"] = violations
    return source, violations


def run_daily(
    conn,
    registry,
    date: str,
    *,
    persist: bool = True,
    top_n: int = 10,
    expected_tail_date: str | None | object = _EXPECTED_TAIL_UNSET,
) -> dict:
    """追加一个 canonical 交易日；已有日期不可原地重算。"""
    expected_tail = expected_tail_date
    if persist:
        expected_tail, guard_failure = _check_append_guard(
            conn,
            date,
            expected_tail,
        )
        if guard_failure is not None:
            return guard_failure

    inputs = collector.collect_daily_inputs(registry, date)
    if inputs.get("status") != "ok":
        return _source_failed_record(date, inputs)

    codes = [row["code"] for row in inputs["rows"] if row.get("code")]
    transaction_started = False
    try:
        previous_record = None
        if persist:
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True
            expected_tail, guard_failure = _check_append_guard(
                conn,
                date,
                expected_tail,
            )
            if guard_failure is not None:
                conn.rollback()
                transaction_started = False
                return guard_failure
            if expected_tail is not None:
                previous_record = repo.get_daily_stats(conn, expected_tail)

        watermarks = repo.get_watermarks(conn, codes)
        detected = detector.detect_new_highs(inputs["rows"], watermarks, date)
        source, coverage_violations = _detection_source(
            inputs,
            detected,
            top_n=top_n,
            previous_record=previous_record,
        )
        if coverage_violations:
            if transaction_started:
                conn.rollback()
                transaction_started = False
            source.update(
                {
                    "failed_source": "coverage_guard",
                    "error": "全市场数据完整性门禁未通过",
                }
            )
            return _failed_record(
                "coverage_failed",
                date,
                source,
                market_count=detected["market_count"],
            )

        sectors = aggregator.aggregate_by_sector(detected["new_highs"])
        record = {
            "status": "ok",
            "date": date,
            "market_count": detected["market_count"],
            "new_high_count": len(detected["new_highs"]),
            "sector_summary": sectors,
            "stocks": detected["new_highs"],
            "source": source,
        }

        if not persist:
            return record

        repo.upsert_watermarks(conn, detected["watermark_updates"])
        repo.save_daily_stats(conn, record)
        conn.commit()
        transaction_started = False
        return record
    except Exception:
        if transaction_started:
            conn.rollback()
        raise


def _due_summary(
    status: str,
    *,
    processed_dates: list[str] | None = None,
    failed_date: str | None = None,
    target_record: dict | None = None,
    failure_detail: dict | None = None,
) -> dict:
    return {
        "status": status,
        "processed_dates": list(processed_dates or []),
        "failed_date": failed_date,
        "target_record": target_record,
        "failure_detail": failure_detail,
    }


def _natural_dates(start_exclusive: str, end_inclusive: str) -> list[str]:
    current = datetime.date.fromisoformat(start_exclusive) + datetime.timedelta(days=1)
    end = datetime.date.fromisoformat(end_inclusive)
    dates = []
    while current <= end:
        dates.append(current.isoformat())
        current += datetime.timedelta(days=1)
    return dates


def _calendar_span(conn, start_exclusive: str, end_inclusive: str):
    rows = repo.get_trade_calendar_rows(conn, start_exclusive, end_inclusive)
    calendar_by_date = {row["date"]: bool(row["is_open"]) for row in rows}
    expected_dates = _natural_dates(start_exclusive, end_inclusive)
    missing_date = next(
        (date for date in expected_dates if date not in calendar_by_date),
        None,
    )
    return expected_dates, calendar_by_date, missing_date


def run_due_dates(conn, registry, target_date: str, *, top_n: int = 10) -> dict:
    """从健康尾日动态规划并连续追加到目标开放日。"""
    missing_tables = repo.get_missing_required_tables(conn)
    if missing_tables:
        return _due_summary(
            "schema_missing",
            failed_date=target_date,
            failure_detail={"missing_tables": missing_tables},
        )

    processed_dates = []
    while True:
        stats_end, watermark_end = _tail_pair(conn)
        if not stats_end or not watermark_end:
            return _due_summary(
                "baseline_missing",
                processed_dates=processed_dates,
                failed_date=target_date,
                failure_detail={
                    "stats_end": stats_end,
                    "watermark_end": watermark_end,
                },
            )
        if stats_end != watermark_end:
            return _due_summary(
                "baseline_mismatch",
                processed_dates=processed_dates,
                failed_date=target_date,
                failure_detail={
                    "stats_end": stats_end,
                    "watermark_end": watermark_end,
                },
            )
        if target_date <= stats_end:
            target_record = repo.get_daily_stats(conn, target_date)
            if target_record is None:
                return _due_summary(
                    "historical_gap",
                    processed_dates=processed_dates,
                    failed_date=target_date,
                    failure_detail={"target_date": target_date, "stats_end": stats_end},
                )
            return _due_summary(
                "ok" if processed_dates else "already_complete",
                processed_dates=processed_dates,
                target_record=target_record,
            )

        expected_dates, calendar_by_date, missing_date = _calendar_span(
            conn,
            stats_end,
            target_date,
        )
        if missing_date is not None:
            return _due_summary(
                "calendar_missing",
                processed_dates=processed_dates,
                failed_date=missing_date,
                failure_detail={"missing_date": missing_date},
            )
        next_open_date = next(
            (date for date in expected_dates if calendar_by_date[date]),
            None,
        )
        if next_open_date is None:
            return _due_summary(
                "non_trading_day",
                processed_dates=processed_dates,
                failed_date=target_date,
                failure_detail={"target_date": target_date},
            )

        record = run_daily(
            conn,
            registry,
            next_open_date,
            persist=True,
            top_n=top_n,
            expected_tail_date=stats_end,
        )
        record_status = record.get("status")
        if record_status == "ok":
            processed_dates.append(next_open_date)
            continue
        if record_status in {"already_complete", "tail_changed"}:
            continue
        return _due_summary(
            record_status or "source_failed",
            processed_dates=processed_dates,
            failed_date=next_open_date,
            failure_detail=record.get("source"),
        )


def run_trend(conn, date: str, *, days: int = 30) -> list[dict]:
    return repo.get_recent_stats(conn, date, days)


def _backfill_summary(
    *,
    processed: list[str] | None = None,
    already_complete: list[str] | None = None,
    closed_dates: list[str] | None = None,
    terminal_failure: dict | None = None,
) -> dict:
    processed = list(processed or [])
    already_complete = list(already_complete or [])
    closed_dates = list(closed_dates or [])
    skipped = [terminal_failure] if terminal_failure else []
    return {
        "status": terminal_failure["status"] if terminal_failure else "ok",
        "failed_date": terminal_failure["date"] if terminal_failure else None,
        "failure_detail": terminal_failure["source"] if terminal_failure else None,
        "processed_count": len(processed),
        "already_complete_count": len(already_complete),
        "closed_count": len(closed_dates),
        "skipped_count": len(skipped),
        "processed_dates": processed,
        "already_complete_dates": already_complete,
        "closed_dates": closed_dates,
        "skipped": skipped,
    }


def run_backfill(
    conn,
    registry,
    dates: list[str],
    *,
    top_n: int = 10,
) -> dict:
    ordered_dates = sorted(dates)
    stats_end, watermark_end = _tail_pair(conn)
    if stats_end != watermark_end:
        return _backfill_summary(
            terminal_failure={
                "date": ordered_dates[0] if ordered_dates else None,
                "status": "baseline_mismatch",
                "source": {
                    "stats_end": stats_end,
                    "watermark_end": watermark_end,
                },
            }
        )
    if not ordered_dates:
        return _backfill_summary()

    day_before_start = (
        datetime.date.fromisoformat(ordered_dates[0]) - datetime.timedelta(days=1)
    ).isoformat()
    continuity_start = None
    if stats_end is None:
        continuity_start = day_before_start
    elif ordered_dates[-1] > stats_end:
        continuity_start = stats_end

    if continuity_start is not None:
        suffix_dates, suffix_calendar, missing_suffix_date = _calendar_span(
            conn,
            continuity_start,
            ordered_dates[-1],
        )
        if missing_suffix_date is not None:
            return _backfill_summary(
                terminal_failure={
                    "date": missing_suffix_date,
                    "status": "calendar_missing",
                    "source": {"missing_date": missing_suffix_date},
                }
            )
        requested_dates = set(ordered_dates)
        skipped_open_date = next(
            (
                date
                for date in suffix_dates
                if suffix_calendar[date] and date not in requested_dates
            ),
            None,
        )
        if skipped_open_date is not None:
            return _backfill_summary(
                terminal_failure={
                    "date": skipped_open_date,
                    "status": "historical_gap",
                    "source": {
                        "failed_source": "tail_guard",
                        "stats_end": stats_end,
                        "missing_open_date": skipped_open_date,
                    },
                }
            )

    calendar_by_date = {
        row["date"]: bool(row["is_open"])
        for row in repo.get_trade_calendar_rows(
            conn,
            day_before_start,
            ordered_dates[-1],
        )
    }
    missing_date = next(
        (date for date in ordered_dates if date not in calendar_by_date),
        None,
    )
    if missing_date is not None:
        return _backfill_summary(
            terminal_failure={
                "date": missing_date,
                "status": "calendar_missing",
                "source": {"missing_date": missing_date},
            }
        )

    processed = []
    already_complete = []
    closed_dates = []
    expected_tail = stats_end
    terminal_failure = None
    for date in ordered_dates:
        if not calendar_by_date[date]:
            closed_dates.append(date)
            continue

        record = run_daily(
            conn,
            registry,
            date,
            persist=True,
            top_n=top_n,
            expected_tail_date=expected_tail,
        )
        status = record.get("status")
        if status == "ok":
            processed.append(date)
            expected_tail = date
            continue
        if status == "already_complete":
            already_complete.append(date)
            continue

        terminal_failure = {
            "date": date,
            "status": status or "source_failed",
            "source": record.get("source"),
        }
        break

    return _backfill_summary(
        processed=processed,
        already_complete=already_complete,
        closed_dates=closed_dates,
        terminal_failure=terminal_failure,
    )
