"""用此前五个 SSE 开放日的相同采样窗口，对比个股首次穿越的每分钟成交额。"""
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
import json
import sqlite3
from statistics import median

from db.connection import get_readonly_connection
from .evidence import calendar, digest, number
from services.intraday_monitor.guards import shanghai_now
from services.intraday_summary.state import atomic_write_text
from .alerts import alert_window
from .analytics import comparable
from .archive import read_report

DAYS = 5
_CACHE_NAME = '.turnover-baseline-v1.json'
_DATA_ERRORS = (OSError, ValueError, KeyError, TypeError)
_CALENDAR_ERRORS = _DATA_ERRORS + (sqlite3.Error,)


def _timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('报价或采样时间缺时区')
    return shanghai_now(stamp)


def _slot(snapshot):
    captured = _timestamp(snapshot['captured_at'])
    if captured.date().isoformat() != snapshot['trade_date']:
        raise ValueError('采样事实日不符')
    window = alert_window(captured)
    if not window:
        raise ValueError('采样不在连续交易时段')
    if snapshot.get('scheduled_at'):
        plan = _timestamp(snapshot['scheduled_at'])
        if (plan.date() != captured.date() or plan.hour * 60 + plan.minute != window[1]
                or plan.second or plan.microsecond or plan > captured):
            raise ValueError('计划时点与实际采样槽不符')
    return window[1]


def _sample(code, first, last, source):
    if not first.get('source') or first['source'] != source or last.get('source') != source:
        raise ValueError('两端行情与当次穿越来源不同')
    if first.get('interval_minutes') != 10 or not first.get('context_sha256') or not comparable(first, last):
        raise ValueError('两端采样不满足同半场相邻窗口')
    if _slot(last) - _slot(first) != 10:
        raise ValueError('起止计划槽不相邻')
    before = first['stocks'].get(code)
    after = last['stocks'].get(code)
    if not before or not after:
        raise ValueError('个股缺一端行情')
    timestamps, amounts = [], []
    for snapshot, row in ((first, before), (last, after)):
        quote_at = _timestamp(row['quote_at'])
        at = _timestamp(snapshot['captured_at'])
        if at.date() != quote_at.date() or not 0 <= (at - quote_at).total_seconds() <= 90:
            raise ValueError('个股报价与采样时间不匹配')
        timestamps.append(quote_at)
        amount = number(row['amount'])
        if amount < 0:
            raise ValueError('累计成交额无效')
        amounts.append(amount)
    seconds = (timestamps[1] - timestamps[0]).total_seconds()
    if seconds <= 0 or amounts[1] < amounts[0]:
        raise ValueError('个股报价时间未前进或累计成交额倒退')
    amount = amounts[1] - amounts[0]
    return dict(date=last['trade_date'], source=source, start_batch=first.get('batch'), end_batch=last.get('batch'),
                start_quote_at=timestamps[0].isoformat(), end_quote_at=timestamps[1].isoformat(),
                amount_yuan=amount, quote_seconds=seconds, yuan_per_minute=number(amount * 60 / seconds))


def _history_dates(day, history_days, db_path):
    if history_days is None:
        with closing(get_readonly_connection(db_path)) as conn:
            result = calendar(conn, day)
        if not result['is_open']:
            raise ValueError('目标日非 SSE 开放日')
        history_days = result['history']
    days = list(history_days[-DAYS:])
    if (len(days) != DAYS or days != sorted(set(days)) or any(date.fromisoformat(d) >= date.fromisoformat(day)
                                                              for d in days)):
        raise ValueError('前五个开放日不完整')
    return days


def _load_cache(path):
    try:
        data = json.loads(path.read_bytes())
        entries = data['entries']
        if isinstance(entries, dict) and data['sha256'] == digest(entries):
            return entries
    except _DATA_ERRORS:
        pass
    return {}


def _wanted_codes(snapshot, by_slot):
    if snapshot.get('interval_minutes') != 10:
        return set()
    return by_slot.get(_slot(snapshot), set())


