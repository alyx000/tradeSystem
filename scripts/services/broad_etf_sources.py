"""两只沪市宽基 ETF 自身的未复权五分钟 K 线，保留来源原值及实际收包时刻。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from zoneinfo import ZoneInfo

import requests

from providers.tushare_transport import TushareMirrorClient
from services.course_cross_market import http_failure
from utils.network_env import use_http_proxy_for_data_collection


TZ = ZoneInfo('Asia/Shanghai')
FUNDS = {'512100.SH': '南方中证1000ETF', '510300.SH': '华泰柏瑞沪深300ETF'}
BENCHMARKS = {'000852.SH': '中证1000', '000300.SH': '沪深300'}
FIELDS = 'ts_code,trade_time,open,high,low,close,vol,amount'
EM_URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
ENDS = frozenset(range(575, 691, 5)) | frozenset(range(785, 901, 5))


def _read_tushare(code, day):
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise ValueError('项目未配置 TUSHARE_TOKEN')
    params = dict(ts_code=code, freq='5min', start_date=day+' 09:30:00', end_date=day+' 15:00:00')
    try:
        frame = TushareMirrorClient(token, timeout=8).etf_mins(fields=FIELDS, **params)
    except RuntimeError as exc:
        message = str(exc).lower()
        if any(word in message for word in ('权限', '无权', 'permission')):
            raise ValueError('etf_mins 上游明确报告权限不足') from None
        if '限流' in message or '频率' in message:
            raise ValueError('etf_mins 上游报告频率限制') from None
        raise ValueError('etf_mins 上游返回业务错误') from None
    # 以项目桥接器返回的原始表（行序、字段、原值）签名，不将其冒充 HTTP 原文。
    rows = frame.to_dict('records')
    encoded = json.dumps(dict(columns=list(frame.columns), rows=rows), default=str, ensure_ascii=False,
                         separators=(',', ':')).encode()
    return rows, dict(raw_sha256=hashlib.sha256(encoded).hexdigest(), hash_basis='返回表的列和原始行',
                      api='etf_mins', params=params)


def _read_eastmoney(code, day):
    symbol = code.split('.')[0]
    ymd = day.replace('-', '')
    params = dict(secid='1.'+symbol, klt='5', fqt='0', beg=ymd, end=ymd, lmt='1000',
                  fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56,f57')
    with requests.Session() as client:
        client.trust_env = use_http_proxy_for_data_collection()
        response = client.get(EM_URL, params=params, timeout=(3, 6),
                              headers={'User-Agent':'Mozilla/5.0', 'Referer':'https://quote.eastmoney.com/'})
        response.raise_for_status()
        packet = response.json()
        data = packet.get('data')
        if packet.get('rc') != 0 or not isinstance(data, dict):
            raise ValueError('东财未返回有效 K 线响应')
        if (data.get('code') != symbol or str(data.get('market')) != '1'
                or not isinstance(data.get('klines'), list)):
            raise ValueError('东财 K 线代码、市场或结构未通过核验')
        if code in BENCHMARKS and data.get('name') != BENCHMARKS[code]:
            raise ValueError('东财返回的现货指数名称不匹配')
        return data['klines'], dict(raw_sha256=hashlib.sha256(response.content).hexdigest(),
                                    hash_basis='HTTP 响应字节', api=EM_URL, params=params,
                                    returned_name=data.get('name'))


def _decimal(value):
    try:
        num = Decimal(str(value))
        if not num.is_finite() or not math.isfinite(float(num)):
            raise ValueError
        return num
    except (InvalidOperation, ValueError):
        raise ValueError('价量字段含非有限数值') from None


def _bar(raw, stamp, values, provider):
    op, hi, lo, cl = map(_decimal, values[:4])
    if not (0 < lo <= op <= hi and lo <= cl <= hi):
        raise ValueError('高低开收价格不合理')
    bar = dict(source_time=stamp.isoformat(), source_label=str(raw['trade_time']) if isinstance(raw, dict)
               else raw.split(',',1)[0], open=float(op), high=float(hi), low=float(lo), close=float(cl), raw=raw)
    if provider == 'eastmoney_index':
        return bar
    vol, amt = map(_decimal, values[4:])
    if vol < 0 or amt < 0:
        raise ValueError('成交量或成交额为负')
    multiplier = 100 if provider == 'eastmoney' else 1
    shares = vol * multiplier
    if shares != shares.to_integral_value() or (amt == 0) != (shares == 0):
        raise ValueError('成交量单位或量额零值矛盾')
    # 允许东财以整手显示的舍入和源成交额的元位舍入，防止将手当成份。
    tolerance = hi * (multiplier - 1) + 1
    if shares and not (lo * shares - tolerance <= amt <= hi * shares + tolerance):
        raise ValueError('按来源单位核对成交额、成交量和区间价格不符')
    return dict(bar, volume=int(shares), volume_unit='份', raw_volume_unit='手' if multiplier == 100 else '份',
                amount_yuan=float(amt))


def decode(provider, code, day, rows):
    bars, conflicts, rejected = {}, set(), []
    if not isinstance(rows, list):
        raise ValueError('源返回的行情列表无效')
    for idx, row in enumerate(rows):
        if provider == 'tushare':
            if not isinstance(row, dict) or row.get('ts_code') != code:
                raise ValueError('Tushare 返回错误证券代码')
            label = row.get('trade_time')
        else:
            if not isinstance(row, str):
                raise ValueError('东财原始 K 线结构异常')
            label, *values = row.split(',')
        try:
            stamp = datetime.fromisoformat(str(label))
        except (ValueError, TypeError):
            raise ValueError('分钟时间标签无效') from None
        stamp = stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
        if stamp.date().isoformat() != day:
            raise ValueError('响应含非请求交易日的行情')
        key = stamp.strftime('%H:%M')
        if stamp.second or stamp.microsecond or stamp.hour * 60 + stamp.minute not in ENDS:
            rejected.append(dict(index=idx, source_label=str(label), reason='不属于连续交易的5分钟结束时刻'))
            continue
        if key in conflicts:
            continue
        try:
            if provider == 'tushare':
                values = [row[f] for f in ('open','high','low','close','vol','amount')]
            else:
                op, cl, hi, lo, vol, amt = values
                values = [op, hi, lo, cl, vol, amt]
            candidate = _bar(row, stamp, values, provider)
            if key in bars and any(candidate.get(f) != bars[key].get(f) for f in
                                   ('open','high','low','close','volume','amount_yuan')):
                raise ValueError('同一闭合时点存在冲突')
            bars.setdefault(key, candidate)
        except (KeyError, ValueError, TypeError) as exc:
            bars.pop(key, None)
            conflicts.add(key)
            rejected.append(dict(index=idx, source_label=str(label), reason=str(exc)))
    return bars, rejected


def fetch_source(provider, code, day):
    """各调用仅发一只基金一次有界请求；不落盘，不持久化账户或访问凭据。"""
    if provider not in ('tushare','eastmoney') or code not in FUNDS or date.fromisoformat(day).isoformat() != day:
        raise ValueError('未登记的来源、基金或日期')
    return _fetch(provider, code, day)


def fetch_benchmark(code, day):
    """同窗口两只现货指数，只保存可核验的价格。"""
    if code not in BENCHMARKS or date.fromisoformat(day).isoformat() != day:
        raise ValueError('未登记的现货指数或日期')
    return _fetch('eastmoney_index', code, day)


def _fetch(provider, code, day):
    result = dict(provider=provider, code=code, trade_date=day, requested_at=datetime.now(TZ).isoformat(),
                  status='source_failed', bars={}, gap=None, raw_sha256=None)
    try:
        loader = _read_tushare if provider == 'tushare' else _read_eastmoney
        rows, meta = loader(code, day)
        result.update(meta, received_at=datetime.now(TZ).isoformat(), raw_count=len(rows))
        bars, rejected = decode(provider, code, day, rows)
        result.update(bars=bars, rejected=rejected)
        if bars:
            result['status'] = 'complete' if len(bars) == 48 else 'partial'
        if len(bars) != 48:
            result['gap'] = f'来源返回可核验的闭合时点 {len(bars)}/48'
    except requests.RequestException as exc:
        result['gap'] = http_failure(exc)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result['gap'] = str(exc)
    result.setdefault('received_at', datetime.now(TZ).isoformat())
    return result
