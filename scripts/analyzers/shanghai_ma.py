"""上证日均线与前一开放日基准；只读取已可见、能定位到证券和事实日的收盘。"""
from __future__ import annotations

from contextlib import closing, nullcontext
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection

CODE = "000001.SH"
PERIODS = (5, 10, 20, 60)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA = "sse-shanghai-daily-ma-v1"


def positive_close(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _window(conn, day):
    rows = dict(conn.execute(
        "SELECT date, is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ?",
        ((day - timedelta(days=180)).isoformat(), day.isoformat())))
    if rows.get(day.isoformat()) == 0:
        return [], "休市"
    days = []
    cursor = day
    while len(days) < max(PERIODS) + 1:
        key = cursor.isoformat()
        if rows.get(key) not in (0, 1):
            return days, f"{key} 交易日历未核验"
        if rows[key] == 1:
            days.append(key)
        cursor -= timedelta(days=1)
    return days, None


def _dated_rows(rows, start=None, end=None):
    """一行一个事实日；出现错票、冲突、区间错位时拒绝该次来源。"""
    if not isinstance(rows, list):
        raise ValueError("日线行格式错误")
    prices = {}
    for row in rows:
        day = row["trade_date"]
        if (row["ts_code"] != CODE or not isinstance(day, str)
                or date.fromisoformat(day).isoformat() != day or (start and day < start)
                or (end and day > end) or day in prices):
            raise ValueError("日线身份、日期或唯一性无效")
        close = positive_close(row.get("close"))
        if close is None:
            raise ValueError("日线收盘无效")
        prices[day] = close
    return prices


def _local_stamp(raw):
    stamp = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    return stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI)


def _quote_price(quote, day):
    identities = {"tushare:index_daily": CODE, "akshare:stock_zh_index_daily": "sh000001",
                  "akshare:index_zh_a_hist": "000001"}
    if not isinstance(quote, dict):
        return None
    source = quote.get("_source")
    if isinstance(source, str) and source in identities and quote.get("code") == identities[source] and quote.get("trade_date") == day:
        return positive_close(quote.get("close"))
    return None


def _original_post_quote(conn, day, cutoff):
    """仅取原盘后信封的时间和目标指数，不反序列化整份历史行情。"""
    row = conn.execute(
        "SELECT json_extract(raw_data, '$.generated_at'), json_extract(raw_data, '$.raw_data.indices.shanghai') "
        "FROM daily_market WHERE date=? AND json_extract(raw_data, '$.date')=? "
        "AND json_extract(raw_data, '$.raw_data.date')=?", (day, day, day)).fetchone()
    if row and row[0]:
        stamp = _local_stamp(row[0])
        if stamp.date().isoformat() == day and stamp.hour >= 15 and stamp <= cutoff:
            return stamp, json.loads(row[1]) if row[1] else {}
    raise ValueError("未找到事实日相符的盘后生成时间")


def _archive_prices(root, days, cutoff):
    folder = root / "data/reports/review-evidence"
    result = {}
    for path in sorted(folder.glob("*/tmp/market_*_000001.SH.json"),
                       key=lambda p: (p.parent.parent.name, p.name.startswith("market_raw")), reverse=True):
        if not days.difference(result):
            break
        folder_day = path.parent.parent.name[:10]
        if not min(days) <= folder_day <= cutoff.date().isoformat():
            continue
        try:
            kind = {"market_raw_000001.SH.json": "tushare-mirror:index_daily",
                    "market_tdx_000001.SH.json": "tdx:index_daily:9"}[path.name]
            data = json.loads(path.read_text())
            stamp = _local_stamp(data["fetched_at"])
            if data["code"] != CODE or data["source"] != kind or stamp > cutoff:
                continue
            if kind == "tdx:index_daily:9" and (data.get("status") != "complete"
                    or data.get("query", {}).get("market") != 1
                    or data.get("query", {}).get("category") != 9
                    or data.get("query", {}).get("code") != "000001"):
                continue
            # 通达信收盘仅到分；同目录优先镜像原始精度，TDX仅填镜像缺少的日期。
            prices = _dated_rows(data["rows"], end=stamp.date().isoformat())
            if stamp.hour < 15:
                prices.pop(stamp.date().isoformat(), None)
            for day in days.difference(result).intersection(prices):
                result[day] = (prices[day], str(path.relative_to(root)))
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            continue
    return result


