"""月线观察池的行业内估值证据。

只读取已经归档到 ``raw_interface_payloads`` 的 ``daily_basic`` 快照，严格选择
不晚于扫描日的最近一份数据。估值仅用于观察层排序，不参与池状态机。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date
from typing import Any, Mapping


VALUATION_VERSION = "monthly_industry_valuation_percentile_v1"
MAX_SNAPSHOT_AGE_DAYS = 7
MIN_INDUSTRY_SAMPLE_SIZE = 5

_FINANCIAL_INDUSTRY_TOKENS = ("银行", "证券", "保险", "多元金融")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _code(row: Mapping[str, Any]) -> str:
    return str(
        row.get("ts_code") or row.get("stock_code") or row.get("code") or ""
    ).strip().upper().split(".", 1)[0]


def _industry(row: Mapping[str, Any] | None) -> str:
    if not isinstance(row, Mapping):
        return ""
    return "".join(str(row.get("sw_l2") or "").split())


def _primary_metric(industry: str, row: Mapping[str, Any]) -> tuple[str, float] | None:
    if any(token in industry for token in _FINANCIAL_INDUSTRY_TOKENS):
        value = _number(row.get("pb"))
        return ("pb", value) if value is not None and value > 0 else None
    for key in ("pe_ttm", "ps_ttm", "pb"):
        value = _number(row.get(key))
        if value is not None and value > 0:
            return key, value
    return None


def _strict_date(value: Any) -> date | None:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == text else None


def _load_snapshot(
    conn: sqlite3.Connection,
    as_of_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target = _strict_date(as_of_date)
    if target is None:
        raise ValueError("as_of_date 必须为 YYYY-MM-DD")
    try:
        row = conn.execute(
            """
            SELECT target_date, provider, row_count, payload_json
            FROM raw_interface_payloads
            WHERE interface_name = 'daily_basic'
              AND status = 'success'
              AND target_date <= ?
            ORDER BY target_date DESC, id DESC
            LIMIT 1
            """,
            (as_of_date,),
        ).fetchone()
    except sqlite3.OperationalError:
        return [], {
            "status": "missing",
            "version": VALUATION_VERSION,
            "reason": "daily_basic 原始事实表不可用",
        }
    if row is None:
        return [], {
            "status": "missing",
            "version": VALUATION_VERSION,
            "reason": "扫描日前没有成功的 daily_basic 快照",
        }
    snapshot_date = _strict_date(row[0])
    if snapshot_date is None:
        return [], {
            "status": "source_failed",
            "version": VALUATION_VERSION,
            "reason": "daily_basic 快照日期非法",
        }
    age_days = (target - snapshot_date).days
    if age_days < 0 or age_days > MAX_SNAPSHOT_AGE_DAYS:
        return [], {
            "status": "stale",
            "version": VALUATION_VERSION,
            "as_of_date": snapshot_date.isoformat(),
            "age_days": age_days,
            "reason": "daily_basic 快照超过允许时效",
        }
    try:
        payload = json.loads(str(row[3] or ""))
    except json.JSONDecodeError:
        return [], {
            "status": "source_failed",
            "version": VALUATION_VERSION,
            "as_of_date": snapshot_date.isoformat(),
            "reason": "daily_basic payload_json 损坏",
        }
    payload_rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(payload_rows, list):
        return [], {
            "status": "source_failed",
            "version": VALUATION_VERSION,
            "as_of_date": snapshot_date.isoformat(),
            "reason": "daily_basic rows 契约损坏",
        }
    expected_count = int(row[2] or 0)
    if expected_count != len(payload_rows):
        return [], {
            "status": "source_failed",
            "version": VALUATION_VERSION,
            "as_of_date": snapshot_date.isoformat(),
            "reason": (
                "daily_basic 行数与原始收据不一致: "
                f"receipt={expected_count}, payload={len(payload_rows)}"
            ),
        }
    return [item for item in payload_rows if isinstance(item, dict)], {
        "status": "success",
        "version": VALUATION_VERSION,
        "as_of_date": snapshot_date.isoformat(),
        "provider": str(row[1] or ""),
        "age_days": age_days,
        "row_count": len(payload_rows),
    }


def load_industry_valuation_views(
    conn: sqlite3.Connection,
    as_of_date: str,
    *,
    industry_map: Mapping[str, Mapping[str, Any]],
    market_codes: set[str],
    min_market_coverage: float = 0.90,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """返回按行业和估值指标分组的低值百分位，值越低排名越靠前。"""
    rows, meta = _load_snapshot(conn, as_of_date)
    if meta["status"] != "success":
        return {}, meta
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _code(row)
        if code and code not in by_code:
            by_code[code] = row
    covered = len(set(by_code) & market_codes)
    coverage = covered / len(market_codes) if market_codes else 0.0
    meta = {**meta, "market_coverage": coverage, "covered_codes": covered}
    if coverage < min_market_coverage:
        return {}, {
            **meta,
            "status": "coverage_failed",
            "reason": (
                f"daily_basic 市场覆盖 {coverage:.2%} 低于 "
                f"{min_market_coverage:.2%}"
            ),
        }

    grouped: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    metric_by_code: dict[str, tuple[str, float, str]] = {}
    for code in sorted(market_codes):
        industry = _industry(industry_map.get(code))
        row = by_code.get(code)
        if not industry or row is None:
            continue
        metric = _primary_metric(industry, row)
        if metric is None:
            continue
        metric_name, value = metric
        grouped[(industry, metric_name)].append((code, value))
        metric_by_code[code] = (metric_name, value, industry)

    views: dict[str, dict[str, Any]] = {}
    for code, (metric_name, value, industry) in metric_by_code.items():
        peers = grouped[(industry, metric_name)]
        sample_size = len(peers)
        if sample_size < MIN_INDUSTRY_SAMPLE_SIZE:
            views[code] = {
                "status": "insufficient",
                "version": VALUATION_VERSION,
                "as_of_date": meta["as_of_date"],
                "industry": industry,
                "metric": metric_name,
                "value": value,
                "industry_sample_size": sample_size,
            }
            continue
        below = sum(peer_value < value for _peer, peer_value in peers)
        equal = sum(peer_value == value for _peer, peer_value in peers)
        percentile = round((below + (equal + 1) / 2) / sample_size * 100.0, 4)
        raw = by_code[code]
        views[code] = {
            "status": "success",
            "version": VALUATION_VERSION,
            "as_of_date": meta["as_of_date"],
            "industry": industry,
            "metric": metric_name,
            "value": value,
            "industry_percentile": percentile,
            "industry_sample_size": sample_size,
            "pe_ttm": _number(raw.get("pe_ttm")),
            "pb": _number(raw.get("pb")),
            "ps_ttm": _number(raw.get("ps_ttm")),
        }
    return views, {**meta, "view_count": len(views)}


__all__ = [
    "MAX_SNAPSHOT_AGE_DAYS",
    "MIN_INDUSTRY_SAMPLE_SIZE",
    "VALUATION_VERSION",
    "load_industry_valuation_views",
]
