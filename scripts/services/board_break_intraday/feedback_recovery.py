"""按严格事实日重算断板反馈，保留原盘后归档和正式运行批次。"""
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import re

import yaml

from analyzers.board_break_feedback import collect_board_break_feedback
from providers.base import DataResult
from services.low_price_effect_history import _atomic_text
from . import data, store


SCHEMA = 'board-break-feedback-recovery-v1'
LIMIT_INPUTS = (('limit_up',), ('limit_up',), ('limit_up', 'limit_down'))


def _limit_hashes(raw, day, keys):
    hashes = {}
    for key in keys:
        section = raw[key]
        rows = section['stocks']
        source = section['_source']
        allowed = {'tushare:limit_list_d', 'akshare:stock_zt_pool_em'} if key == 'limit_up' else {
            'tushare:limit_list_d', 'akshare:stock_zt_pool_dtgc_em', 'tushare:daily+stk_limit'}
        codes = set()
        if (source not in allowed or not data.valid_source(section) or not isinstance(rows, list)
                or section['count'] != len(rows)
                or any(str(section[field]).replace('-', '') != day.replace('-', '')
                       for field in ('date', 'trade_date') if field in section)):
            raise data.EvidenceError(f'{day} {key} 名单失败、不完整或错日')
        pattern = r'\d{6}(?:\.(?:SH|SZ|BJ))?' if source.startswith('akshare:') else r'\d{6}\.(SH|SZ|BJ)'
        for row in rows:
            code = row.get('code') if isinstance(row, dict) else None
            if (not isinstance(code, str) or not re.fullmatch(pattern, code) or code[:6] in codes
                    or any(str(row[field]).replace('-', '') != day.replace('-', '')
                           for field in ('date', 'trade_date') if field in row)):
                raise data.EvidenceError(f'{day} {key} 名单代码非法、重复或错日')
            codes.add(code[:6])
        hashes[key] = data.digest(dict(source=source, stocks=rows))
    return hashes


def _archive_sources(conn, days, outcome_raw):
    sources = []
    for day, keys in zip(days, LIMIT_INPUTS):
        raw = outcome_raw if day == days[-1] else data.archive(conn, day)[0]
        sources.append(dict(date=day, limit_sha256=_limit_hashes(raw, day, keys)))
    return sources


def _valid_quote_receipt(receipt, day):
    return (receipt['date'] == day and receipt['source'] == 'tushare:daily'
            and type(receipt['rows']) is int and receipt['rows'] >= 4000
            and re.fullmatch(r'[0-9a-f]{64}', receipt['sha256']))


def load_recovery(conn, directory, days, outcome_raw):
    day = days[-1]
    path = Path(directory) / f'{day}.json'
    try:
        content = path.read_bytes()
        payload = json.loads(content)
        meta = payload['recovery']
        block = payload['raw_data']['board_break_feedback']
        original = outcome_raw.get('board_break_feedback') or {}
        collected = datetime.fromisoformat(meta['recovered_at'])
        quotes = meta['quote_sources']
        if (payload['schema'] != SCHEMA or payload['date'] != day
                or meta['status'] != 'complete' or not meta['input_by'].strip()
                or collected.tzinfo is None or collected.astimezone(data.TZ).date() <= date.fromisoformat(day)
                or meta['original_status'] != original.get('status', 'missing-data')
                or meta['original_feedback_sha256'] != data.digest(original)
                or meta['feedback_sha256'] != data.digest(block)
                or len(quotes) != 2 or not all(_valid_quote_receipt(r, d) for r, d in zip(quotes, days[1:]))
                or meta['archive_limits'] != _archive_sources(conn, days, outcome_raw)):
            raise data.EvidenceError('断板反馈补采回执或原始来源不符')
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise data.EvidenceError('断板反馈补采回执缺失、损坏或来源已变化') from None
    return block, dict(date=day, source='board_break_feedback_recovery', path=str(path.resolve()),
                       sha256=store.sha(content), recovered_at=meta['recovered_at'],
                       original_status=meta['original_status'])


