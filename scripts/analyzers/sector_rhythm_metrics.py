"""板块节奏的开放日区间收益、上榜强度和各自的取证覆盖。"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
import json
import math
import re
import sqlite3
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection

PERIODS = (5, 10)
_SCHEMA = "sector-rhythm-window-v1"
_DAILY_SOURCES = {"industry": ("tushare:sw_daily", r"[0-9]{6}\.SI"),
                  "concept": ("tushare:ths_daily", r"[0-9]{6}\.TI")}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def open_window(day, count, *, base_dir=None, conn=None):
    """从目标日逐自然日核对 SSE 日历，返回已连续核验的开放日后缀（升序）。"""
    if conn is None:
        if base_dir is None:
            return [], "无同源开放日历"
        try:
            with closing(get_readonly_connection(base_dir / "data/trade.db")) as local:
                return open_window(day, count, conn=local)
        except (OSError, sqlite3.Error):
            return [], "同源开放日历不可用"
    try:
        cursor = date.fromisoformat(day)
        rows = conn.execute(
            "SELECT date, is_open FROM trade_calendar WHERE exchange='SSE' AND date<=? "
            "ORDER BY date DESC LIMIT ?", (day, max(60, count * 4)))
        days = []
        for when, is_open in rows:
            if when != cursor.isoformat() or is_open not in (0, 1):
                break
            if is_open:
                days.append(when)
                if len(days) == count:
                    return days[::-1], None
            elif when == day:
                return [], "事实日休市"
            cursor -= timedelta(days=1)
        return days[::-1], f"{cursor.isoformat()} 开放日历缺失或不连续"
    except (ValueError, TypeError, sqlite3.Error):
        return [], "同源开放日历不可用"


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rank_book(history, all_dates):
    """日榜身份、条目与各日来源；当日榜单来源不同不能沿用同名历史。"""
    days = set(all_dates)
    by_date = defaultdict(dict)
    sources = defaultdict(set)
    for name, entries in history.items():
        for row in entries:
            d = row.get("date")
            if d not in days:
                continue
            declared_source = row.get("_source")
            sources[d].add(declared_source if isinstance(declared_source, str) else None)
            by_date[d][name] = row if name not in by_date[d] else None
    return by_date, sources


def _risk_price_rows(raw, day, kind):
    block = raw.get("sector_adjustment_risk")
    if not isinstance(block, dict) or block.get("schema") != "sector-adjustment-risk-v1":
        return [], "当日板块指数日线归档缺失"
    try:
        stamp = datetime.fromisoformat(block["generated_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        stamp = stamp.astimezone(_SHANGHAI)
        if (block.get("date") != day or stamp.date().isoformat() != day or stamp.hour < 15
                or stamp > datetime.now(_SHANGHAI) or block.get("status") not in ("complete", "partial")):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        return [], "板块日线的事实日、来源状态或收盘可见时点未核验"
    rows = block.get("rows")
    if not isinstance(rows, list):
        return [], "当日板块日线行缺失"
    return [r for r in rows if isinstance(r, dict) and r.get("kind") == kind], None


def _series(name, kind, day, rank, rows, block_error, ranking_source):
    source, pattern = _DAILY_SOURCES[kind]
    if ranking_source != source:
        return None, {}, "榜单与可核验指数日线的来源或分类不同"
    if block_error:
        return None, {}, block_error
    candidates = [r for r in rows if r.get("name") == name]
    if len(candidates) != 1 or not rank:
        return None, {}, "同日榜单与指数身份无法唯一对应"
    row = candidates[0]
    code = row.get("code")
    if (row.get("daily_source") != source or row.get("as_of") != day
            or row.get("status") not in ("complete", "partial")
            or not isinstance(code, str) or not re.fullmatch(pattern, code)
            or sum(r.get("code") == code for r in rows) != 1):
        return None, {}, "指数代码、事实日或日线来源未核验"
    if any(rank.get(k) is not None and rank.get(k) != code for k in ("ts_code", "code")):
        return None, {}, "榜单证券代码与指数日线冲突"
    bars = row.get("daily_bars")
    if not isinstance(bars, list):
        return code, {}, "无可核验的同日指数日线"
    prices = {}
    for bar in bars:
        if not isinstance(bar, dict):
            return code, {}, "指数日线格式无效"
        when = bar.get("time")
        try:
            if date.fromisoformat(when).isoformat() != when or when > day:
                raise ValueError
        except (TypeError, ValueError):
            return code, {}, "指数日线事实日错误或包含未来行情"
        if any(k in bar and bar[k] != code for k in ("ts_code", "code")):
            return code, {}, "指数日线证券身份冲突"
        value = _finite(bar.get("close"))
        prices[when] = value if when not in prices and value is not None and value > 0 else None
    return code, prices, None


def _apply_row(item, kind, raw, day, by_date, sources, rows, block_error, window_dates):
    name = item.get("name")
    section = raw.get(f"sector_{kind}") or {}
    source = section.get("_source") if isinstance(section, dict) else None
    if not isinstance(source, str):
        source = None
    target = by_date.get(day, {}).get(name)
    code, prices, source_error = _series(name, kind, day, target, rows, block_error, source)
    if not source_error and len(window_dates) >= 2:
        prior, latest = (prices.get(d) for d in window_dates[-2:])
        quoted = _finite(target.get("observed_change_pct", target.get("change_pct")))
        # 老版日榜只有板块名；按同源唯一名称加当日涨跌复核其与代码日线的对应。
        if (not latest or not prior or quoted is None
                or abs((latest / prior - 1) * 100 - quoted) > 0.011):
            source_error = "榜单与指数收盘涨跌无法相互核验"
            prices = {}

    result = dict(item)
    evidence = {"schema": _SCHEMA}
    for n in PERIODS:
        prefix = f"{n}d"
        expected = window_dates[-(n + 1):]
        rank_days = window_dates[-n:]
        known, ranked, moves, uncertain, missing_changes = [], [], [], [], []
        for d in rank_days:
            daily = by_date.get(d, {})
            if (not source or sources.get(d) != {source} or (name in daily and daily[name] is None)
                    or (name not in daily and len(daily) != 30)):
                uncertain.append(d)
                continue
            entry = daily.get(name)
            if entry:
                old_code = entry.get("ts_code") or entry.get("code")
                if code and old_code and old_code != code:
                    uncertain.append(d)
                    continue
                ranked.append(d)
                value = _finite(entry.get("observed_change_pct", entry.get("change_pct")))
                if value is None:
                    missing_changes.append(d)
                else:
                    moves.append(value)
            known.append(d)
        result[f"ranking_covered_days_{prefix}"] = len(known)
        result[f"top30_days_{prefix}"] = len(ranked) if known else None
        result[f"top30_sum_{prefix}_pct"] = (round(math.fsum(moves), 2)
                                              if not missing_changes and (moves or len(known) == n) else None)
        missing = [d for d in expected if not prices.get(d)]
        reason = source_error
        if len(expected) < n + 1:
            reason = "连续开放日历不足"
        elif not reason and missing:
            reason = "区间日线收盘缺失、无效或重复"
        start = expected[0] if len(expected) == n + 1 else None
        value = _finite((prices[day] / prices[start] - 1) * 100) if not reason else None
        if value is None and not reason:
            reason = "区间涨跌无效"
        result[f"period_return_{prefix}_pct"] = round(value, 2) if value is not None else None
        evidence[prefix] = {
            "status": "partial" if reason else "complete", "reason": reason,
            "source": _DAILY_SOURCES[kind][0] if code else None, "ts_code": code,
            "start_date": start, "end_date": day, "observed_closes": len(expected) - len(missing),
            "expected_closes": n + 1, "missing_close_dates": missing,
            "start_close": prices.get(start), "end_close": prices.get(day),
            "ranking_source": source, "ranked_dates": ranked, "ranking_missing_dates": uncertain,
            "ranking_status": "complete" if len(known) == n else "partial",
            "ranked_change_missing_dates": missing_changes,
        }
    result["rhythm_window_evidence"] = evidence
    return result


def project_rhythm_metrics(items, raw, kind, day, history, all_dates, *, base_dir=None, window_dates=None):
    """只投影新的两类指标，保留旧分类及其原始输入；无可比日线时返回覆盖原因。"""
    if window_dates is None:
        window_dates, _ = open_window(day, max(PERIODS) + 1, base_dir=base_dir)
    if raw.get("date") != day or not window_dates or window_dates[-1] != day:
        window_dates = []
    by_date, sources = _rank_book(history, all_dates)
    rows, block_error = _risk_price_rows(raw, day, kind)
    return [_apply_row(item, kind, raw, day, by_date, sources, rows, block_error, window_dates)
            for item in items if isinstance(item, dict)]


def _read_rank(raw, day, kind, history, dates):
    section = raw.get(f"sector_{kind}") or {}
    def matches_day(row):
        return all(str(row[k]) in {day, day.replace("-", "")} for k in ("date", "trade_date", "as_of") if k in row)

    if (not isinstance(section, dict) or section.get("error") or not matches_day(section)
            or section.get("status") in ("source_failed", "partial", "failed", "skipped")):
        return
    rows = section.get("data")
    if not isinstance(rows, list) or not rows or len(rows) > 30 or not isinstance(section.get("_source"), str) or not section["_source"]:
        return
    names = [r.get("name") if isinstance(r, dict) else None for r in rows]
    if (not all(isinstance(name, str) and name.strip() for name in names) or len(set(names)) != len(names)
            or any(not matches_day(r) for r in rows)):
        return
    dates.append(day)
    for row in rows:
        history[row["name"]].append({**row, "date": day, "_source": section["_source"]})


def archived_rhythm_view(raw, day, conn):
    """使用调用方的日历与最多十日的两份小型榜单，只读核验原信封的板块节奏。"""
    if not isinstance(raw, dict):
        return {}
    keys = [f"sector_rhythm_{k}" for k in _DAILY_SOURCES if isinstance(raw.get(f"sector_rhythm_{k}"), list)]
    if not keys:
        return {}
    # 新档已经锁定当时证据，留存的完整点和缺口不随当前数据变化。
    def recorded(item, kind):
        evidence = item.get("rhythm_window_evidence") if isinstance(item, dict) else None
        if raw.get("date") != day or not isinstance(evidence, dict) or evidence.get("schema") != _SCHEMA:
            return False
        source, code_pattern = _DAILY_SOURCES[kind]
        for n in PERIODS:
            period = f"{n}d"
            block = evidence.get(period)
            value = item.get(f"period_return_{period}_pct")
            if (not isinstance(block, dict) or block.get("end_date") != day
                    or block.get("expected_closes") != n + 1):
                return False
            if block.get("status") == "partial":
                if value is not None or not block.get("reason"):
                    return False
                continue
            start = _finite(block.get("start_close"))
            end = _finite(block.get("end_close"))
            if (block.get("status") != "complete" or block.get("source") != source
                    or not re.fullmatch(code_pattern, str(block.get("ts_code") or ""))
                    or block.get("observed_closes") != n + 1 or block.get("missing_close_dates") != []
                    or not isinstance(block.get("start_date"), str) or not block["start_date"] < day
                    or start is None or start <= 0 or end is None or end <= 0
                    or _finite(value) is None or value != round((end / start - 1) * 100, 2)):
                return False
        return True

    view = {k: raw[k] for k in keys if all(recorded(r, k.removeprefix("sector_rhythm_")) for r in raw[k])}
    kinds = [k for k in _DAILY_SOURCES if f"sector_rhythm_{k}" in keys and f"sector_rhythm_{k}" not in view]
    if not kinds:
        return view
    dates, _ = open_window(day, max(PERIODS) + 1, conn=conn)
    history = {k: defaultdict(list) for k in kinds}
    available = {k: [] for k in kinds}
    if dates and conn is not None:
        # 只读投影两个榜单，不把每份数万行的原始日线反序列化到 Python。
        try:
            prior = conn.execute("""
                SELECT date, json_extract(raw_data,'$.date'), json_extract(raw_data,'$.raw_data.date'),
                       json_extract(raw_data,'$.raw_data.sector_industry'),
                       json_extract(raw_data,'$.raw_data.sector_concept'), json_extract(raw_data,'$.generated_at')
                FROM daily_market WHERE date>=? AND date<? AND json_valid(raw_data) ORDER BY date
            """, (dates[-min(10, len(dates))], day))
            for d, envelope_day, raw_day, industry, concept, generated_at in prior:
                if d not in dates or envelope_day != d or raw_day != d:
                    continue
                try:
                    stamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                    stamp = stamp.replace(tzinfo=_SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(_SHANGHAI)
                    # 原日盘后归档可以证明该行在目标日之前可见。
                    if stamp.date().isoformat() != d or stamp.hour < 15:
                        continue
                except (TypeError, ValueError, AttributeError):
                    continue
                for kind, blob in (("industry", industry), ("concept", concept)):
                    if kind not in history or not blob:
                        continue
                    try:
                        source = json.loads(blob)
                    except (TypeError, ValueError):
                        continue
                    _read_rank({f"sector_{kind}": source}, d, kind, history[kind], available[kind])
        except sqlite3.Error:
            pass
    for kind in kinds:
        _read_rank(raw, day, kind, history[kind], available[kind])
        key = f"sector_rhythm_{kind}"
        old = [r for r in raw[key] if isinstance(r, dict)]
        projected = project_rhythm_metrics(old, raw, kind, day, history[kind], available[kind], window_dates=dates)
        view[key] = [original if recorded(original, kind) else fresh for original, fresh in zip(old, projected)]
    return view
