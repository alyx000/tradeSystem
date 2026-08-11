"""连板股断板后的下一交易日反馈统计（只读事实层）。

时间轴固定为：T-2 连板 >= 2 → T-1 不再涨停且有有效日线（断板）→ T 反馈。
本统计不复用 ``board-break`` 观察清单的主板、涨幅上限、非跌停等候选规则，
避免把选股口径混入市场赚钱效应统计。
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from pathlib import Path

import yaml

from utils import is_st_stock
from utils.trade_date import get_prev_trade_date


DAILY_DIR = Path(__file__).resolve().parent.parent.parent / "daily"


def _finite_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bare_code(value) -> str:
    return str(value or "").strip().upper().split(".", 1)[0]


def _limit_times(value) -> int | None:
    number = _finite_float(value)
    if number is None:
        return None
    return int(number)


def _load_raw_data(daily_dir: Path, trade_date: str) -> tuple[dict | None, str | None]:
    path = daily_dir / trade_date / "post-market.yaml"
    if not path.is_file():
        return None, f"缺少 {trade_date} 盘后归档"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, f"{trade_date} 盘后归档不可读: {exc}"
    if not isinstance(payload, Mapping):
        return None, f"{trade_date} 盘后归档结构非法"
    archived_date = payload.get("date")
    if archived_date not in (None, trade_date):
        return None, f"{trade_date} 盘后归档日期错位: {archived_date}"
    raw_data = payload.get("raw_data", payload)
    if not isinstance(raw_data, Mapping):
        return None, f"{trade_date} raw_data 结构非法"
    return dict(raw_data), None


def _limit_up_rows(raw_data: Mapping, trade_date: str) -> tuple[list | None, str | None]:
    section = raw_data.get("limit_up")
    if not isinstance(section, Mapping):
        return None, f"{trade_date} 缺少涨停事实"
    if section.get("error"):
        return None, f"{trade_date} 涨停事实来源失败: {section.get('error')}"
    rows = section.get("stocks")
    if not isinstance(rows, list):
        return None, f"{trade_date} 涨停事实缺少 stocks"
    return rows, None


def _quote_date_matches(value, expected_date: str) -> bool:
    text = str(value or "").strip()
    return text in {expected_date, expected_date.replace("-", "")}


def _load_market_quotes(registry, trade_date: str) -> tuple[dict[str, dict] | None, str | None, str]:
    result = registry.call("get_market_daily_quotes", trade_date)
    source = str(getattr(result, "source", "") or "")
    rows = getattr(result, "data", None)
    if not getattr(result, "success", False) or not isinstance(rows, list) or not rows:
        error = str(getattr(result, "error", None) or f"{trade_date} 全市场日线为空")
        return None, error, source
    quotes: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not _quote_date_matches(row.get("trade_date"), trade_date):
            continue
        code = _bare_code(row.get("code") or row.get("ts_code"))
        if code:
            quotes[code] = dict(row)
    if not quotes:
        return None, f"{trade_date} 全市场日线无日期匹配的有效代码", source
    return quotes, None, source


def _valid_quote_row(row: Mapping | None, required: tuple[str, ...]) -> tuple[dict | None, str | None]:
    if not isinstance(row, Mapping):
        return None, "行情不可得"
    values = {key: _finite_float(row.get(key)) for key in required}
    if any(values[key] is None for key in required):
        return None, f"行情缺少有效 {'/'.join(required)}"
    for price_key in ("open", "close", "pre_close"):
        if price_key in values and values[price_key] <= 0:
            return None, f"行情 {price_key} 非正数"
    data = dict(row)
    data.update(values)
    return data, None


def _code_set(rows: list | None) -> tuple[set[str], int]:
    codes: set[str] = set()
    dirty = 0
    for row in rows or []:
        if not isinstance(row, Mapping):
            dirty += 1
            continue
        code = _bare_code(row.get("code") or row.get("ts_code"))
        if not code:
            dirty += 1
            continue
        codes.add(code)
    return codes, dirty


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 2) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def collect_board_break_feedback(
    registry,
    outcome_date: str,
    today_raw_data: Mapping,
    *,
    daily_dir: Path = DAILY_DIR,
) -> dict:
    """统计 T-1 断板股在 T 日的开盘、收盘与涨跌停反馈。"""
    break_date = get_prev_trade_date(registry, outcome_date)
    connected_date = get_prev_trade_date(registry, break_date)
    base = {
        "status": "source_failed",
        "source_connected_date": connected_date,
        "break_date": break_date,
        "outcome_date": outcome_date,
        "definition": "T-2连板>=2且非ST，T-1有交易但不再涨停，T统计下一交易日反馈",
        "errors": [],
        "details": [],
    }

    connected_raw, error = _load_raw_data(daily_dir, connected_date)
    if error:
        return {**base, "errors": [error]}
    break_raw, error = _load_raw_data(daily_dir, break_date)
    if error:
        return {**base, "errors": [error]}

    connected_rows, error = _limit_up_rows(connected_raw or {}, connected_date)
    if error:
        return {**base, "errors": [error]}
    break_limit_rows, error = _limit_up_rows(break_raw or {}, break_date)
    if error:
        return {**base, "errors": [error]}

    current_limit_rows, current_limit_error = _limit_up_rows(today_raw_data, outcome_date)
    current_down = today_raw_data.get("limit_down")
    current_down_error = None
    if not isinstance(current_down, Mapping):
        current_down_error = f"{outcome_date} 缺少跌停事实"
        current_down_rows: list[dict] = []
    elif current_down.get("error"):
        current_down_error = f"{outcome_date} 跌停事实来源失败: {current_down.get('error')}"
        current_down_rows = []
    elif not isinstance(current_down.get("stocks"), list):
        current_down_error = f"{outcome_date} 跌停事实缺少 stocks"
        current_down_rows: list = []
    else:
        current_down_rows = current_down["stocks"]

    connected_by_code: dict[str, dict] = {}
    dirty_source_count = 0
    for row in connected_rows or []:
        if not isinstance(row, Mapping):
            dirty_source_count += 1
            continue
        code = _bare_code(row.get("code") or row.get("ts_code"))
        height = _limit_times(row.get("limit_times", row.get("nums")))
        name = str(row.get("name") or "").strip()
        if not code or height is None or not name:
            dirty_source_count += 1
            continue
        if height < 2 or is_st_stock(name):
            continue
        existing = connected_by_code.get(code)
        if existing is None or height > existing["height"]:
            connected_by_code[code] = {"code": code, "name": name, "height": height}
    connected = list(connected_by_code.values())

    break_limit_codes, dirty_break_limit_count = _code_set(break_limit_rows)
    # T-1 涨停榜靠“代码缺席”证明断板；只要存在无法识别的代码，就无法排除该脏行
    # 正是某只 T-2 连板股的续板记录。此时整项 fail-closed，不能产出被污染的比例/明细。
    if dirty_break_limit_count:
        errors = [f"{dirty_break_limit_count}条T-1涨停记录代码无效，无法可靠判定断板"]
        if dirty_source_count:
            errors.append(f"{dirty_source_count}条T-2涨停记录无法核验代码、连板高度或非ST身份")
        return {
            **base,
            "errors": errors,
            "connected_count": len(connected),
            "dirty_source_count": dirty_source_count,
            "dirty_break_limit_count": dirty_break_limit_count,
        }
    break_candidates = [row for row in connected if row["code"] not in break_limit_codes]

    break_confirmed: list[dict] = []
    missing_break_quotes: list[dict] = []
    quote_sources: set[str] = set()
    break_quotes: dict[str, dict] | None = {}
    break_quote_error = None
    if break_candidates:
        break_quotes, break_quote_error, source = _load_market_quotes(registry, break_date)
        if source:
            quote_sources.add(source)
        if break_quotes is not None:
            for row in break_candidates:
                quote, quote_error = _valid_quote_row(
                    break_quotes.get(row["code"]), ("close", "pct_chg")
                )
                if quote is None:
                    missing_break_quotes.append({
                        "code": row["code"], "name": row["name"], "error": quote_error
                    })
                    continue
                break_confirmed.append({
                    **row,
                    "break_change_pct": round(quote["pct_chg"], 2),
                })

    current_limit_codes, dirty_current_limit_count = _code_set(current_limit_rows)
    current_down_codes, dirty_current_down_count = _code_set(current_down_rows)
    limit_status_uncertain = bool(current_limit_error or dirty_current_limit_count)
    down_status_uncertain = bool(current_down_error or dirty_current_down_count)

    details: list[dict] = []
    missing_outcome_quotes: list[dict] = []
    outcome_quotes: dict[str, dict] | None = {}
    outcome_quote_error = None
    if break_confirmed:
        outcome_quotes, outcome_quote_error, source = _load_market_quotes(registry, outcome_date)
        if source:
            quote_sources.add(source)
        if outcome_quotes is not None:
            for row in break_confirmed:
                quote, quote_error = _valid_quote_row(
                    outcome_quotes.get(row["code"]),
                    ("open", "close", "pre_close", "pct_chg"),
                )
                if quote is None:
                    missing_outcome_quotes.append({
                        "code": row["code"], "name": row["name"], "error": quote_error
                    })
                    continue
                # 用 T 日自身 pre_close / pct_chg，避免除权除息时跨日原始价手算出假涨跌。
                open_pct = round((quote["open"] - quote["pre_close"]) / quote["pre_close"] * 100, 2)
                close_pct = round(quote["pct_chg"], 2)
                if row["code"] in current_limit_codes:
                    outcome = "再涨停"
                elif row["code"] in current_down_codes:
                    outcome = "跌停"
                elif close_pct > 0:
                    outcome = "上涨（涨停状态未核验）" if limit_status_uncertain else "上涨"
                elif close_pct < 0:
                    outcome = "下跌（跌停状态未核验）" if down_status_uncertain else "下跌"
                else:
                    outcome = "平盘"
                details.append({
                    "code": row["code"],
                    "name": row["name"],
                    "previous_height": row["height"],
                    "break_change_pct": row["break_change_pct"],
                    "feedback_open_pct": open_pct,
                    "feedback_close_pct": close_pct,
                    "outcome": outcome,
                })

    errors: list[str] = []
    if dirty_source_count:
        errors.append(f"{dirty_source_count}条T-2涨停记录无法核验代码、连板高度或非ST身份")
    if break_quote_error:
        errors.append(f"断板日全市场行情失败: {break_quote_error}")
    if outcome_quote_error:
        errors.append(f"反馈日全市场行情失败: {outcome_quote_error}")
    if break_confirmed and current_limit_error:
        errors.append(current_limit_error)
    elif break_confirmed and dirty_current_limit_count:
        errors.append(f"{dirty_current_limit_count}条T日涨停记录代码无效")
    if break_confirmed and current_down_error:
        errors.append(current_down_error)
    elif break_confirmed and dirty_current_down_count:
        errors.append(f"{dirty_current_down_count}条T日跌停记录代码无效")
    if missing_break_quotes:
        errors.append(f"{len(missing_break_quotes)}只断板候选缺少断板日行情")
    if missing_outcome_quotes:
        errors.append(f"{len(missing_outcome_quotes)}只已确认断板股缺少反馈日行情")

    if dirty_source_count and not connected:
        status = "source_failed"
    elif break_candidates and not break_confirmed:
        status = "source_failed"
    elif break_confirmed and not details:
        status = "source_failed"
    elif errors:
        status = "partial"
    else:
        status = "ok"

    open_values = [row["feedback_open_pct"] for row in details]
    close_values = [row["feedback_close_pct"] for row in details]
    sample_count = len(details)
    break_count = len(break_confirmed)
    open_up_count = sum(value > 0 for value in open_values)
    close_up_count = sum(value > 0 for value in close_values)
    relimit_count = (
        None if limit_status_uncertain
        else sum(row["outcome"] == "再涨停" for row in details)
    )
    limit_down_count = (
        None if down_status_uncertain
        else sum(row["outcome"] == "跌停" for row in details)
    )

    empty_reason = None
    if not connected:
        empty_reason = "no_connected_candidates"
    elif not break_candidates:
        empty_reason = "no_board_breaks"

    return {
        **base,
        "status": status,
        "errors": errors,
        "empty_reason": empty_reason,
        "connected_count": len(connected),
        "break_candidate_count": len(break_candidates),
        "break_count": break_count,
        "sample_count": sample_count,
        "break_coverage_pct": (
            round(break_count / len(break_candidates) * 100, 1)
            if break_candidates else None
        ),
        "feedback_coverage_pct": round(sample_count / break_count * 100, 1) if break_count else None,
        # 兼容首版内部字段；明确等同反馈日行情覆盖，不再承担断板候选核验覆盖语义。
        "coverage_pct": round(sample_count / break_count * 100, 1) if break_count else None,
        "open_up_count": open_up_count,
        "open_up_rate": round(open_up_count / sample_count, 3) if sample_count else None,
        "open_mean_pct": _mean(open_values),
        "open_median_pct": _median(open_values),
        "close_up_count": close_up_count,
        "close_up_rate": round(close_up_count / sample_count, 3) if sample_count else None,
        "close_mean_pct": _mean(close_values),
        "close_median_pct": _median(close_values),
        "relimit_count": relimit_count,
        "relimit_rate": (
            round(relimit_count / sample_count, 3)
            if sample_count and relimit_count is not None else None
        ),
        "limit_down_count": limit_down_count,
        "limit_down_rate": (
            round(limit_down_count / sample_count, 3)
            if sample_count and limit_down_count is not None else None
        ),
        "dirty_source_count": dirty_source_count,
        "missing_break_quotes": missing_break_quotes,
        "missing_outcome_quotes": missing_outcome_quotes,
        "sources": {
            "connected_limit_up": (connected_raw or {}).get("limit_up", {}).get("_source"),
            "break_limit_up": (break_raw or {}).get("limit_up", {}).get("_source"),
            "daily_quotes": sorted(quote_sources),
        },
        "details": sorted(details, key=lambda row: row["feedback_close_pct"], reverse=True),
    }
