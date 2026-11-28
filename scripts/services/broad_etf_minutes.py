"""两只场内宽基 ETF 的闭合五分钟成交，盘中观察与收盘后回放分档。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ, calendar, digest
from services.board_break_intraday.store import lock
from services.broad_etf_sources import FUNDS, fetch_source
from services.volume_record.service import atomic_write


REPORTS = Path(__file__).resolve().parents[2] / 'data/reports'
SCHEMA = 'broad-etf-minutes-v1'
BENCHMARKS = {'512100.SH': '000852.SH', '510300.SH': '000300.SH'}
ENDS = tuple(f'{minute//60:02}:{minute%60:02}' for start in (570, 780)
             for minute in range(start + 5, start + 121, 5))
SLOTS = {minute for start in (572, 782) for minute in range(start, start + 120, 10)}
VALUES = ('open', 'high', 'low', 'close', 'volume', 'amount_yuan')
PROVIDERS = ('tushare', 'eastmoney')


def _read(path, identity):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or 'sha256' not in data:
        raise ValueError(f'归档缺少有效哈希，保留原件 {path.name}')
    checksum = data.pop('sha256')
    if digest(data) != checksum or any(data.get(k) != v for k, v in identity.items()):
        raise ValueError(f'归档哈希或身份不符，保留原件 {path.name}')
    return data, checksum


def _stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('实收时间缺少时区')
    return result.astimezone(TZ)


def _observe(code, day, due, mode, plan, loader, providers=PROVIDERS):
    calls, bars = [], {}
    for provider in providers:
        receipt = dict(provider=provider, code=code, trade_date=day)
        try:
            source = loader(provider, code, day)
            rows = source['bars']
            receipt = {k: v for k, v in source.items() if k != 'bars'}
            received = _stamp(receipt['received_at'])
            requested = _stamp(receipt['requested_at'])
            if (receipt['provider'], receipt['code'], receipt['trade_date']) != (provider, code, day):
                raise ValueError('返回基金、来源或日期与请求不符')
            if received < requested or (mode == 'live' and not plan <= requested <= received < plan + timedelta(minutes=3)):
                raise ValueError('请求或实收时间超出采样窗口')
            if rows and not source.get('raw_sha256'):
                raise ValueError('缺少来源响应哈希')
            current = {}
            for end in due:
                if end not in rows:
                    continue
                row = rows[end]
                bar_end = datetime.fromisoformat(f'{day}T{end}:00').replace(tzinfo=TZ)
                if _stamp(row['source_time']) != bar_end or bar_end > received:
                    raise ValueError('返回K线事实日、闭合边界或实际收包时间不符')
                current[end] = dict(row, provider=provider, received_at=received.isoformat(), raw_sha256=source['raw_sha256'])
            receipt['accepted_count'] = len(current)
            calls.append(receipt)
            for end, value in current.items():
                bars.setdefault(end, []).append(value)
            if set(bars) == set(due):
                break
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            calls.append(dict(receipt, status='source_failed', gap=str(exc), accepted_count=0))
    return calls, bars


def _merge(product, observations, fields=VALUES):
    def values(bar):
        return tuple(Decimal(str(bar[k])) for k in fields)

    for end, additions in observations.items():
        for new in additions:
            old = product['bars'].setdefault(end, new)
            if values(old) == values(new):
                continue
            conflicts = product['conflicts'].setdefault(end, [])
            if all(values(v) != values(new) for v in conflicts):
                conflicts.append(new)


def _micro_reference(reports, day, through, observed):
    """微盘股只作为另列的市场对照，严格按原生五分钟结束标签配对。"""
    path = reports / 'tdx-index-5m' / (day + '.json')
    result = dict(code='880823.TDX', name='通达信微盘股', path=str(path), bars={}, conflicts={}, status='source_failed')
    try:
        data, sha = _read(path, dict(schema='tdx-index-ohlc-5m-v1', source='tdx:index_5min', trade_date=day,
                                    timezone='Asia/Shanghai', bar_time='收盘时刻'))
        index = data['indices']['880823.TDX']
        for end, row in index['conflicts'].items():
            if end in ENDS and end <= through and _stamp(row['captured_at']) <= observed:
                result['conflicts'][end] = row
        for end, row in index['bars'].items():
            if end not in ENDS or end > through or end in index['conflicts']:
                continue
            caught = _stamp(row['captured_at'])
            if caught > observed or caught < _stamp(f'{day}T{end}:00+08:00'):
                continue
            if row.get('source_time') != f'{day} {end}':
                raise ValueError('微盘股实际源标签错位')
            close = Decimal(str(row['close']))
            if not close.is_finite() or close <= 0:
                raise ValueError('微盘股来源收盘价无效')
            result['bars'][end] = dict(close=str(close), captured_at=row['captured_at'], source_time=row['source_time'], archive_sha256=sha)
        result.update(archive_sha256=sha, status='observed' if result['bars'] else 'missing')
    except (KeyError, OSError, ValueError, TypeError, ArithmeticError) as exc:
        result.update(gap=type(exc).__name__, bars={})
    return result


def _combine_micro(previous, current):
    old_bars = dict(previous.get('bars', {}))
    conflicts = dict(previous.get('conflicts', {}))
    for end, bar in current['bars'].items():
        existing = old_bars.setdefault(end, bar)
        if Decimal(existing['close']) != Decimal(bar['close']):
            conflicts[end] = bar
    conflicts.update(current.get('conflicts', {}))
    return dict(current, bars=old_bars, conflicts=conflicts, latest_attempt_status=current['status'],
                status='observed' if old_bars.keys() - conflicts.keys() else current['status'])


def _combine_breadth(previous, current):
    points = dict(previous.get('timeseries', {}))
    conflicts = dict(previous.get('conflicts', {}))
    disputed = current.get('conflicts', {})
    if isinstance(disputed, list):
        disputed = {slot: '同槽多个来源批次或日内冻结名单不一致' for slot in disputed}
    conflicts.update(disputed)
    for slot, point in current.get('timeseries', {}).items():
        old = points.setdefault(slot, point)
        if old['source_sha256'] != point['source_sha256']:
            conflicts[slot] = dict(previous=old['source_sha256'], latest=point['source_sha256'])
        elif point.get('receipt_sha256') and not old.get('receipt_sha256'):
            # 本次已读原行情及回执；同一批次内容可补旧版未存的回执哈希。
            points[slot] = point
    return dict(current, timeseries=points, conflicts=conflicts)


def _pair_breadth(good, breadth):
    paired = {}
    for slot, point in breadth['timeseries'].items():
        if slot in breadth['conflicts'] or ('valid' in point and not point['valid']):
            continue
        sample = _stamp(point['captured_at'])
        # 每槽仅配已闭合、离实采至多三分钟的五分钟K线。午后首槽无可比点。
        end = (sample - timedelta(minutes=sample.minute % 5,
                                  seconds=sample.second, microseconds=sample.microsecond))
        key = end.strftime('%H:%M')
        offset = (sample-end).total_seconds()
        if key in good and 0 <= offset < 180:
            paired[key] = dict(breadth_slot=slot, sample_at=point['captured_at'],
                               clock_difference_seconds=offset, batch_sha256=point['source_sha256'])
    return paired


def _index_loader(_provider, code, day):
    from services.broad_etf_sources import fetch_benchmark
    return fetch_benchmark(code, day)


def collect(day, actor, *, scheduled=None, reports=REPORTS, db_path=None, loader=fetch_source,
            dry_run=False, clock=None, benchmark_loader=_index_loader, breadth_loader=None):
    clock = clock or (lambda: datetime.now(TZ))
    now = clock().astimezone(TZ)
    if not actor.strip() or date.fromisoformat(day).isoformat() != day:
        raise ValueError('请求日期或请求者无效')
    mode = 'live' if scheduled else 'replay'
    base = dict(schema=SCHEMA, trade_date=day, mode=mode)
    if scheduled:
        plan = _stamp(scheduled)
        if (plan.hour*60 + plan.minute not in SLOTS or plan.second or plan.microsecond
                or plan.date().isoformat() != day):
            raise ValueError('计划时刻不属于目标日既有盘中时槽')
        if not plan <= now < plan + timedelta(minutes=3):
            return dict(base, status='skipped', gap='非实际盘中采样时槽')
        through = plan.strftime('%H:%M')
    else:
        plan = None
        through = '15:00'
        if now < _stamp(day + 'T15:00:00+08:00'):
            return dict(base, status='skipped', gap='尚未收盘')
    with closing(get_readonly_connection(db_path)) as conn:
        if not calendar(conn, day)['is_open']:
            return dict(base, status='skipped', gap='SSE休市')
    due = [end for end in ENDS if end <= through]
    if not due:
        return dict(base, status='skipped', gap='当日首根5分钟尚未闭合')
    reports = Path(reports)
    root = reports / 'broad-etf-minutes'
    path = root / day / (mode + '.json')
    with (nullcontext() if dry_run else lock(root, day + '/' + mode)):
        if path.exists():
            report, _ = _read(path, base)
            if set(report['funds']) != set(FUNDS):
                raise ValueError('旧档基金名单不符')
            # 机器时钟回拨或较早槽的重复调用，不能为已有晚点的实收重新标注更早截止。
            if mode == 'live':
                observations = [a['completed_at'] for a in report.get('attempts', [])]
                breadth_through = report.get('market_breadth', {}).get('through')
                if breadth_through:
                    observations.append(breadth_through)
                if due[-1] < report.get('through', '') or any(_stamp(value) > now for value in observations):
                    return dict(base, status='skipped', gap='已有更晚的实采证据，保留日档',
                                through=report['through'], path=str(path))
        else:
            report = dict(base, funds={code: dict(name=name, benchmark_code=BENCHMARKS[code], bars={}, conflicts={})
                                       for code, name in FUNDS.items()}, attempts=[])
        todo = [code for code, fund in report['funds'].items() if
                not set(due).issubset(fund['bars'].keys() - fund['conflicts'].keys())]
        indices = report.setdefault('benchmark_references', {
            code: dict(code=code, bars={}, conflicts={}) for code in BENCHMARKS.values()})
        needed_indices = [code for code, series in indices.items()
                          if not set(due).issubset(series['bars'].keys() - series['conflicts'].keys())]
        attempt = dict(requested_at=now.isoformat(), scheduled_at=scheduled, input_by=actor,
                       cutoff=due[-1], sources={}, benchmark_sources={})
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {code: pool.submit(_observe, code, day, due, mode, plan, loader) for code in todo}
            index_futures = {code: pool.submit(_observe, code, day, due, mode, plan, benchmark_loader,
                                               ('eastmoney_index',)) for code in needed_indices}
            for code, future in futures.items():
                calls, bars = future.result()
                attempt['sources'][code] = calls
                _merge(report['funds'][code], bars)
            for code, future in index_futures.items():
                calls, bars = future.result()
                attempt['benchmark_sources'][code] = calls
                _merge(indices[code], bars, fields=('open', 'high', 'low', 'close'))
        observed = clock().astimezone(TZ)
        deadline = max(due[-1], report.get('through', ''))
        expected = [end for end in ENDS if end <= deadline]
        reference = _combine_micro(report.get('microcap_reference', {}),
                                   _micro_reference(reports, day, deadline, observed))
        breadth_until = min(observed, _stamp(day+'T15:00:00+08:00'))
        try:
            if breadth_loader is None:
                from services.broad_etf_breadth import breadth_reference
                latest_breadth = breadth_reference(reports/'intraday-events', day, breadth_until,
                                                   previous=report.get('market_breadth'))
            else:
                latest_breadth = breadth_loader(reports/'intraday-events', day, breadth_until)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            latest_breadth = dict(status='source_failed', timeseries={}, conflicts={}, gaps=[type(exc).__name__])
        breadth = _combine_breadth(report.get('market_breadth', {}), latest_breadth)
        for series in indices.values():
            series['observed_count'] = len(set(expected) & (series['bars'].keys() - series['conflicts'].keys()))
        report['market_breadth'] = breadth
        total = 0
        for fund in report['funds'].values():
            good = set(fund['bars']) - set(fund['conflicts'])
            fund['missing_or_conflicted'] = [end for end in expected if end not in good]
            fund['observed_count'] = len(set(expected) & good)
            index = indices[fund['benchmark_code']]
            fund['paired_benchmark_ends'] = sorted(good & (index['bars'].keys() - index['conflicts'].keys()))
            fund['paired_microcap_ends'] = sorted(good & (reference['bars'].keys() - reference['conflicts'].keys()))
            fund['nearby_market_breadth'] = _pair_breadth(good, breadth)
            total += fund['observed_count']
        need = len(expected) * len(FUNDS)
        report.update(through=deadline, microcap_reference=reference, observed_count=total, expected_count=need,
                      expected_full_day=len(ENDS)*len(FUNDS),
                      status='complete' if total == need else 'partial' if total else 'source_failed')
        if todo or needed_indices:
            report['attempts'].append(dict(attempt, completed_at=observed.isoformat()))
        encoded = json.dumps(dict(report, sha256=digest(report)), ensure_ascii=False, allow_nan=False, indent=2)
        if not dry_run and (not path.is_file() or path.read_text() != encoded):
            atomic_write(path, encoded)
    return dict(base, status=report['status'], through=deadline, observed_count=total, expected_count=need,
                benchmark_reference_counts={k: v['observed_count'] for k, v in indices.items()},
                microcap_reference_count=len(reference['bars'].keys() - reference['conflicts'].keys()),
                market_breadth_slots=len(breadth['timeseries'].keys() - breadth['conflicts'].keys()),
                path=None if dry_run else str(path))
