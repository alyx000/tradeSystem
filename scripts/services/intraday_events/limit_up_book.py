"""固定核心及盘中首次观察到日高触及涨停股票的同批买一盘口。"""
from collections import Counter
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json
import math

from db.connection import get_readonly_connection
from services.board_break_intraday import data
from services.course_core_minutes import _local_limits
from services.intraday_monitor.guards import quote_datetime
from . import limit_down_book as shared
from .analytics import comparable
from .turnover_baseline import _slot, _timestamp

ROOT = shared.ROOT.parent / 'limit-up-orderbook'
BOOK_FILE = 'limit-up-book-source.json'


def _references(root, conn, day, prior, universe, registry, now, actor):
    """只读取既有同次限价结果；旧版只存下限时可使用同日原始限价归档。"""
    path = root / 'references' / (day + '.json')
    old = shared._read(path, day)
    identity = data.digest(sorted(universe))
    if old and (old['previous_date'] != prior or old['universe_sha256'] != identity):
        raise ValueError('涨停盘口的参考日期或证券范围变更')
    if old.get('status') == 'complete':
        return old
    refs = dict(old) if old else dict(trade_date=day, previous_date=prior, universe_sha256=identity,
                                      ceilings={}, no_daily_limit=[], source=None)
    gaps = []
    try:
        common = shared._read(shared_reference(root, day), day)
        if common.get('ceilings') and common.get('floor_source'):
            if common['universe_sha256'] != identity or common['previous_date'] != prior:
                raise ValueError('共享限价日期或证券范围不符')
            ceilings, unlimited, proof = common['ceilings'], common['no_daily_limit'], common['floor_source']
        else:
            prices = _local_limits(day, universe, root.parent, include_unlimited=True)
            if not prices:
                raise ValueError('找不到已核验的同日实际涨停价')
            first = next(iter(prices.values()))
            proof = {k: first[k] for k in ('archive', 'source_sha256', 'requested_at', 'source')}
            ceilings = {c: r['up_limit'] for c, r in prices.items() if r['down_limit'] > 0}
            unlimited = sorted(c for c, r in prices.items() if r['down_limit'] == 0 and r['up_limit'] == 99999.99)
        if not proof.get('source_sha256') or not proof.get('requested_at') or proof.get('source') != shared.SOURCE:
            raise ValueError('涨停价来源或取数回执缺失')
        if any(ceilings.get(c) != p for c, p in refs['ceilings'].items()) or set(refs['no_daily_limit']) - set(unlimited):
            raise ValueError('涨停价与此前有效参考冲突或覆盖缩减')
        refs.update(ceilings=ceilings, no_daily_limit=unlimited, source=proof)
    except shared.ERRORS as exc:
        gaps.append(str(exc))
    missing = sorted(universe - set(refs['ceilings']) - set(refs['no_daily_limit']))
    refs.update(input_by=actor, missing_codes=missing, gaps=gaps, status='partial' if gaps or missing else 'complete')
    shared._write(path, refs)
    return refs


def shared_reference(root, day):
    return root.parent / 'limit-down-orderbook/references' / (day + '.json')


def _frozen(ctx, captured):
    if not ctx.get('frozen_at') or _timestamp(ctx['frozen_at']) > captured:
        return {}
    universe = set(ctx['universe'])
    fixed = {}
    for kind, stocks in [('previous_core', ctx.get('cores', [])), ('handwritten_anchor', (ctx.get('anchors') or {}).get('stocks', []))]:
        for member in stocks:
            if member.get('source_date') == ctx['previous_date'] and member['code'] in universe:
                fixed.setdefault(member['code'], []).append(kind)
    return fixed


def stage_books(root, conn, folder, payload, cal, books):
    snap = payload.get('snapshot')
    if not books or not snap or snap.get('source') != 'sina':
        return
    day = payload['trade_date']
    receipt = json.loads((folder / 'receipt.json').read_bytes())
    selected = set(_frozen(payload['context'], _timestamp(snap['captured_at'])))
    refs = shared._read(root / 'references' / (day + '.json'), day)
    if not refs.get('ceilings'):
        refs = shared._read(shared_reference(root, day), day)
    if refs.get('previous_date') == cal['previous'] and refs.get('universe_sha256') == data.digest(sorted(payload['context']['universe'])):
        ceilings = refs.get('ceilings', {})
        selected.update(r['code'] for r in snap['raw_rows'] if r.get('high') and r['high'] == ceilings.get(r['code']))
    shared._save_books(folder, receipt, snap, selected, books, BOOK_FILE)


