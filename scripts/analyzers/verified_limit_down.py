"""用同日未复权收盘价及逐股跌停价重建名单，逐股对账交易覆盖。"""
from __future__ import annotations

from datetime import datetime
from analyzers.strong_stock_feedback import _number, _row_code

SOURCE = 'tushare:daily+stk_limit'
METHODS = dict(quotes='get_market_daily_quotes', limits='get_stock_limit_prices',
               universe='get_stock_universe_as_of', st='get_stock_st', suspended='get_suspend_list')


def _rows(items, day, *, dated=True, allow_empty=False):
    if not isinstance(items, list) or (not items and not allow_empty):
        raise ValueError('来源为空或结构错误')
    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('来源行结构错误')
        if dated and str(item.get('trade_date', '')).replace('-', '') != day:
            raise ValueError('来源日期错位')
        # 跌停价表也含B股，部分镜像的B股交易所后缀有误。
        if str(item.get('ts_code', '')).startswith(('200', '201', '900')):
            continue
        code = _row_code(item)
        if code in result:
            raise ValueError(f'{code} 来源行重复')
        result[code] = item
    return result


def verified_list(day, *, quotes, limits, universe, st, suspended):
    date = day.replace('-', '')
    quotes = _rows(quotes, date)
    if not isinstance(limits, list):
        raise ValueError('缺少逐股跌停价')
    # stk_limit 可同时返回基金。仅取沪深北股票代码范围。
    limits = _rows([r for r in limits if isinstance(r, dict) and
                    str(r.get('ts_code') or r.get('code')).startswith(('00','30','6','4','8','92'))], date)
    names = _rows(universe, date, dated=False)
    st = _rows(st, date)
    suspended = _rows(suspended, date, allow_empty=True)
    active = {}
    for code, row in names.items():
        start = str(row.get('list_date') or '').replace('-', '')
        end = str(row.get('delist_date') or '').replace('-', '')
        datetime.strptime(start, '%Y%m%d')
        if end:
            datetime.strptime(end, '%Y%m%d')
        if start <= date and (not end or date <= end):
            active[code] = row
    if not quotes or set(quotes) - set(limits) or set(limits) - set(active):
        raise ValueError('日线、跌停价与上市证券范围未对齐')
    missing = set(active) - set(quotes)
    if any(suspended.get(c, {}).get('suspend_type') != 'S'
           or suspended[c].get('suspend_timing') for c in missing):
        raise ValueError('有上市股票缺日线且无全天停牌证明')
    if any(c in quotes and r.get('suspend_type') == 'S' and not r.get('suspend_timing')
           for c, r in suspended.items()):
        raise ValueError('全天停牌股票存在当日行情')
    stocks = []
    for code, quote in sorted(quotes.items()):
        name = active[code].get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'{code} 缺证券名称')
        close = _number(quote['close'], positive=True)
        down = _number(limits[code]['down_limit'])
        if down == 0 and limits[code].get('up_limit') == 99999.99:
            continue  # 不设日涨跌幅限制的价格记录
        if down <= 0 or close < down:
            raise ValueError(f'{code} 收盘价或跌停价异常')
        if close == down and '退' not in name:
            stocks.append(dict(code=code, name=st.get(code, {}).get('name') or name,
                               is_st=code in st or 'ST' in name.upper(), close=close,
                               down_limit=down, pct_chg=quote.get('pct_chg')))
    return dict(_source=SOURCE, trade_date=day, status='complete', count=len(stocks),
                count_ex_st=sum(not row['is_st'] for row in stocks), stocks=stocks,
                coverage=dict(active=len(active), quoted=len(quotes), suspended_without_quote=len(missing)))


def collect(registry, day, *, quotes=None, st=None):
    supplied = dict(quotes=quotes, st=st)
    try:
        inputs = {}
        for key, method in METHODS.items():
            response = supplied.get(key)
            if response is None:
                response = registry.call(method, day)
            if not response.success:
                raise ValueError(f'{method} 来源失败')
            inputs[key] = response.data
        return verified_list(day, **inputs)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as exc:
        return dict(_source=SOURCE, trade_date=day, status='source_failed', error=str(exc))
