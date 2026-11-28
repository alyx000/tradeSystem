"""收盘后按当日 A 股真实跌停价还原触价及收盘状态。"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from analyzers.strong_stock_feedback import _code, _number

KEY = "limit_down_touch"
SCHEMA = "limit-down-touch-v1"
TZ = ZoneInfo("Asia/Shanghai")
SOURCES = dict(quotes="tushare:daily", limits="tushare:stk_limit", universe="tushare:stock_basic:L+D+P:as_of",
               st="tushare:stock_st", suspended="tushare:suspend_d")
SCOPE = "沪深北A股；剔除ST、退市、B股、全天停牌及当日无价格限制证券。"
NOTE = "触价指最低成交价等于当日实际跌停价。日线可核对收盘是否离开，首次开板、回封次数及盘口挂单需另查盘中记录。"


def closed_session(day, db_path, now=None, *, same_day=False):
    clock = (now or datetime.now(TZ)).astimezone(TZ)
    if same_day and clock.date().isoformat() != day:
        raise ValueError("历史触价须使用当日已归档证券身份与行情")
    if clock < datetime.combine(date.fromisoformat(day), time(16), TZ):
        raise ValueError("目标日尚未完成盘后采集")
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            days = conn.execute("SELECT is_open FROM trade_calendar WHERE date=? AND exchange='SSE'", (day,)).fetchall()
        if days != [(1,)]:
            raise ValueError("交易日历未核实目标日为SSE开放日")
    except sqlite3.Error as exc:
        raise ValueError("SSE交易日历不可读") from exc


def _digest(data):
    return sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()).hexdigest()


def unavailable(day, reason):
    return dict(schema=SCHEMA, trade_date=day, status="source_failed", gaps=[reason],
                touched=None, closed_at_limit=None, closed_above_limit=None, stocks=[])


def _from_evidence(day, evidence):
    if evidence["trade_date"] != day or evidence["sources"] != SOURCES:
        raise ValueError("跌停触价证据来源或日期错位")
    cov = evidence["coverage"]
    basis = evidence["basis"]
    if (not isinstance(basis, list) or cov["quoted"] < 4000 or cov["active"] != cov["suspended"] + cov["quoted"]
            or cov["quoted"] != len(basis) + cov["excluded_st_or_delisting"] + cov["unlimited"]
            or min(cov["suspended"], cov["excluded_st_or_delisting"], cov["unlimited"]) < 0 or not basis):
        raise ValueError("跌停触价全市场证据范围未核齐")
    if type(cov.get("active")) is not int or any(type(cov[k]) is not int for k in ("quoted","suspended","excluded_st_or_delisting","unlimited")):
        raise ValueError("市场范围计数类型错误")
    stocks, seen = [], set()
    for code, name, low, high, closing, limit, upper, volume in basis:
        _number(volume, positive=True)
        if _code(code) != code or code in seen or not isinstance(name, str) or not name.strip():
            raise ValueError("跌停触价样本身份冲突")
        seen.add(code)
        lo, hi, close, down, up = (_number(value, positive=True) for value in (low, high, closing, limit, upper))
        if not down <= lo <= close <= hi <= up or down >= up:
            raise ValueError(f"{code} 低高收或真实跌停价冲突")
        if lo == down:
            stocks.append(dict(code=code, name=name, low=lo, high=hi, close=close, down_limit=down,
                               state="closed_at_limit" if close == down else "closed_above_limit"))
    closing = sorted(row["code"] for row in stocks if row["state"] == "closed_at_limit")
    if closing != evidence["verified_closed_codes"]:
        raise ValueError("收盘跌停名单与独立核验不一致")
    return dict(schema=SCHEMA, trade_date=day, status="complete", touched=len(stocks), closed_at_limit=len(closing),
                closed_above_limit=len(stocks)-len(closing), sample_count=len(basis), coverage=cov, stocks=stocks,
                scope=SCOPE, definition=NOTE, evidence=evidence, source_sha256=_digest(evidence), gaps=[])


def calculate(day, aligned_inputs, closed_block, sources):
    quotes, limits, active, st = aligned_inputs
    if sources != SOURCES or closed_block["trade_date"] != day or closed_block["status"] != "complete":
        raise ValueError("五项同日原始来源或收盘交叉校验不足")
    if not st.keys() <= active.keys() or any("ST" in row["name"].upper() and c not in st for c, row in active.items()):
        raise ValueError("ST名单与事实日上市身份无法对齐")
    basis, excluded, unlimited = [], 0, 0
    for code, q in sorted(quotes.items()):
        name = active[code]["name"]
        if code in st or "ST" in name.upper() or "退" in name:
            excluded += 1
            continue
        if limits[code].get("down_limit") == 0 and limits[code].get("up_limit") == 99999.99:
            unlimited += 1
            continue
        _number(q.get("vol"), positive=True)
        basis.append([code, name, q["low"], q["high"], q["close"], limits[code]["down_limit"], limits[code]["up_limit"], q["vol"]])
    evidence = dict(trade_date=day, sources=dict(sources), basis=basis,
                    coverage=dict(active=len(active), quoted=len(quotes), suspended=len(active)-len(quotes),
                                  excluded_st_or_delisting=excluded, unlimited=unlimited),
                    verified_closed_codes=sorted(r["code"] for r in closed_block["stocks"] if not r["is_st"]))
    return _from_evidence(day, evidence)


def verified(block, day):
    if not isinstance(block, dict) or block.get("trade_date") != day or block.get("schema") != SCHEMA:
        raise ValueError("同日跌停触价归档缺失或日期未核验")
    if block.get("status") != "complete":
        raise ValueError("；".join(block.get("gaps") or ["跌停触价源失败"]))
    result = _from_evidence(day, block["evidence"])
    if any(result[key] != block.get(key) for key in ("touched", "closed_at_limit", "closed_above_limit", "stocks", "source_sha256", "coverage", "sample_count")):
        raise ValueError("跌停触价原始证据与归档不符")
    if isinstance(block.get("latest_attempt"), dict) and block["latest_attempt"].get("status") == "source_failed":
        result["latest_attempt"] = block["latest_attempt"]
    return result


def retain_previous(attempt, daily_root):
    if attempt["status"] == "complete":
        return attempt
    from services.review_limit_down_touch import load
    from services.review_feedback_trends import _read
    day = attempt["trade_date"]
    previous = load(daily_root, day, _read(Path(daily_root)/day/"post-market.yaml", yaml_file=True))
    if previous["status"] == "complete":
        previous["latest_attempt"] = dict(status=attempt["status"], gaps=attempt["gaps"],
                                          checked_at=datetime.now(TZ).isoformat())
        return previous
    return attempt


def summary(block):
    return (f"非ST跌停触价 {block['touched']} 只，收盘仍在跌停 {block['closed_at_limit']} 只，"
            f"收盘已离开跌停 {block['closed_above_limit']} 只。"
            + (" 本次来源失败，沿用同日已核验的归档。" if block.get("latest_attempt") else ""))


def _detail(row):
    return "收盘仍在跌停" if row["state"] == "closed_at_limit" else "收盘已离开"


def html(block, day):
    try:
        result = verified(block, day)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return f'<p data-limit-down-touch="missing">[事实] {escape(day)} 非ST跌停触价待核（{escape(str(exc))}）。</p>'
    rows = "".join(f'<tr><td>{escape(r["name"])} {escape(r["code"])}</td><td>{r["down_limit"]:.2f}</td>'
                   f'<td>{r["close"]:.2f}</td><td>{_detail(r)}</td></tr>' for r in result["stocks"])
    return (f'<div data-limit-down-touch="v1"><p>[事实] {escape(day)} {summary(result)}</p>'
            f'<details><summary>触价名单与口径</summary><p>{SCOPE}{NOTE} 有效样本 {result["sample_count"]} 只。</p>'
            '<table><thead><tr><th>证券</th><th>实际跌停价</th><th>收盘价</th><th>收盘状态</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details></div>')
