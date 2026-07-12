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
    sw_map = sw_res.data if sw_res.success and sw_res.data else {}
    adj_map = {
        (x.get("ts_code") or x.get("code")): x.get("adj_factor")
        for x in adj_res.data
        if x.get("ts_code") or x.get("code")
    }

    rows = []
    adj_missing = 0
    for q in quote_res.data:
        code = q.get("code") or q.get("ts_code")
        entry = sw_map.get(code) or {}
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
            "adj_factor_count": len(adj_map),
            "adj_factor_missing": adj_missing,
        },
    }
