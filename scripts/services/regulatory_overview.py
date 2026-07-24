"""盘后监管异动总览：事实源归档之上的审计型派生层。

`stk_alert` / `stk_shock` / `stk_high_shock` 属于交易所事实整理；
偏离值、触发空间与理论价格只属于程序计算，不代表交易建议。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import json
import logging
import re
from typing import Any, Iterable

from db import queries as Q
from db.connection import get_db
from db.migrate import migrate

logger = logging.getLogger(__name__)

CALCULATION_POLICY_VERSION = "regulatory-v2"
OVERVIEW_FACT_TYPE = "regulatory_anomaly_overview"
OPTIONAL_CANDIDATE_CAP = 300
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm_date(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 8:
        return None
    value = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _as_float(raw: Any) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _pct_map(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        trade_date = _norm_date(row.get("trade_date") or row.get("trade_date_norm"))
        pct = _as_float(row.get("pct_chg"))
        if trade_date and pct is not None:
            out[trade_date] = pct
    return out


def _close_map(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        trade_date = _norm_date(row.get("trade_date") or row.get("trade_date_norm"))
        close = _as_float(row.get("close"))
        if trade_date and close is not None:
            out[trade_date] = close
    return out


def _pre_close_map(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        trade_date = _norm_date(row.get("trade_date") or row.get("trade_date_norm"))
        pre_close = _as_float(row.get("pre_close"))
        if trade_date and pre_close is not None:
            out[trade_date] = pre_close
    return out


def _regulatory_stock_pct_map(
    rows: Iterable[dict[str, Any]],
    limit_pct: float,
) -> dict[str, float]:
    """涨跌停收盘按法定限制比例计入偏离值，其他日期保留行情涨跌幅。"""
    materialized = [dict(row) for row in rows]
    pct_by_date = _pct_map(materialized)
    close_by_date = _close_map(materialized)
    pre_close_by_date: dict[str, float] = {}
    for row in materialized:
        trade_date = _norm_date(row.get("trade_date") or row.get("trade_date_norm"))
        pre_close = _as_float(row.get("pre_close"))
        if trade_date and pre_close is not None:
            pre_close_by_date[trade_date] = pre_close

    ordered_dates = sorted(close_by_date)
    for index, trade_date in enumerate(ordered_dates):
        previous_close = pre_close_by_date.get(trade_date)
        if previous_close is None and index > 0:
            previous_close = close_by_date[ordered_dates[index - 1]]
        if previous_close is None or previous_close <= 0:
            continue
        close = close_by_date[trade_date]
        up_limit = _price_limit(previous_close, limit_pct, "up")
        down_limit = _price_limit(previous_close, limit_pct, "down")
        if abs(close - up_limit) < 0.005:
            pct_by_date[trade_date] = limit_pct
        elif abs(close - down_limit) < 0.005:
            pct_by_date[trade_date] = -limit_pct
    return pct_by_date


def calculate_cumulative_deviation(
    stock_pct_by_date: dict[str, float],
    index_pct_by_date: dict[str, float],
    dates: Iterable[str],
) -> float | None:
    """按交易所口径累加逐日涨跌幅偏离值；任一交易日缺失即返回 None。"""
    window = list(dates)
    if not window:
        return None
    if any(
        trade_date not in stock_pct_by_date or trade_date not in index_pct_by_date
        for trade_date in window
    ):
        return None
    return sum(
        stock_pct_by_date[trade_date] - index_pct_by_date[trade_date]
        for trade_date in window
    )


def _compounded_return(
    pct_by_date: dict[str, float],
    dates: Iterable[str],
) -> float | None:
    value = 1.0
    seen = False
    for trade_date in dates:
        if trade_date not in pct_by_date:
            return None
        value *= 1.0 + pct_by_date[trade_date] / 100.0
        seen = True
    return value - 1.0 if seen else None


def calculate_period_return_deviation(
    stock_pct_by_date: dict[str, float],
    index_pct_by_date: dict[str, float],
    dates: Iterable[str],
) -> float | None:
    """上交所主板口径：区间个股收益率减区间对应指数收益率。"""
    window = list(dates)
    stock_return = _compounded_return(stock_pct_by_date, window)
    index_return = _compounded_return(index_pct_by_date, window)
    if stock_return is None or index_return is None:
        return None
    return (stock_return - index_return) * 100.0


def calculate_regulatory_deviation(
    stock_pct_by_date: dict[str, float],
    index_pct_by_date: dict[str, float],
    dates: Iterable[str],
    formula: str,
) -> float | None:
    if formula == "period_return_difference":
        return calculate_period_return_deviation(
            stock_pct_by_date,
            index_pct_by_date,
            dates,
        )
    return calculate_cumulative_deviation(
        stock_pct_by_date,
        index_pct_by_date,
        dates,
    )


def benchmark_for_code(ts_code: str) -> str | None:
    code = str(ts_code or "").strip().upper()
    if "." in code:
        bare, suffix = code.split(".", 1)
    else:
        bare, suffix = code, ""
    if not re.fullmatch(r"\d{6}", bare):
        return None

    inferred_suffix: str | None
    if bare.startswith(("688", "689")):
        inferred_suffix = "SH"
        benchmark = "000688.SH"
    elif bare.startswith(("600", "601", "603", "605")):
        inferred_suffix = "SH"
        benchmark = "000001.SH"
    elif bare.startswith(("300", "301")):
        inferred_suffix = "SZ"
        benchmark = "399102.SZ"
    elif bare.startswith(("000", "001", "002", "003")):
        inferred_suffix = "SZ"
        benchmark = "399107.SZ"
    else:
        return None
    if suffix and suffix != inferred_suffix:
        return None
    if suffix not in {"", "SH", "SZ"}:
        return None
    return benchmark


def _canonical_ts_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    if not code:
        return ""
    bare = code.split(".", 1)[0]
    benchmark = benchmark_for_code(code)
    if benchmark is None:
        return code
    suffix = "SH" if benchmark.endswith(".SH") else "SZ"
    return f"{bare}.{suffix}"


def _board_rules(ts_code: str) -> dict[str, Any] | None:
    benchmark = benchmark_for_code(ts_code)
    if benchmark is None:
        return None
    code = ts_code.upper().split(".", 1)[0]
    growth = code.startswith(("300", "301", "688", "689"))
    return {
        "benchmark": benchmark,
        "board": (
            "科创板"
            if code.startswith(("688", "689"))
            else "创业板"
            if growth
            else "沪市主板"
            if ts_code.upper().endswith(".SH")
            else "深市主板"
        ),
        "abnormal_threshold_up": 30.0 if growth else 20.0,
        "abnormal_threshold_down": -30.0 if growth else -20.0,
        "same_direction_count": 3 if growth else 4,
        "severe_10_up": 100.0,
        "severe_10_down": -50.0,
        "severe_30_up": 200.0,
        "severe_30_down": -70.0,
        "limit_pct": 20.0 if growth else 10.0,
        "deviation_formula": (
            "period_return_difference"
            if benchmark == "000001.SH"
            else "daily_deviation_sum"
        ),
    }


def _event_direction(row: dict[str, Any]) -> str:
    explicit = str(row.get("direction") or "").strip().lower()
    if explicit in {"up", "down"}:
        return explicit
    reason = str(row.get("reason") or "")
    if any(token in reason for token in ("下跌", "跌幅", "负向")):
        return "down"
    if any(token in reason for token in ("上涨", "涨幅", "正向")):
        return "up"
    return "unknown"


def dedupe_same_direction_events(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """只按交易所披露事件计数，禁止用重叠滑窗虚构独立异动事件。"""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        code = _canonical_ts_code(row.get("ts_code") or row.get("code"))
        trade_date = _norm_date(row.get("trade_date_norm") or row.get("trade_date"))
        direction = _event_direction(row)
        if not code or not trade_date:
            continue
        key = (code, trade_date, direction)
        if key in seen:
            continue
        seen.add(key)
        row["code"] = code
        row["trade_date_norm"] = trade_date
        row["direction"] = direction
        out.append(row)
    out.sort(key=lambda item: (item["trade_date_norm"], item["code"], item["direction"]))
    return out


def _payload_rows(payload_json: str | None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _display_name(code: str, rows: Iterable[dict[str, Any]]) -> str:
    for row in rows:
        row_code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if row_code == code and name and name.upper() not in {code, code.split(".", 1)[0]}:
            return name
    return code


def _required_next_move(
    stock_pct: dict[str, float],
    index_pct: dict[str, float],
    historical_dates: list[str],
    threshold_pct: float,
    *,
    assumed_index_pct: float = 0.0,
    formula: str = "daily_deviation_sum",
) -> float | None:
    if formula == "period_return_difference":
        stock_return = _compounded_return(stock_pct, historical_dates)
        index_return = _compounded_return(index_pct, historical_dates)
        if stock_return is None or index_return is None or 1.0 + stock_return <= 0:
            return None
        target_index_return = (
            (1.0 + index_return) * (1.0 + assumed_index_pct / 100.0) - 1.0
        )
        target_stock_return = target_index_return + threshold_pct / 100.0
        return ((1.0 + target_stock_return) / (1.0 + stock_return) - 1.0) * 100.0
    historical_deviation = calculate_cumulative_deviation(
        stock_pct,
        index_pct,
        historical_dates,
    )
    if historical_deviation is None:
        return None
    return threshold_pct - historical_deviation + assumed_index_pct


def _target_price(current_close: float, required_pct: float) -> float:
    raw = Decimal(str(current_close)) * (Decimal("1") + Decimal(str(required_pct)) / Decimal("100"))
    rounding = ROUND_CEILING if required_pct >= 0 else ROUND_FLOOR
    return float(raw.quantize(Decimal("0.01"), rounding=rounding))


def _price_limit(current_close: float, limit_pct: float, direction: str) -> float:
    multiplier = (
        Decimal("1") + Decimal(str(limit_pct)) / Decimal("100")
        if direction == "up"
        else Decimal("1") - Decimal(str(limit_pct)) / Decimal("100")
    )
    return float(
        (Decimal(str(current_close)) * multiplier).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def _reachable_at_price_limit(
    current_close: float,
    target_price: float,
    limit_pct: float,
    direction: str,
) -> tuple[bool, float]:
    limit_price = _price_limit(current_close, limit_pct, direction)
    reachable = (
        target_price <= limit_price
        if direction == "up"
        else target_price >= limit_price
    )
    return reachable, limit_price


class RegulatoryOverviewService:
    calculation_policy_version = CALCULATION_POLICY_VERSION

    def __init__(self, registry: Any | None = None, db_path: str | None = None):
        self.registry = registry
        self.db_path = db_path

    def _read_source(
        self,
        conn: Any,
        interface_name: str,
        target_date: str,
    ) -> dict[str, Any]:
        current = conn.execute(
            """
            SELECT interface_name, provider, biz_date, status, row_count,
                   payload_json, source_meta_json, inserted_at
            FROM raw_interface_payloads
            WHERE interface_name = ? AND biz_date = ?
            ORDER BY inserted_at DESC
            LIMIT 1
            """,
            (interface_name, target_date),
        ).fetchone()
        latest_run = conn.execute(
            """
            SELECT status, row_count, notes, finished_at
            FROM ingest_runs
            WHERE interface_name = ? AND biz_date = ?
            ORDER BY started_at DESC, rowid DESC
            LIMIT 1
            """,
            (interface_name, target_date),
        ).fetchone()

        selected = current
        if selected is None:
            selected = conn.execute(
                """
                SELECT interface_name, provider, biz_date, status, row_count,
                       payload_json, source_meta_json, inserted_at
                FROM raw_interface_payloads
                WHERE interface_name = ? AND biz_date < ? AND status IN ('success', 'partial')
                ORDER BY biz_date DESC, inserted_at DESC
                LIMIT 1
                """,
                (interface_name, target_date),
            ).fetchone()

        selected_dict = dict(selected) if selected is not None else {}
        run_dict = dict(latest_run) if latest_run is not None else {}
        rows = _payload_rows(selected_dict.get("payload_json"))
        raw_status = str(selected_dict.get("status") or "")
        run_status = str(run_dict.get("status") or "")
        selected_date = str(selected_dict.get("biz_date") or "")

        if run_status == "failed":
            status = "stale" if rows else "failed"
        elif run_status == "empty" and rows:
            status = "late"
        elif selected_date and selected_date < target_date:
            status = "stale"
        elif raw_status == "empty" or (run_status == "empty" and not rows):
            status = "empty"
        elif raw_status == "partial" or run_status == "partial":
            status = "partial"
        elif rows or raw_status == "success":
            status = "success"
        else:
            status = "failed"

        source_meta = _json_object(selected_dict.get("source_meta_json"))
        return {
            "interface": interface_name,
            "provider": selected_dict.get("provider"),
            "status": status,
            "snapshot_date": selected_date or None,
            "row_count": len(rows),
            "rows": rows,
            "query": source_meta.get("query") if isinstance(source_meta.get("query"), dict) else {},
            "error": run_dict.get("notes") if run_status == "failed" else None,
            "usable": bool(rows) or status == "empty",
        }

    def _load_open_dates(self, conn: Any, target_date: str) -> list[str]:
        start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=70)).date().isoformat()
        rows = conn.execute(
            """
            SELECT date
            FROM trade_calendar
            WHERE date BETWEEN ? AND ? AND is_open = 1
            ORDER BY date
            """,
            (start, target_date),
        ).fetchall()
        dates = [str(row["date"] if hasattr(row, "keys") else row[0]) for row in rows]
        if len(dates) >= 40 or self.registry is None:
            return dates[-40:]
        calendar_dates = [target_date]
        if start[:4] != target_date[:4]:
            calendar_dates.append(f"{int(target_date[:4]) - 1}-12-31")
        for calendar_date in calendar_dates:
            calendar = self.registry.call("get_trade_calendar", calendar_date)
            if not calendar.success or not isinstance(calendar.data, list):
                continue
            for row in calendar.data:
                if int(row.get("is_open", 0) or 0) != 1:
                    continue
                value = _norm_date(row.get("cal_date") or row.get("trade_date"))
                if value and start <= value <= target_date:
                    dates.append(value)
        return sorted(set(dates))[-40:]

    def _effective_date(
        self,
        conn: Any,
        target_date: str,
        alert_state: dict[str, Any],
    ) -> tuple[str, str]:
        query = alert_state.get("query") or {}
        candidate = str(query.get("effective_trading_date") or "")
        if (
            alert_state.get("snapshot_date") == target_date
            and query.get("snapshot_date") == target_date
            and _DATE_RE.match(candidate)
        ):
            return candidate, "complete"
        next_date = Q.get_next_trade_date(conn, target_date)
        if next_date:
            return next_date, "complete"
        return target_date, "missing_next_open"

    def _build_monitoring(
        self,
        rows: list[dict[str, Any]],
        effective_date: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        current: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        for row in rows:
            code = _canonical_ts_code(row.get("ts_code") or row.get("code"))
            start = _norm_date(row.get("start_date_norm") or row.get("start_date"))
            end = _norm_date(row.get("end_date_norm") or row.get("end_date"))
            if not code or not start or not end:
                continue
            item = {
                "ts_code": code,
                "code": code,
                "name": str(row.get("name") or code),
                "monitor_start": start,
                "monitor_end": end,
                "alert_type": str(row.get("type") or row.get("alert_type") or ""),
                "source": "tushare:stk_alert",
                "evidence_type": "fact",
                "fact_level": "[事实]",
            }
            (current if start <= effective_date <= end else history).append(item)
        current.sort(key=lambda item: (item["monitor_start"], item["code"]), reverse=True)
        history.sort(key=lambda item: (item["monitor_end"], item["code"]), reverse=True)
        return current, history

    def _recent_rows(
        self,
        rows: list[dict[str, Any]],
        start_date: str,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            trade_date = _norm_date(row.get("trade_date_norm") or row.get("trade_date"))
            if trade_date and trade_date >= start_date:
                item = dict(row)
                item["trade_date_norm"] = trade_date
                out.append(item)
        return out

    def _calculate_candidates(
        self,
        *,
        target_date: str,
        open_dates: list[str],
        alert_rows: list[dict[str, Any]],
        shock_rows: list[dict[str, Any]],
        high_rows: list[dict[str, Any]],
        limit_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.registry is None or len(open_dates) < 3:
            return {
                "status": "missing_calendar" if len(open_dates) < 3 else "missing_registry",
                "hot": [],
                "today": [],
                "next_day": [],
                "legacy": [],
                "missing_codes": [],
                "missing_market_dates": [],
                "candidate_count": 0,
                "optional_truncated": False,
                "reset_boundaries": [],
                "excluded_no_limit_dates": [],
            }

        recent_start = open_dates[-30] if len(open_dates) >= 30 else open_dates[0]
        recent_shocks = self._recent_rows(shock_rows, recent_start)
        recent_highs = self._recent_rows(high_rows, recent_start)
        all_source_rows = alert_rows + recent_shocks + recent_highs + limit_rows

        mandatory = {
            _canonical_ts_code(row.get("ts_code") or row.get("code"))
            for row in alert_rows + recent_highs
        }
        mandatory.discard("")
        optional_rank: dict[str, tuple[int, int, str]] = {}
        for priority, rows in ((0, limit_rows), (1, recent_shocks)):
            for row in rows:
                code = _canonical_ts_code(row.get("ts_code") or row.get("code"))
                if not code or code in mandatory:
                    continue
                event_date = _norm_date(
                    row.get("trade_date_norm") or row.get("trade_date")
                ) or target_date
                rank = (
                    priority,
                    -int(event_date.replace("-", "")),
                    code,
                )
                previous_rank = optional_rank.get(code)
                if previous_rank is None or rank < previous_rank:
                    optional_rank[code] = rank
        optional_unique = [
            code
            for code, _rank in sorted(
                optional_rank.items(),
                key=lambda item: item[1],
            )
        ]
        optional_truncated = len(optional_unique) > OPTIONAL_CANDIDATE_CAP
        candidate_codes = sorted(mandatory) + optional_unique[:OPTIONAL_CANDIDATE_CAP]

        if not candidate_codes:
            return {
                "status": "complete",
                "hot": [],
                "today": [],
                "next_day": [],
                "legacy": [],
                "missing_codes": [],
                "missing_market_dates": [],
                "candidate_count": 0,
                "optional_truncated": optional_truncated,
                "reset_boundaries": [],
                "excluded_no_limit_dates": [],
            }

        st_result = self.registry.call("get_stock_st", target_date)
        if not st_result.success or not isinstance(st_result.data, list):
            return {
                "status": "missing_st_status",
                "hot": [],
                "today": [],
                "next_day": [],
                "legacy": [],
                "missing_codes": candidate_codes,
                "missing_market_dates": [],
                "candidate_count": len(candidate_codes),
                "optional_truncated": optional_truncated,
                "reset_boundaries": [],
                "excluded_no_limit_dates": [],
            }
        st_codes = {
            _canonical_ts_code(row.get("ts_code") or row.get("code"))
            for row in st_result.data or []
            if isinstance(row, dict)
        }
        basic_result = self.registry.call("get_stock_basic_batch", candidate_codes)
        if not basic_result.success or not isinstance(basic_result.data, list):
            return {
                "status": "missing_stock_basic",
                "hot": [],
                "today": [],
                "next_day": [],
                "legacy": [],
                "missing_codes": candidate_codes,
                "missing_market_dates": [],
                "candidate_count": len(candidate_codes),
                "optional_truncated": optional_truncated,
                "reset_boundaries": [],
                "excluded_no_limit_dates": [],
            }
        listing_dates = {
            _canonical_ts_code(row.get("ts_code") or row.get("code")): _norm_date(
                row.get("list_date")
            )
            for row in basic_result.data
            if isinstance(row, dict)
        }

        index_cache: dict[str, dict[str, float]] = {}
        hot: list[dict[str, Any]] = []
        today: list[dict[str, Any]] = []
        next_day: list[dict[str, Any]] = []
        legacy_by_code: dict[str, dict[str, Any]] = {}
        missing_codes: list[str] = []
        missing_market_dates: list[dict[str, Any]] = []
        reset_boundaries: list[dict[str, Any]] = []
        excluded_no_limit_dates: list[dict[str, Any]] = []

        deduped_events = dedupe_same_direction_events(recent_shocks)
        latest_high_by_code: dict[str, str] = {}
        for row in high_rows:
            code = _canonical_ts_code(row.get("ts_code") or row.get("code"))
            trade_date = _norm_date(row.get("trade_date_norm") or row.get("trade_date"))
            if (
                code
                and trade_date
                and trade_date <= target_date
                and trade_date > latest_high_by_code.get(code, "")
            ):
                latest_high_by_code[code] = trade_date

        for code in candidate_codes:
            rules = _board_rules(code)
            if rules is None or code in st_codes:
                continue
            list_date = listing_dates.get(code)
            if not list_date:
                missing_codes.append(code)
                continue
            ipo_no_limit_dates = (
                [
                    trade_date
                    for trade_date in open_dates
                    if trade_date >= list_date
                ][:5]
                if list_date >= open_dates[0]
                else []
            )
            reset_event_date = latest_high_by_code.get(code)
            reset_start_date: str | None = None
            calculation_dates = open_dates
            next_calculation_dates = open_dates
            if reset_event_date and reset_event_date >= open_dates[0]:
                reset_start_date = next(
                    (trade_date for trade_date in open_dates if trade_date > reset_event_date),
                    None,
                )
                post_reset_dates = (
                    [
                        trade_date
                        for trade_date in open_dates
                        if reset_start_date and trade_date >= reset_start_date
                    ]
                    if reset_start_date
                    else []
                )
                next_calculation_dates = post_reset_dates
                if reset_event_date < target_date:
                    calculation_dates = post_reset_dates
                reset_boundaries.append({
                    "code": code,
                    "confirmed_high_shock_date": reset_event_date,
                    "recalculation_start_date": reset_start_date,
                    "rule": "next_open_after_confirmed_high_shock",
                })
            elif reset_event_date:
                reset_boundaries.append({
                    "code": code,
                    "confirmed_high_shock_date": reset_event_date,
                    "recalculation_start_date": None,
                    "rule": "reset_precedes_loaded_calculation_window",
                })
            if not calculation_dates or target_date not in calculation_dates:
                continue
            stock_result = self.registry.call(
                "get_stock_daily_range",
                code,
                open_dates[0],
                target_date,
            )
            if not stock_result.success or not isinstance(stock_result.data, list):
                missing_codes.append(code)
                continue
            raw_stock_pct = _pct_map(stock_result.data)
            stock_pct = (
                raw_stock_pct
                if rules["deviation_formula"] == "period_return_difference"
                else _regulatory_stock_pct_map(
                    stock_result.data,
                    rules["limit_pct"],
                )
            )
            closes = _close_map(stock_result.data)
            pre_closes = _pre_close_map(stock_result.data)
            observed_no_limit_dates = [
                trade_date
                for trade_date in calculation_dates
                if abs(raw_stock_pct.get(trade_date, 0.0)) > rules["limit_pct"] + 0.5
            ]
            no_limit_dates = sorted(
                set(ipo_no_limit_dates) | set(observed_no_limit_dates)
            )
            calculation_dates = [
                trade_date
                for trade_date in calculation_dates
                if trade_date >= list_date and trade_date not in no_limit_dates
            ]
            next_calculation_dates = [
                trade_date
                for trade_date in next_calculation_dates
                if trade_date >= list_date and trade_date not in no_limit_dates
            ]
            if no_limit_dates:
                excluded_no_limit_dates.append({
                    "code": code,
                    "list_date": list_date,
                    "dates": no_limit_dates,
                    "rule": "ipo_first_five_or_observed_beyond_price_limit",
                })
            if not calculation_dates or target_date not in calculation_dates:
                continue
            benchmark = rules["benchmark"]
            if benchmark not in index_cache:
                index_result = self.registry.call(
                    "get_index_daily_range",
                    benchmark,
                    open_dates[0],
                    target_date,
                )
                index_cache[benchmark] = (
                    _pct_map(index_result.data)
                    if index_result.success and isinstance(index_result.data, list)
                    else {}
                )
            index_pct = index_cache[benchmark]
            if target_date not in closes or not index_pct:
                missing_codes.append(code)
                continue
            required_dates = (
                calculation_dates[-30:]
                if len(calculation_dates) >= 30
                else calculation_dates
            )
            missing_dates = [
                trade_date
                for trade_date in required_dates
                if trade_date not in stock_pct or trade_date not in index_pct
            ]
            if missing_dates:
                missing_codes.append(code)
                missing_market_dates.append({
                    "code": code,
                    "benchmark_code": benchmark,
                    "dates": missing_dates,
                })
                continue

            name = _display_name(code, all_source_rows)
            best_hot: tuple[float, int] | None = None
            for window_days in range(3, min(10, len(calculation_dates)) + 1):
                deviation = calculate_regulatory_deviation(
                    stock_pct,
                    index_pct,
                    calculation_dates[-window_days:],
                    rules["deviation_formula"],
                )
                if deviation is not None and deviation > 0 and (
                    best_hot is None or deviation > best_hot[0]
                ):
                    best_hot = (deviation, window_days)
            if best_hot is not None:
                hot_item = {
                    "ts_code": code,
                    "code": code,
                    "name": name,
                    "board": rules["board"],
                    "benchmark_code": benchmark,
                    "benchmark": benchmark,
                    "deviation_formula": rules["deviation_formula"],
                    "window_days": best_hot[1],
                    "deviation_pct": round(best_hot[0], 4),
                    "current_close": closes[target_date],
                    "data_status": "complete",
                    "evidence_type": "calculation",
                    "fact_level": "[计算]",
                }
                hot.append(hot_item)

            code_events = [
                event
                for event in deduped_events
                if event["code"] == code and event["direction"] != "unknown"
            ]

            def _event_count(direction: str, dates: list[str]) -> int:
                date_set = set(dates)
                return sum(
                    1
                    for event in code_events
                    if event["direction"] == direction
                    and event["trade_date_norm"] in date_set
                )

            criteria: list[tuple[int, str, float, float, str]] = []
            if len(calculation_dates) >= 10:
                dev10 = calculate_regulatory_deviation(
                    stock_pct,
                    index_pct,
                    calculation_dates[-10:],
                    rules["deviation_formula"],
                )
                if dev10 is not None and dev10 >= rules["severe_10_up"]:
                    criteria.append((2, "deviation_10d", dev10, rules["severe_10_up"], "up"))
                if dev10 is not None and dev10 <= rules["severe_10_down"]:
                    criteria.append((2, "deviation_10d", dev10, rules["severe_10_down"], "down"))
            if len(calculation_dates) >= 30:
                dev30 = calculate_regulatory_deviation(
                    stock_pct,
                    index_pct,
                    calculation_dates[-30:],
                    rules["deviation_formula"],
                )
                if dev30 is not None and dev30 >= rules["severe_30_up"]:
                    criteria.append((3, "deviation_30d", dev30, rules["severe_30_up"], "up"))
                if dev30 is not None and dev30 <= rules["severe_30_down"]:
                    criteria.append((3, "deviation_30d", dev30, rules["severe_30_down"], "down"))
            for direction in ("up", "down"):
                count = _event_count(direction, calculation_dates[-10:])
                if count >= rules["same_direction_count"]:
                    criteria.append((
                        2,
                        "same_direction_count",
                        float(count),
                        float(rules["same_direction_count"]),
                        direction,
                    ))

            for risk_level, criterion, actual, threshold, direction in criteria:
                prior = legacy_by_code.get(code)
                if prior is None or int(prior["risk_level"]) < risk_level:
                    legacy_by_code[code] = {
                        "ts_code": code,
                        "name": name,
                        "regulatory_type": 2,
                        "risk_level": risk_level,
                        "reason": f"{criterion}: {actual:.2f} / 阈值 {threshold:.2f}",
                        "publish_date": target_date,
                        "source": f"calculated:{CALCULATION_POLICY_VERSION}",
                        "risk_score": 0.95 if risk_level == 3 else 0.75,
                        "detail_json": {
                            "criterion": criterion,
                            "actual": actual,
                            "threshold": threshold,
                            "benchmark": benchmark,
                            "deviation_formula": rules["deviation_formula"],
                        },
                    }

            today_candidates: list[dict[str, Any]] = []
            if len(calculation_dates) >= 2:
                previous_date = calculation_dates[-2]
                today_price_base = pre_closes.get(target_date)
                price_base_status = "target_pre_close"
                if today_price_base is None:
                    today_price_base = closes.get(previous_date)
                    price_base_status = "previous_close_fallback"
                current_index_pct = index_pct.get(target_date)
            else:
                previous_date = ""
                today_price_base = None
                price_base_status = "missing"
                current_index_pct = None
            for window_days, risk_level, up_threshold, down_threshold in (
                (10, 2, rules["severe_10_up"], rules["severe_10_down"]),
                (30, 3, rules["severe_30_up"], rules["severe_30_down"]),
            ):
                if (
                    len(calculation_dates) < window_days
                    or today_price_base is None
                    or current_index_pct is None
                ):
                    continue
                historical = calculation_dates[-window_days:-1]
                for direction, threshold in (("up", up_threshold), ("down", down_threshold)):
                    required = _required_next_move(
                        stock_pct,
                        index_pct,
                        historical,
                        threshold,
                        assumed_index_pct=current_index_pct,
                        formula=rules["deviation_formula"],
                    )
                    if required is None or (direction == "up" and required <= 0) or (
                        direction == "down" and required >= 0
                    ):
                        continue
                    target_price = _target_price(today_price_base, required)
                    reachable, limit_price = _reachable_at_price_limit(
                        today_price_base,
                        target_price,
                        rules["limit_pct"],
                        direction,
                    )
                    if not reachable:
                        continue
                    actual_pct = stock_pct[target_date]
                    today_candidates.append({
                        "ts_code": code,
                        "code": code,
                        "name": name,
                        "direction": direction,
                        "criterion": f"deviation_{window_days}d",
                        "deviation_formula": rules["deviation_formula"],
                        "window_days": window_days,
                        "threshold": threshold,
                        "required_pct": round(required, 4),
                        "required_move_pct": round(required, 4),
                        "actual_pct": round(actual_pct, 4),
                        "remaining_move_pct": round(required - actual_pct, 4),
                        "triggered_at_close": (
                            actual_pct >= required
                            if direction == "up"
                            else actual_pct <= required
                        ),
                        "price_base_date": target_date,
                        "price_base": today_price_base,
                        "price_base_status": price_base_status,
                        "target_price": target_price,
                        "limit_price": limit_price,
                        "reachable_at_limit": True,
                        "limit_price_status": "theoretical",
                        "index_assumption": "target_date_actual",
                        "risk_level": risk_level,
                        "evidence_type": "calculation",
                        "fact_level": "[计算]",
                    })

            for direction in ("up", "down"):
                prior_dates = calculation_dates[:-1][-9:]
                count = _event_count(direction, prior_dates)
                if (
                    count != rules["same_direction_count"] - 1
                    or len(calculation_dates) < 3
                    or today_price_base is None
                    or current_index_pct is None
                ):
                    continue
                threshold = (
                    rules["abnormal_threshold_up"]
                    if direction == "up"
                    else rules["abnormal_threshold_down"]
                )
                required = _required_next_move(
                    stock_pct,
                    index_pct,
                    calculation_dates[-3:-1],
                    threshold,
                    assumed_index_pct=current_index_pct,
                    formula=rules["deviation_formula"],
                )
                if required is None or (direction == "up" and required <= 0) or (
                    direction == "down" and required >= 0
                ):
                    continue
                target_price = _target_price(today_price_base, required)
                reachable, limit_price = _reachable_at_price_limit(
                    today_price_base,
                    target_price,
                    rules["limit_pct"],
                    direction,
                )
                if not reachable:
                    continue
                actual_pct = stock_pct[target_date]
                today_candidates.append({
                    "ts_code": code,
                    "code": code,
                    "name": name,
                    "direction": direction,
                    "criterion": "same_direction_count",
                    "deviation_formula": rules["deviation_formula"],
                    "window_days": 3,
                    "event_window_days": 10,
                    "threshold": threshold,
                    "current_event_count": count,
                    "required_pct": round(required, 4),
                    "required_move_pct": round(required, 4),
                    "actual_pct": round(actual_pct, 4),
                    "remaining_move_pct": round(required - actual_pct, 4),
                    "triggered_at_close": (
                        actual_pct >= required
                        if direction == "up"
                        else actual_pct <= required
                    ),
                    "price_base_date": target_date,
                    "price_base": today_price_base,
                    "price_base_status": price_base_status,
                    "target_price": target_price,
                    "limit_price": limit_price,
                    "reachable_at_limit": True,
                    "limit_price_status": "theoretical",
                    "index_assumption": "target_date_actual",
                    "risk_level": 2,
                    "evidence_type": "calculation",
                    "fact_level": "[计算]",
                })

            for direction in ("up", "down"):
                directional = [
                    item
                    for item in today_candidates
                    if item["direction"] == direction
                ]
                if directional:
                    today.append(
                        min(
                            directional,
                            key=lambda item: abs(item["required_move_pct"]),
                        )
                    )

            next_candidates: list[dict[str, Any]] = []
            for window_days, up_threshold, down_threshold in (
                (10, rules["severe_10_up"], rules["severe_10_down"]),
                (30, rules["severe_30_up"], rules["severe_30_down"]),
            ):
                if len(next_calculation_dates) < window_days - 1:
                    continue
                historical = next_calculation_dates[-(window_days - 1):]
                for direction, threshold in (("up", up_threshold), ("down", down_threshold)):
                    required = _required_next_move(
                        stock_pct,
                        index_pct,
                        historical,
                        threshold,
                        formula=rules["deviation_formula"],
                    )
                    if required is None or (direction == "up" and required <= 0) or (
                        direction == "down" and required >= 0
                    ):
                        continue
                    target_price = _target_price(closes[target_date], required)
                    reachable, limit_price = _reachable_at_price_limit(
                        closes[target_date],
                        target_price,
                        rules["limit_pct"],
                        direction,
                    )
                    next_candidates.append({
                        "ts_code": code,
                        "code": code,
                        "name": name,
                        "direction": direction,
                        "criterion": f"deviation_{window_days}d",
                        "deviation_formula": rules["deviation_formula"],
                        "window_days": window_days,
                        "threshold": threshold,
                        "required_pct": round(required, 4),
                        "required_move_pct": round(required, 4),
                        "target_price": target_price,
                        "limit_price": limit_price,
                        "reachable_at_limit": reachable,
                        "limit_price_status": "theoretical",
                        "index_assumption": "flat",
                        "price_base_date": target_date,
                        "price_base": closes[target_date],
                        "evidence_type": "calculation",
                        "fact_level": "[计算]",
                    })
            for direction in ("up", "down"):
                count = _event_count(direction, next_calculation_dates[-9:])
                if (
                    count != rules["same_direction_count"] - 1
                    or len(next_calculation_dates) < 2
                ):
                    continue
                threshold = (
                    rules["abnormal_threshold_up"]
                    if direction == "up"
                    else rules["abnormal_threshold_down"]
                )
                required = _required_next_move(
                    stock_pct,
                    index_pct,
                    next_calculation_dates[-2:],
                    threshold,
                    formula=rules["deviation_formula"],
                )
                if required is None or (direction == "up" and required <= 0) or (
                    direction == "down" and required >= 0
                ):
                    continue
                target_price = _target_price(closes[target_date], required)
                reachable, limit_price = _reachable_at_price_limit(
                    closes[target_date],
                    target_price,
                    rules["limit_pct"],
                    direction,
                )
                next_candidates.append({
                    "ts_code": code,
                    "code": code,
                    "name": name,
                    "direction": direction,
                    "criterion": "same_direction_count",
                    "deviation_formula": rules["deviation_formula"],
                    "window_days": 3,
                    "threshold": threshold,
                    "current_event_count": count,
                    "required_pct": round(required, 4),
                    "required_move_pct": round(required, 4),
                    "target_price": target_price,
                    "limit_price": limit_price,
                    "reachable_at_limit": reachable,
                    "limit_price_status": "theoretical",
                    "index_assumption": "flat",
                    "price_base_date": target_date,
                    "price_base": closes[target_date],
                    "evidence_type": "calculation",
                    "fact_level": "[计算]",
                })

            reachable = [item for item in next_candidates if item["reachable_at_limit"]]
            for direction in ("up", "down"):
                directional = [item for item in reachable if item["direction"] == direction]
                if directional:
                    next_day.append(min(directional, key=lambda item: abs(item["required_move_pct"])))

        hot.sort(key=lambda item: (-item["deviation_pct"], item["code"]))
        today.sort(key=lambda item: (-item["risk_level"], item["code"], item["criterion"]))
        next_day.sort(key=lambda item: (abs(item["required_move_pct"]), item["code"]))
        return {
            "status": (
                "partial"
                if missing_codes or optional_truncated or len(open_dates) < 30
                else "complete"
            ),
            "hot": hot,
            "today": today,
            "next_day": next_day,
            "legacy": list(legacy_by_code.values()),
            "missing_codes": sorted(set(missing_codes)),
            "missing_market_dates": missing_market_dates,
            "candidate_count": len(candidate_codes),
            "optional_truncated": optional_truncated,
            "reset_boundaries": reset_boundaries,
            "excluded_no_limit_dates": excluded_no_limit_dates,
        }

    def _build_suspend_legacy(
        self,
        target_date: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from collectors.regulatory import (
            _suspend_api_snapshot_for_code,
            _suspend_change_reason_index,
            _type1_regulatory_reason,
            ensure_names_for_ts_codes,
            resolve_stock_display_name,
        )

        codes = sorted(
            {
                _canonical_ts_code(row.get("ts_code") or row.get("code"))
                for row in rows
                if isinstance(row, dict)
            }
            - {""}
        )
        name_map: dict[str, str] = {}
        change_rows: list[Any] = []
        if self.registry is not None and codes:
            ensure_names_for_ts_codes(self.registry, name_map, codes)
            change_result = self.registry.call(
                "get_suspend_change_reasons",
                target_date,
                codes,
            )
            if change_result.success and isinstance(change_result.data, list):
                change_rows = change_result.data
        change_by_code = _suspend_change_reason_index(change_rows)

        out: list[dict[str, Any]] = []
        for row in rows:
            code = _canonical_ts_code(row.get("ts_code") or row.get("code"))
            if not code:
                continue
            name = resolve_stock_display_name(code, row.get("name"), name_map)
            reason = _type1_regulatory_reason(row, change_by_code, code)
            out.append({
                "ts_code": code,
                "name": name,
                "regulatory_type": 1,
                "risk_level": 1,
                "reason": reason,
                "publish_date": target_date,
                "source": "tushare:suspend_d",
                "risk_score": 1.0,
                "detail_json": {
                    "raw": row,
                    "suspend_change_reason": change_by_code.get(code),
                    "suspend_api": _suspend_api_snapshot_for_code(change_rows, code),
                },
            })
        return out

    def _preserve_previous_complete_sections(
        self,
        overview: dict[str, Any],
        previous: dict[str, Any] | None,
        *,
        calculation_status: str,
        alert_usable: bool,
        high_shock_usable: bool,
    ) -> dict[str, Any]:
        """降级重跑保留同日已完成分区，同时把本轮失败状态暴露给读端。"""
        if (
            not isinstance(previous, dict)
            or overview.get("status") == "complete"
        ):
            return overview

        preserved: list[str] = []
        previous_calculation_complete = (
            previous.get("calculation_meta", {}).get("status") == "complete"
        )
        if (
            calculation_status != "complete"
            and (
                previous.get("status") == "complete"
                or previous_calculation_complete
            )
        ):
            for key in ("hot_deviations", "trigger_candidates"):
                if key in previous:
                    overview[key] = previous[key]
                    preserved.append(key)
        if (
            not alert_usable
            and previous.get("status") == "complete"
            and "monitoring" in previous
        ):
            overview["monitoring"] = previous["monitoring"]
            preserved.append("monitoring")
        if (
            not high_shock_usable
            and previous.get("status") == "complete"
            and "recent_high_shocks" in previous
        ):
            overview["recent_high_shocks"] = previous["recent_high_shocks"]
            preserved.append("recent_high_shocks")

        if preserved:
            overview.setdefault("calculation_meta", {})[
                "preserved_previous_complete_sections"
            ] = sorted(set(preserved))
        return overview

    def _persist_overview(
        self,
        overview: dict[str, Any],
        *,
        alert_state: dict[str, Any],
        suspend_state: dict[str, Any],
        alert_rows: list[dict[str, Any]],
        type1_rows: list[dict[str, Any]],
        type2_rows: list[dict[str, Any]],
    ) -> None:
        target_date = overview["snapshot_date"]
        with get_db(self.db_path) as conn:
            migrate(conn)
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO market_fact_snapshots
                    (snapshot_id, biz_date, fact_type, subject_type, subject_code, subject_name,
                     facts_json, source_interfaces_json, confidence, inserted_at, updated_at)
                    VALUES (?, ?, ?, 'market', 'CN', ?, ?, ?, ?, datetime('now'), datetime('now'))
                    ON CONFLICT(biz_date, fact_type, subject_type, subject_code) DO UPDATE SET
                        subject_name = excluded.subject_name,
                        facts_json = excluded.facts_json,
                        source_interfaces_json = excluded.source_interfaces_json,
                        confidence = excluded.confidence,
                        updated_at = datetime('now')
                    """,
                    (
                        f"{target_date}:{OVERVIEW_FACT_TYPE}:market:CN",
                        target_date,
                        OVERVIEW_FACT_TYPE,
                        "A股监管异动总览",
                        json.dumps(overview, ensure_ascii=False, sort_keys=True),
                        json.dumps(
                            overview.get("calculation_meta", {}).get(
                                "input_interfaces",
                                ["stk_alert", "stk_shock", "stk_high_shock"],
                            ),
                            ensure_ascii=False,
                        ),
                        "high" if overview["status"] == "complete" else "medium",
                    ),
                )

                if suspend_state["status"] in {"success", "empty", "late"} and (
                    suspend_state.get("snapshot_date") == target_date
                ):
                    conn.execute(
                        "DELETE FROM stock_regulatory_monitor WHERE publish_date = ? AND regulatory_type = 1",
                        (target_date,),
                    )
                    Q.batch_upsert_regulatory_monitors(conn, type1_rows)

                if (
                    overview.get("status") == "complete"
                    and overview.get("calculation_meta", {}).get("status") == "complete"
                ):
                    conn.execute(
                        "DELETE FROM stock_regulatory_monitor WHERE publish_date = ? AND regulatory_type = 2",
                        (target_date,),
                    )
                    Q.batch_upsert_regulatory_monitors(conn, type2_rows)

                if alert_state["status"] in {"success", "empty", "late"} and (
                    alert_state.get("snapshot_date") == target_date
                ):
                    Q.replace_stk_alert_snapshot(conn, target_date, alert_rows)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def build(self, target_date: str, *, persist: bool = True) -> dict[str, Any]:
        if not _DATE_RE.match(target_date):
            raise ValueError("target_date must be YYYY-MM-DD")
        datetime.strptime(target_date, "%Y-%m-%d")

        with get_db(self.db_path) as conn:
            migrate(conn)
            previous_overview = get_regulatory_overview(conn, target_date)
            source_states = {
                name: self._read_source(conn, name, target_date)
                for name in ("stk_alert", "stk_shock", "stk_high_shock")
            }
            suspend_state = self._read_source(conn, "regulatory_suspend", target_date)
            limit_state = self._read_source(conn, "limit_step", target_date)
            open_dates = self._load_open_dates(conn, target_date)
            effective_date, effective_date_status = self._effective_date(
                conn,
                target_date,
                source_states["stk_alert"],
            )

        alert_state = source_states["stk_alert"]
        alert_rows = alert_state["rows"]
        current_alerts, historical_alerts = self._build_monitoring(alert_rows, effective_date)
        recent_start = open_dates[-30] if len(open_dates) >= 30 else (
            open_dates[0]
            if open_dates
            else (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=60)).date().isoformat()
        )
        recent_high_rows = self._recent_rows(source_states["stk_high_shock"]["rows"], recent_start)
        recent_high_shocks = [
            {
                "ts_code": _canonical_ts_code(row.get("ts_code") or row.get("code")),
                "code": _canonical_ts_code(row.get("ts_code") or row.get("code")),
                "name": str(row.get("name") or row.get("ts_code") or row.get("code") or ""),
                "trade_date": row["trade_date_norm"],
                "period_start": _norm_date(row.get("period_start")),
                "period_end": _norm_date(row.get("period_end")),
                "reason": str(row.get("reason") or "交易所确认严重异常波动"),
                "source": "tushare:stk_high_shock",
                "evidence_type": "fact",
                "fact_level": "[事实]",
            }
            for row in recent_high_rows
            if str(row.get("ts_code") or row.get("code") or "").strip()
        ]
        recent_high_shocks.sort(key=lambda item: (item["trade_date"], item["code"]), reverse=True)

        calculation = self._calculate_candidates(
            target_date=target_date,
            open_dates=open_dates,
            alert_rows=alert_rows,
            shock_rows=source_states["stk_shock"]["rows"],
            high_rows=source_states["stk_high_shock"]["rows"],
            limit_rows=limit_state["rows"] if limit_state.get("snapshot_date") == target_date else [],
        )
        calculation_source_states = {
            **source_states,
            "limit_step": limit_state,
        }
        calculation_sources_current = all(
            state["status"] in {"success", "empty"}
            and state.get("snapshot_date") == target_date
            for state in calculation_source_states.values()
        )
        if (
            not calculation_sources_current
            and calculation["status"] == "complete"
        ):
            calculation["status"] = "partial_sources"
        source_status = {
            name: {
                key: state.get(key)
                for key in (
                    "interface",
                    "provider",
                    "status",
                    "snapshot_date",
                    "row_count",
                    "query",
                    "error",
                )
            }
            for name, state in calculation_source_states.items()
        }
        usable_count = sum(1 for state in source_states.values() if state["usable"])
        source_degraded = any(
            state["status"] not in {"success", "empty"} for state in source_states.values()
        ) or not calculation_sources_current
        status = "failed" if usable_count == 0 else (
            "partial"
            if (
                source_degraded
                or calculation["status"] != "complete"
                or effective_date_status != "complete"
            )
            else "complete"
        )
        overview = {
            "snapshot_date": target_date,
            "effective_trading_date": effective_date,
            "status": status,
            "calculation_policy_version": CALCULATION_POLICY_VERSION,
            "source_status": source_status,
            "monitoring": {
                "current": current_alerts,
                "history": historical_alerts,
            },
            "hot_deviations": calculation["hot"],
            "trigger_candidates": {
                "today": calculation["today"],
                "next_day": calculation["next_day"],
            },
            "recent_high_shocks": recent_high_shocks,
            "calculation_meta": {
                "status": calculation["status"],
                "candidate_count": calculation["candidate_count"],
                "optional_candidate_cap": OPTIONAL_CANDIDATE_CAP,
                "optional_truncated": calculation["optional_truncated"],
                "missing_codes": calculation["missing_codes"],
                "missing_market_dates": calculation["missing_market_dates"],
                "reset_boundaries": calculation["reset_boundaries"],
                "excluded_no_limit_dates": calculation["excluded_no_limit_dates"],
                "effective_date_status": effective_date_status,
                "hot_window": "max_positive_3_to_10_open_days",
                "deviation_formula_by_board": {
                    "沪市主板": "period_stock_return-index_return",
                    "深市主板": "sum(stock_daily_pct-index_daily_pct)",
                    "创业板": "sum(stock_daily_pct-index_daily_pct)",
                    "科创板": "sum(stock_daily_pct-index_daily_pct)",
                },
                "reset_rule": "next_open_after_confirmed_high_shock_missing_dates_are_partial",
                "today_index_assumption": "target_date_actual",
                "today_price_base": "target_date_pre_close",
                "next_day_index_assumption": "flat",
                "input_interfaces": [
                    "stk_alert",
                    "stk_shock",
                    "stk_high_shock",
                    "regulatory_suspend",
                    "limit_step",
                    "trade_calendar",
                    "get_stock_st",
                    "get_stock_daily_range",
                    "get_index_daily_range",
                    "get_stock_basic_batch",
                    "get_suspend_change_reasons",
                ],
                "replayable": False,
                "provenance_note": (
                    "监管原始接口已归档；行情、ST 与交易日历为本轮 provider/本地事实输入，"
                    "当前仅记录能力与日期口径，未归档完整行情输入。"
                ),
            },
            "disclaimer": "偏离值、触发空间和理论价格均为程序计算，不构成投资建议。",
        }
        overview = self._preserve_previous_complete_sections(
            overview,
            previous_overview,
            calculation_status=calculation["status"],
            alert_usable=bool(source_states["stk_alert"]["usable"]),
            high_shock_usable=bool(source_states["stk_high_shock"]["usable"]),
        )

        normalized_alert_rows = [
            {
                "ts_code": item["code"],
                "name": item["name"],
                "monitor_start": item["monitor_start"],
                "monitor_end": item["monitor_end"],
                "alert_type": item["alert_type"],
                "source": item["source"],
                "detail_json": {"fact_level": item["fact_level"]},
            }
            for item in current_alerts + historical_alerts
        ]
        type1_rows = self._build_suspend_legacy(target_date, suspend_state["rows"])
        if persist:
            self._persist_overview(
                overview,
                alert_state=alert_state,
                suspend_state=suspend_state,
                alert_rows=normalized_alert_rows,
                type1_rows=type1_rows,
                type2_rows=calculation["legacy"],
            )
        return overview


def get_regulatory_overview(conn: Any, target_date: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT facts_json
        FROM market_fact_snapshots
        WHERE biz_date = ? AND fact_type = ?
          AND subject_type = 'market' AND subject_code = 'CN'
        LIMIT 1
        """,
        (target_date, OVERVIEW_FACT_TYPE),
    ).fetchone()
    if row is None:
        return None
    raw = row["facts_json"] if hasattr(row, "keys") else row[0]
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