def _window_result(days, period, prices):
    selected = days[:period]
    missing = [d for d in reversed(selected) if d not in prices]
    count = len(selected) - len(missing)
    data = dict(observed=count, expected=period, missing_dates=missing,
                start=selected[-1] if len(selected) == period else None,
                end=selected[0] if selected else None)
    if count == period:
        mean = math.fsum(prices[d][0] for d in selected) / period
        data.update(value=round(mean, 2), above=prices[selected[0]][0] > mean)
    return data


def cross_signal(label, value, current, previous):
    if not isinstance(current, bool) or not isinstance(previous, bool) or current == previous:
        return None
    return {
        "type": "ma_cross", "signal": ("突破" if current else "跌破") + label,
        "direction": "positive" if current else "negative", "value": value,
        "description": f"上证指数收盘价{'上穿' if current else '下穿'}{label}（{value}）",
    }


def _recorded_window(evidence, field, name):
    group = evidence.get(field)
    window = group.get(name) if isinstance(group, dict) else None
    return window if isinstance(window, dict) else {}


def daily_cross_signals(ma, day):
    evidence = ma.get("daily_ma_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA or evidence.get("trade_date") != day:
        return []
    prev_day = evidence.get("previous_date")
    if not isinstance(prev_day, str) or prev_day >= day:
        return []
    signals = []
    for period in PERIODS:
        name = f"ma{period}"
        current = _recorded_window(evidence, "windows", name)
        prev = _recorded_window(evidence, "previous_windows", name)
        if any(w.get("observed") != period or w.get("expected") != period or w.get("end") != date_key
               or w.get("missing_dates") != [] or positive_close(w.get("value")) is None
               for w, date_key in ((current, day), (prev, prev_day))):
            continue
        if current["value"] != ma.get(name) or current.get("above") is not ma.get(f"above_{name}"):
            continue
        signal = cross_signal(name.upper(), current["value"], current.get("above"), prev.get("above"))
        if signal:
            signals.append(signal)
    return signals


def collect(day, quote, root, registry, *, now=None, conn=None):
    day = date.fromisoformat(day)
    key = day.isoformat()
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    cutoff = min(now, datetime.combine(day, datetime.max.time(), SHANGHAI))
    historical_run = day < now.date() or conn is not None
    days, prices = [], {}
    problems = []
    try:
        own_connection = conn is None
        connection = closing(get_readonly_connection(root / "data/trade.db")) if own_connection else nullcontext(conn)
        with connection as conn:
            if own_connection:
                conn.execute("BEGIN")
            days, problem = _window(conn, day)
            if problem:
                problems.append(problem)
            if days and historical_run:
                # 以该日原盘后报告时间为界，防止晚间补取的行情进入原报告视角。
                try:
                    cutoff, archived = _original_post_quote(conn, key, cutoff)
                    close = _quote_price(archived, key)
                    if close is not None:
                        prices[key] = (close, f"daily_market:{archived['_source']}")
                except (sqlite3.Error, TypeError, ValueError, AttributeError):
                    # 缺少原报告生成时间，只允许收盘时已存在的证据。
                    cutoff = min(cutoff, datetime.combine(day, datetime.min.time(), SHANGHAI).replace(hour=15))
                    problems.append("原盘后生成时间未核验，仅采用上海收盘时已可见的证据")
            if days and now.date() >= day and cutoff.hour >= 15:
                history = conn.execute("""
                    SELECT trade_date, close FROM market_timing_signal
                    WHERE index_code=? AND data_source='tushare:index_daily'
                      AND trade_date BETWEEN ? AND ?
                      AND julianday(updated_at) BETWEEN julianday(trade_date || ' 07:00:00') AND julianday(?)
                    GROUP BY trade_date HAVING COUNT(*) = 1
                    """, (CODE, days[-1], key, cutoff.isoformat()))
                for fact_day, close in history:
                    if fact_day in days and positive_close(close) is not None:
                        prices.setdefault(fact_day, (float(close), "market_timing_signal:tushare:index_daily"))
    except sqlite3.Error as exc:
        problems.append(f"日历或指数归档不可用（{type(exc).__name__}）")

    ready = days and day <= now.date() and cutoff.hour >= 15
    if ready:
        incoming = _quote_price(quote, key)
        if not historical_run and incoming is not None:
            prices[key] = (incoming, quote["_source"])
        missing = set(days).difference(prices)
        if missing and root is not None:
            prices.update(_archive_prices(root, missing, cutoff))
        missing.difference_update(prices)
        if missing and not historical_run:
            start, end = min(missing), max(missing)
            try:
                response = registry.call("get_index_daily_range", CODE, start, end)
                if not response.success or response.source != "tushare:index_daily":
                    raise ValueError("区间来源不可用")
                rows = _dated_rows(response.data, start, end)
                prices.update({d: (rows[d], response.source) for d in missing.intersection(rows)})
            except (AttributeError, KeyError, TypeError, ValueError, RuntimeError, OSError):
                problems.append("指数区间补查未通过核验")
        if key not in prices:
            problems.append("目标日收盘未取得当时可用的证券及日期证据")
        elif incoming is not None and incoming != prices[key][0]:
            prices.pop(key)
            problems.append("目标日收盘与当时可见归档冲突")
    elif days:
        problems.append("目标日尚未收盘")

    current = {f"ma{p}": _window_result(days, p, prices) for p in PERIODS}
    previous = {f"ma{p}": _window_result(days[1:], p, prices) for p in PERIODS}
    evidence = dict(schema=SCHEMA, trade_date=key, as_of=cutoff.isoformat(),
                    previous_date=days[1] if len(days) > 1 else None,
                    windows=current, previous_windows=previous, problems=problems,
                    closes={d: dict(close=v, source=s) for d, (v, s) in sorted(prices.items())})
    output = {"daily_ma_evidence": evidence}
    for name, window in current.items():
        if "value" in window:
            output[name] = window["value"]
            output[f"above_{name}"] = window["above"]
    return output


def archived_view(ma, day, quote, conn):
    """在调用方的库中核验过往日均线；外部连接不允许隐式映射到生产归档。"""
    names = {k for p in PERIODS for k in (f"ma{p}", f"above_ma{p}")}
    old = {k: v for k, v in ma.items() if k in names}
    result = {k: v for k, v in ma.items() if k not in names}
    saved = ma.get("daily_ma_evidence")
    if isinstance(saved, dict) and saved.get("schema") == SCHEMA and saved.get("trade_date") == day:
        # 新版原档自带当时的证据，只提升完整窗口；缺口保持原样。
        for period in PERIODS:
            key = f"ma{period}"
            window = _recorded_window(saved, "windows", key)
            if (window.get("end") == day and window.get("expected") == window.get("observed") == period
                    and window.get("missing_dates") == [] and isinstance(window.get("above"), bool)
                    and positive_close(window.get("value")) is not None):
                result[key] = window["value"]
                result[f"above_{key}"] = window["above"]
        return result
    if old:
        result["archived_unverified"] = {"values": old, "reason": "旧归档未记录严格开放日窗口"}
    root = None
    if conn is not None:
        for _, schema, filename in conn.execute("PRAGMA database_list"):
            path = Path(filename)
            if schema == "main" and path.name == "trade.db" and path.parent.name == "data":
                root = path.parent.parent
        result.update(collect(day, quote, root, None, conn=conn))
    else:
        empty = {f"ma{p}": _window_result([], p, {}) for p in PERIODS}
        result["daily_ma_evidence"] = dict(schema=SCHEMA, trade_date=day, as_of=None, previous_date=None,
                                           windows=empty, previous_windows=empty, closes={},
                                           problems=["读取方未提供同源数据库，旧日均线待核"])
    return result