def _level(book, side):
    if not isinstance(book, dict):
        return False
    price, shares = book.get(side + '1_price'), book.get(side + '1_size')
    return (type(shares) is int and shares >= 0 and type(price) in (int, float) and math.isfinite(price) and price >= 0
            and (price == 0) == (shares == 0))


def _book_state(row, book):
    if not _level(book, 'bid') or not _level(book, 'ask') or book.get('size_unit') != 'shares' or not book.get('unit_evidence'):
        return 'unverified'
    if (row['price'] == row['up_limit'] and book['bid1_price'] == row['up_limit']
            and book['bid1_size'] > 0 and book['ask1_size'] == 0):
        return 'buy_queue_at_limit'
    return 'no_confirmed_queue'


def _amount(row):
    if _book_state(row, row['book']) == 'buy_queue_at_limit':
        b = row['book']
        return float((Decimal(str(b['bid1_price'])) * b['bid1_size']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    return None


def _batch(report, receipt, refs, books):
    snap = report.get('snapshot') or {}
    ctx = report.get('context') or {}
    captured = _timestamp(report['captured_at'])
    fixed = _frozen(ctx, captured) if ctx.get('universe') else {}
    batch = dict(batch=receipt['batch'], report_sha256=receipt['sha256'], captured_at=report['captured_at'],
             trade_date=report['trade_date'], scheduled_at=report.get('scheduled_at'), interval_minutes=10,
             source=snap.get('source'), context_sha256=snap.get('context_sha256'), fixed_members=fixed,
             rows={}, without_limit={}, errors={}, limit_source=refs.get('source'))
    selection_gaps = []
    if not (ctx.get('sources') or {}).get('cores'):
        selection_gaps.append('前日人工核心来源未核验')
    if (ctx.get('anchors') or {}).get('status') != 'complete':
        selection_gaps.append('手写锚点未完整核验')
    if not ctx.get('frozen_at') or _timestamp(ctx['frozen_at']) > captured:
        selection_gaps.append('固定样本的冻结时点未核验')
    batch['selection_gaps'] = selection_gaps
    batch['slot'] = _slot(batch)
    if (not snap or report['schema'] != 'intraday-events-v1' or snap.get('interval_minutes') != 10
            or snap.get('source') != 'sina' or not snap.get('context_sha256')
            or snap.get('raw_sha256') != data.digest(snap['raw_rows'])):
        return dict(batch, status='source_failed', errors={'sample': report.get('error', '原始十分钟新浪报价未经核验')})
    for raw in snap['raw_rows']:
        code = raw['code']
        stock = snap['stocks'].get(code)
        up = refs['ceilings'].get(code)
        if not stock or (code not in fixed and (not up or raw.get('high') != up)):
            continue
        if up is None:
            batch['without_limit'][code] = dict(price=stock['price'], quote_at=stock['quote_at'], book=books.get(code),
                                             reason='no_daily_limit' if code in refs['no_daily_limit'] else 'missing_limit')
            continue
        try:
            stamp = quote_datetime(raw)
            price, high = (data.number(raw[k], positive=True) for k in ('price', 'high'))
            if (not stamp or stamp.date() != captured.date() or not 0 <= (captured - stamp).total_seconds() <= 90
                    or raw['name'] != stock['name'] or stamp.isoformat() != stock['quote_at']
                    or price != stock['price'] or not price <= high <= up):
                raise ValueError('时间、身份、价格或日内最高与参考上限不符')
        except shared.ERRORS as exc:
            batch['errors'][code] = str(exc)
            continue
        book = books.get(code)
        row = dict(name=stock['name'], quote_at=stock['quote_at'], price=price, up_limit=up, high_so_far=high,
                   high_touched=high == up, at_limit=price == up, book=book, fixed_reasons=fixed.get(code, []))
        row['book_state'] = _book_state(row, book)
        batch['rows'][code] = row
    return dict(batch, status='sampled', raw_sha256=snap['raw_sha256'])


def _summarize(day, batches, refs, errors, actors):
    batches = sorted(batches, key=lambda batch: (batch['captured_at'], batch['batch']))
    slots = Counter(batch['slot'] for batch in batches)
    unknown_error = set(errors) - {batch['batch'] for batch in batches}
    expected, observed, first_high, changes = set(), set(), {}, []
    previous = None
    prices = bids = pairs = queues = missing_rows = 0
    for batch in batches:
        linked = (previous and not unknown_error and previous['batch'] not in errors and batch['batch'] not in errors
                  and previous['status'] == batch['status'] == 'sampled' and previous['source'] == batch['source'] == 'sina'
                  and slots[batch['slot']] == slots[previous['slot']] == 1 and batch['slot'] - previous['slot'] == 10 and comparable(previous, batch))
        expected.update(batch.get('fixed_members', {}))
        expected.update(batch['rows'])
        expected.difference_update(refs.get('no_daily_limit', []))
        observed.update(batch['rows'])
        batch['missing_selected_quotes'] = sorted(expected - set(batch['rows']))
        missing_rows += len(batch['missing_selected_quotes'])
        for code, row in batch['rows'].items():
            prices += 1
            bids += _level(row['book'], 'bid')
            pairs += _book_state(row, row['book']) != 'unverified'
            amount = row['buy_queue_amount_yuan'] = _amount(row)
            queues += amount is not None
            if row['high_touched']:
                first_high.setdefault(code, row['quote_at'])
            before = previous['rows'].get(code) if linked else None
            if (before and amount is not None and before.get('buy_queue_amount_yuan') is not None
                    and before['up_limit'] == row['up_limit']
                    and 420 <= (_timestamp(row['quote_at']) - _timestamp(before['quote_at'])).total_seconds() <= 780):
                old_amount = before['buy_queue_amount_yuan']
                changes.append(dict(code=code, name=row['name'], start_batch=previous['batch'], end_batch=batch['batch'],
                                    start_at=before['quote_at'], end_at=row['quote_at'], start_yuan=old_amount,
                                    end_yuan=amount, delta_yuan=round(amount - old_amount, 2)))
        previous = batch
    missing_slots = [f'{s//60:02d}:{s%60:02d}' for s in shared.SLOTS if slots[s] != 1]
    sampled = sum(batch['status'] == 'sampled' for batch in batches)
    coverage = dict(expected_slots=len(shared.SLOTS), sampled_slots=sampled, missing_or_duplicate_slots=missing_slots,
                  selected_codes=len(observed), price_rows=prices, bid_book_rows=bids, verified_book_rows=pairs,
                  buy_queue_rows=queues if pairs else None, missing_selected_quotes=missing_rows)
    selection_gaps = sorted({gap for batch in batches for gap in batch.get('selection_gaps', [])})
    partial = (errors or selection_gaps or missing_slots or prices != pairs or missing_rows or refs['status'] != 'complete'
               or sampled != len(batches) or any(batch['errors'] for batch in batches))
    status = 'partial' if partial else 'complete'
    return dict(schema='limit-up-orderbook-v1', trade_date=day, status=status if sampled else 'source_failed',
                input_by=sorted(actors),
                previous_date=refs['previous_date'], references=f'references/{day}.json', reference_status=refs['status'],
                first_observed_high_at_limit=first_high, coverage=coverage, batches=batches, errors=errors,
                selection_gaps=selection_gaps, queue_changes=changes)


def collect(root, conn, day, cal, folders, actor, *, books=None, now=None, replay=False):
    return shared.collect_side(root, conn, day, cal, folders, actor, registry=None, books=books, now=now, replay=replay,
                               book_file=BOOK_FILE, prepare_refs=_references, make_batch=_batch, summarize=_summarize,
                               book_state=_book_state, missing_limit_field='without_limit', event_field='queue_changes')


def replay_day(day, actor, *, events_root=None, root=ROOT, db_path=None):
    root = Path(root)
    events_root = Path(events_root) if events_root is not None else root.parent / 'intraday-events'
    with closing(get_readonly_connection(db_path)) as conn:
        cal = data.calendar(conn, day)
        folders = sorted(p for p in (events_root / day).glob('*') if p.is_dir())
        return collect(root, conn, day, cal, folders, actor, replay=True)
