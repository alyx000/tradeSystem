"""断板复核专用的东方财富未复权一分钟 K 线备源。"""
from datetime import date, datetime
import hashlib
import re

import requests

from providers.base import DataResult


SOURCE = 'eastmoney:stock/kline/get:1min'
URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'


def get_stock_minutes(code, day):
    """请求单市场、单代码、单日。分钟窗口、OHLC 和量额由共同校验器检查。"""
    try:
        if not re.fullmatch(r'\d{6}\.(SH|SZ)', code) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('请求身份或日期非法')
        market = 1 if code.endswith('.SH') else 0
        compact = day.replace('-', '')
        params = dict(secid=f'{market}.{code[:6]}', fields1='f1,f2,f3,f4,f5,f6',
                      fields2='f51,f52,f53,f54,f55,f56,f57,f58', klt=1, fqt=0,
                      beg=compact, end=compact, lmt=1000)
        response = requests.get(URL, params=params, timeout=6, allow_redirects=False)
        if response.status_code != 200:
            return DataResult(None, SOURCE, error=f'东方财富 HTTP {response.status_code}')
        payload = response.json()
        block = payload['data']
        if (payload['rc'] != 0 or not isinstance(block, dict) or block['code'] != code[:6]
                or type(block['market']) is not int or block['market'] != market):
            raise ValueError('响应身份不符')
        bars = block['klines']
        if not isinstance(bars, list) or not 0 < len(bars) < 1000:
            raise ValueError('无有效单日 K 线')
        rows = []
        for bar in bars:
            stamp, opening, close, high, low, lots, amount, _ = bar.split(',')
            minute = datetime.strptime(stamp, '%Y-%m-%d %H:%M')
            if minute.strftime('%Y-%m-%d %H:%M') != stamp or minute.date().isoformat() != day:
                raise ValueError('分钟日期错位')
            rows.append(dict(ts_code=code, trade_time=stamp+':00', open=opening, close=close,
                             high=high, low=low, vol=float(lots)*100, amount=amount, raw_lots=lots))
        checksum = hashlib.sha256(response.content).hexdigest()
        return DataResult(rows, SOURCE, source_url=URL,
                          note=f'原始响应SHA256={checksum}；未复权单日1分钟K线；响应市场与代码已核验；'
                               '成交量按源手数×100折股，成交额元；保留源零成交行，首根按源分桶')
    except requests.RequestException as exc:
        return DataResult(None, SOURCE, error='东方财富请求失败：'+type(exc).__name__)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return DataResult(None, SOURCE, error='东方财富响应身份、单日分钟或字段校验失败')
