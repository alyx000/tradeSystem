"""按沪深上市宇宙核验题材份额分母，逐日保留停牌证据和未解释的行情缺口。"""
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import math
import sqlite3
from zoneinfo import ZoneInfo

from analyzers import sector_increment as si
from analyzers.strong_stock_feedback import _code

SOURCE = 'tushare:suspend_d'
UNIVERSE_SOURCE = 'tushare:stock_basic:L+D+P:as_of'
SHANGHAI = ZoneInfo('Asia/Shanghai')


def current_receipt(response, day, now):
    if response is None or not response.success:
        return None
    try:
        # DataResult 默认使用运行主机的本地时间；在同一个进程中转为明确时区。
        at = datetime.fromisoformat(response.fetched_at).astimezone(SHANGHAI)
        if at > now or at.date().isoformat() != day or at.hour < 15:
            return dict(error='当日停牌回执采集时间错位')
        return dict(source=response.source, trade_date=day, received_at=at.isoformat(), rows=response.data)
    except (ValueError, TypeError, AttributeError):
        return dict(error='当日停牌回执采集时间非法')


def previous_receipt(conn, day):
    """只读前一开放日收盘后至当日结束时最新的原始回执。SQLite 入库时间为 UTC。"""
    start = datetime.combine(datetime.fromisoformat(day).date(), time(15), SHANGHAI)
    end = (start+timedelta(days=1)).replace(hour=0)
    bounds = [at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') for at in (start, end)]
    try:
        row = conn.execute(
            "SELECT id,provider,status,inserted_at,row_count,payload_json,payload_hash FROM raw_interface_payloads "
            "WHERE interface_name='regulatory_suspend' AND biz_date=? AND target_date=? "
            "AND inserted_at>=? AND inserted_at<? ORDER BY inserted_at DESC,id DESC LIMIT 1",
            (day, day, *bounds)).fetchone()
        if not row or row[2] != 'success':
            return None
        if row[1] != SOURCE:
            return dict(error='停牌归档来源未核验')
        if hashlib.sha256(row[5].encode('utf-8')).hexdigest() != row[6]:
            return dict(error='停牌归档哈希不符')
        payload = json.loads(row[5])
        rows = payload['rows']
        if (payload.get('provider') != SOURCE or payload.get('interface_name') != 'regulatory_suspend'
                or payload.get('biz_date') != day or payload.get('params', {}).get('trade_date') != day.replace('-', '')
                or not isinstance(rows, list) or row[4] != len(rows)):
            return dict(error='停牌归档元数据或行数不符')
        at = datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc).astimezone(SHANGHAI)
        return dict(source=SOURCE, trade_date=day, received_at=at.isoformat(), rows=rows, raw_payload_id=row[0])
    except sqlite3.Error:
        return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return dict(error='停牌归档结构非法')


def _halted_codes(receipt, day, *, archived):
    if receipt is None:
        return set()
    if isinstance(receipt, dict) and receipt.get('error'):
        raise ValueError(str(receipt['error']))
    if (not isinstance(receipt, dict) or receipt.get('source') != SOURCE or receipt.get('trade_date') != day
            or not isinstance(receipt.get('rows'), list)):
        raise ValueError('停牌回执来源、日期或结构不符')
    if archived and (type(receipt.get('raw_payload_id')) is not int or receipt['raw_payload_id'] <= 0):
        raise ValueError('停牌归档编号非法')
    at = datetime.fromisoformat(receipt['received_at'])
    if at.tzinfo is None or at.astimezone(SHANGHAI).date().isoformat() != day or at.astimezone(SHANGHAI).hour < 15:
        raise ValueError('停牌回执未在事实日收盘后取得')
    seen, halted = set(), set()
    for row in receipt['rows']:
        code = row['ts_code']
        if (_code(code) != code or code in seen or row.get('code', code) != code
                or row.get('trade_date') != day.replace('-', '')):
            raise ValueError('停牌行重复、日期或证券身份错位')
        seen.add(code)
        if row.get('suspend_type') not in ('S', 'R') or 'suspend_timing' not in row:
            raise ValueError('停牌类别或时段未核验')
        if si._eligible(code) and row['suspend_type'] == 'S' and row['suspend_timing'] in ('', None):
            halted.add(code)
    return halted


def denominator_coverage(block, amounts, receipts, previous, day):
    result = dict(status='partial', dates={})
    try:
        sources = [entry['source'] for entry in block.get('sources', [])]
        if sources[:2] != ['tushare:daily'] * 2:
            raise ValueError('两日成交额来源未核验')
        if sources[2:3] != [UNIVERSE_SOURCE] or (previous[:7] != day[:7] and sources[4:5] != [UNIVERSE_SOURCE]):
            raise ValueError('两日上市证券宇宙来源未核验')
        for offset, d in enumerate((previous, day)):
            quoted = {c for c, pair in amounts.items() if pair[offset] is not None}
            coverage = block['coverage'][d]
            missing = set(coverage['missing_codes'])
            if (not isinstance(coverage['missing_codes'], list) or len(missing) != len(coverage['missing_codes'])
                    or any(_code(c) != c or not si._eligible(c) for c in missing) or missing & quoted
                    or coverage['quote_count'] != len(quoted) or coverage['universe_count'] != len(quoted) + len(missing)
                    or not quoted or not math.isclose(coverage['ratio'], len(quoted) / coverage['universe_count'])):
                raise ValueError(f'{d} 沪深宇宙与逐股行情计数不符')
            receipt_error = None
            try:
                halted = _halted_codes(receipts.get(d), d, archived=d == previous)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                halted, receipt_error = set(), str(exc) or '停牌回执非法'
            result['dates'][d] = dict(quote_count=len(quoted), universe_count=coverage['universe_count'],
                                      confirmed_halts=sorted(missing & halted), unexplained_codes=sorted(missing - halted),
                                      conflicting_codes=sorted(halted - missing))
            if receipt_error:
                result['dates'][d]['receipt_error'] = receipt_error
        if all(not item['unexplained_codes'] and not item['conflicting_codes'] and not item.get('receipt_error')
               for item in result['dates'].values()):
            result['status'] = 'complete'
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result['reason'] = str(exc)
    return result
