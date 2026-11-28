"""百亿扫描的事实日沪深北名单。缓存仅保存身份与上市日期，行情另行实时取。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from collections import Counter
from datetime import date
from pathlib import Path

from db.connection import get_readonly_connection
from utils.price_limit import limit_pct_for
from .guards import previous_open_dates
from .state import save_state, state_lock

MIN_UNIVERSE = 4000  # 与既有盘中全市场扫描一致
SOURCE = "tushare:stock_basic:L+D+P:as_of"
STOCK = re.compile(r"(?:(?:60[0-9]{4}|68[89][0-9]{3})\.SH|(?:00|30)[0-9]{4}\.SZ|(?:[48][0-9]{5}|92[0-9]{4})\.BJ)")
B_SHARE = re.compile(r"(?:(?:200|201)[0-9]{3}\.SZ|900[0-9]{3}\.SH)")
EXCHANGES = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
MARKETS = {"SH": {"主板", "科创板"}, "SZ": {"主板", "中小板", "创业板"}, "BJ": {"北交所"}}
SECURITY = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")


def _stock_identity(code, row):
    if not isinstance(code, str) or not SECURITY.fullmatch(code):
        return False
    number, exchange = code.split(".")
    market = row.get("market")
    if market is not None and market not in MARKETS[exchange]:
        return False
    if STOCK.fullmatch(code) or B_SHARE.fullmatch(code):
        return True
    # 新发行号段保留在分母中，直到板型可核验；旧号段错交易所、基金及无身份记录不能冒充新股。
    if any(STOCK.fullmatch(f"{number}.{suffix}") or B_SHARE.fullmatch(f"{number}.{suffix}")
           for suffix in EXCHANGES):
        return False
    return market in MARKETS[exchange] and row.get("symbol") == number and limit_pct_for(code) is not None


def _day(value):
    text = str(value or "")
    if len(text) == 8 and text.isascii() and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError("名单日期格式非法")
    return text


def _check_coverage(listings):
    exchanges = Counter(code[-2:] for code in listings)
    if len(listings) < MIN_UNIVERSE or set(exchanges) != EXCHANGES.keys():
        raise ValueError(f"事实日 A 股清单覆盖不足（{len(listings)} 只，沪深北 {dict(exchanges)}）")


def _verified_pages(receipt, day, count):
    if not isinstance(receipt, dict) or receipt.get("trade_date") != day:
        return False
    pages = receipt.get("pages")
    if not isinstance(pages, dict) or set(pages) != {"L", "D", "P"} or receipt.get("page_size") != 2000:
        return False
    for series in pages.values():
        if (not isinstance(series, list) or not 1 <= len(series) <= 16 or type(series[-1]) is not int or series[-1] != 0
                or any(type(n) is not int or not 1 <= n <= 2000 for n in series[:-1])):
            return False
    total = sum(sum(v) for v in pages.values())
    return type(receipt.get("selected")) is int and count <= receipt["selected"] <= total


def _load_source(registry, day):
    providers = getattr(registry, "providers", [])
    if providers and not any(
        p.name == "tushare" and p.supports("get_stock_universe_as_of")
        and (getattr(p, "_initialized", False) or p.initialize()) for p in providers
    ):
        raise ValueError("事实日股票清单数据源初始化失败")
    result = registry.call("get_stock_universe_as_of", day, exact_day=True)
    if not result.success or result.source != SOURCE or not isinstance(result.data, list):
        raise ValueError(f"事实日沪深北股票清单来源失败：{result.error or '来源不符'}")
    receipt = json.loads(result.note)
    if (not _verified_pages(receipt, day, len(result.data)) or receipt["selected"] != len(result.data)):
        raise ValueError("事实日清单日期、L/D/P分页或总数未核验")
    listings = {}
    seen = set()
    for row in result.data:
        if not isinstance(row, dict):
            raise ValueError("股票清单记录无效")
        code = row.get("ts_code")
        if not _stock_identity(code, row):
            raise ValueError("证券代码与交易所不符")
        if (row.get("symbol", code[:6]) != code[:6] or row.get("code", code) not in (code[:6], code)
                or row.get("exchange", code[-2:]) not in (code[-2:], EXCHANGES[code[-2:]])
                or code in seen):
            raise ValueError(f"证券身份冲突或重复（{code}）")
        seen.add(code)
        if any(_day(row[k]) != day for k in ("trade_date", "as_of_date") if k in row):
            raise ValueError("股票清单记录事实日错位")
        listed = _day(row.get("list_date"))
        delisted = _day(row["delist_date"]) if row.get("delist_date") else None
        if listed > day or (delisted and listed > delisted):
            raise ValueError(f"上市、退市日期与事实日不符（{code}）")
        # 再核对上市区间，保留当日停牌对象。
        if not B_SHARE.fullmatch(code) and (not delisted or delisted >= day):
            listings[code] = listed
    _check_coverage(listings)
    return listings, receipt


def _digest(day, listings, receipt):
    payload = json.dumps([SOURCE, day, listings, receipt], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _cached(path, day):
    try:
        cache = json.loads(path.read_bytes())
        listings = cache["listings"]
        if (cache["version"] != 1 or cache["source"] != SOURCE or cache["trade_date"] != day
                or not isinstance(listings, dict) or not _verified_pages(cache["receipt"], day, len(listings))
                or cache["sha256"] != _digest(day, listings, cache["receipt"])
                or any(not SECURITY.fullmatch(code) or _day(listed) > day for code, listed in listings.items())):
            return None
        _check_coverage(listings)
        return listings
    except (FileNotFoundError, ValueError, TypeError, KeyError):
        return None


def _previous_quote_counts(day, db_path):
    """前一日逐股成交行情仅作各市上市数的下界。没有可核验证据时仍要求首次清单完整分页。"""
    try:
        with closing(get_readonly_connection(db_path)) as conn:
            row = conn.execute(
                "SELECT json_extract(raw_data,'$.date'),json_extract(raw_data,'$.raw_data.date'),"
                "json_extract(raw_data,'$.raw_data.zhong_prefix_effect.trade_date'),"
                "json_extract(raw_data,'$.raw_data.zhong_prefix_effect.evidence.quote_source'),"
                "json_extract(raw_data,'$.raw_data.zhong_prefix_effect.coverage.valid_count'),"
                "json_extract(raw_data,'$.raw_data.zhong_prefix_effect.evidence.quotes') "
                "FROM daily_market WHERE date=?", (day,)).fetchone()
        if not row or tuple(row[:4]) != (day, day, day, "tushare:daily"):
            return None
        quotes = json.loads(row[5])
        if not isinstance(quotes, list) or not MIN_UNIVERSE <= len(quotes) == row[4]:
            return None
        codes = set()
        for quote in quotes:
            code = quote["ts_code"]
            if (not SECURITY.fullmatch(code) or quote.get("code", code) != code
                    or _day(quote["trade_date"]) != day or code in codes):
                return None
            codes.add(code)
        counts = Counter(c[-2:] for c in codes if not B_SHARE.fullmatch(c))
        return counts if counts.keys() == EXCHANGES.keys() else None
    except (sqlite3.Error, OSError, KeyError, ValueError, TypeError):
        return None


def load_listings(registry, day, state_path: Path, dry_run: bool, db_path=None):
    def cache_path(d):
        return state_path.with_name(f"{state_path.stem}.universe-{d}.json")

    def fetch():
        listings, receipt = _load_source(registry, day)
        previous = previous_open_dates(day, 1, db_path=db_path)
        prior = _cached(cache_path(previous[0]), previous[0]) if previous else None
        prior_counts = Counter(code[-2:] for code in prior) if prior else None
        reference = "same_source_universe" if prior_counts else None
        if previous and not prior_counts:
            prior_counts = _previous_quote_counts(previous[0], db_path)
            reference = "archived_daily_quotes_lower_bound" if prior_counts else None
        receipt["reference"] = reference
        receipt["previous_trade_date"] = previous[0] if prior_counts else None
        now_counts = Counter(code[-2:] for code in listings)
        receipt["exchange_counts"] = dict(now_counts)
        if prior_counts:
            receipt["previous_exchange_counts"] = dict(prior_counts)
            for exchange, count in prior_counts.items():
                if now_counts[exchange] < count * 0.98:
                    raise ValueError(f"{exchange}名单较上一开放日缩水（{now_counts[exchange]}/{count}，低于98%）")
        return listings, receipt

    path = cache_path(day)
    if dry_run:
        return _cached(path, day) or fetch()[0]
    with state_lock(path):
        previous = _cached(path, day)
        if previous is not None:
            return previous
        listings, receipt = fetch()
        save_state(path, dict(version=1, trade_date=day, source=SOURCE, listings=listings, receipt=receipt,
                              sha256=_digest(day, listings, receipt)))
        return listings
