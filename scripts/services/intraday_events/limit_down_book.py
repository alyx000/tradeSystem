"""从已有十分钟报价保留跌停观察样本。事件仅表示两个实测端点之间的变化。"""
from collections import Counter
from contextlib import closing
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import pickle
import sqlite3

from db.connection import get_readonly_connection
from providers.base import DataResult
from services.board_break_intraday import data, store
from services.course_core_minutes import _daily_rows, _local_limits
from services.intraday_monitor.guards import quote_datetime, shanghai_now
from services.intraday_summary.state import atomic_write_text
from .analytics import comparable
from .archive import read_report
from .sources import call
from .turnover_baseline import _slot, _timestamp

ROOT = Path(__file__).resolve().parents[3] / 'data/reports/limit-down-orderbook'
SLOTS = [base + 10*i for base in (572, 782) for i in range(12)]
ERRORS = (OSError, ValueError, TypeError, KeyError, sqlite3.Error, EOFError, pickle.UnpicklingError)
SOURCE = 'tushare:stk_limit'
BOOK_FILE = 'limit-down-book-source.json'


def _read(path, day):
    if not path.exists():
        return {}
    record = json.loads(path.read_bytes())
    signed = record.pop('sha256')
    if record.get('trade_date') != day or data.digest(record) != signed:
        raise ValueError('旧档来源日期或哈希不符')
    return record


def _write(path, obj):
    signed = dict(obj, sha256=data.digest(obj))
    encoded = json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n'
    if not path.is_file() or path.read_text() != encoded:
        atomic_write_text(path, encoded)


def _references(root, conn, day, prior, universe, registry, now, actor):
    path = root / 'references' / (day + '.json')
    old = _read(path, day)
    identity = data.digest(sorted(universe))
    if old and (old['previous_date'] != prior or old['universe_sha256'] != identity):
        raise ValueError('同日采样证券宇宙或上一开放日发生变化')
    if old and old['status'] == 'complete' and 'no_daily_limit' in old:
        return old
    if old and registry is not None and (now-_timestamp(old['attempt_at'])).total_seconds() < 1800:
        return old
    out = dict(trade_date=day, previous_date=prior, universe_count=len(universe), universe_sha256=identity,
               previous_members=(old or {}).get('previous_members'), previous_source=old.get('previous_source'),
               floors=old.get('floors', {}), ceilings=old.get('ceilings', {}), no_daily_limit=old.get('no_daily_limit', []),
               floor_source=old.get('floor_source'), input_by=actor,
               attempt_at=now.isoformat(), selection_basis=old.get('selection_basis', 'unverified'),
               previous_read_at=old.get('previous_read_at'), gaps=[])
    if out['previous_members'] is None or 'selection_basis' not in old:
        try:
            raw, provenance = data.archive(conn, prior)
            members = data.limit_rows(raw, prior, 'limit_down')
            out.update(previous_members={r['code']: r['name'] for r in members},
                       selection_basis='live_prior_archive' if registry is not None else 'historical_prior_archive',
                       previous_read_at=now.isoformat(),
                       previous_source=dict(provenance, provider=raw['limit_down']['_source'],
                                            post_generated_at=raw.get('generated_at')))
        except ERRORS as exc:
            out['gaps'].append('前一开放日跌停名单未核验：'+str(exc))
    out['previous_excluded_by_scope'] = sorted(set(out['previous_members'] or {}) - universe)
    try:
        try:
            prices = _local_limits(day, universe, root.parent, include_unlimited=True)
        except ERRORS:
            if registry is None:
                raise
            prices = {}
        if prices:
            first = next(iter(prices.values()))
            proof = {k: first[k] for k in ('archive', 'source_sha256', 'requested_at', 'source')}
        elif registry is not None:
            rows, provider = call(registry, 'get_stock_limit_prices', day)
            prices = _daily_rows(DataResult(rows, provider), SOURCE, day, universe, ('up_limit', 'down_limit'),
                                 include_unlimited=True)
            proof = dict(source=provider, requested_at=now.isoformat(), source_sha256=data.digest(rows))
        else:
            raise ValueError('历史日期无已核验限价归档')
        floors = {code: r['down_limit'] for code, r in prices.items() if r['down_limit'] > 0}
        unlimited = sorted(c for c,r in prices.items() if r['down_limit'] == 0 and r['up_limit'] == 99999.99)
        if (any(code not in floors or floors[code] != floor for code, floor in out['floors'].items())
                or set(out['no_daily_limit']) - set(unlimited)):
            raise ValueError('限价刷新未完整保留已有逐股证据')
        ceilings = {code: r['up_limit'] for code, r in prices.items() if code in floors}
        if any(ceilings.get(code) != limit for code, limit in out['ceilings'].items()):
            raise ValueError('涨停价刷新未完整保留已有逐股证据')
        out.update(floors=floors, ceilings=ceilings, no_daily_limit=unlimited, floor_source=proof)
    except ERRORS as exc:
        out['gaps'].append('逐股跌停参考价缺口：'+str(exc))
    missing = sorted(universe - set(out['floors']) - set(out['no_daily_limit']))
    out.update(missing_floor_codes=missing,
               status='partial' if missing or out['previous_members'] is None else 'complete')
    _write(path, out)
    return out


