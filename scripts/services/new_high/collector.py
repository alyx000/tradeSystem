from __future__ import annotations

from .constants import UNCLASSIFIED


def collect_daily_inputs(registry, date: str) -> dict:
    quote_res = registry.call("get_market_daily_quotes", date)
    if not quote_res.success or not quote_res.data:
        return {
            "status": "source_failed",
            "failed_source": "get_market_daily_quotes",
            "error": quote_res.error,
            "quote_source": quote_res.source,
        }

    adj_res = registry.call("get_adj_factor", date)
    if not adj_res.success or not adj_res.data:
        return {
            "status": "source_failed",
            "failed_source": "get_adj_factor",
            "error": adj_res.error,
            "adj_factor_source": adj_res.source,
        }

    sw_res = registry.call("get_stock_sw_industry_map")
    if not sw_res.success or not sw_res.data:
        return {
            "status": "source_failed",
            "failed_source": "get_stock_sw_industry_map",
            "error": sw_res.error or "empty industry map",
            "industry_source": sw_res.source,
        }
    sw_map = sw_res.data
    adj_map = {}
    duplicate_adj_factor_count = 0
    invalid_adj_factor_code_count = 0
    for item in adj_res.data:
        code = item.get("ts_code") or item.get("code")
        if not code:
            invalid_adj_factor_code_count += 1
            continue
        if code in adj_map:
            duplicate_adj_factor_count += 1
            continue
        adj_map[code] = item.get("adj_factor")

    quote_by_code = {}
    duplicate_quote_count = 0
    invalid_quote_code_count = 0
    for quote in quote_res.data:
        code = quote.get("code") or quote.get("ts_code")
        if not code:
            invalid_quote_code_count += 1
            continue
        if code in quote_by_code:
            duplicate_quote_count += 1
            continue
        quote_by_code[code] = quote

    rows = []
    adj_missing = 0
    industry_mapped_count = 0
    for code, q in quote_by_code.items():
        entry = sw_map.get(code) or {}
        if entry.get("sw_l2"):
            industry_mapped_count += 1
        adj_factor = adj_map.get(code)
        if adj_factor is None:
            adj_missing += 1
        rows.append({
            "code": code,
            "name": entry.get("name") or q.get("name") or "",
            "industry": entry.get("sw_l2") or UNCLASSIFIED,
            "high": q.get("high"),
            "pct_chg": q.get("pct_chg"),
            "amount": q.get("amount"),
            "adj_factor": adj_factor,
        })

    return {
        "status": "ok",
        "rows": rows,
        "source": {
            "quote_source": quote_res.source,
            "adj_factor_source": adj_res.source,
            "industry_source": sw_res.source if sw_res.success else f"sw_failed:{sw_res.error}",
            "quote_count": len(quote_res.data),
            "quote_raw_count": len(quote_res.data),
            "quote_unique_count": len(quote_by_code),
            "duplicate_quote_count": duplicate_quote_count,
            "invalid_quote_code_count": invalid_quote_code_count,
            "adj_factor_count": len(adj_map),
            "adj_factor_raw_count": len(adj_res.data),
            "adj_factor_unique_count": len(adj_map),
            "duplicate_adj_factor_count": duplicate_adj_factor_count,
            "invalid_adj_factor_code_count": invalid_adj_factor_code_count,
            "adj_factor_missing": adj_missing,
            "industry_mapped_count": industry_mapped_count,
        },
    }
