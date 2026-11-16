"""Fail-closed input checks for publishing daily leaders; no writes or fetching."""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from zoneinfo import ZoneInfo

from db import queries as Q
from services.daily_leaders.selection import canonical_stock_code

SHANGHAI = ZoneInfo('Asia/Shanghai')
MIN_QUOTES = 4000
DATED_BLOCKS = ('limit_step', 'limit_cpt_list', 'sector_moneyflow_ths',
                'concept_moneyflow_ths', 'sector_moneyflow_dc', 'concept_moneyflow_dc')
EMPTY_ALLOWED = {'limit_step', 'limit_cpt_list'}


class DataNotReady(RuntimeError):
    """Only this failure is eligible for a scheduled readiness retry."""


def now_shanghai():
    return dt.datetime.now(SHANGHAI)


def _day(value):
    text = str(value or '').replace('-', '')
    return text if len(text) == 8 and text.isdigit() else ''


def is_open_day(conn, date):
    try:
        row = conn.execute('SELECT is_open, exchange FROM trade_calendar WHERE date=?', (date,)).fetchone()
    except sqlite3.Error as exc:
        raise DataNotReady('交易日历不可读') from exc
    if row is None or row[1] != 'SSE' or row[0] not in (0, 1):
        raise DataNotReady('缺少当日已核验的SSE交易日历')
    return bool(row[0])


def _verified_empty(conn, key, date):
    # The collector omits successful empty ladder blocks. Only an explicit,
    # dated empty receipt can distinguish that from a failed/missing source.
    try:
        row = conn.execute('''SELECT status,row_count,payload_json FROM raw_interface_payloads
            WHERE interface_name=? AND biz_date=? AND target_date=?
            ORDER BY inserted_at DESC,id DESC LIMIT 1''', (key, date, date)).fetchone()
        return bool(row and row[0] == 'empty' and row[1] == 0 and json.loads(row[2]) == [])
    except (sqlite3.Error, ValueError, TypeError):
        return False


def check_post_market(conn, date, prefill):
    """Require a dated persisted snapshot and verify the actual consumed blocks."""
    if not is_open_day(conn, date):
        raise DataNotReady('非开放日，不发布候选稿')
    try:
        row = Q.get_daily_market(conn, date)
        env = json.loads(row['raw_data']) if isinstance(row['raw_data'], str) else row['raw_data']
        raw = env['raw_data']
        stamp = dt.datetime.fromisoformat(env['generated_at'])
        if stamp.tzinfo:
            stamp = stamp.astimezone(SHANGHAI)
        if (not isinstance(raw, dict) or row['date'] != date or env['date'] != date
                or raw['date'] != date or stamp.date().isoformat() != date or stamp.hour < 16):
            raise ValueError('snapshot date or generation time mismatch')
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise DataNotReady('当日收盘后的盘后归档尚未完整入库') from exc
    if prefill.get('is_trading_day') is not True:
        raise DataNotReady('复盘预填未确认当日为开放日')
    market = prefill.get('market')
    if not isinstance(market, dict) or market.get('date') != date:
        raise DataNotReady('复盘预填缺少当日盘后快照')
    for key in DATED_BLOCKS + ('sector_industry', 'sector_concept'):
        block = raw.get(key)
        if key in EMPTY_ALLOWED and block is None and _verified_empty(conn, key, date):
            # Feed the explicitly verified empty source into this attempt only.
            market[key] = {'data': [], '_source': 'verified_empty_receipt'}
            continue
        if (not isinstance(block, dict) or block.get('error') or not block.get('_source')
                or block.get('status', 'success') not in ('success', 'complete', 'ok')):
            raise DataNotReady(f'{key} 来源缺失或失败')
        rows = block.get('data')
        if not isinstance(rows, list) or (not rows and key not in EMPTY_ALLOWED):
            raise DataNotReady(f'{key} 数据未就绪')
        if not rows and not _verified_empty(conn, key, date):
            raise DataNotReady(f'{key} 空榜缺少成功空回执')
        if any(not isinstance(r, dict) for r in rows):
            raise DataNotReady(f'{key} 数据结构异常')
        if key in DATED_BLOCKS and any(_day(r.get('trade_date')) != _day(date) for r in rows):
            raise DataNotReady(f'{key} 数据日期缺失或不是当日')
        if key == 'limit_step':
            codes = [canonical_stock_code(r.get('ts_code') or r.get('code')) for r in rows]
            if (any(not c for c in codes) or len(set(codes)) != len(codes)
                    or any(not _finite(r.get('nums'), positive=True)
                           or not float(r['nums']).is_integer() for r in rows)):
                raise DataNotReady('连板梯队股票身份或板数无效')
        elif key in DATED_BLOCKS:
            required = ('pct_chg', 'up_nums') if key == 'limit_cpt_list' else ('pct_change', 'net_amount')
            if any(not str(r.get('name') or '').strip()
                   or any(not _finite(r.get(field)) for field in required) for r in rows):
                raise DataNotReady(f'{key} 关键字段无效')
        else:
            if any(not str(r.get('name') or '').strip()
                   or not _finite(r.get('change_pct')) for r in rows):
                raise DataNotReady(f'{key} 板块名称或涨跌幅无效')
        if market.get(key) != block:
            raise DataNotReady(f'{key} 盘后快照正在更新，须重新读取')
    return {'status': 'ready', 'trade_date': date, 'post_generated_at': env['generated_at']}


