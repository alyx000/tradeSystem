"""tail-scan 筛选层（[事实]）：实时全市场快照 → 涨幅>7% ∩ 非ST ∩ 成交额>20亿 ∩ 未涨停。

无状态：不建池不落库。仅依赖实时快照（唯一盘中源），全部字段为 T 日实时。
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re

from services.tail_scan.constants import (
    IPO_NO_LIMIT_MAX_NATURAL_DAYS,
    IPO_NO_LIMIT_OPEN_DAYS,
)
from utils import is_st_stock
from utils.price_limit import compute_limit_prices


def _amount_yi(amount) -> float | None:
    try:
        return float(amount) / 1e8
    except (TypeError, ValueError):
        return None


def _is_limit_up(code: str, name: str, price: float, pre_close: float) -> bool:
    """按板块制度与交易所分币舍入后的正式涨停价判定。"""
    up_price = compute_limit_prices(pre_close, code, name).get("up_limit")
    if up_price is None:
        return False
    try:
        return float(price) >= float(up_price)
    except (TypeError, ValueError):
        return False


def _close_pos(price, high, low) -> float | None:
    try:
        span = high - low
        return round((price - low) / span, 4) if span > 0 else None
    except (TypeError, ValueError):
        return None


def _amplitude(high, low, pre_close) -> float | None:
    try:
        return round((high - low) / pre_close * 100, 2) if pre_close else None
    except (TypeError, ValueError):
        return None


def filter_quotes(
    quotes: list[dict],
    *,
    min_pct: float,
    min_amount_yi: float,
    no_limit_codes: set[str] | None = None,
) -> list[dict]:
    """四条件筛选 + 尾盘强度快照。

    ``no_limit_codes`` 仅用于已经由上市日与交易日历确认的无涨跌幅限制日；
    这些股票即使触及普通板块理论涨停价，也不属于“已涨停”。
    """
    no_limit_codes = no_limit_codes or set()
    out = []
    for q in quotes or []:
        pct = q.get("pct_chg")
        amt_yi = _amount_yi(q.get("amount"))
        name = q.get("name", "")
        if pct is None or amt_yi is None:
            continue
        if pct <= min_pct or amt_yi <= min_amount_yi:
            continue
        if is_st_stock(name):
            continue
        code, price = q.get("code", ""), q.get("price")
        high, low, pre = q.get("high"), q.get("low"), q.get("pre_close")
        is_limit_up = _is_limit_up(code, name, price, pre)
        if is_limit_up and code not in no_limit_codes:
            continue
        out.append({
            "code": code, "name": name, "price": price, "pct_chg": pct,
            "amount_yi": round(amt_yi, 2), "open": q.get("open"),
            "high": high, "low": low, "pre_close": pre,
            "is_limit_up": False,
            "close_pos": _close_pos(price, high, low),
            "amplitude": _amplitude(high, low, pre),
        })
    return out


def _quote_ok(r) -> bool:
    return getattr(r, "success", False) and isinstance(r.data, list)


def _norm_date(raw) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 8:
        return None
    value = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _stock_universe(registry, date: str) -> tuple[list[str], dict[str, str]]:
    r = registry.call("get_stock_basic_list", date)
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        return [], {}
    codes: list[str] = []
    listing_dates: dict[str, str] = {}
    for row in r.data:
        code = str(row.get("ts_code") or "").strip().upper()
        if not code:
            continue
        codes.append(code)
        list_date = _norm_date(row.get("list_date"))
        if list_date:
            listing_dates[code] = list_date
    return codes, listing_dates


def _possible_no_limit_codes(listing_dates: dict[str, str], target_date: str) -> set[str]:
    """只保留近 30 自然日上市代码，覆盖前五开放日与法定长假。"""
    target = datetime.strptime(target_date, "%Y-%m-%d")
    return {
        code
        for code, list_date in listing_dates.items()
        if 0
        <= (target - datetime.strptime(list_date, "%Y-%m-%d")).days
        <= IPO_NO_LIMIT_MAX_NATURAL_DAYS
    }


def _load_no_limit_codes(
    registry,
    target_date: str,
    listing_dates: dict[str, str],
    codes: set[str],
) -> tuple[set[str], str | None]:
    """用完整交易日历确认上市后前五个开放日；缺一天也拒绝猜测。"""
    years = {int(target_date[:4])}
    years.update(int(listing_dates[code][:4]) for code in codes)
    states: dict[str, bool] = {}
    required_start = min(listing_dates[code] for code in codes)
    for year in sorted(years):
        request_date = target_date if year == int(target_date[:4]) else f"{year}-12-31"
        result = registry.call("get_trade_calendar", request_date)
        if not getattr(result, "success", False) or not isinstance(result.data, list):
            return set(), f"交易日历获取失败（get_trade_calendar:{year}）"
        for row in result.data:
            if not isinstance(row, dict):
                return set(), f"交易日历含非法记录（get_trade_calendar:{year}）"
            cal_date = _norm_date(row.get("cal_date") or row.get("trade_date"))
            if not cal_date:
                return set(), f"交易日历含非法日期（get_trade_calendar:{year}）"
            if not required_start <= cal_date <= target_date:
                continue
            try:
                flag = int(row.get("is_open"))
            except (TypeError, ValueError):
                return set(), f"交易日历含非法开闭市状态（{cal_date}）"
            if flag not in (0, 1):
                return set(), f"交易日历含非法开闭市状态（{cal_date}）"
            is_open = flag == 1
            if cal_date in states and states[cal_date] is not is_open:
                return set(), f"交易日历日期状态冲突（{cal_date}）"
            states[cal_date] = is_open

    expected_dates: set[str] = set()
    cursor = datetime.strptime(required_start, "%Y-%m-%d")
    end = datetime.strptime(target_date, "%Y-%m-%d")
    while cursor <= end:
        expected_dates.add(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    if set(states) != expected_dates:
        return set(), (
            "交易日历自然日覆盖不完整"
            f"（observed={len(states)},expected={len(expected_dates)}）"
        )

    ordered_open_dates = sorted(d for d, is_open in states.items() if is_open)
    no_limit_codes: set[str] = set()
    for code in codes:
        list_date = listing_dates[code]
        if not states.get(list_date, False):
            return set(), f"上市日未标记为开放日（{code}:{list_date}）"
        first_open_dates = [d for d in ordered_open_dates if d >= list_date][
            :IPO_NO_LIMIT_OPEN_DAYS
        ]
        if target_date in first_open_dates:
            no_limit_codes.add(code)
    return no_limit_codes, None


def scan(registry, date: str, *, min_pct: float, min_amount_yi: float) -> dict:
    """编排：全市场码 → 实时快照 → 四条件筛选。码源或行情源失败 → source_failed。"""
    codes, listing_dates = _stock_universe(registry, date)
    if not codes:
        return {"status": "source_failed", "quote_date": date, "quote_time": "",
                "candidates": [], "scanned": 0, "matched": 0,
                "error": "全市场代码清单获取失败（get_stock_basic_list）"}
    # sina _fetch_raw 任一分片失败即整批 error（~7 片，单点脆弱）。14:40 单次触发下补一次重试，
    # 仍失败才 source_failed（launchd 单次触发无二次机会，重试是最低成本兜底）。
    r = registry.call("get_realtime_quotes", codes)
    if not _quote_ok(r):
        r = registry.call("get_realtime_quotes", codes)  # 重试一次
    if not _quote_ok(r):
        return {"status": "source_failed", "quote_date": date, "quote_time": "",
                "candidates": [], "scanned": len(codes), "matched": 0,
                "error": f"实时行情获取失败（含重试）：{getattr(r, 'error', '未知')}"}
    quotes = r.data
    possible_no_limit_codes = _possible_no_limit_codes(listing_dates, date)
    missing_listing_codes = set(codes) - set(listing_dates)
    provisional_no_limit_codes = possible_no_limit_codes | missing_listing_codes
    # 先把近 30 自然日上市代码作为“待确认例外”保留；只有通过前三项硬过滤且触及
    # 普通理论涨停价的代码才需要查交易日历，避免无关的额外源依赖。
    cands = filter_quotes(
        quotes,
        min_pct=min_pct,
        min_amount_yi=min_amount_yi,
        no_limit_codes=provisional_no_limit_codes,
    )
    quotes_by_code = {str(q.get("code") or "").strip().upper(): q for q in quotes}
    limit_exception_codes = {
        c["code"]
        for c in cands
        if c["code"] in provisional_no_limit_codes
        and _is_limit_up(
            c["code"],
            c.get("name", ""),
            quotes_by_code.get(c["code"], {}).get("price"),
            quotes_by_code.get(c["code"], {}).get("pre_close"),
        )
    }
    if limit_exception_codes:
        unknown_listing_codes = limit_exception_codes & missing_listing_codes
        if unknown_listing_codes:
            sample = ",".join(sorted(unknown_listing_codes)[:3])
            return {"status": "source_failed", "quote_date": date, "quote_time": "",
                    "candidates": [], "scanned": len(quotes), "matched": 0,
                    "error": f"新股无涨跌幅限制日判定失败：上市日期缺失（{sample}）"}
        no_limit_codes, calendar_error = _load_no_limit_codes(
            registry, date, listing_dates, limit_exception_codes
        )
        if calendar_error:
            return {"status": "source_failed", "quote_date": date, "quote_time": "",
                    "candidates": [], "scanned": len(quotes), "matched": 0,
                    "error": f"新股无涨跌幅限制日判定失败：{calendar_error}"}
        cands = [
            c for c in cands
            if c["code"] not in limit_exception_codes or c["code"] in no_limit_codes
        ]
    qd = quotes[0].get("quote_date", date) if quotes else date
    qt = quotes[0].get("quote_time", "") if quotes else ""
    return {"status": "ok", "quote_date": qd, "quote_time": qt,
            "candidates": cands, "scanned": len(quotes), "matched": len(cands),
            "error": None}