class _HistoricalInputs:
    def __init__(self, cal, provider):
        self.cal = cal
        self.provider = provider
        self.quotes = {}

    def call(self, method, day):
        if method == 'is_trade_day':
            return DataResult(self.cal['dates'][day] == 1, 'trade_calendar:SSE')
        if method != 'get_market_daily_quotes':
            return DataResult(None, 'recovery', error='补采仅接受已归档名单及全市场日线')
        if day in self.quotes:
            return self.quotes[day]
        result = self.provider.get_market_daily_quotes(day)
        rows = result.data
        if not result.success or result.source != 'tushare:daily' or not isinstance(rows, list) or len(rows) < 4000:
            raise data.EvidenceError(f'{day}全市场日线失败或覆盖不足')
        codes = set()
        for row in rows:
            code = row.get('ts_code') if isinstance(row, dict) else None
            if (not isinstance(code, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code)
                    or row.get('code', code) != code or code[:6] in codes
                    or str(row.get('trade_date')).replace('-', '') != day.replace('-', '')):
                raise data.EvidenceError(f'{day}全市场日线日期或代码不符')
            codes.add(code[:6])
        self.quotes[day] = result
        return result


def recover(conn, day, input_by, daily_dir, directory, provider_factory):
    current = datetime.now(data.TZ)
    target = date.fromisoformat(day)
    if target.isoformat() != day or target >= current.date() or not input_by.strip():
        raise data.EvidenceError('须指定已完成的事实日和请求者')
    cal = data.calendar(conn, day)
    if not cal['is_open']:
        raise data.EvidenceError('事实日不是SSE开放日')
    days = [*cal['history'][-2:], day]
    outcome_raw, original_source = data.archive(conn, day)
    original = outcome_raw.get('board_break_feedback') or {}
    try:
        block = data.checked_feedback(conn, original, days)
    except (data.EvidenceError, KeyError, TypeError):
        pass
    else:
        return dict(status='already_complete', date=day, sample_count=block['sample_count'],
                    source=original_source['source'])

    with store.lock(directory, 'feedback-recovery-' + day):
        try:
            block, source = load_recovery(conn, directory, days, outcome_raw)
            data.checked_feedback(conn, block, days)
        except (data.EvidenceError, KeyError, TypeError):
            pass
        else:
            return dict(status='already_recovered', sample_count=block['sample_count'], **source)

        archive_sources = _archive_sources(conn, days, outcome_raw)
        originals = {}
        for evidence, keys in zip(archive_sources, LIMIT_INPUTS):
            source_day = evidence['date']
            path = Path(daily_dir) / source_day / 'post-market.yaml'
            content = path.read_bytes()
            envelope = yaml.load(content, Loader=yaml.CSafeLoader)
            if (envelope['date'] != source_day or envelope['raw_data']['date'] != source_day
                    or _limit_hashes(envelope['raw_data'], source_day, keys) != evidence['limit_sha256']):
                raise data.EvidenceError(f'{source_day}盘后名单与数据库归档不一致')
            originals[path] = store.sha(content)

        inputs = _HistoricalInputs(cal, provider_factory())
        block = collect_board_break_feedback(inputs, day, outcome_raw, daily_dir=Path(daily_dir))
        data.checked_feedback(conn, block, days)
        if not block['sample_count']:
            raise data.EvidenceError('此补采入口仅处理有反馈样本的日线缺口')
        if len(inputs.quotes) != 2 or any(path_hash != store.sha(path.read_bytes()) for path, path_hash in originals.items()):
            raise data.EvidenceError('回补日线不齐或盘后归档在采集中变化')
        latest, _ = data.archive(conn, day)
        if (data.digest(latest.get('board_break_feedback') or {}) != data.digest(original)
                or _archive_sources(conn, days, latest) != archive_sources):
            raise data.EvidenceError('数据库事实在采集中变化')
        quote_sources = [dict(date=d, source=r.source, rows=len(r.data), sha256=data.digest(r.data))
                         for d, r in inputs.quotes.items()]
        sources = [dict(path=str(path.resolve()), sha256=sha) for path, sha in originals.items()]
        meta = dict(status='complete', original_status=original.get('status', 'missing-data'),
                    original_feedback_sha256=data.digest(original), input_by=input_by.strip(),
                    recovered_at=datetime.now(data.TZ).isoformat(), feedback_sha256=data.digest(block),
                    sources=sources, archive_limits=archive_sources, quote_sources=quote_sources)
        payload = dict(schema=SCHEMA, date=day, recovery=meta, raw_data=dict(board_break_feedback=block))
        _atomic_text(Path(directory) / f'{day}.json', store.encode(payload).decode())
        _, source = load_recovery(conn, directory, days, latest)
        return dict(status='complete', sample_count=block['sample_count'], quote_sources=quote_sources, **source)
