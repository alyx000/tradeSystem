"""情绪核心生命周期监控编排。"""
from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from services.emotion_leader import constants as C
from services.emotion_leader.history import discover_lifecycles, load_history
from services.emotion_leader.metrics import fetch_metrics
from services.emotion_leader.state import plan_incremental_refresh


def _industry_map(registry) -> tuple[dict, str | None, str]:
    try:
        result = registry.call("get_stock_sw_industry_map")
    except Exception as exc:  # noqa: BLE001
        return {}, f"申万行业调用异常:{exc}", ""
    data = getattr(result, "data", None)
    if not getattr(result, "success", False) or not isinstance(data, dict) or not data:
        return {}, str(getattr(result, "error", "") or "申万行业映射不可得"), str(getattr(result, "source", "") or "")
    normalized = {str(code).split(".")[0]: value for code, value in data.items() if isinstance(value, dict)}
    return normalized, None, str(getattr(result, "source", "") or "")


def _median(rows: list[dict], key: str) -> float | None:
    values = [row.get(key) for row in rows if isinstance(row.get(key), (int, float))]
    return round(statistics.median(values), 2) if values else None


def _age_in_trade_days(trade_dates: list[str], since: str | None, target: str) -> int | None:
    if not since or since not in trade_dates or target not in trade_dates:
        return None
    return max(0, trade_dates.index(target) - trade_dates.index(since))


def _current_state(item: dict, up_codes: set[str], down_codes: set[str]) -> str:
    if item["code"] in up_codes:
        return "涨停"
    if item["code"] in down_codes:
        return "跌停"
    pct = item.get("today_pct_chg")
    if not isinstance(pct, (int, float)):
        return "未计算"
    if pct >= 5:
        return "上涨"
    if pct <= -5:
        return "明显回撤"
    return "震荡"


def run_daily(
    conn,
    registry,
    target_date: str,
    *,
    lookback_days: int = C.DEFAULT_LOOKBACK_DAYS,
    previous_report: dict | None = None,
    full_refresh: bool = False,
) -> dict:
    history = load_history(conn, target_date, lookback_days=lookback_days)
    base = {
        "date": target_date,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "coverage": history["coverage"],
        "missing_dates": history["missing_dates"],
        "source_errors": list(history["errors"]),
        "definition": "非ST二连板自动发现；三板或当日连板高度前二自动晋级；复盘第5步人工核心合并",
        "fact_source": "sqlite:daily_market.raw_data + provider:get_stock_daily_range/get_stock_adj_factor_range",
    }
    if not history["target_ok"]:
        return {
            **base,
            "status": "source_failed",
            "active": [],
            "archived": [],
            "new_candidates": [],
            "promoted_today": [],
            "height_breakthrough": {
                "status": "missing_data",
                "source_status": "failed",
                "as_of": target_date,
                "lookback_open_days": C.HEIGHT_BREAKTHROUGH_LOOKBACK_OPEN_DAYS,
                "previous_window_start": "",
                "previous_window_end": "",
                "current_max_height": None,
                "previous_max_height": None,
                "leaders": [],
                "reason": "目标日涨跌停事实不完整",
            },
            "summary": {},
        }

    discovered = discover_lifecycles(conn, history)
    height_breakthrough = discovered["height_breakthrough"]
    if height_breakthrough.get("status") == "missing_data":
        reason = str(height_breakthrough.get("reason") or "高度节点证据不完整")
        base["source_errors"].append(f"情绪高度节点:{reason}")
    promoted = discovered["promoted"]
    refresh_items, cached_archived, refresh = plan_incremental_refresh(
        promoted,
        previous_report,
        discovered["current_limit_up_codes"],
        target_date,
        full_refresh=full_refresh,
    )
    base["refresh"] = refresh
    sw_map, sw_error, sw_source = _industry_map(registry)
    if sw_error:
        base["source_errors"].append(sw_error)

    def _load(item: dict) -> tuple[dict, dict]:
        return item, fetch_metrics(registry, item, target_date)

    enriched: list[dict] = []
    if refresh_items:
        with ThreadPoolExecutor(max_workers=C.FETCH_WORKERS, thread_name_prefix="emotion-leader") as executor:
            for item, metric in executor.map(_load, refresh_items):
                row = {**item, **metric}
                row["metric_as_of"] = target_date
                sw = sw_map.get(item["code"].split(".")[0], {})
                sw_l2 = str(sw.get("sw_l2") or "").strip()
                row["industry"] = item.get("manual_sector") or sw_l2 or item.get("limit_industry") or "未分类"
                row["industry_source"] = (
                    "manual_step5" if item.get("manual_sector")
                    else sw_source if sw_l2
                    else "limit_list" if item.get("limit_industry")
                    else "missing"
                )
                if item.get("manual_phase") in C.MANUAL_WAVE_LABELS:
                    row["wave_label"] = item["manual_phase"]
                    row["wave_source"] = "manual_step5"
                else:
                    row["wave_source"] = "mechanical_judgment"
                row["days_since_limit_up"] = _age_in_trade_days(
                    discovered["trade_dates"], item.get("last_limit_up_date"), target_date)
                row["current_state"] = _current_state(
                    row, discovered["current_limit_up_codes"], discovered["current_down_codes"])
                row["archived"] = bool(
                    not item.get("manual_confirmed")
                    and isinstance(row.get("days_since_limit_up"), int)
                    and row["days_since_limit_up"] > C.ARCHIVE_AFTER_TRADE_DAYS
                    and isinstance(row.get("distance_from_peak_pct"), (int, float))
                    and row["distance_from_peak_pct"] <= C.ARCHIVE_DRAWDOWN_PCT
                )
                enriched.append(row)

    active = [row for row in enriched if not row.get("archived")]
    archived = [row for row in enriched if row.get("archived")] + cached_archived
    active.sort(key=lambda row: (
        row["code"] in discovered["current_limit_up_codes"],
        row.get("new_peak_today") is True,
        row.get("max_gain_pct") if isinstance(row.get("max_gain_pct"), (int, float)) else -10_000,
        row["code"],
    ), reverse=True)
    archived.sort(key=lambda row: (row.get("last_limit_up_date") or "", row["code"]), reverse=True)

    metric_errors = [
        f"{row['code']}:{row.get('metric_error')}"
        for row in enriched if row.get("metric_status") != "ok"
    ]
    base["source_errors"].extend(metric_errors)
    candidates_today = [
        item for item in discovered["candidates"]
        if item.get("candidate_date") == target_date
    ]
    promoted_today = [row for row in active if row.get("promoted_date") == target_date]
    status = "partial" if base["source_errors"] or history["missing_dates"] else "ok"
    return {
        **base,
        "status": status,
        "active": active,
        "archived": archived,
        "new_candidates": candidates_today,
        "promoted_today": promoted_today,
        "height_breakthrough": height_breakthrough,
        "all_candidate_count": len(discovered["candidates"]),
        "summary": {
            "active_count": len(active),
            "archived_count": len(archived),
            "today_limit_up_count": sum(row["code"] in discovered["current_limit_up_codes"] for row in active),
            "today_limit_down_count": sum(row["code"] in discovered["current_down_codes"] for row in active),
            "new_peak_count": sum(row.get("new_peak_today") is True for row in active),
            "height_breakthrough_count": len(
                height_breakthrough.get("leaders") or []
            ),
            "interval_gain_median_pct": _median(active, "interval_gain_pct"),
            "distance_from_peak_median_pct": _median(active, "distance_from_peak_pct"),
        },
    }