def _book_evidence(folder, receipt, filename=BOOK_FILE):
    record = _read(folder / filename, receipt['trade_date'])
    if record and (record['source'] != 'sina' or record['batch'] != receipt['batch']
                   or record['report_sha256'] != receipt['sha256']):
        raise ValueError('实采盘口与原行情回执不符')
    return record.get('rows', {})


def _save_books(folder, receipt, snapshot, selected, books, filename=BOOK_FILE):
    rows = _book_evidence(folder, receipt, filename)
    for code in selected & books.keys() & snapshot['stocks'].keys():
        q = snapshot['stocks'][code]
        evidence = dict(price=q['price'], quote_at=q['quote_at'], book=books[code])
        if code in rows and rows[code] != evidence:
            raise ValueError('同批同股盘口与已有来源冲突')
        rows[code] = evidence
    if rows:
        _write(folder / filename, dict(trade_date=receipt['trade_date'], source='sina', batch=receipt['batch'],
               report_sha256=receipt['sha256'], captured_at=snapshot['captured_at'], rows=rows))


def stage_books(root, conn, folder, payload, cal, books):
    """先存本槽已知名单，可在限价暂缺、后续副流程失败时复核原盘口。"""
    snapshot = payload.get('snapshot')
    if not books or not snapshot or snapshot['source'] != 'sina':
        return
    receipt = json.loads((folder/'receipt.json').read_bytes())
    refs = _read(root/'references'/(receipt['trade_date']+'.json'), receipt['trade_date'])
    previous = refs.get('previous_members')
    if previous is None:
        try:
            raw, _ = data.archive(conn, cal['previous'])
            previous = [r['code'] for r in data.limit_rows(raw, cal['previous'], 'limit_down')]
        except ERRORS:
            previous = []
    floors = refs.get('floors', {})
    selected = set(previous)
    selected.update(r['code'] for r in snapshot['raw_rows'] if r.get('low') is not None
                    and r['low'] == floors.get(r['code']))
    _save_books(folder, receipt, snapshot, selected, books)


def _price_row(raw, normalized, captured, floor):
    stamp = quote_datetime(raw)
    if (not stamp or stamp.date() != captured.date() or not 0 <= (captured - stamp).total_seconds() <= 90
            or stamp.isoformat() != normalized['quote_at'] or raw['name'] != normalized['name']):
        raise ValueError('原报价时间或身份未通过核验')
    price = data.number(raw['price'], positive=True)
    low = data.number(raw['low'], positive=True)
    if price != normalized['price'] or not floor <= low <= price:
        raise ValueError('原报价、当日最低价或同日跌停价不一致')
    return dict(name=raw['name'], quote_at=stamp.isoformat(), price=price, low_so_far=low, down_limit=floor,
                at_limit=(price == floor), low_touched=(low == floor))


