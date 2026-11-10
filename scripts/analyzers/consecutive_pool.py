"""连板股票池数量与成交额：复用同批涨停名单/日线，只保存事实证据。"""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from datetime import date, datetime
import math
import sqlite3
from zoneinfo import ZoneInfo

from analyzers.strong_stock_feedback import _row_code
from utils.limit_identity import verified_streak

KEY = 'consecutive_pool'
SCHEMA = 'consecutive-pool-v1'
NOTE = ('当日收盘涨停且连续涨停≥2天，剔ST、退市和B股；按涨停来源覆盖范围统计，'
        '不把N天M板等同连续涨停。每日成员变化，非固定组合。成交额采用当日日线amount'
        '（千元÷100000＝亿元），不含盘后固定价格交易额，不是封单金额或资金净流入。')
LIMIT_SOURCES = {'tushare:limit_list_d', 'akshare:stock_zt_pool_em'}


def missing(day, reason, status='source_failed'):
    return dict(schema=SCHEMA, trade_date=day, status=status, count=None, amount_yi=None,
                amount_count=0, rows=[], gaps=[str(reason)], definition=NOTE)


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def calculate(day, evidence):
    """从保存的证据重算；名单完整和成交额完整分开，不把部分和当总额。"""
    result = missing(day, '')
    try:
        date.fromisoformat(day)
        if evidence['trade_date'] != day:
            raise ValueError('来源日期错位')
        limit = evidence['limit_up']
        source = limit.get('_source')
        stocks = limit.get('stocks')
        if (limit.get('error') or source not in LIMIT_SOURCES or not isinstance(stocks, list)
                or type(limit.get('count')) is not int or limit['count'] != len(stocks)
                or limit.get('status', 'complete') not in {'complete', 'ok', 'success'}):
            raise ValueError('涨停名单缺失、失败或覆盖计数不符')
        selected, seen, unknown = [], set(), []
        for row in stocks:
            code = _row_code(row)
            name = row.get('name')
            if code in seen or not isinstance(name, str) or not name.strip():
                raise ValueError('涨停名单股票重复或名称缺失')
            if row.get('trade_date') is not None and str(row['trade_date']).replace('-', '') != day.replace('-', ''):
                raise ValueError('涨停名单行日期错位')
            seen.add(code)
            if 'ST' in name.upper() or '退' in name or code.startswith(('200', '201', '900')):
                continue
            height, basis = verified_streak(row, source)
            if height is None:
                unknown.append(code)
            elif height >= 2:
                selected.append(dict(code=code, name=name.strip(), height=height, streak_basis=basis, amount_yi=None))
        # 逐股行情只要求该池100%匹配；全市场其他股票缺失不污染本池的覆盖分母。
        quotes, quote_error = {}, None
        try:
            if evidence.get('quote_source') != 'tushare:daily' or not isinstance(evidence.get('quotes'), list):
                raise ValueError('日线成交额来源缺失或单位未核验')
            for row in evidence['quotes']:
                code = _row_code(row)
                if code in quotes or str(row.get('trade_date', '')).replace('-', '') != day.replace('-', ''):
                    raise ValueError('日线股票重复或日期错位')
                quotes[code] = row
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            quotes, quote_error = {}, str(exc)
        for row in selected:
            amount = _number(quotes.get(row['code'], {}).get('amount'))
            row['amount_yi'] = amount / 100000 if amount is not None else None
        count = len(selected) if not unknown else None
        covered = sum(r['amount_yi'] is not None for r in selected)
        amount = math.fsum(r['amount_yi'] for r in selected) if count is not None and covered == count else None
        gaps = []
        if unknown:
            gaps.append(f'{len(unknown)}只涨停股连板天数未核验，池总数和总额未计算')
        if covered != len(selected):
            gaps.append(f'已确认连板股成交额覆盖{covered}/{len(selected)}，总额未计算')
            if quote_error:
                gaps.append(quote_error)
        result.update(status='partial' if gaps else 'complete', count=count, amount_yi=amount,
                      amount_count=covered, rows=sorted(selected, key=lambda r: (-r['height'], r['code'])),
                      gaps=gaps, unknown_codes=unknown, limit_source=source,
                      quote_source=evidence.get('quote_source'), evidence=deepcopy(evidence))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result['gaps'] = [str(exc)]
    return result


def collect(day, *, limit_up, quote_result, db_path=None, now=None):
    """不调用provider；沿用post定时任务与标准信封双写。"""
    try:
        from db.connection import get_readonly_connection
        with closing(get_readonly_connection(db_path)) as conn:
            row = conn.execute("SELECT is_open FROM trade_calendar WHERE exchange='SSE' AND date=?", (day,)).fetchone()
        if row is None or row[0] not in (0, 1):
            return missing(day, '目标日SSE交易日历缺失')
        if row[0] == 0:
            return missing(day, '休市日不采集', 'skipped')
        current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if current.tzinfo is None:
            raise ValueError('采集时刻缺少时区')
        current = current.astimezone(ZoneInfo('Asia/Shanghai'))
        if day > current.date().isoformat() or (day == current.date().isoformat() and current.hour < 16):
            return missing(day, '尚未完成盘后数据窗口', 'skipped')
        evidence = dict(trade_date=day, limit_up=deepcopy(limit_up),
                        quote_source=quote_result.source if quote_result.success else None,
                        quotes=deepcopy(quote_result.data) if quote_result.success else None)
        # 只归档涨停名单对应日线，减少重复保存全市场行情。
        if isinstance(evidence['quotes'], list) and isinstance(limit_up, dict):
            codes = {_row_code(r) for r in limit_up.get('stocks', [])}
            evidence['quotes'] = [r for r in evidence['quotes'] if _row_code(r) in codes]
        block = calculate(day, evidence)
        block['collected_at'] = current.isoformat()
        return block
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        return missing(day, str(exc))


def from_archive(payload, day):
    if not isinstance(payload, dict) or str(payload.get('date')) != day:
        return missing(day, '当日盘后归档缺失或日期错位', 'missing-data')
    raw = payload.get('raw_data')
    if not isinstance(raw, dict):
        return missing(day, '盘后原始事实缺失', 'missing-data')
    if KEY in raw:
        block = raw[KEY]
        if (not isinstance(block, dict) or block.get('schema') != SCHEMA
                or block.get('trade_date') != day):
            return missing(day, '连板池快照格式或日期非法')
        if block.get('status') not in {'complete', 'partial'}:
            return missing(day, '；'.join(block.get('gaps') or ['连板池来源失败']),
                           block['status'] if block.get('status') in {'skipped', 'source_failed'} else 'source_failed')
        return calculate(day, block.get('evidence'))
    # 旧归档只读重算；不使用涨停provider旧amount_billion（历史单位不可靠）。
    zhong = raw.get('zhong_prefix_effect')
    stored = zhong.get('evidence', {}) if isinstance(zhong, dict) and zhong.get('trade_date') == day else {}
    if not isinstance(stored, dict):
        stored = {}  # 单日坏金额证据只降级该指标，不中断整个历史窗口。
    block = calculate(day, dict(trade_date=day, limit_up=raw.get('limit_up'),
                                quote_source=stored.get('quote_source'), quotes=stored.get('quotes')))
    block['historical_recalculation'] = True
    return block


def summary(block):
    count = '待核' if block.get('count') is None else str(block['count'])
    amount = '待核' if block.get('amount_yi') is None else f"{block['amount_yi']:.2f}"
    return f'连板股票池（{count}只）（{amount}亿元）'