def _read_quote(folder, by_slot, cache):
    receipt = json.loads((folder / 'receipt.json').read_bytes())
    day = folder.parent.name
    path = folder / 'report.json'
    stat = path.stat()
    fingerprint = [digest(receipt), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    key = f'{day}/{folder.name}'
    entry = cache.get(key) or {}
    retained = set()
    if entry.get('fingerprint') == fingerprint:
        retained = set(entry['codes'])
        if _wanted_codes(entry['snapshot'], by_slot) <= retained:
            return entry['snapshot'], False
    report = read_report(folder, receipt)
    snapshot = report.get('snapshot')
    codes = retained
    if snapshot is None:
        # 失败批次在排序中占位，阻断从它两侧拼接一个历史窗口。
        compact = dict(trade_date=day, captured_at=report['captured_at'], interval_minutes=None)
    else:
        compact = {k: snapshot[k] for k in ('captured_at', 'trade_date', 'interval_minutes', 'context_sha256', 'source')}
        compact['scheduled_at'] = report.get('scheduled_at')
        codes = retained | _wanted_codes(compact, by_slot)
        stocks = snapshot.get('stocks')
        if not isinstance(stocks, dict):
            raise ValueError('批次个股行情格式无效')
        compact['stocks'] = {}
        for code in codes:
            row = stocks.get(code)
            if not isinstance(row, dict) or not isinstance(row.get('quote_at'), str):
                continue
            try:
                amount = number(row.get('amount'))
            except (ValueError, OverflowError):
                continue
            compact['stocks'][code] = dict(quote_at=row['quote_at'], amount=amount)
    compact.update(batch=folder.name, scheduled_at=report.get('scheduled_at'))
    if _timestamp(compact['captured_at']).date().isoformat() != day:
        raise ValueError('批次实际时间错日')
    cache[key] = dict(fingerprint=fingerprint, codes=sorted(codes), snapshot=compact)
    return compact, True


def _history_windows(folder, by_slot_codes, cache):
    snapshots = []
    dirty = False
    if not folder.is_dir():
        raise ValueError('缺该日采样归档')
    for batch in sorted(p for p in folder.iterdir() if p.is_dir()):
        quote, changed = _read_quote(batch, by_slot_codes, cache)
        snapshots.append(quote)
        dirty |= changed
    by_slot = defaultdict(list)
    ordered = sorted(snapshots, key=lambda s: _timestamp(s['captured_at']))
    for idx, snapshot in enumerate(ordered):
        if snapshot.get('interval_minutes') == 10:
            by_slot[_slot(snapshot)].append(idx)
    windows = {}
    for end, positions in by_slot.items():
        starts = by_slot.get(end - 10, [])
        if len(starts) == len(positions) == 1 and starts[0] + 1 == positions[0]:
            windows[end] = (ordered[starts[0]], ordered[positions[0]])
    return windows, dirty


def crossing_turnover_baselines(day_root, crossings, *, db_path=None, history_days=None):
    """crossings[(证券代码, 穿越批 captured_at)] = (穿越前快照, 穿越快照)。仅使用本地归档。"""
    results, eligible = {}, {}
    by_slot_codes = defaultdict(set)
    for key, (first, last) in crossings.items():
        code, at = key
        row = results[key] = dict(ratio=None, valid_days=None, expected_days=DAYS, current=None,
                                  current_yuan_per_minute=None, median_yuan_per_minute=None, samples=[], reason=None)
        try:
            if last['trade_date'] != day_root.name or at != last['captured_at']:
                raise ValueError('首次穿越与当日采样绑定不符')
            sample = _sample(code, first, last, last.get('source'))
            row.update(current=sample, current_yuan_per_minute=sample['yuan_per_minute'])
            end = _slot(last)
            eligible[key] = (end, sample['source'])
            by_slot_codes[end].add(code)
            by_slot_codes[end - 10].add(code)
        except _DATA_ERRORS as exc:
            row['reason'] = str(exc) if isinstance(exc, ValueError) else '首次穿越区间行情缺失'
    if not eligible:
        return results
    try:
        days = _history_dates(day_root.name, history_days, db_path)
    except _CALENDAR_ERRORS:
        for key in eligible:
            results[key]['reason'] = '前五个 SSE 开放日未核验'
        return results

    cache_path = day_root / _CACHE_NAME
    cache = _load_cache(cache_path)
    changed = False
    for day in days:
        try:
            windows, dirty = _history_windows(day_root.parent / day, by_slot_codes, cache)
            changed |= dirty
            issue = None
        except _DATA_ERRORS as exc:
            windows = {}
            issue = str(exc) if isinstance(exc, ValueError) else '当天归档缺失或无法绑定回执'
        for key, (slot, source) in eligible.items():
            try:
                if slot not in windows:
                    raise ValueError(issue or '缺可核验的相邻同槽批次')
                sample = _sample(key[0], *windows[slot], source)
            except _DATA_ERRORS as exc:
                sample = dict(date=day, reason=str(exc) if isinstance(exc, ValueError) else '个股区间行情未核验')
            results[key]['samples'].append(sample)
    if changed:
        try:
            atomic_write_text(cache_path, json.dumps(dict(entries=cache, sha256=digest(cache)),
                                                     ensure_ascii=False, allow_nan=False))
        except OSError:
            # 只读目录仍可基于已核验的原件当次计算。
            pass
    for key in eligible:
        row = results[key]
        rates = [s['yuan_per_minute'] for s in row['samples'] if 'yuan_per_minute' in s]
        row['valid_days'] = len(rates)
        if len(rates) != DAYS:
            row['reason'] = '五日同槽行情覆盖不足'
            continue
        row['median_yuan_per_minute'] = median(rates)
        if row['median_yuan_per_minute'] > 0:
            row['ratio'] = row['current_yuan_per_minute'] / row['median_yuan_per_minute']
        else:
            row['reason'] = '历史中位每分钟成交额为零'
    return results
