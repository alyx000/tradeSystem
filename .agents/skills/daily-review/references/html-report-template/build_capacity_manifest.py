#!/usr/bin/env python3
"""从只读全市场行情生成容量中军资格 sidecar。

用法：
    python3 build_capacity_manifest.py REPORT_DATE --as-of TRADE_DATE \
        --direction 通信设备 --direction 半导体 --direction 电力 \
        --output TMP/capacity_REPORT_DATE.json

本脚本只读取 Tushare 镜像，不写数据库。排名按 ``daily.amount`` 降序、同额按
``ts_code`` 排序；方向固定使用申万二级，最多三个。生成的 sidecar 由
``assemble_report.py`` 在正式落盘前逐代码对账，避免 Agent 自报排名。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date as date_type
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "capacity-health-v1"
MIN_MARKET_UNIVERSE = 4_000
MAX_DIRECTIONS = 3
MIN_REFERENCE_COVERAGE = 0.90
PAGE_SIZE = 2_000


class ManifestBuildError(RuntimeError):
    pass


def _valid_date(value: str) -> bool:
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


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


def _paged_market_daily(provider, trade_date: str) -> tuple[list[dict], str]:
    """按 offset 拉满镜像 daily；测试 provider 无 pro 时走同契约封装。"""

    pro = getattr(provider, "pro", None)
    if pro is None:
        rows, source = _result_data(
            provider.get_market_daily_quotes(trade_date),
            f"get_market_daily_quotes({trade_date})",
        )
        return list(rows or []), source

    compact_date = trade_date.replace("-", "")
    rows: list[dict] = []
    offset = 0
    for _ in range(10):
        frame = pro.daily(
            trade_date=compact_date,
            fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
            limit=PAGE_SIZE,
            offset=offset,
        )
        page = provider._df_to_records(frame)
        if not page:
            break
        rows.extend(page)
        offset += PAGE_SIZE
    else:
        raise ManifestBuildError(f"{trade_date} daily 分页超过安全上限")
    return rows, "tushare:daily:paged"


def _rank_market(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw in rows:
        code = str(raw.get("ts_code") or raw.get("code") or "").strip().upper()
        try:
            amount = float(raw.get("amount"))
        except (TypeError, ValueError):
            continue
        if not code or code in seen or amount < 0:
            continue
        seen.add(code)
        item = dict(raw)
        item["ts_code"] = code
        item["amount"] = amount
        normalized.append(item)
    normalized.sort(key=lambda row: (-row["amount"], row["ts_code"]))
    return normalized, {
        row["ts_code"]: rank for rank, row in enumerate(normalized, start=1)
    }


def _last_five_trade_dates(provider, as_of: str) -> tuple[list[str], str]:
    probes = [as_of]
    year = int(as_of[:4])
    probes.extend(f"{year - offset}-12-31" for offset in (1, 2))
    dates: list[str] = []
    sources: list[str] = []
    for probe in probes:
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
                dates.append(value)
        if len(set(dates)) >= 5:
            break
    dates = sorted(set(dates))[-5:]
    if len(dates) != 5 or dates[-1] != as_of:
        raise ManifestBuildError("无法取得截至 as-of 的最近 5 个开放日")
    return dates, "+".join(dict.fromkeys(sources))


def build_manifest(
    report_date: str,
    as_of: str,
    directions: Sequence[str],
    *,
    provider=None,
) -> dict:
    if not _valid_date(report_date) or not _valid_date(as_of):
        raise ValueError("report_date/as_of 必须是有效 YYYY-MM-DD")
    if as_of > report_date:
        raise ValueError("as_of 不得晚于 report_date")
    selected = tuple(dict.fromkeys(item.strip() for item in directions if item.strip()))
    if not 1 <= len(selected) <= MAX_DIRECTIONS:
        raise ValueError(f"必须选择 1-{MAX_DIRECTIONS} 个申万二级方向")

    provider = provider or _provider()
    market_rows, market_source = _paged_market_daily(provider, as_of)
    ranked, market_ranks = _rank_market(market_rows or [])
    if len(ranked) < MIN_MARKET_UNIVERSE:
        raise ManifestBuildError(
            f"全市场行情仅 {len(ranked)} 行，低于完整性地板 {MIN_MARKET_UNIVERSE}"
        )

    industry_map, direction_source = _result_data(
        provider.get_stock_sw_industry_map(), "get_stock_sw_industry_map"
    )
    if not isinstance(industry_map, dict) or len(industry_map) < MIN_MARKET_UNIVERSE:
        raise ManifestBuildError("申万二级成员映射不完整")

    reference_rows: list[dict] = []
    reference_source = direction_source
    if hasattr(provider, "get_stock_basic_list"):
        reference_rows, reference_source = _result_data(
            provider.get_stock_basic_list(as_of), "get_stock_basic_list"
        )
    reference_codes = {
        str(row.get("ts_code") or row.get("code") or "").strip().upper()
        for row in reference_rows or []
        if row.get("ts_code") or row.get("code")
    }
    reference_count = len(reference_codes) or len(industry_map)
    market_coverage = len(ranked) / reference_count if reference_count else 0.0
    mapped_count = sum(
        1
        for row in ranked
        if str((industry_map.get(row["ts_code"]) or {}).get("sw_l2") or "").strip()
    )
    industry_coverage = mapped_count / len(ranked) if ranked else 0.0
    if (
        reference_count < MIN_MARKET_UNIVERSE
        or not MIN_REFERENCE_COVERAGE <= market_coverage <= 1.05
        or industry_coverage < MIN_REFERENCE_COVERAGE
    ):
        raise ManifestBuildError(
            "全市场/上市基线/申万映射覆盖不足: "
            f"market={len(ranked)} reference={reference_count} "
            f"market_coverage={market_coverage:.3f} "
            f"industry_coverage={industry_coverage:.3f}"
        )

    grouped: dict[str, list[dict]] = {}
    for row in ranked:
        direction = str((industry_map.get(row["ts_code"]) or {}).get("sw_l2") or "")
        if direction:
            grouped.setdefault(direction, []).append(row)
    missing_directions = [item for item in selected if item not in grouped]
    if missing_directions:
        raise ManifestBuildError(
            "申万二级方向不存在或成员不完整: " + ", ".join(missing_directions)
        )

    direction_ranks: dict[str, int] = {}
    for rows in grouped.values():
        for rank, row in enumerate(rows, start=1):
            direction_ranks[row["ts_code"]] = rank

    trade_dates, calendar_source = _last_five_trade_dates(provider, as_of)
    five_day_ranks: dict[str, dict[str, int]] = {}
    for trade_date in trade_dates:
        rows, _ = _paged_market_daily(provider, trade_date)
        daily_ranked, ranks = _rank_market(rows or [])
        if len(daily_ranked) < MIN_MARKET_UNIVERSE:
            raise ManifestBuildError(f"{trade_date} 全市场行情不完整")
        five_day_ranks[trade_date] = ranks

    eligible: list[dict] = []
    for row in ranked:
        code = row["ts_code"]
        direction = str((industry_map.get(code) or {}).get("sw_l2") or "")
        market_rank = market_ranks[code]
        direction_rank = direction_ranks.get(code, 0)
        if direction not in selected or market_rank > 50 or not 1 <= direction_rank <= 2:
            continue
        tier = "core" if market_rank <= 30 else "candidate"
        eligible.append(
            {
                "ts_code": code,
                "name": str((industry_map.get(code) or {}).get("name") or ""),
                "direction": direction,
                "market_rank": market_rank,
                "direction_rank": direction_rank,
                "top50_days": sum(
                    1
                    for trade_date in trade_dates
                    if five_day_ranks[trade_date].get(code, 10**9) <= 50
                ),
                "tier": tier,
                "amount_yi": round(row["amount"] / 100_000, 2),
            }
        )

    return {
        "schema": SCHEMA,
        "report_date": report_date,
        "as_of": as_of,
        "status": "complete",
        "complete": True,
        "rank_metric": "daily.amount",
        "market_source": market_source,
        "market_reference_source": reference_source,
        "market_reference_count": reference_count,
        "market_coverage": round(market_coverage, 6),
        "direction_source": direction_source,
        "industry_coverage": round(industry_coverage, 6),
        "calendar_source": calendar_source,
        "generator": "build_capacity_manifest.py",
        "market_universe_count": len(ranked),
        "directions": [
            {"id": item, "member_count": len(grouped[item])} for item in selected
        ],
        "rank_trade_dates": trade_dates,
        "rows": eligible,
        "errors": [],
    }


def _failure_manifest(
    report_date: str, as_of: str, directions: Sequence[str], message: str
) -> dict:
    return {
        "schema": SCHEMA,
        "report_date": report_date,
        "as_of": as_of,
        "status": "failed",
        "complete": False,
        "rank_metric": "daily.amount",
        "market_source": "",
        "market_reference_source": "",
        "market_reference_count": 0,
        "market_coverage": 0.0,
        "direction_source": "",
        "industry_coverage": 0.0,
        "calendar_source": "",
        "generator": "build_capacity_manifest.py",
        "market_universe_count": 0,
        "directions": [{"id": item} for item in directions],
        "rank_trade_dates": [],
        "rows": [],
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
    parser.add_argument("--direction", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_manifest(args.report_date, args.as_of, args.direction)
    except ValueError as exc:
        print(f"FAIL [usage] {exc}", file=sys.stderr)
        return 2
    except ManifestBuildError as exc:
        payload = _failure_manifest(
            args.report_date, args.as_of, args.direction, str(exc)
        )
    path = _atomic_write(payload, args.output)
    print(
        f"OK {path} status={payload['status']} "
        f"universe={payload['market_universe_count']} rows={len(payload['rows'])}"
    )
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
