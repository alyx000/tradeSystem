"""同日盘后跌停价格核验结果与择时运行时快照。"""
from __future__ import annotations

import json
import sqlite3

from analyzers.consecutive_limit_down import index_limit_down_list
from analyzers.verified_limit_down import SOURCE

SNAPSHOT_KEY = "verified_limit_down"
ORIGINAL_KEY = "original_limit_down"


def archive_fact(conn, day):
    """只读目标日盘后归档，不重新调取榜单或跨日补数。"""
    def unknown(reason):
        return dict(trade_date=day, status="unknown", count=None, reason=reason)

    try:
        row = conn.execute(
            "SELECT json_extract(raw_data,'$.date'), json_extract(raw_data,'$.raw_data.date'), "
            "json_extract(raw_data,'$.raw_data.limit_down') FROM daily_market WHERE date=?", (day,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if not any(s in str(exc) for s in ("no such table", "no such column", "malformed JSON")):
            raise
        return unknown("盘后归档缺失或不可解析")
    if not row or row[2] is None:
        return unknown("缺少同日盘后跌停名单")
    if tuple(row[:2]) != (day, day):
        return unknown("盘后归档事实日不符")
    try:
        block = json.loads(row[2])
        if not isinstance(block, dict) or block.get("_source") != SOURCE:
            return unknown("缺少逐股收盘价与跌停价核验")
        stocks = index_limit_down_list(block, day)
        for code, row in stocks.items():
            name = row["name"].upper()
            if (code.startswith(("200", "201", "900")) or "退" in name
                    or ("ST" in name and not row["is_st"])
                    or type(row["down_limit"]) not in (int, float)):
                return unknown("盘后跌停名单的身份或价格证据冲突")
        return dict(trade_date=day, status="verified", count=block["count_ex_st"],
                    source=SOURCE, coverage=block["coverage"])
    except (ValueError, TypeError, KeyError):
        return unknown("盘后跌停名单的日期、价格、计数或上市覆盖未通过核验")


def recorded_row(row, day):
    """重跑后仍区分最早留存和刚刷新过的当前行。"""
    try:
        detail = json.loads(row.get("fractal_json") or "{}")
    except (ValueError, TypeError):
        detail = {}
    detail = detail if isinstance(detail, dict) else {}
    current_count = row.get("limit_down_count")
    if ORIGINAL_KEY not in detail and SNAPSHOT_KEY not in detail:
        count = current_count if type(current_count) is int and current_count >= 0 else None
        return dict(status="legacy", count=count, trade_date=day)

    fact = detail.get(ORIGINAL_KEY, detail.get(SNAPSHOT_KEY))
    if isinstance(fact, dict) and fact.get("trade_date") == day:
        count = fact.get("count")
        status = fact.get("status")
        valid_count = type(count) is int and count >= 0
        if ORIGINAL_KEY in detail or count == current_count:
            if status == "unknown" and count is None:
                return fact
            if status == "legacy" and ORIGINAL_KEY in detail and (count is None or valid_count):
                return fact
            if status == "verified" and fact.get("source") == SOURCE and valid_count:
                cov = fact.get("coverage")
                keys = ("active", "quoted", "suspended_without_quote")
                if (isinstance(cov, dict) and all(type(cov.get(k)) is int and cov[k] >= 0 for k in keys)
                        and 0 < cov["quoted"] and count <= cov["quoted"]
                        and cov["quoted"] + cov["suspended_without_quote"] == cov["active"]):
                    return fact
    return dict(status="unknown", count=None, trade_date=day, reason="择时快照证据不符")


def recorded_fact(rows, day):
    """各指数留存不同则不发布单一初报数字。"""
    facts = [recorded_row(r, day) for r in rows]
    if not facts or any(f != facts[0] for f in facts):
        return dict(status="unknown", count=None, trade_date=day, reason="各指数留存的择时快照不一致")
    return facts[0]
