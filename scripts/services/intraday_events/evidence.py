"""盘中采样使用的只读交易日历、归档和数字校验。"""
from datetime import date, timedelta
import hashlib
import json
import math
import re


class EvidenceError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise EvidenceError('数值不能为布尔值')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise EvidenceError('数值缺失') from None
    if not math.isfinite(value) or (positive and value <= 0):
        raise EvidenceError('数值非法')
    return value


def calendar(conn, day):
    target = date.fromisoformat(day)
    # 覆盖前一年：元旦及长假后不能用自然日回退，也不能取最近行情代替。
    start = date(target.year - 1, 1, 1)
    end = date(target.year, 12, 31)
    rows = conn.execute('SELECT date,is_open,exchange FROM trade_calendar WHERE date BETWEEN ? AND ? ORDER BY date',
                        (start.isoformat(), end.isoformat())).fetchall()
    # 只强制目标年；跨年前20个开放日不足时再强制前一年。
    mapping = {r[0]: (r[1], r[2]) for r in rows}
    def year_complete(year):
        first, last = date(year, 1, 1), date(year, 12, 31)
        expected = {(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)}
        actual = {d for d in mapping if d.startswith(str(year)+'-')}
        if actual != expected or any(mapping[d][0] not in (0, 1) or mapping[d][1] != 'SSE' for d in expected):
            raise EvidenceError(f'{year}年完整SSE日历缺失或非法')
    year_complete(target.year)
    opens = [d for d in sorted(mapping) if mapping[d] == (1, 'SSE') and d < day]
    if len([d for d in opens if d.startswith(str(target.year))]) < 25:
        year_complete(target.year-1)
    if len(opens) < 25:
        raise EvidenceError('严格前序开放日不足25日')
    return dict(is_open=mapping[day][0] == 1, previous=opens[-1], history=opens[-25:],
                dates={d: v[0] for d, v in mapping.items()})


def archive(conn, day):
    row = conn.execute('SELECT raw_data FROM daily_market WHERE date=?', (day,)).fetchone()
    if row is None:
        raise EvidenceError(f'{day}盘后归档缺失')
    try:
        obj = json.loads(row[0])
        raw = obj['raw_data']
        if obj['date'] != day or raw['date'] != day:
            raise EvidenceError('归档日期不符')
        return raw, dict(date=day, source='daily_market.raw_data', sha256=hashlib.sha256(row[0].encode()).hexdigest())
    except (KeyError, TypeError, json.JSONDecodeError):
        raise EvidenceError('盘后归档结构损坏') from None


def valid_source(block):
    return (isinstance(block, dict) and not block.get('error') and not block.get('errors')
            and block.get('status', 'complete') in {'complete', 'ok', 'success'})


def limit_rows(raw, expected_day=None, key='limit_up'):
    block = raw.get(key, {})
    if not isinstance(block, dict):
        raise EvidenceError(key+'名单结构非法')
    rows = block.get('stocks')
    day = expected_day or raw.get('date')
    allowed = {'tushare:limit_list_d', 'akshare:stock_zt_pool_em'} if key=='limit_up' else {'tushare:limit_list_d', 'akshare:stock_zt_pool_dtgc_em', 'tushare:daily+stk_limit'}
    if (not day or not valid_source(block) or not isinstance(rows, list) or block.get('count') != len(rows)
            or block.get('_source') not in allowed
            or any(not isinstance(r,dict) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(r.get('code','')))
                   or any(str(r[k]).replace('-','') != day.replace('-','') for k in ('trade_date','date') if k in r) for r in rows)
            or any(str(block[k]).replace('-','') != day.replace('-','') for k in ('trade_date','date') if k in block)
            or len({r.get('code') for r in rows}) != len(rows)):
        raise EvidenceError(key+'名单失败、不完整、错日或重复')
    return rows
