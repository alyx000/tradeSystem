"""持仓跟踪信号聚合。"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any

from api.market_enrich import enrich_daily_market_row
from db import queries as Q
from db.dual_write import _normalize_stock_code_for_match, parse_post_market_envelope


def _date_digits(date_text: str) -> str:
    return str(date_text or "").replace("-", "").strip()


def _section_rows(section: Any) -> list[dict[str, Any]]:
    if not section:
        return []
    if isinstance(section, list):
        return [row for row in section if isinstance(row, dict)]
    if isinstance(section, dict):
        data = section.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_sector(holding_sector: str | None, candidate: str | None) -> bool:
    left = str(holding_sector or "").strip().lower()
    right = str(candidate or "").strip().lower()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _normalized_holdings(holdings: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in holdings:
        if not isinstance(item, dict):
            continue
        stock_code = str(item.get("stock_code") or item.get("code") or "").strip()
        stock_name = str(item.get("stock_name") or item.get("name") or "").strip()
        if not stock_code:
            continue
        out.append({
            **item,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "sector": item.get("sector"),
            "entry_price": item.get("entry_price", item.get("cost")),
            "current_price": item.get("current_price"),
            "shares": item.get("shares"),
            "status": item.get("status", "active"),
        })
    return out


def _extract_holdings_detail_map(envelope: dict | None) -> dict[str, dict[str, Any]]:
    if not envelope or not isinstance(envelope, dict):
        return {}
    rows = envelope.get("holdings_data")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or "error" in row:
            continue
        norm = _normalize_stock_code_for_match(row.get("code"))
        if norm:
            out[norm] = row
    return out


def _merge_snapshot_fallback(
    detail_map: dict[str, dict[str, Any]],
    snapshot_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not snapshot_map:
        return detail_map
    out = dict(detail_map)
    merge_keys = (
        "code",
        "name",
        "close",
        "pnl_pct",
        "turnover_rate",
        "ma5",
        "ma10",
        "ma20",
        "volume_vs_ma5",
    )
    for norm, row in snapshot_map.items():
        snapshot_detail = {
            "code": row.get("stock_code"),
            "name": row.get("stock_name"),
            "close": row.get("close"),
            "pnl_pct": row.get("pnl_pct"),
            "turnover_rate": row.get("turnover_rate"),
            "ma5": row.get("ma5"),
            "ma10": row.get("ma10"),
            "ma20": row.get("ma20"),
            "volume_vs_ma5": row.get("volume_vs_ma5"),
        }
        if norm not in out:
            out[norm] = snapshot_detail
            continue
        merged = dict(out[norm])
        for key in merge_keys:
            if merged.get(key) in (None, ""):
                merged[key] = snapshot_detail.get(key)
        out[norm] = merged
    return out


def _get_market_basis(conn, date_str: str, market_row: dict | None = None) -> tuple[dict | None, dict | None]:
    raw_market = dict(market_row) if market_row else Q.get_daily_market(conn, date_str)
    if raw_market is None:
        raw_market = Q.get_prev_daily_market(conn, date_str)
    if raw_market is None:
        return None, None
    envelope = parse_post_market_envelope(raw_market.get("raw_data"))
    enriched = dict(raw_market)
    enrich_daily_market_row(enriched)
    return enriched, envelope


def _load_limit_price_map(conn, date_str: str) -> dict[str, dict[str, Any]]:
    rows = Q.get_latest_raw_interface_rows(conn, interface_name="stk_limit", biz_date=date_str)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if not norm:
            continue
        out[norm] = {
            "up_limit": row.get("up_limit"),
            "down_limit": row.get("down_limit"),
            "pre_close": row.get("pre_close"),
        }
    return out


def _load_st_set(conn, date_str: str) -> set[str]:
    rows = Q.get_latest_raw_interface_rows(conn, interface_name="stock_st", biz_date=date_str)
    out: set[str] = set()
    for row in rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if norm:
            out.add(norm)
    return out


def _load_share_float_map(conn, date_str: str) -> dict[str, list[dict[str, Any]]]:
    rows = Q.get_latest_raw_interface_rows(conn, interface_name="share_float", biz_date=date_str)
    today_digits = _date_digits(date_str)
    future_cutoff = (_date.fromisoformat(date_str) + timedelta(days=30)).strftime("%Y%m%d")
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if not norm:
            continue
        float_date = str(row.get("float_date") or "")
        if float_date and not (today_digits <= float_date <= future_cutoff):
            continue
        out.setdefault(norm, []).append({
            "ann_date": row.get("ann_date"),
            "float_date": row.get("float_date"),
            "shares": row.get("float_share"),
        })
    for value in out.values():
        value.sort(key=lambda item: (str(item.get("float_date") or ""), str(item.get("ann_date") or "")))
    return out


def _load_announcement_maps(conn, date_str: str, holdings: list[dict]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    codes = {_normalize_stock_code_for_match(item.get("stock_code")) for item in holdings}
    codes.discard("")
    if not codes:
        return {}, {}
    end = _date.fromisoformat(date_str)
    start = (end - timedelta(days=7)).isoformat()
    start_digits = _date_digits(start)
    end_digits = _date_digits(date_str)

    ann_rows = Q.list_raw_interface_rows(
        conn,
        interface_name="anns_d",
        biz_date_from=start,
        biz_date_to=date_str,
    )
    announcements: dict[str, list[dict[str, Any]]] = {}
    for row in ann_rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if norm not in codes:
            continue
        ann_date = str(row.get("ann_date") or "")
        if ann_date and not (start_digits <= ann_date <= end_digits):
            continue
        announcements.setdefault(norm, []).append({
            "ann_date": row.get("ann_date"),
            "title": row.get("title"),
        })
    for value in announcements.values():
        value.sort(key=lambda item: (str(item.get("ann_date") or ""), str(item.get("title") or "")), reverse=True)

    disclosure_rows = Q.get_latest_raw_interface_rows(
        conn,
        interface_name="disclosure_date",
        biz_date=date_str,
    )
    disclosure_dates: dict[str, list[dict[str, Any]]] = {}
    for row in disclosure_rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if norm not in codes:
            continue
        ann_date = str(row.get("ann_date") or row.get("pre_date") or "")
        if ann_date and ann_date < end_digits:
            continue
        disclosure_dates.setdefault(norm, []).append({
            "ann_date": row.get("ann_date") or row.get("pre_date"),
            "report_end": row.get("report_end") or row.get("end_date"),
        })
    for value in disclosure_dates.values():
        value.sort(key=lambda item: (str(item.get("ann_date") or ""), str(item.get("report_end") or "")))
    return announcements, disclosure_dates


def _find_theme_signal(sector: str | None, themes: list[dict[str, Any]], strongest_rows: list[dict[str, Any]], ths_rows: list[dict[str, Any]], dc_rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched_theme = next((row for row in themes if _match_sector(sector, row.get("theme_name"))), None)
    matched_strongest = next((row for row in strongest_rows if _match_sector(sector, row.get("name"))), None)
    matched_ths = next((row for row in ths_rows if _match_sector(sector, row.get("name") or row.get("industry"))), None)
    matched_dc = next((row for row in dc_rows if _match_sector(sector, row.get("name"))), None)
    return {
        "is_main_theme": matched_theme is not None,
        "main_theme_name": matched_theme.get("theme_name") if matched_theme else None,
        "is_strongest_sector": matched_strongest is not None,
        "strongest_sector_name": matched_strongest.get("name") if matched_strongest else None,
        "sector_flow_confirmed": matched_ths is not None or matched_dc is not None,
        "sector_flow_source": "ths" if matched_ths is not None else ("dc" if matched_dc is not None else None),
    }


def _build_risk_flags(date_str: str, *, price_snapshot: dict[str, Any], technical_signals: dict[str, Any], theme_signals: dict[str, Any], event_signals: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    base_date = _date.fromisoformat(date_str)

    if event_signals.get("has_disclosure_plan"):
        for item in event_signals.get("disclosure_dates", []):
            ann_date = str(item.get("ann_date") or "")
            if len(ann_date) == 8:
                event_date = _date.fromisoformat(f"{ann_date[:4]}-{ann_date[4:6]}-{ann_date[6:]}")
                delta = (event_date - base_date).days
                if 0 <= delta <= 3:
                    flags.append({
                        "level": "high",
                        "label": "财报临近",
                        "reason": f"{ann_date} 有披露计划",
                    })
                    break

    if event_signals.get("is_st"):
        flags.append({
            "level": "high",
            "label": "ST",
            "reason": "当前股票处于 ST 名单内",
        })

    current_price = _to_float(price_snapshot.get("current_price"))
    down_limit = _to_float(price_snapshot.get("down_limit"))
    if current_price is not None and down_limit is not None and down_limit > 0 and current_price <= down_limit * 1.01:
        flags.append({
            "level": "high",
            "label": "临近跌停价",
            "reason": f"现价接近跌停边界 {down_limit:.2f}",
        })

    if not theme_signals.get("is_main_theme") and (technical_signals.get("sector_change_pct") or 0) < 0:
        flags.append({
            "level": "medium",
            "label": "非主线承压",
            "reason": "非主线且所属板块当日走弱",
        })

    if technical_signals.get("above_ma5") is False:
        flags.append({
            "level": "medium",
            "label": "跌破 MA5",
            "reason": "现价位于 MA5 下方",
        })

    if technical_signals.get("volume_vs_ma5") == "以下":
        flags.append({
            "level": "medium",
            "label": "量弱",
            "reason": "成交量低于 5 日均量",
        })

    turnover_rate = _to_float(technical_signals.get("turnover_rate"))
    if turnover_rate is not None and turnover_rate <= 1.0:
        flags.append({
            "level": "medium",
            "label": "换手偏低",
            "reason": f"换手率仅 {turnover_rate:.2f}%",
        })

    if not flags and theme_signals.get("is_main_theme") and theme_signals.get("sector_flow_confirmed") and technical_signals.get("above_ma5") is True:
        flags.append({
            "level": "low",
            "label": "主线内",
            "reason": "主线内且资金确认、技术位置正常",
        })
    return flags


def build_holding_signals(
    conn,
    date_str: str,
    *,
    holdings: list[dict] | None = None,
    market_row: dict | None = None,
    limit_price_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_holdings = _normalized_holdings(holdings or Q.get_holdings(conn, status="active"))
    market, envelope = _get_market_basis(conn, date_str, market_row=market_row)
    holdings_detail_map = _merge_snapshot_fallback(
        _extract_holdings_detail_map(envelope),
        Q.get_latest_holding_quote_snapshots(conn, date_str),
    )
    limit_map = _load_limit_price_map(conn, date_str)
    if limit_price_overrides:
        limit_map.update(limit_price_overrides)
    announcements, disclosure_dates = _load_announcement_maps(conn, date_str, normalized_holdings)
    st_set = _load_st_set(conn, date_str)
    share_float_map = _load_share_float_map(conn, date_str)
    task_map = Q.get_latest_open_holding_tasks(conn, date_str)

    themes = Q.get_active_themes(conn)
    strongest_rows = _section_rows((market or {}).get("limit_cpt_list"))
    strongest_rows = sorted(strongest_rows, key=lambda item: (_to_float(item.get("rank")) or 9999, -(_to_float(item.get("up_nums")) or 0)))
    ths_rows = sorted(_section_rows((market or {}).get("sector_moneyflow_ths")), key=lambda item: -(_to_float(item.get("net_amount")) or 0))
    dc_rows = sorted(_section_rows((market or {}).get("sector_moneyflow_dc")), key=lambda item: -(_to_float(item.get("net_amount")) or 0))

    items: list[dict[str, Any]] = []
    for holding in normalized_holdings:
        code = holding["stock_code"]
        norm = _normalize_stock_code_for_match(code)
        detail = holdings_detail_map.get(norm, {})
        limit_prices = limit_map.get(norm, {})
        current_price = _to_float(detail.get("close")) or _to_float(holding.get("current_price"))
        entry_price = _to_float(holding.get("entry_price"))
        pnl_pct = _to_float(detail.get("pnl_pct"))
        if pnl_pct is None and current_price is not None and entry_price not in (None, 0):
            pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)

        technical_signals = {
            "ma5": _to_float(detail.get("ma5")),
            "ma10": _to_float(detail.get("ma10")),
            "ma20": _to_float(detail.get("ma20")),
            "above_ma5": None,
            "above_ma10": None,
            "above_ma20": None,
            "volume_vs_ma5": detail.get("volume_vs_ma5") if detail.get("volume_vs_ma5") in ("以上", "以下") else None,
            "turnover_rate": _to_float(detail.get("turnover_rate")),
            "turnover_status": None,
            "sector_change_pct": _to_float(detail.get("sector_change_pct")),
        }
        if technical_signals["turnover_rate"] is not None:
            turnover_rate = technical_signals["turnover_rate"]
            technical_signals["turnover_status"] = "活跃" if turnover_rate >= 5 else ("偏低" if turnover_rate <= 1 else "正常")
        for key in ("ma5", "ma10", "ma20"):
            ma_value = technical_signals[key]
            technical_signals[f"above_{key}"] = current_price > ma_value if current_price is not None and ma_value is not None else None

        theme_signals = _find_theme_signal(
            holding.get("sector"),
            themes,
            strongest_rows,
            ths_rows,
            dc_rows,
        )
        event_signals = {
            "has_recent_announcement": bool(announcements.get(norm)),
            "recent_announcements": (announcements.get(norm) or [])[:3],
            "has_disclosure_plan": bool(disclosure_dates.get(norm)),
            "disclosure_dates": (disclosure_dates.get(norm) or [])[:5],
            "is_st": norm in st_set,
            "share_float_upcoming": (share_float_map.get(norm) or [])[:3],
        }
        price_snapshot = {
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "up_limit": _to_float(limit_prices.get("up_limit")),
            "down_limit": _to_float(limit_prices.get("down_limit")),
            "pre_close": _to_float(limit_prices.get("pre_close")),
        }
        risk_flags = _build_risk_flags(
            date_str,
            price_snapshot=price_snapshot,
            technical_signals=technical_signals,
            theme_signals=theme_signals,
            event_signals=event_signals,
        )
        items.append({
            "stock_code": code,
            "stock_name": holding.get("stock_name"),
            "sector": holding.get("sector"),
            "price_snapshot": price_snapshot,
            "technical_signals": technical_signals,
            "theme_signals": theme_signals,
            "event_signals": event_signals,
            "latest_task": task_map.get(norm),
            "risk_flags": risk_flags,
        })

    return {
        "date": date_str,
        "items": items,
    }
