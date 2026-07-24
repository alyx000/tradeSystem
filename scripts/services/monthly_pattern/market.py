"""月线全市场行情的日历、覆盖率与前复权口径。"""
from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import datetime
from typing import Any


class SourceCoverageError(RuntimeError):
    """来源为空、截断、重复或复权覆盖不足，不能继续扫描。"""


def _date(raw: Any) -> str | None:
    text = str(raw or "").strip().replace("-", "")
    if len(text) < 8 or not text[:8].isdigit():
        return None
    text = text[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _code(row: dict) -> str:
    return str(row.get("ts_code") or row.get("stock_code") or row.get("code") or "").split(".")[0]


def _month_index(month: str) -> int:
    year, number = month.split("-", 1)
    return int(year) * 12 + int(number)


def validate_consecutive_month_ends(
    month_ends: list[str],
    *,
    expected_months: int,
) -> None:
    """要求完成月窗口数量精确且自然月连续，禁止用更早月份填补中间缺口。"""
    if len(month_ends) != expected_months:
        raise SourceCoverageError(
            f"完成月交易日历仅 {len(month_ends)} 个月，要求 {expected_months} 个月"
        )
    normalized = [_date(item) for item in month_ends]
    if any(item is None for item in normalized) or normalized != sorted(set(normalized)):
        raise SourceCoverageError("完成月交易日历包含非法、重复或乱序日期")
    months = [str(item)[:7] for item in normalized]
    for previous, current in zip(months, months[1:]):
        if _month_index(current) != _month_index(previous) + 1:
            raise SourceCoverageError(
                f"完成月交易日历不连续: {previous} -> {current}"
            )


def select_as_of_universe_codes(
    universe_rows: list[dict],
    month_end: str,
) -> set[str]:
    """按上市/退市日期从外部 stock_basic 清单构造目标月可审计分母。"""
    normalized_month_end = _date(month_end)
    if normalized_month_end is None:
        raise ValueError(f"非法 month_end: {month_end}")
    month_start = normalized_month_end[:7] + "-01"
    output: set[str] = set()
    for row in universe_rows:
        code = _code(row)
        if not code:
            continue
        list_date = _date(row.get("list_date"))
        delist_date = _date(row.get("delist_date"))
        if list_date is not None and list_date > normalized_month_end:
            continue
        # 月中退市股票在该月仍形成月线，只有月初前已退市才排除。
        if delist_date is not None and delist_date < month_start:
            continue
        output.add(code)
    if not output:
        raise SourceCoverageError(f"{month_end} 外部股票宇宙为空")
    return output


def select_completed_month_ends(
    calendar_rows: list[dict],
    as_of_date: str,
    *,
    months: int,
) -> list[str]:
    """从完整交易日历选择截至 as-of 已结束月份的最后开放日。"""
    if months <= 0:
        raise ValueError("months 必须为正整数")
    as_of = _date(as_of_date)
    if as_of is None:
        raise ValueError(f"非法日期: {as_of_date}")
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    current_month_complete = (
        as_of_dt.day == calendar.monthrange(as_of_dt.year, as_of_dt.month)[1]
    )
    last_open_by_month: dict[str, str] = {}
    for row in calendar_rows:
        try:
            is_open = int(row.get("is_open", 0)) == 1
        except (TypeError, ValueError):
            is_open = False
        day = _date(row.get("cal_date") or row.get("date"))
        if not is_open or day is None:
            continue
        month = day[:7]
        if day > last_open_by_month.get(month, ""):
            last_open_by_month[month] = day
    completed = sorted(
        day
        for month, day in last_open_by_month.items()
        if day <= as_of
        and (month < as_of[:7] or (month == as_of[:7] and current_month_complete))
    )
    return completed[-months:]


def join_month_quotes_and_factors(
    quotes: list[dict],
    factors: list[dict],
    *,
    month_end: str,
    min_rows: int = 4000,
    min_factor_coverage: float = 0.95,
    universe_rows: list[dict] | None = None,
    min_universe_coverage: float = 0.95,
    universe_source: str = "stock_basic",
    return_manifest: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """校验全市场月线和月末复权因子后，形成可持久化的原始月线事实。"""
    normalized_month_end = _date(month_end)
    if normalized_month_end is None:
        raise ValueError(f"非法 month_end: {month_end}")
    if universe_rows is None and len(quotes) < min_rows:
        raise SourceCoverageError(
            f"{month_end} 月线行情仅 {len(quotes)} 行，低于完整性地板 {min_rows}"
        )
    quote_map: dict[str, dict] = {}
    for row in quotes:
        if row.get("trade_date") is not None:
            returned_date = _date(row.get("trade_date"))
            if returned_date != normalized_month_end:
                raise SourceCoverageError(
                    f"{month_end} 月线行情日期错位: {returned_date or row.get('trade_date')}"
                )
        code = _code(row)
        if not code:
            continue
        if code in quote_map:
            raise SourceCoverageError(f"{month_end} 月线行情存在重复股票代码: {code}")
        quote_map[code] = row

    universe_codes: set[str] | None = None
    quote_universe_coverage: float | None = None
    if universe_rows is not None:
        universe_codes = select_as_of_universe_codes(universe_rows, normalized_month_end)
        quote_universe_coverage = len(set(quote_map) & universe_codes) / len(universe_codes)
        if quote_universe_coverage < min_universe_coverage:
            raise SourceCoverageError(
                f"{month_end} 月线行情对外部股票宇宙覆盖率 "
                f"{quote_universe_coverage:.2%}，低于 {min_universe_coverage:.2%} "
                f"({len(set(quote_map) & universe_codes)}/{len(universe_codes)})"
            )

    factor_map: dict[str, float] = {}
    for row in factors:
        if row.get("trade_date") is not None:
            returned_date = _date(row.get("trade_date"))
            if returned_date != normalized_month_end:
                raise SourceCoverageError(
                    f"{month_end} 复权因子日期错位: {returned_date or row.get('trade_date')}"
                )
        code = _code(row)
        factor = _number(row.get("adj_factor"))
        if not code or factor is None or factor <= 0:
            continue
        if code in factor_map:
            raise SourceCoverageError(f"{month_end} 复权因子存在重复股票代码: {code}")
        factor_map[code] = factor

    joined_code_set = set(quote_map) & set(factor_map)
    if universe_codes is not None:
        unexpected_codes = sorted(joined_code_set - universe_codes)
        if unexpected_codes:
            preview = ",".join(unexpected_codes[:5])
            raise SourceCoverageError(
                f"{month_end} 行情/复权包含 {len(unexpected_codes)} 个"
                f"外部股票宇宙之外代码（示例: {preview}）"
            )
        joined_code_set &= universe_codes
    joined_codes = sorted(joined_code_set)
    denominator = max(len(quote_map), 1)
    factor_coverage = len(joined_codes) / denominator
    if factor_coverage < min_factor_coverage:
        raise SourceCoverageError(
            f"{month_end} 复权因子覆盖率 {factor_coverage:.2%}，"
            f"低于 {min_factor_coverage:.2%}"
        )

    output: list[dict] = []
    for code in joined_codes:
        row = quote_map[code]
        prices = {field: _number(row.get(field)) for field in ("open", "high", "low", "close")}
        if any(value is None or value <= 0 for value in prices.values()):
            continue
        if not (
            prices["high"] >= max(prices["open"], prices["close"])
            and prices["low"] <= min(prices["open"], prices["close"])
            and prices["high"] >= prices["low"]
        ):
            continue
        volume = _number(
            row.get("vol") if "vol" in row else row.get("volume")
        )
        amount = _number(row.get("amount"))
        if volume is None or amount is None or volume < 0 or amount < 0:
            continue
        output.append(
            {
                "month_end": normalized_month_end,
                "stock_code": code,
                "stock_name": row.get("name") or row.get("stock_name"),
                **prices,
                "volume": volume,
                "amount": amount,
                "adj_factor": factor_map[code],
                "source": row.get("_source") or "tushare:monthly+adj_factor",
            }
        )
    if universe_codes is None and len(output) < min_rows:
        raise SourceCoverageError(
            f"{month_end} 清洗后有效月线仅 {len(output)} 行，低于完整性地板 {min_rows}"
        )
    valid_universe_coverage: float | None = None
    if universe_codes is not None:
        output_codes = {str(row["stock_code"]) for row in output}
        valid_universe_coverage = len(output_codes & universe_codes) / len(universe_codes)
        if valid_universe_coverage < min_universe_coverage:
            raise SourceCoverageError(
                f"{month_end} 清洗后对外部股票宇宙覆盖率 "
                f"{valid_universe_coverage:.2%}，低于 {min_universe_coverage:.2%}"
            )
    if not return_manifest:
        return output
    manifest = {
        "month_end": normalized_month_end,
        "status": "certified",
        "universe_source": universe_source,
        "universe_count": len(universe_codes or quote_map),
        "quote_count": len(quote_map),
        "factor_count": len(factor_map),
        "joined_count": len(output),
        "quote_coverage": (
            quote_universe_coverage
            if quote_universe_coverage is not None
            else 1.0
        ),
        "factor_coverage": factor_coverage,
        "source_meta": {
            "valid_universe_coverage": valid_universe_coverage,
            "min_universe_coverage": min_universe_coverage,
            "min_factor_coverage": min_factor_coverage,
        },
    }
    return output, manifest


def _manifest_effective_coverage(manifest: dict) -> float:
    universe_count = int(manifest.get("universe_count") or 0)
    joined_count = int(manifest.get("joined_count") or 0)
    if universe_count <= 0:
        raise SourceCoverageError(
            f"{manifest.get('month_end')} manifest 外部股票宇宙分母非法"
        )
    quote_coverage = _number(manifest.get("quote_coverage"))
    factor_coverage = _number(manifest.get("factor_coverage"))
    joined_coverage = joined_count / universe_count
    candidates = [joined_coverage]
    if quote_coverage is not None:
        candidates.append(quote_coverage)
    if factor_coverage is not None:
        candidates.append(factor_coverage)
    source_meta = manifest.get("source_meta")
    if isinstance(source_meta, dict):
        valid_coverage = _number(source_meta.get("valid_universe_coverage"))
        if valid_coverage is not None:
            candidates.append(valid_coverage)
    return min(candidates)


def validate_month_manifest_sequence(
    manifests: list[dict],
    *,
    min_adjacent_coverage_ratio: float = 0.98,
    max_adjacent_coverage_ratio: float = 1.02,
) -> None:
    """按外部宇宙归一后的有效覆盖比较相邻月，拦截单月共同截断/异常缩水。"""
    ordered = sorted(manifests, key=lambda item: str(item.get("month_end") or ""))
    for previous, current in zip(ordered, ordered[1:]):
        previous_month = str(previous.get("month_end") or "")[:7]
        current_month = str(current.get("month_end") or "")[:7]
        if (
            len(previous_month) != 7
            or len(current_month) != 7
            or _month_index(current_month) != _month_index(previous_month) + 1
        ):
            continue
        previous_coverage = _manifest_effective_coverage(previous)
        current_coverage = _manifest_effective_coverage(current)
        if previous_coverage <= 0:
            raise SourceCoverageError(
                f"{previous.get('month_end')} manifest 有效覆盖率非法"
            )
        ratio = current_coverage / previous_coverage
        if not min_adjacent_coverage_ratio <= ratio <= max_adjacent_coverage_ratio:
            raise SourceCoverageError(
                f"相邻月有效覆盖异常: {previous.get('month_end')} "
                f"{previous_coverage:.2%} -> {current.get('month_end')} "
                f"{current_coverage:.2%} (ratio={ratio:.4f}, "
                f"允许 {min_adjacent_coverage_ratio:.4f}.."
                f"{max_adjacent_coverage_ratio:.4f})"
            )


def apply_month_end_qfq(rows: list[dict]) -> list[dict]:
    """按每只股票窗口末月因子，对月 OHLC 四价做同口径前复权。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        code = _code(row)
        if code:
            grouped[code].append(dict(row))

    adjusted: list[dict] = []
    for code, stock_rows in grouped.items():
        stock_rows.sort(key=lambda item: str(item.get("month_end") or ""))
        latest_factor = _number(stock_rows[-1].get("adj_factor"))
        if latest_factor is None or latest_factor <= 0:
            raise SourceCoverageError(f"{code} 窗口末月复权因子缺失")
        previous_factor: float | None = None
        for row in stock_rows:
            factor = _number(row.get("adj_factor"))
            if factor is None or factor <= 0:
                raise SourceCoverageError(f"{code} {row.get('month_end')} 复权因子缺失")
            row["price_shape_valid"] = bool(
                previous_factor is None
                or math.isclose(
                    factor,
                    previous_factor,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            ratio = factor / latest_factor
            for field in ("open", "high", "low", "close"):
                value = _number(row.get(field))
                if value is None:
                    raise SourceCoverageError(f"{code} {row.get('month_end')} {field} 缺失")
                row[field] = round(value * ratio, 8)
            row["stock_code"] = code
            adjusted.append(row)
            previous_factor = factor
    return sorted(adjusted, key=lambda item: (item["stock_code"], item["month_end"]))
