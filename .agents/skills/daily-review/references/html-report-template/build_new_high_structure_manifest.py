#!/usr/bin/env python3
"""只读生成前复权滚动 60/120/250 日新高结构 sidecar。

本脚本与 ``new-high`` 的全历史高水位任务是两套口径：这里按市场交易日脊柱，
用 ``daily.high * adj_factor`` 比较当前日与窗口内此前交易日的最高值。只读行情，
不写 SQLite、报告或计划层。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import date as date_type
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "rolling-new-high-structure-v1"
WINDOWS = (60, 120, 250)
MIN_MARKET_UNIVERSE = 4_000
MIN_ADJ_COVERAGE = 0.95
MAX_WORKERS = 12
PAGE_SIZE = 2_000


class ManifestBuildError(RuntimeError):
    pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _valid_date(value: str) -> bool:
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _provider():
    repo_root = _repo_root()
    scripts_dir = repo_root / "scripts"
    sys.path.insert(0, str(scripts_dir))

    from dotenv import load_dotenv
    import tushare as ts
    import tushare.pro.client as client
    from providers.tushare_provider import TushareProvider

    load_dotenv(scripts_dir / ".env")
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise ManifestBuildError("TUSHARE_TOKEN 未配置")
    client.DataApi._DataApi__http_url = "http://tushare.xyz"
    provider = TushareProvider({"token": token})
    provider.pro = ts.pro_api(token)
    provider._initialized = True
    return provider


def _result_data(result: Any, capability: str):
    if not getattr(result, "success", False):
        raise ManifestBuildError(
            f"{capability} 失败: {getattr(result, 'error', 'unknown')}"
        )
    return result.data, str(getattr(result, "source", ""))


def _trade_dates(provider, as_of: str, required: int = 251) -> tuple[list[str], str]:
    year = int(as_of[:4])
    dates: set[str] = set()
    sources: list[str] = []
    for offset in range(4):
        probe = as_of if offset == 0 else f"{year - offset}-12-31"
        rows, source = _result_data(
            provider.get_trade_calendar(probe), "get_trade_calendar"
        )
        sources.append(source)
        for row in rows or []:
            if str(row.get("is_open")) != "1":
                continue
            raw = str(row.get("cal_date") or "")[:8]
            if len(raw) != 8 or not raw.isdigit():
                continue
            value = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            if value <= as_of:
                dates.add(value)
        if len(dates) >= required:
            break
    ordered = sorted(dates)[-required:]
    if len(ordered) != required or ordered[-1] != as_of:
        raise ManifestBuildError(
            f"无法取得截至 {as_of} 的 {required} 个开放日"
        )
    return ordered, "+".join(dict.fromkeys(sources))


def _fetch_day(provider, trade_date: str) -> tuple[str, list[dict], list[dict], str, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            quotes, quote_source = _result_data(
                provider.get_market_daily_quotes(trade_date),
                f"get_market_daily_quotes({trade_date})",
            )
            factors, factor_source = _result_data(
                provider.get_adj_factor(trade_date),
                f"get_adj_factor({trade_date})",
            )
            quote_codes = {
                str(item.get("ts_code") or item.get("code") or "").strip().upper()
                for item in quotes or []
            }
            factor_codes = {
                str(item.get("ts_code") or item.get("code") or "").strip().upper()
                for item in factors or []
            }
            factor_coverage = len(quote_codes & factor_codes) / max(1, len(quote_codes))
            if factor_coverage < MIN_ADJ_COVERAGE:
                pro = getattr(provider, "pro", None)
                if pro is None:
                    raise ManifestBuildError(
                        f"{trade_date} 复权因子疑似截断且 provider 不支持分页"
                    )
                paged: list[dict] = []
                offset = 0
                for _ in range(10):
                    frame = pro.query(
                        "adj_factor",
                        trade_date=trade_date.replace("-", ""),
                        limit=PAGE_SIZE,
                        offset=offset,
                    )
                    page = provider._df_to_records(frame)
                    if not page:
                        break
                    paged.extend(page)
                    offset += PAGE_SIZE
                else:
                    raise ManifestBuildError(
                        f"{trade_date} adj_factor 分页超过安全上限"
                    )
                factors = paged
                factor_source = "tushare:adj_factor:paged"
            if len(quote_codes) < MIN_MARKET_UNIVERSE:
                raise ManifestBuildError(
                    f"{trade_date} 全市场行情仅 {len(quote_codes)} 个唯一代码"
                )
            return (
                trade_date,
                list(quotes or []),
                list(factors or []),
                quote_source,
                factor_source,
            )
        except Exception as exc:  # provider/网络异常统一有限重试后 fail-closed
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise ManifestBuildError(f"{trade_date} 行情/复权因子失败: {last_error}")


def _normalize_day(quotes: list[dict], factors: list[dict]) -> dict[str, dict]:
    factor_map: dict[str, float] = {}
    for row in factors:
        code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        try:
            factor = float(row.get("adj_factor"))
        except (TypeError, ValueError):
            continue
        if code and math.isfinite(factor) and factor > 0:
            factor_map[code] = factor

    normalized: dict[str, dict] = {}
    for row in quotes:
        code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        try:
            high = float(row.get("high"))
            amount = float(row.get("amount") or 0)
            pct_chg = float(row.get("pct_chg") or 0)
        except (TypeError, ValueError):
            continue
        factor = factor_map.get(code)
        if (
            not code
            or factor is None
            or not math.isfinite(high)
            or high <= 0
        ):
            continue
        normalized[code] = {
            "adj_high": round(high * factor, 10),
            "amount": amount,
            "pct_chg": pct_chg,
        }
    unique_quote_codes = {
        str(row.get("ts_code") or row.get("code") or "").strip().upper()
        for row in quotes
        if row.get("ts_code") or row.get("code")
    }
    coverage = len(normalized) / max(1, len(unique_quote_codes))
    if coverage < MIN_ADJ_COVERAGE:
        raise ManifestBuildError(
            f"复权因子覆盖率仅 {coverage:.3f}"
        )
    return normalized


def calculate_structure(
    report_date: str,
    as_of: str,
    trade_dates: Sequence[str],
    normalized_by_date: dict[str, dict[str, dict]],
    industry_map: dict[str, dict],
    listing_dates: dict[str, str],
    *,
    quote_source: str,
    factor_source: str,
    calendar_source: str,
    industry_source: str,
) -> dict:
    if not _valid_date(report_date) or not _valid_date(as_of):
        raise ValueError("report_date/as_of 必须是有效 YYYY-MM-DD")
    ordered = list(trade_dates)
    if len(ordered) < 251 or ordered[-1] != as_of or ordered != sorted(set(ordered)):
        raise ValueError("必须提供截至 as_of 的至少 251 个唯一升序交易日")
    prev_as_of = ordered[-2]

    def detect(target_index: int, window: int) -> set[str]:
        target_date = ordered[target_index]
        current = normalized_by_date[target_date]
        previous_dates = ordered[max(0, target_index - window + 1):target_index]
        oldest_window_date = previous_dates[0]
        found: set[str] = set()
        for code, item in current.items():
            if listing_dates.get(code, "9999-99-99") > oldest_window_date:
                continue
            previous = [
                normalized_by_date[day][code]["adj_high"]
                for day in previous_dates
                if code in normalized_by_date[day]
            ]
            if previous and item["adj_high"] > max(previous) + 1e-10:
                found.add(code)
        return found

    current_sets = {window: detect(len(ordered) - 1, window) for window in WINDOWS}
    previous_sets = {window: detect(len(ordered) - 2, window) for window in WINDOWS}
    current_60 = current_sets[60]
    previous_60 = previous_sets[60]
    current_rows = normalized_by_date[as_of]
    reference_count = len(listing_dates)
    daily_market_counts = {
        day: len(normalized_by_date[day]) for day in ordered
    }
    daily_market_coverage_min = min(
        count / max(1, reference_count) for count in daily_market_counts.values()
    )

    sector_counts = Counter(
        str((industry_map.get(code) or {}).get("sw_l2") or "未分类")
        for code in current_60
    )
    sectors = [
        {
            "industry": industry,
            "count": count,
            "share_pct": round(count / len(current_60) * 100, 2)
            if current_60
            else 0.0,
        }
        for industry, count in sorted(
            sector_counts.items(), key=lambda item: (-item[1], item[0] == "未分类", item[0])
        )
    ]
    cr3_count = sum(item["count"] for item in sectors[:3])
    representatives = []
    for code in sorted(
        current_60,
        key=lambda item: (-float(current_rows[item].get("amount") or 0), item),
    )[:5]:
        meta = industry_map.get(code) or {}
        representatives.append(
            {
                "ts_code": code,
                "name": str(meta.get("name") or ""),
                "industry": str(meta.get("sw_l2") or "未分类"),
                "amount_yi": round(float(current_rows[code].get("amount") or 0) / 100_000, 2),
                "pct_chg": round(float(current_rows[code].get("pct_chg") or 0), 2),
                "windows": [window for window in WINDOWS if code in current_sets[window]],
            }
        )

    overlap = len(current_60 & previous_60)
    market_count = len(current_rows)
    industry_covered = sum(1 for code in current_rows if code in industry_map)
    return {
        "schema": SCHEMA,
        "generator": "build_new_high_structure_manifest.py",
        "report_date": report_date,
        "as_of": as_of,
        "prev_as_of": prev_as_of,
        "status": "complete",
        "complete": True,
        "basis": "rolling-adjusted-high",
        "windows": list(WINDOWS),
        "trade_dates": ordered,
        "daily_market_counts": daily_market_counts,
        "daily_market_coverage_min": round(daily_market_coverage_min, 6),
        "market_count": market_count,
        "market_reference_count": reference_count,
        "market_coverage": round(market_count / max(1, reference_count), 6),
        "industry_coverage": round(industry_covered / max(1, market_count), 6),
        "counts": {
            str(window): {
                "current": len(current_sets[window]),
                "previous": len(previous_sets[window]),
                "delta": len(current_sets[window]) - len(previous_sets[window]),
            }
            for window in WINDOWS
        },
        "sixty_day_overlap": overlap,
        "sixty_day_retention_pct": round(overlap / len(previous_60) * 100, 2)
        if previous_60
        else None,
        "sixty_day_turnover_pct": round((len(current_60) - overlap) / len(current_60) * 100, 2)
        if current_60
        else None,
        "sector_cr3_pct": round(cr3_count / len(current_60) * 100, 2)
        if current_60
        else 0.0,
        "sectors": sectors,
        "representatives": representatives,
        "current_codes": {str(window): sorted(current_sets[window]) for window in WINDOWS},
        "previous_codes": {str(window): sorted(previous_sets[window]) for window in WINDOWS},
        "sources": {
            "quote": quote_source,
            "adj_factor": factor_source,
            "calendar": calendar_source,
            "industry": industry_source,
        },
        "errors": [],
    }


def build_manifest(
    report_date: str,
    as_of: str,
    *,
    provider=None,
    workers: int = MAX_WORKERS,
) -> dict:
    if not _valid_date(report_date) or not _valid_date(as_of):
        raise ValueError("report_date/as_of 必须是有效 YYYY-MM-DD")
    if as_of > report_date:
        raise ValueError("as_of 不得晚于 report_date")
    provider = provider or _provider()
    trade_dates, calendar_source = _trade_dates(provider, as_of)
    industry_map, industry_source = _result_data(
        provider.get_stock_sw_industry_map(), "get_stock_sw_industry_map"
    )
    if not isinstance(industry_map, dict) or len(industry_map) < MIN_MARKET_UNIVERSE:
        raise ManifestBuildError("申万二级成员映射不完整")
    basic_rows, basic_source = _result_data(
        provider.get_stock_basic_list(as_of), "get_stock_basic_list"
    )
    listing_dates: dict[str, str] = {}
    for row in basic_rows or []:
        code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        raw = str(row.get("list_date") or "").replace("-", "")[:8]
        if code and len(raw) == 8 and raw.isdigit():
            listing_dates[code] = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(listing_dates) < MIN_MARKET_UNIVERSE:
        raise ManifestBuildError("上市日期基线不完整")

    fetched: dict[str, tuple[list[dict], list[dict], str, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 24))) as pool:
        futures = {pool.submit(_fetch_day, provider, day): day for day in trade_dates}
        for future in as_completed(futures):
            day, quotes, factors, quote_source, factor_source = future.result()
            fetched[day] = (quotes, factors, quote_source, factor_source)

    normalized_by_date: dict[str, dict[str, dict]] = {}
    for day in trade_dates:
        try:
            normalized_by_date[day] = _normalize_day(
                fetched[day][0], fetched[day][1]
            )
        except ManifestBuildError as exc:
            raise ManifestBuildError(f"{day} {exc}") from exc
    truncated_days = [
        day
        for day in trade_dates
        if len(normalized_by_date[day]) / max(1, len(listing_dates)) < 0.90
    ]
    if truncated_days:
        sample = ", ".join(truncated_days[:3])
        raise ManifestBuildError(
            f"历史日全市场覆盖不足 90%：{sample}"
        )
    quote_sources = "+".join(dict.fromkeys(fetched[day][2] for day in trade_dates))
    factor_sources = "+".join(dict.fromkeys(fetched[day][3] for day in trade_dates))
    return calculate_structure(
        report_date,
        as_of,
        trade_dates,
        normalized_by_date,
        industry_map,
        listing_dates,
        quote_source=quote_sources,
        factor_source=factor_sources,
        calendar_source=calendar_source,
        industry_source=f"{industry_source}+{basic_source}",
    )


def _failure_manifest(report_date: str, as_of: str, message: str) -> dict:
    return {
        "schema": SCHEMA,
        "generator": "build_new_high_structure_manifest.py",
        "report_date": report_date,
        "as_of": as_of,
        "prev_as_of": None,
        "status": "failed",
        "complete": False,
        "basis": "rolling-adjusted-high",
        "windows": list(WINDOWS),
        "trade_dates": [],
        "daily_market_counts": {},
        "daily_market_coverage_min": 0.0,
        "market_count": 0,
        "market_reference_count": 0,
        "market_coverage": 0.0,
        "industry_coverage": 0.0,
        "counts": {},
        "sectors": [],
        "representatives": [],
        "current_codes": {},
        "previous_codes": {},
        "sources": {},
        "errors": [message],
    }


def _atomic_write(payload: dict, output: Path) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, output)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_date")
    parser.add_argument("--as-of", required=True, dest="as_of")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_manifest(
            args.report_date,
            args.as_of,
            workers=args.workers,
        )
    except ValueError as exc:
        print(f"FAIL [usage] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        payload = _failure_manifest(args.report_date, args.as_of, str(exc))
    path = _atomic_write(payload, args.output)
    print(
        f"OK {path} status={payload['status']} "
        f"market={payload['market_count']} counts={payload.get('counts', {})}"
    )
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