def _batch(report, receipt, refs, books):
    snap = report.get('snapshot') or {}
    base = dict(batch=receipt['batch'], report_sha256=receipt['sha256'], captured_at=report['captured_at'],
                trade_date=report['trade_date'], scheduled_at=report.get('scheduled_at'), interval_minutes=10,
                source=snap.get('source'), context_sha256=snap.get('context_sha256'), rows={}, errors={},
                floor_source_sha256=(refs.get('floor_source') or {}).get('source_sha256'),
                floor_reference_at=(refs.get('floor_source') or {}).get('requested_at'),
                previous_members_read_at=refs.get('previous_read_at') or refs.get('attempt_at'),
                previous_selection_basis=refs.get('selection_basis'), without_floor={})
    base['slot'] = _slot(base)
    if not snap:
        return dict(base, status='source_failed', errors={'sample': report.get('error', '本槽采样失败')})
    if (report['schema'] != 'intraday-events-v1' or snap.get('interval_minutes') != 10
            or snap.get('source') != 'sina' or not snap.get('context_sha256')
            or snap['raw_sha256'] != data.digest(snap['raw_rows'])):
        return dict(base, status='source_failed', errors={'sample':'十分钟原始行情、来源或哈希未核验'})
    previous = set(refs.get('previous_members') or {})
    captured = _timestamp(base['captured_at'])
    if (base['previous_selection_basis'] == 'live_prior_archive' and base['previous_members_read_at']
            and _timestamp(base['previous_members_read_at']) > captured):
        base['previous_selection_basis'] = 'prior_archive_read_after_quote'
    for raw in snap['raw_rows']:
        code = raw['code']
        floor = refs['floors'].get(code)
        stock = snap['stocks'].get(code)
        if not stock:
            continue
        if not floor:
            if code in previous:
                base['without_floor'][code] = dict(price=stock['price'], name=stock['name'], quote_at=stock['quote_at'],
                                                   book=books.get(code), down_limit=None,
                                                   reason='no_daily_limit' if code in refs.get('no_daily_limit', [])
                                                   else 'missing_reference')
            continue
        try:
            row = _price_row(raw, stock, captured, floor)
        except ERRORS as exc:
            if code in previous or raw.get('low') == floor:
                base['errors'][code] = str(exc)
            continue
        if code in previous or row['low_touched']:
            book = books.get(code)
            row.update(previous_limit_down=(code in previous if refs.get('previous_members') is not None else None),
                       book=book, book_state=_book_state(row, book))
            base['rows'][code] = row
    return dict(base, raw_sha256=snap['raw_sha256'], valid_market_quotes=len(snap['stocks']), status='sampled')


def _book_state(row, book):
    if not book or book.get('ask1_price') is None or book.get('ask1_size') is None:
        return 'missing'
    if not row['at_limit']:
        return 'above_limit'
    # “有卖单”只以源的双边一档为准，最新价触限本身没有队列含义。
    if (book.get('ask1_price') == row['down_limit'] and (book.get('ask1_size') or 0) > 0
            and book.get('bid1_price') == 0 and book.get('bid1_size') == 0):
        return 'sell_queue_at_limit'
    return 'queue_unconfirmed'


