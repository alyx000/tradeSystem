"""主线板块串阳首阴扫描。

口径：昨日以前连续 >=5 根阳线，串阳段无涨停、最大单日涨幅 <=7%，最近无涨停，
首阴日不处于中期高位，且出现第一根近期最大量阴线。
输出是盘后只读观察池候选，全部属于 [判断]，不写计划层。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from services.string_yang import constants as C
from services.string_yang import mainline
from services.volume_concentration.aggregator import UNCLASSIFIED
from utils import is_st_stock
from utils.price_limit import limit_pct_for


def bare_code(code: str) -> str:
    return str(code or "").split(".")[0].strip()


def _lookback_start(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=C.RANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def _is_yang(bar: dict) -> bool:
    return bar.get("close") is not None and bar.get("open") is not None and bar["close"] > bar["open"]


def _is_yin(bar: dict) -> bool:
    return bar.get("close") is not None and bar.get("open") is not None and bar["close"] < bar["open"]


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _price_ma_ratio(bars: list[dict]) -> float | None:
    recent = (bars or [])[-C.PRICE_MA_LOOKBACK_BARS:]
    if len(recent) < C.PRICE_MA_LOOKBACK_BARS:
        return None
    closes = [b.get("close") for b in recent]
    if any(v is None for v in closes):
        return None
    ma = sum(float(v) for v in closes) / len(closes)
    today_close = closes[-1]
    return float(today_close) / ma if ma else None


def _limit_threshold(code: str, name: str = "") -> float | None:
    pct = limit_pct_for(code, is_st=is_st_stock(name))
    return pct * C.LIMIT_DETECT_FACTOR if pct is not None else None


def _has_limit_up(bars: list[dict], code: str, name: str = "") -> bool:
    threshold = _limit_threshold(code, name)
    if threshold is None:
        return False
    return any((b.get("pct_chg") is not None and b["pct_chg"] >= threshold) for b in bars)


def _recent_bars_before_today(bars: list[dict]) -> list[dict]:
    if C.RECENT_LIMIT_LOOKBACK_DAYS <= 0:
        return []
    return (bars or [])[:-1][-C.RECENT_LIMIT_LOOKBACK_DAYS:]


def _consecutive_yang_before_today(bars: list[dict]) -> list[dict]:
    out: list[dict] = []
    for bar in reversed((bars or [])[:-1]):
        if not _is_yang(bar):
            break
        out.append(bar)
    return list(reversed(out))


def detect_setup(bars: list[dict], code: str, name: str = "") -> tuple[bool, dict]:
    """检测串阳首阴形态。

    bars 需按日期升序，最后一根为今日。只在今日已经出现阴线时返回 matched=True。
    """
    bars = bars or []
    if len(bars) < C.STRING_YANG_MIN_COUNT + 1:
        return False, {"reason": "insufficient_history", "insufficient_history": True}
    today = bars[-1]
    if not _is_yin(today):
        return False, {"reason": "today_not_yin", "insufficient_history": False}

    yang_bars = _consecutive_yang_before_today(bars)
    yang_count = len(yang_bars)
    if yang_count < C.STRING_YANG_MIN_COUNT:
        return False, {"reason": "yang_count_below_min", "yang_count": yang_count, "insufficient_history": False}

    pct_values = [b.get("pct_chg") for b in yang_bars]
    if any(p is None for p in pct_values):
        return False, {"reason": "pct_missing", "yang_count": yang_count, "insufficient_history": True}
    max_yang_pct = max(float(p) for p in pct_values)
    if max_yang_pct > C.MAX_YANG_PCT:
        return False, {
            "reason": "yang_pct_too_high",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "insufficient_history": False,
        }
    string_start_open = yang_bars[0].get("open")
    string_end_close = yang_bars[-1].get("close")
    string_total_pct = None
    if string_start_open and string_end_close:
        string_total_pct = (float(string_end_close) / float(string_start_open) - 1.0) * 100.0
    if string_total_pct is None:
        return False, {"reason": "price_missing", "yang_count": yang_count, "insufficient_history": True}
    price_ma_ratio = _price_ma_ratio(bars)
    if price_ma_ratio is not None and price_ma_ratio > C.MAX_PRICE_MA_RATIO:
        return False, {
            "reason": "price_position_too_high",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "string_total_pct": string_total_pct,
            "price_ma_ratio": price_ma_ratio,
            "price_ma_lookback_bars": C.PRICE_MA_LOOKBACK_BARS,
            "max_price_ma_ratio": C.MAX_PRICE_MA_RATIO,
            "insufficient_history": False,
        }
    if _has_limit_up(yang_bars, code, name):
        return False, {
            "reason": "limit_up_in_string",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "insufficient_history": False,
        }
    if _has_limit_up(_recent_bars_before_today(bars), code, name):
        return False, {
            "reason": "recent_limit_up",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "recent_limit_lookback_days": C.RECENT_LIMIT_LOOKBACK_DAYS,
            "insufficient_history": False,
        }

    prev_day_amount = bars[-2].get("amount")
    prev5_amounts = [b.get("amount") for b in bars[-6:-1]]
    prev5_amount = _avg([b.get("amount") for b in bars[-6:-1]])
    yang_amount = _avg([b.get("amount") for b in yang_bars])
    today_amount = today.get("amount")
    ratio_prev_day = (today_amount / prev_day_amount) if today_amount is not None and prev_day_amount else None
    ratio_prev5 = (today_amount / prev5_amount) if today_amount is not None and prev5_amount else None
    ratio_yang = (today_amount / yang_amount) if today_amount is not None and yang_amount else None
    prev5_valid_amounts = [a for a in prev5_amounts if a is not None]
    prev5_max_amount = max(prev5_valid_amounts) if len(prev5_valid_amounts) == min(5, len(bars) - 1) else None
    ratio_prev5_max = (today_amount / prev5_max_amount) if today_amount is not None and prev5_max_amount else None
    if ratio_prev_day is None or ratio_prev5_max is None:
        return False, {
            "reason": "amount_missing",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "insufficient_history": True,
        }
    if ratio_prev5_max <= 1.0:
        return False, {
            "reason": "yin_volume_not_recent_max",
            "yang_count": yang_count,
            "max_yang_pct": max_yang_pct,
            "today_amount_ratio_vs_prev_day": ratio_prev_day,
            "today_amount_ratio_vs_prev5": ratio_prev5,
            "today_amount_ratio_vs_prev5_max": ratio_prev5_max,
            "insufficient_history": False,
        }

    return True, {
        "reason": "matched",
        "yang_count": yang_count,
        "max_yang_pct": max_yang_pct,
        "string_total_pct": string_total_pct,
        "price_ma_ratio": price_ma_ratio,
        "today_pct_chg": today.get("pct_chg"),
        "today_amount": today_amount,
        "today_amount_ratio_vs_prev_day": ratio_prev_day,
        "today_amount_ratio_vs_prev5": ratio_prev5,
        "today_amount_ratio_vs_prev5_max": ratio_prev5_max,
        "today_amount_ratio_vs_yang": ratio_yang,
        "string_start_date": yang_bars[0].get("trade_date"),
        "string_end_date": yang_bars[-1].get("trade_date"),
        "insufficient_history": False,
    }


def _fetch_bars(registry, code: str, start: str, date: str) -> list[dict]:
    r = registry.call("get_stock_daily_range", code, start, date)
    return r.data if getattr(r, "success", False) and isinstance(r.data, list) else []


def _candidate_universe(
    sw_map: dict,
    main_sectors: set[str],
    main_concepts: set[str] | None = None,
    concept_map: dict[str, set[str]] | None = None,
) -> tuple[list[dict], int]:
    out: list[dict] = []
    st_or_delist = 0
    main_concepts = main_concepts or set()
    concept_map = concept_map or {}
    for raw_code, info in sorted(sw_map.items()):
        if not isinstance(info, dict):
            continue
        sw_l2 = info.get("sw_l2") or UNCLASSIFIED
        code = bare_code(raw_code)
        if not code:
            continue
        branch_concepts = sorted((concept_map.get(code) or set()) & main_concepts)
        if sw_l2 not in main_sectors and not branch_concepts:
            continue
        if is_st_stock(info.get("name", "")):
            st_or_delist += 1
            continue
        out.append({
            "code": code,
            "name": info.get("name", ""),
            "sw_l2": sw_l2,
            "branch_concepts": branch_concepts,
        })
    return out, st_or_delist


def _sort_key(candidate: dict) -> tuple[float, float, str]:
    ratio = candidate.get("amount_ratio_vs_prev5_max")
    amount = candidate.get("today_amount")
    return (float(ratio or 0.0), float(amount or 0.0), candidate["code"])


def run_daily(
    conn: sqlite3.Connection,
    registry,
    date: str,
    *,
    top_k: int = C.DEFAULT_TOP_K_SECTORS,
    top_concepts: int = C.DEFAULT_TOP_CONCEPTS,
    teacher_lookback_days: int = C.TEACHER_LOOKBACK_DAYS,
    use_llm: bool = False,
    llm_runner=None,
) -> dict:
    judgment = mainline.judge_mainline(
        conn,
        registry,
        date,
        top_k=top_k,
        top_concepts=top_concepts,
        teacher_lookback_days=teacher_lookback_days,
        use_llm=use_llm,
        llm_runner=llm_runner,
    )
    main_sectors = set(judgment.main_sectors)
    main_concepts = set(judgment.main_concepts)
    degraded = judgment.degraded
    if not main_sectors and not main_concepts:
        return {
            "status": "ok",
            "date": date,
            "main_sectors": [],
            "mainline": judgment.public_payload(),
            "main_sector_degraded": degraded,
            "candidates": [],
            "rejects": {"no_main_sector": 1},
            "data_errors": [],
            "source_errors": judgment.source_errors,
        }

    sw = registry.call("get_stock_sw_industry_map")
    if not (getattr(sw, "success", False) and isinstance(sw.data, dict)):
        return {
            "status": "source_failed",
            "date": date,
            "main_sectors": sorted(main_sectors),
            "mainline": judgment.public_payload(),
            "main_sector_degraded": degraded,
            "candidates": [],
            "rejects": {},
            "data_errors": [],
            "source_errors": judgment.source_errors + ["sw_map"],
        }

    start = _lookback_start(date)
    candidates: list[dict] = []
    data_errors: list[str] = []
    rejects = {
        "not_main_sector": 0,
        "bar_missing": 0,
        "today_not_yin": 0,
        "yang_count_below_min": 0,
        "yang_pct_too_high": 0,
        "price_position_too_high": 0,
        "limit_up_in_string": 0,
        "recent_limit_up": 0,
        "pct_missing": 0,
        "price_missing": 0,
        "amount_missing": 0,
        "yin_volume_not_recent_max": 0,
        "insufficient_history": 0,
        "st_or_delist": 0,
    }
    universe, st_or_delist = _candidate_universe(sw.data, main_sectors, main_concepts, judgment.stock_concept_map)
    rejects["st_or_delist"] = st_or_delist
    rejects["not_main_sector"] = max(0, len(sw.data) - len(universe) - st_or_delist)

    for item in universe:
        bars = _fetch_bars(registry, item["code"], start, date)
        if not bars or bars[-1].get("trade_date") != date:
            rejects["bar_missing"] += 1
            data_errors.append(item["code"])
            continue
        matched, detail = detect_setup(bars, item["code"], item.get("name", ""))
        if not matched:
            reason = detail.get("reason") or "insufficient_history"
            rejects[reason] = rejects.get(reason, 0) + 1
            continue
        candidates.append({
            **item,
            **detail,
            "amount_ratio_vs_prev_day": detail.get("today_amount_ratio_vs_prev_day"),
            "amount_ratio_vs_prev5": detail.get("today_amount_ratio_vs_prev5"),
            "amount_ratio_vs_prev5_max": detail.get("today_amount_ratio_vs_prev5_max"),
            "amount_ratio_vs_yang": detail.get("today_amount_ratio_vs_yang"),
        })

    candidates.sort(key=_sort_key, reverse=True)
    if universe and not candidates and rejects.get("bar_missing") == len(universe):
        return {
            "status": "source_failed",
            "date": date,
            "main_sectors": sorted(main_sectors),
            "mainline": judgment.public_payload(),
            "main_sector_degraded": degraded,
            "candidates": [],
            "rejects": rejects,
            "data_errors": data_errors,
            "source_errors": judgment.source_errors + ["stock_daily_range"],
        }
    return {
        "status": "ok",
        "date": date,
        "main_sectors": sorted(main_sectors),
        "mainline": judgment.public_payload(),
        "main_sector_degraded": degraded,
        "candidates": candidates,
        "rejects": rejects,
        "data_errors": data_errors,
        "source_errors": judgment.source_errors,
    }
