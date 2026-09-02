"""盘后低价股赚钱效应的全市场等权统计。

本模块只计算客观横截面指标，不给出强弱评级、价格目标或交易建议。
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any


LOW_PRICE_MAX_YUAN = 10.0
VERY_LOW_PRICE_MAX_YUAN = 5.0
MIN_UNIQUE_QUOTE_COUNT = 4000
MIN_VALID_QUOTE_RATIO = 0.98
MIN_AMOUNT_COVERAGE_RATIO = 0.98

_B_SHARE_PREFIXES = ("200", "201", "900")
_A_SHARE_SUFFIXES = {"SH", "SZ", "BJ"}


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    bare, dot, suffix = text.partition(".")
    if len(bare) != 6 or not bare.isdigit():
        return ""
    if dot and suffix not in _A_SHARE_SUFFIXES:
        return ""
    return bare


def _date_matches(value: Any, expected: str) -> bool:
    text = str(value or "").strip()
    return text in {expected, expected.replace("-", "")}


def _code_set_from_rows(rows: list[dict] | None) -> set[str]:
    return {
        code
        for row in rows or []
        if isinstance(row, Mapping)
        for code in [_canonical_code(row.get("code") or row.get("ts_code"))]
        if code
    }


def _limit_code_set(section: Any, label: str) -> tuple[set[str] | None, str | None, str]:
    if not isinstance(section, Mapping):
        return None, f"{label}事实缺失", ""
    source = str(section.get("_source") or section.get("source") or "")
    if section.get("error"):
        return None, f"{label}来源失败:{section.get('error')}", source
    rows = section.get("stocks")
    if rows is None and int(section.get("count") or 0) == 0:
        rows = []
    if not isinstance(rows, list):
        return None, f"{label}事实缺少 stocks", source
    codes = _code_set_from_rows(rows)
    expected_count = int(section.get("count") or len(rows))
    if len(codes) < expected_count or len(codes) < len(rows):
        return None, f"{label}事实代码覆盖不足:{len(codes)}/{expected_count}", source
    return codes, None, source


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def _bucket_metrics(
    rows: list[dict],
    *,
    limit_up_codes: set[str] | None,
    limit_down_codes: set[str] | None,
) -> dict:
    pcts = [row["pct_chg"] for row in rows]
    sample_count = len(rows)
    advance_count = sum(value > 0 for value in pcts)
    flat_count = sum(value == 0 for value in pcts)
    decline_count = sum(value < 0 for value in pcts)
    strong_gain_count = sum(value >= 5 for value in pcts)
    strong_loss_count = sum(value <= -5 for value in pcts)
    limit_up_count = (
        sum(row["code"] in limit_up_codes for row in rows)
        if limit_up_codes is not None
        else None
    )
    limit_down_count = (
        sum(row["code"] in limit_down_codes for row in rows)
        if limit_down_codes is not None
        else None
    )
    amount_valid_count = sum(row["amount"] is not None for row in rows)
    amount_coverage_ratio = (
        round(amount_valid_count / sample_count, 4) if sample_count else None
    )
    amount_billion = None
    if (
        amount_coverage_ratio is not None
        and amount_coverage_ratio >= MIN_AMOUNT_COVERAGE_RATIO
    ):
        # Tushare daily.amount 单位为千元；1 亿元 = 100000 千元。
        amount_billion = round(
            sum(row["amount"] for row in rows if row["amount"] is not None) / 1e5,
            2,
        )
    return {
        "sample_count": sample_count,
        "advance_count": advance_count,
        "flat_count": flat_count,
        "decline_count": decline_count,
        "advance_rate": _rate(advance_count, sample_count),
        "pct_chg_median": round(statistics.median(pcts), 2) if pcts else None,
        "pct_chg_mean": round(statistics.mean(pcts), 2) if pcts else None,
        "strong_gain_count": strong_gain_count,
        "strong_gain_rate": _rate(strong_gain_count, sample_count),
        "strong_loss_count": strong_loss_count,
        "strong_loss_rate": _rate(strong_loss_count, sample_count),
        "limit_up_count": limit_up_count,
        "limit_up_rate": (
            _rate(limit_up_count, sample_count) if limit_up_count is not None else None
        ),
        "limit_down_count": limit_down_count,
        "limit_down_rate": (
            _rate(limit_down_count, sample_count) if limit_down_count is not None else None
        ),
        "amount_valid_count": amount_valid_count,
        "amount_coverage_ratio": amount_coverage_ratio,
        "amount_billion": amount_billion,
    }


def calculate_low_price_effect(
    quote_rows: list[dict],
    st_rows: list[dict],
    trade_date: str,
    *,
    quote_source: str = "",
    st_source: str = "",
    limit_up_section: Any = None,
    limit_down_section: Any = None,
    min_unique_quote_count: int = MIN_UNIQUE_QUOTE_COUNT,
) -> dict:
    """从全市场日线计算低价股横截面统计，严格区分来源失败与有效空集。"""
    gaps: list[str] = []
    duplicate_codes: set[str] = set()
    invalid_quote_count = 0
    quote_by_code: dict[str, dict] = {}

    for raw in quote_rows:
        if not isinstance(raw, Mapping):
            invalid_quote_count += 1
            continue
        code = _canonical_code(raw.get("code") or raw.get("ts_code"))
        close = _finite_float(raw.get("close"))
        pct_chg = _finite_float(raw.get("pct_chg"))
        if (
            not code
            or not _date_matches(raw.get("trade_date"), trade_date)
            or close is None
            or close <= 0
            or pct_chg is None
        ):
            invalid_quote_count += 1
            continue
        if code in quote_by_code:
            duplicate_codes.add(code)
            continue
        amount = _finite_float(raw.get("amount"))
        quote_by_code[code] = {
            "code": code,
            "close": close,
            "pct_chg": pct_chg,
            "amount": amount if amount is not None and amount >= 0 else None,
        }

    raw_quote_count = len(quote_rows)
    unique_quote_count = len(quote_by_code)
    valid_quote_ratio = (
        round(unique_quote_count / raw_quote_count, 4) if raw_quote_count else 0.0
    )
    base = {
        "trade_date": trade_date,
        "definition": {
            "version": "v1",
            "low_price_max_yuan": LOW_PRICE_MAX_YUAN,
            "very_low_price_max_yuan": VERY_LOW_PRICE_MAX_YUAN,
            "classification_basis": "当日未复权收盘价",
            "universe": "当日有有效日线的沪深北 A 股，剔除 ST/退市及沪深 B 股",
            "weighting": "个股等权；成交额占比按当日 amount 加权",
        },
        "source": {
            "quotes": quote_source,
            "st": st_source,
        },
        "coverage": {
            "raw_quote_count": raw_quote_count,
            "unique_quote_count": unique_quote_count,
            "invalid_quote_count": invalid_quote_count,
            "duplicate_quote_count": len(duplicate_codes),
            "valid_quote_ratio": valid_quote_ratio,
            "minimum_unique_quote_count": min_unique_quote_count,
            "minimum_valid_quote_ratio": MIN_VALID_QUOTE_RATIO,
        },
        "gaps": gaps,
    }

    if duplicate_codes:
        return {
            **base,
            "status": "source_failed",
            "error": f"全市场日线存在重复代码:{len(duplicate_codes)}",
        }
    if unique_quote_count < min_unique_quote_count:
        return {
            **base,
            "status": "source_failed",
            "error": f"全市场有效日线不足:{unique_quote_count}/{min_unique_quote_count}",
        }
    if valid_quote_ratio < MIN_VALID_QUOTE_RATIO:
        return {
            **base,
            "status": "source_failed",
            "error": f"全市场日线有效率不足:{valid_quote_ratio:.1%}",
        }

    st_codes = _code_set_from_rows(st_rows)
    base["coverage"].update({
        "st_raw_count": len(st_rows),
        "st_valid_code_count": len(st_codes),
    })
    if not st_codes:
        return {
            **base,
            "status": "source_failed",
            "error": "ST/退市名单为空，无法执行低价股口径剔除",
        }
    if len(st_codes) < len(st_rows):
        return {
            **base,
            "status": "source_failed",
            "error": f"ST/退市名单代码覆盖不足:{len(st_codes)}/{len(st_rows)}",
        }

    excluded_b_share_count = 0
    excluded_st_count = 0
    eligible_rows: list[dict] = []
    for row in quote_by_code.values():
        if row["code"].startswith(_B_SHARE_PREFIXES):
            excluded_b_share_count += 1
            continue
        if row["code"] in st_codes:
            excluded_st_count += 1
            continue
        eligible_rows.append(row)
    if not eligible_rows:
        return {
            **base,
            "status": "source_failed",
            "error": "剔除 ST/退市与 B 股后无有效 A 股样本",
        }

    limit_up_codes, limit_up_gap, limit_up_source = _limit_code_set(
        limit_up_section, "涨停"
    )
    limit_down_codes, limit_down_gap, limit_down_source = _limit_code_set(
        limit_down_section, "跌停"
    )
    base["source"].update({
        "limit_up": limit_up_source,
        "limit_down": limit_down_source,
    })
    for gap in (limit_up_gap, limit_down_gap):
        if gap:
            gaps.append(gap)

    low_rows = [row for row in eligible_rows if row["close"] <= LOW_PRICE_MAX_YUAN]
    very_low_rows = [
        row for row in eligible_rows if row["close"] <= VERY_LOW_PRICE_MAX_YUAN
    ]
    low_mid_rows = [
        row
        for row in eligible_rows
        if VERY_LOW_PRICE_MAX_YUAN < row["close"] <= LOW_PRICE_MAX_YUAN
    ]

    market = _bucket_metrics(
        eligible_rows,
        limit_up_codes=limit_up_codes,
        limit_down_codes=limit_down_codes,
    )
    low_price = _bucket_metrics(
        low_rows,
        limit_up_codes=limit_up_codes,
        limit_down_codes=limit_down_codes,
    )
    very_low = _bucket_metrics(
        very_low_rows,
        limit_up_codes=limit_up_codes,
        limit_down_codes=limit_down_codes,
    )
    low_mid = _bucket_metrics(
        low_mid_rows,
        limit_up_codes=limit_up_codes,
        limit_down_codes=limit_down_codes,
    )

    for label, metrics in (
        ("全市场", market),
        ("低价股", low_price),
        ("≤5元", very_low),
        ("5～10元", low_mid),
    ):
        coverage_ratio = metrics.get("amount_coverage_ratio")
        if (
            metrics.get("sample_count")
            and (
                coverage_ratio is None
                or coverage_ratio < MIN_AMOUNT_COVERAGE_RATIO
            )
        ):
            gaps.append(
                f"{label}成交额字段覆盖不足:"
                f"{metrics.get('amount_valid_count', 0)}/{metrics.get('sample_count', 0)}"
            )

    market_median = market.get("pct_chg_median")
    low_median = low_price.get("pct_chg_median")
    low_price["median_excess_vs_market_pp"] = (
        round(low_median - market_median, 2)
        if low_median is not None and market_median is not None
        else None
    )
    market_amount = market.get("amount_billion")
    low_amount = low_price.get("amount_billion")
    low_price["amount_share_pct"] = (
        round(low_amount / market_amount * 100, 2)
        if market_amount and low_amount is not None
        else None
    )
    base["coverage"].update({
        "eligible_market_count": len(eligible_rows),
        "excluded_st_count": excluded_st_count,
        "excluded_b_share_count": excluded_b_share_count,
        "amount_valid_count": market.get("amount_valid_count", 0),
        "amount_coverage_ratio": market.get("amount_coverage_ratio"),
        "minimum_amount_coverage_ratio": MIN_AMOUNT_COVERAGE_RATIO,
    })
    return {
        **base,
        "status": "partial" if gaps else "complete",
        "market_benchmark": market,
        "low_price": low_price,
        "bands": [
            {
                "key": "price_le_5",
                "label": "≤5元",
                "min_exclusive": None,
                "max_inclusive": VERY_LOW_PRICE_MAX_YUAN,
                **very_low,
            },
            {
                "key": "price_5_to_10",
                "label": "5～10元",
                "min_exclusive": VERY_LOW_PRICE_MAX_YUAN,
                "max_inclusive": LOW_PRICE_MAX_YUAN,
                **low_mid,
            },
        ],
    }


def collect_low_price_effect(
    registry,
    trade_date: str,
    *,
    stock_st_result: Any,
    limit_up_section: Any,
    limit_down_section: Any,
) -> dict:
    """从 provider 拉取全市场日线，并复用盘后已取得的 ST/涨跌停事实。"""
    try:
        quote_result = registry.call("get_market_daily_quotes", trade_date)
    except Exception as exc:  # noqa: BLE001 - 外部源异常归一为状态，不阻断盘后主链。
        return {
            "status": "source_failed",
            "trade_date": trade_date,
            "error": f"全市场日线调用异常:{exc}",
            "gaps": [],
        }
    quote_rows = getattr(quote_result, "data", None)
    if not getattr(quote_result, "success", False) or not isinstance(quote_rows, list) or not quote_rows:
        return {
            "status": "source_failed",
            "trade_date": trade_date,
            "error": str(getattr(quote_result, "error", "") or "全市场日线为空"),
            "source": {"quotes": str(getattr(quote_result, "source", "") or "")},
            "gaps": [],
        }

    st_rows = getattr(stock_st_result, "data", None)
    if (
        not getattr(stock_st_result, "success", False)
        or not isinstance(st_rows, list)
        or not st_rows
    ):
        return {
            "status": "source_failed",
            "trade_date": trade_date,
            "error": str(getattr(stock_st_result, "error", "") or "ST/退市名单为空"),
            "source": {
                "quotes": str(getattr(quote_result, "source", "") or ""),
                "st": str(getattr(stock_st_result, "source", "") or ""),
            },
            "gaps": [],
        }

    return calculate_low_price_effect(
        quote_rows,
        st_rows,
        trade_date,
        quote_source=str(getattr(quote_result, "source", "") or ""),
        st_source=str(getattr(stock_st_result, "source", "") or ""),
        limit_up_section=limit_up_section,
        limit_down_section=limit_down_section,
    )