def _finite(value, *, positive=False):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
        return math.isfinite(number) and (number > 0 if positive else True)
    except (ValueError, TypeError, OverflowError):
        return False


class CheckedRegistry:
    """Observe the existing calls, then check them before ranking or publication."""
    def __init__(self, registry):
        self.registry = registry
        self.results = {}

    def initialize_all(self):
        if callable(getattr(self.registry, 'initialize_all', None)):
            self.registry.initialize_all()

    def call(self, name, *args, **kwargs):
        result = self.registry.call(name, *args, **kwargs)
        self.results[name] = result
        return result

    def validate(self, date, prefill):
        for key in ('get_market_daily_quotes', 'get_stock_basic_list', 'get_stock_sw_industry_map'):
            result = self.results.get(key)
            if not getattr(result, 'success', False) or not getattr(result, 'data', None):
                raise DataNotReady(f'{key} 来源未就绪')
        quotes = self.results['get_market_daily_quotes'].data
        basics = self.results['get_stock_basic_list'].data
        if not isinstance(quotes, list) or not isinstance(basics, list) or len(quotes) < MIN_QUOTES:
            raise DataNotReady('全市场当日日线不足4000条或结构异常')
        codes = []
        for row in quotes:
            if not isinstance(row, dict):
                raise DataNotReady('日线结构异常')
            code = canonical_stock_code(row.get('ts_code') or row.get('code'))
            if (not code or _day(row.get('trade_date')) != _day(date)
                    or not _finite(row.get('pct_chg')) or not _finite(row.get('amount'))
                    or float(row['amount']) < 0 or not _finite(row.get('close'), positive=True)):
                raise DataNotReady('日线存在日期错位或无效量价')
            codes.append(code)
        if len(set(codes)) != len(codes):
            raise DataNotReady('日线股票代码重复')
        universe = {canonical_stock_code(r.get('ts_code') or r.get('code')): r.get('name')
                    for r in basics if isinstance(r, dict)}
        universe.pop('', None)
        # Explicitly exclude B shares from the A-share universe denominator.
        universe = {c: n for c, n in universe.items() if not c.startswith(('200', '201', '900'))}
        if not universe or len(set(codes) & universe.keys()) / len(universe) < .98:
            raise DataNotReady('当日日线覆盖不足上市A股的98%')
        if any(not universe.get(c) for c in codes):
            raise DataNotReady('日线股票身份或名称未完整匹配')
        rows = (prefill.get('market') or {}).get('stock_quotes', {}).get('data', [])
        mainland = [r for r in rows if str(r.get('code', '')).endswith(('.SH', '.SZ'))]
        if len(rows) != len(quotes) or not mainland:
            raise DataNotReady('用于筛选的行情不完整')
        if sum(bool(r.get('sw_l2')) for r in mainland) / len(mainland) < .98:
            raise DataNotReady('沪深行情申万行业覆盖不足98%')
        # Require an industry for every eligible large-turnover candidate;
        # tolerate only the small unmatched tail outside the admission universe.
        if any(not r.get('sw_l2') for r in rows if (r.get('amount_yi') or 0) >= 20):
            raise DataNotReady('成交额达到准入门槛的股票缺少行业分类')
        return {'quote_count': len(quotes), 'universe_count': len(universe)}