def _summarize(day, batches, refs, errors, actors):
    ordered = sorted(batches, key=lambda b: (b['captured_at'], b['batch']))
    slots = Counter(b['slot'] for b in ordered)
    # 旧档已核验时间的坏批次只阻断相接窗口。坏批次无法定位时，全天的区间先留待复核。
    known_batches = {b['batch'] for b in ordered}
    unknown_error = bool(set(errors)-known_batches)
    prior = refs.get('previous_members') or {}
    selected = set()
    events, first_low, wall_in_chain = [], {}, set()
    expected = set(prior) - set(refs.get('previous_excluded_by_scope', []))
    missing_quote_rows = wall_rows = quote_rows = book_rows = paired_rows = 0
    last = None
    for batch in ordered:
        connected = (last and not unknown_error and last['batch'] not in errors and batch['batch'] not in errors
                     and last['status'] == batch['status'] == 'sampled'
                     and slots[last['slot']] == slots[batch['slot']] == 1 and batch['slot'] - last['slot'] == 10
                     and last['source'] == batch['source'] and comparable(last, batch))
        if not connected:
            wall_in_chain.clear()
        wall_in_chain.intersection_update(batch['rows'])
        for code, row in batch['rows'].items():
            selected.add(code)
            quote_rows += 1
            book = row['book'] or {}
            ask_valid = book.get('ask1_price') is not None and book.get('ask1_size') is not None
            book_rows += ask_valid
            paired_rows += ask_valid and book.get('bid1_price') is not None and book.get('bid1_size') is not None
            queued = _book_state(row, book) == 'sell_queue_at_limit'
            row['sell_queue_amount_yuan'] = None
            if (queued and book.get('size_unit') == 'shares' and book.get('unit_evidence')
                    and type(book['ask1_size']) is int):
                amount = Decimal(str(book['ask1_price'])) * book['ask1_size']
                row['sell_queue_amount_yuan'] = float(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            wall_rows += queued
            if row['low_touched']:
                first_low.setdefault(code, row['quote_at'])
            before = last['rows'].get(code) if connected else None
            if before and not 420 <= (_timestamp(row['quote_at'])-_timestamp(before['quote_at'])).total_seconds() <= 780:
                before = None
            if before is None:
                wall_in_chain.discard(code)
            elif row['at_limit'] != before['at_limit']:
                kind = 'left_limit_price' if before['at_limit'] else 'reached_limit_price'
                if not before['at_limit'] and before['low_touched']:
                    kind = 'returned_to_limit_price'
                if before['book_state'] == 'sell_queue_at_limit' and not row['at_limit']:
                    kind = 'left_observed_sell_queue'
                elif queued and code in wall_in_chain:
                    kind = 'returned_with_observed_sell_queue'
                events.append(dict(kind=kind, code=code, name=row['name'], start_exclusive=before['quote_at'],
                                   end_inclusive=row['quote_at'], start_batch=last['batch'], end_batch=batch['batch'],
                                   start_price=before['price'], end_price=row['price'], down_limit=row['down_limit']))
            if queued:
                wall_in_chain.add(code)
        for row in batch.get('without_floor', {}).values():
            row['sell_queue_amount_yuan'] = None
        expected.update(batch['rows'])
        batch['missing_selected_codes'] = sorted(expected-set(batch['rows']))
        missing_quote_rows += len(batch['missing_selected_codes'])
        last = batch
    missing = [s for s in SLOTS if slots[s] != 1]
    coverage = dict(expected_slots=24, sampled_slots=sum(b['status']=='sampled' for b in ordered),
                    missing_or_duplicate_slots=[f'{s//60:02d}:{s%60:02d}' for s in missing],
                    selected_codes=len(selected), price_rows=quote_rows, book_rows=book_rows,
                    missing_selected_quotes=missing_quote_rows, both_sides_rows=paired_rows, sell_queue_rows=wall_rows if paired_rows else None,
                    exact_intraday_open_count=None, exact_intraday_reseal_count=None)
    status = 'source_failed' if not coverage['sampled_slots'] else 'partial' if (
        errors or missing or refs['status'] != 'complete' or len(ordered) != coverage['sampled_slots']
        or missing_quote_rows or paired_rows < quote_rows or any(b['errors'] for b in ordered)
        or set(prior)-set(refs.get('previous_excluded_by_scope', []))-selected) else 'complete'
    return dict(schema='limit-down-orderbook-v1', trade_date=day, status=status, input_by=sorted(actors),
                references=f'references/{day}.json', reference_status=refs['status'],
                previous_date=refs['previous_date'], previous_members=refs.get('previous_members'),
                selection_basis=refs.get('selection_basis'),
                previous_excluded_by_scope=refs.get('previous_excluded_by_scope', []),
                previous_without_observation=sorted(set(prior)-set(refs.get('previous_excluded_by_scope', []))-selected),
                first_observed_low_at_limit=first_low,
                coverage=coverage, errors=errors, batches=ordered, events=events)


def collect(root, conn, day, cal, folders, actor, *, registry=None, books=None, now=None, replay=False):
    return collect_side(root, conn, day, cal, folders, actor, registry=registry, books=books, now=now, replay=replay,
                        book_file=BOOK_FILE, prepare_refs=_references, make_batch=_batch, summarize=_summarize,
                        book_state=_book_state, missing_limit_field='without_floor')


def collect_side(root, conn, day, cal, folders, actor, *, registry, books, now, replay,
                 book_file, prepare_refs, make_batch, summarize, book_state, missing_limit_field,
                 event_field='events'):
    """两侧盘口共用原始批次、引用核验、重试保护和原子写入。"""
    root = Path(root)
    if not str(actor or '').strip() or not cal['is_open']:
        raise ValueError('需要请求者和已核验的开放日')
    now = shanghai_now(now)
    if day > now.date().isoformat():
        raise ValueError('不能归档未来日期')
    path = root / (day+'.json')
    with store.lock(root, day):
        old = _read(path, day)
        batches = {b['batch']: b for b in old.get('batches', [])}
        errors = dict(old.get('errors', {}))
        refs = _read(root / 'references' / (day+'.json'), day)
        prepared = False
        for folder in folders:
            key = folder.name
            report = receipt = None
            try:
                receipt = json.loads((folder / 'receipt.json').read_bytes())
                report = read_report(folder, receipt)
                if receipt['trade_date'] != day or report.get('schema') != 'intraday-events-v1':
                    raise ValueError('批次类型或日期不符')
                snap = report.get('snapshot')
                if snap and snap.get('interval_minutes') != 10:
                    continue
                ctx = report.get('context') or {}
                if snap and (ctx.get('previous_date') != cal['previous'] or ctx.get('trade_date') != day
                        or data.digest(ctx) != snap.get('context_sha256')):
                    raise ValueError('固定样本出处或事实日不符')
                if snap and not prepared:
                    refs = prepare_refs(root, conn, day, cal['previous'], set(ctx['universe']), registry, now, actor)
                    prepared = True
                if snap and data.digest(sorted(ctx['universe'])) != refs['universe_sha256']:
                    raise ValueError('批次证券范围与本日参考限价不一致')
                existing = batches.get(key)
                if existing and existing['report_sha256'] != receipt['sha256']:
                    raise ValueError('已归档批次原始证据发生冲突')
                stored = _book_evidence(folder, receipt, book_file)
                combined = {}
                for code, evidence in stored.items():
                    original = (snap or {}).get('stocks', {}).get(code, {})
                    if original.get('price') != evidence['price'] or original.get('quote_at') != evidence['quote_at']:
                        raise ValueError('盘口源时间或最新价与原行情不符')
                    combined[code] = evidence['book']
                if books:
                    combined.update(books)
                found = make_batch(report, receipt, refs, combined)
                if books and snap:
                    selected = set(found['rows']) | set(found.get(missing_limit_field, {}))
                    _save_books(folder, receipt, snap, selected, books, book_file)
                if existing:
                    # 历史重放无法逆推出先前实采盘口，按原始报价绑定保留。
                    for code, row in found['rows'].items():
                        prior = existing['rows'].get(code, {})
                        if prior.get('book') and prior.get('quote_at') == row['quote_at'] and not row['book']:
                            row.update(book=prior['book'], book_state=prior['book_state'])
                batches[key] = found
                errors.pop(key, None)
            except ERRORS as exc:
                errors[key] = str(exc)
                if key not in batches and receipt and report:
                    try:
                        at = dict(trade_date=day, captured_at=report['captured_at'], scheduled_at=report.get('scheduled_at'))
                        batches[key] = dict(at, batch=key, slot=_slot(at), report_sha256=receipt['sha256'],
                                            source=None, context_sha256=None, interval_minutes=10,
                                            status='source_failed', rows={}, errors={'sample':str(exc)})
                    except ERRORS:
                        pass
        if replay:
            present = {p.name for p in folders}
            errors.update({k:'旧证据的原始批次此次不可读' for k in batches if k not in present})
        refs = refs or dict(status='source_failed', previous_date=cal['previous'], previous_members=None)
        result = summarize(day, batches.values(), refs, errors, set(old.get('input_by', [])) | {actor})
        _write(path, result)
        return dict(status=result['status'], path=str(path), coverage=result['coverage'],
                    events=len(result[event_field]), source_errors=errors)


def replay_day(day, actor, *, events_root=None, root=ROOT, db_path=None):
    if events_root is None:
        events_root = root.parent / 'intraday-events'
    with closing(get_readonly_connection(db_path)) as conn:
        cal = data.calendar(conn, day)
        folders = sorted(p for p in (Path(events_root)/day).glob('*') if p.is_dir())
        return collect(root, conn, day, cal, folders, actor, replay=True)
