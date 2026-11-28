"""以前夜已知的固定名单复核同日十分钟题材扩散。所有输入来自本机已归档事实。"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median

from analyzers import tracked_theme_members as tracked
from analyzers.theme_launch import rankings
from services.board_break_intraday.data import TZ, calendar, digest, number
from services.course_core_minutes import _read as read_core_archive
from services.course_intraday_universe import RULE_VERSION
from services.emotion_leader.suspensions import load_suspensions
from services.handwritten_review import load_entry_before
from services.intraday_events.archive import read_report
from services.intraday_events.analytics import comparable
from services.intraday_events.turnover_baseline import _sample, _slot
from services.intraday_monitor.guards import is_intraday_session, quote_datetime


SCHEMA = 'theme-intraday-breadth-v1'
SLOTS = tuple(start + 10 * n for start in (572, 782) for n in range(12))
ERRORS = (ValueError, KeyError, TypeError, OSError, AttributeError)


def _checked(block):
    return isinstance(block, dict) and block.get('sha256') == digest({k: v for k, v in block.items() if k != 'sha256'})


def previous_archive(conn, prior, cutoff):
    row = conn.execute('SELECT raw_data,created_at FROM daily_market WHERE date=?', (prior,)).fetchone()
    if not row:
        raise ValueError('缺前一开放日盘后原始信封')
    envelope = json.loads(row[0])
    created = datetime.fromisoformat(row[1])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    captured = datetime.fromisoformat(envelope['generated_at'])
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=TZ)
    if (envelope.get('date') != prior or captured.astimezone(TZ).date().isoformat() != prior
            or created >= cutoff or captured >= cutoff):
        raise ValueError('前一开放日信封在开盘前不可核验')
    return envelope['raw_data'], dict(source='daily_market.raw_data', date=prior, created_at=created.isoformat(),
                                      generated_at=captured.isoformat(), sha256=sha256(row[0].encode()).hexdigest())


def concept_groups(raw, prior, requests, cutoff):
    """身份和名单都只取前一开放日。部分概念目录只可确认显式编码或已核准的别名。"""
    if raw.get('date') != prior:
        raise ValueError('前夜盘后来源日期错位')
    raw = dict(raw)
    block = raw.pop(tracked.KEY, None)
    full = None
    if _checked(block) and block.get('schema') == tracked.SCHEMA and block.get('trade_date') == prior:
        raw[tracked.KEY] = block
        if block.get('catalog_source') == tracked.CATALOG:
            try:
                full = tracked._catalog_rows(block.get('catalog'), prior)
            except ERRORS:
                pass
    catalog = full if full is not None else tracked._daily_identities(raw, prior)
    if full is None:
        catalog = dict(catalog)
        try:
            previous_rank = rankings(raw.get('limit_cpt_list'), prior)
        except (ValueError, TypeError, KeyError):
            previous_rank = []
        for row in previous_rank:
            if row['code'] in catalog and catalog[row['code']] != row['name']:
                raise ValueError('前夜概念代码对应多个名称')
            catalog[row['code']] = row['name']
    rows, groups = [], {}
    for item in requests:
        name = item['requested']
        allow = full is not None or name in tracked.KNOWN_CONCEPTS or re.search(r'[0-9]{6}\.TI`?$', name)
        code = tracked._resolve(name, catalog) if allow else None
        row = dict(requested=name, code=code, status='identity_unverified' if not code else 'members_unavailable')
        rows.append(row)
        if not code:
            continue
        snapshot, members = tracked._stored(raw, code, prior)
        if members:
            fetched = datetime.fromisoformat(snapshot['fetched_at'])
            if fetched.tzinfo is None or fetched >= cutoff or fetched.astimezone(TZ).date().isoformat() != prior:
                continue
            key = 'ths:' + code
            groups[key] = dict(name=catalog[code], kind='previous_session_full_concept', members=members,
                               source=dict(code=code, date=prior, provider=tracked.SOURCE,
                                           fetched_at=snapshot['fetched_at'], sha256=digest(snapshot)))
            row.update(group=key, status='verified')
    return rows, groups


def named_groups(path, day, prior, cutoff, selection):
    evidence = read_core_archive(path, day)
    universe = (evidence or {}).get('universe') or {}
    proof = universe.get('source') or {}
    fixed = proof.get('handwritten') or {}
    if (not _checked(universe) or universe.get('rule_version') != RULE_VERSION
            or universe.get('trade_date') != day or universe.get('as_of_date') != prior
            or universe.get('cutoff_at') != cutoff.isoformat()
            or not fixed or any(fixed.get(k) != selection.get(k) for k in
                                ('recorded_at', 'date', 'source_sha256', 'supplement_sha256'))):
        raise ValueError('点名篮子与开盘前手写稿或冻结名单版本不符')
    members = {r['code']: r for r in universe['members']}
    groups = {}
    for row in members.values():
        for peer in row['comparisons']:
            code, theme, source = peer['code'], peer['theme'], peer['source']
            if source not in {'handwritten', 'volume_concepts'} or '前夜点名' not in peer['basis']:
                continue
            if code not in members or source not in proof or theme not in members[code]['theme_groups']:
                raise ValueError('点名对照与原名单来源不符')
            key = 'named:' + theme
            group = groups.setdefault(key, dict(name=theme, kind='named_core_and_peers', members={}, pairs=[],
                        source=dict(date=prior, universe_sha256=universe['sha256'], handwritten=fixed, evidence={})))
            group['members'].update({row['code']: row['name'], code: members[code]['name']})
            group['pairs'].append(dict(core=row['code'], peer=code, basis=peer['basis']))
            group['source']['evidence'][source] = proof[source]
    return groups


def _compact(report, receipt, wanted, day):
    snap = report.get('snapshot') or {}
    if (snap.get('interval_minutes') != 10 or report.get('trade_date') != day
            or snap.get('trade_date') != day or receipt.get('trade_date') != day):
        raise ValueError('无已核验十分钟行情')
    sample = {k: snap[k] for k in ('trade_date', 'captured_at', 'interval_minutes', 'context_sha256', 'source')}
    sample.update(stocks={}, scheduled_at=report.get('scheduled_at'), batch=receipt['batch'], sha256=receipt['sha256'])
    slot = _slot(sample)
    captured = datetime.fromisoformat(sample['captured_at'])
    rows = snap['raw_rows']
    if (not snap['source'] or not isinstance(rows, list) or snap['raw_sha256'] != digest(rows)
            or not report.get('context') or digest(report['context']) != snap['context_sha256']):
        raise ValueError('原始报价或股票范围哈希不符')
    counts = Counter(q.get('code') for q in rows if isinstance(q, dict) and q.get('code') in wanted)
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = raw.get('code')
        if code not in counts or counts[code] != 1 or code not in snap['stocks']:
            continue
        q = snap['stocks'][code]
        stamp = quote_datetime(raw)
        try:
            price = number(raw['price'], positive=True)
            ref = number(raw['pre_close'], positive=True)
            amount = number(raw['amount'])
            if (not stamp or not is_intraday_session(stamp) or stamp.date().isoformat() != day
                    or not 0 <= (captured-stamp).total_seconds() <= 90
                    or q['quote_at'] != stamp.isoformat() or price != q['price'] or amount < 0 or amount != q['amount']):
                continue
        except ERRORS:
            continue
        sample['stocks'][code] = dict(price=price, pre_close=ref, amount=amount, quote_at=stamp.isoformat())
    return slot, sample


def batches(root, day, wanted):
    by_slot, gaps = defaultdict(list), []
    folder = root / day
    for path in sorted(p for p in folder.glob('*') if p.is_dir()):
        report = None
        try:
            receipt = json.loads((path/'receipt.json').read_bytes())
            report = read_report(path, receipt)
            if (report.get('snapshot') or {}).get('interval_minutes') == 5:
                continue
            slot, data = _compact(report, receipt, wanted, day)
            by_slot[slot].append(data)
        except ERRORS as exc:
            gaps.append(f'{path.name} 未通过采样核验：{exc}')
            # 有核验回执的失败批次若占据一个时槽，该槽不能另挑成功记录。
            if report is not None and report.get('captured_at'):
                try:
                    slot = _slot(dict(trade_date=day, captured_at=report['captured_at']))
                    by_slot[slot].append(None)
                except ERRORS:
                    pass
    usable = {}
    for slot, attempts in sorted(by_slot.items()):
        if len(attempts) == 1 and attempts[0] is not None:
            usable[slot] = attempts[0]
        else:
            gaps.append(f'{slot//60:02d}:{slot%60:02d} 存在失败或重复批次')
    return usable, gaps


def point(members, halted, old, current):
    prices, amounts, conflict, missing = {}, {}, [], []
    verified_halts = []
    linked = bool(old and old['source'] == current['source'] and comparable(old, current))
    for code in members:
        q = current['stocks'].get(code)
        if code in halted:
            if q and q['amount'] > 0:
                conflict.append(code)
            else:
                verified_halts.append(code)
            continue
        if not q:
            missing.append(code)
            continue
        prices[code] = (q['price']/q['pre_close']-1)*100
        if linked:
            try:
                amounts[code] = _sample(code, old, current, current['source'])['amount_yuan']
            except ERRORS:
                pass
    up = sum(v > 0 for v in prices.values())
    eligible = len(members)-len(verified_halts)
    return dict(captured_at=current['captured_at'], batch=current['batch'], expected=len(members),
                price_valid=len(prices), up=up if prices else None,
                up_ratio_pct=round(100*up/len(prices), 4) if prices else None,
                median_pct=round(median(prices.values()), 4) if prices else None,
                pct_chg_by_code={k: round(v, 4) for k,v in prices.items()},
                verified_full_day_halts=verified_halts, conflicting_halt=conflict, missing_quotes=missing,
                adjacent_same_session=linked, amount_valid=len(amounts),
                amount_delta_by_code_yuan={k: round(v, 2) for k, v in amounts.items()},
                observed_amount_delta_yuan=round(sum(amounts.values()), 2) if amounts else None,
                full_amount_delta_yuan=round(sum(amounts.values()), 2) if amounts and len(amounts)==eligible else None,
                price_status='complete' if len(prices)==eligible and prices else 'partial')


def build(conn, root, day, input_by, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    result = dict(schema=SCHEMA, trade_date=day, input_by=input_by, status='source_failed',
                  groups={}, requested=[], batches=[], gaps=[], overlap={})
    cal = calendar(conn, day)
    if not cal['is_open'] or day > now.date().isoformat() or (day == now.date().isoformat() and now.hour < 15):
        result.update(status='skipped', gaps=['休市或本日尚未收盘'])
        return result
    prior = result['previous_date'] = cal['previous']
    cutoff = datetime.fromisoformat(day+'T09:30:00+08:00')
    try:
        entry, _, _ = load_entry_before(root/'data/handwritten-review'/prior, cutoff)
        meta = entry['meta']
        requests, basis = tracked._requests(entry)
        selection = {k: meta[k] for k in ('date', 'recorded_at', 'source_sha256', 'supplement_sha256')}
        if selection['date'] != prior:
            raise ValueError('前夜选题日期不符')
    except ERRORS as exc:
        result['gaps'].append(f'前夜选题未核验：{exc}')
        return result
    result['selection'] = dict(selection, basis=basis, requests=requests)
    if not requests:
        result['status'] = 'empty'
        return result
    groups = result['groups']
    try:
        raw, source = previous_archive(conn, prior, cutoff)
        result['membership_archive'] = source
        result['requested'], official = concept_groups(raw, prior, requests, cutoff)
        groups.update(official)
    except ERRORS as exc:
        result['gaps'].append(f'前一开放日的概念成员来源无法核验：{exc}')
    if len(requests) != sum(r['status']=='verified' for r in result['requested']):
        result['gaps'].append('部分事前题材的概念身份或前一开放日完整成员缺失')
    try:
        groups.update(named_groups(root/'data/reports/course-core-minutes'/(day+'.json'), day, prior, cutoff, selection))
    except ERRORS as exc:
        result['gaps'].append(str(exc))
    shared = defaultdict(list)
    for key, group in groups.items():
        for code in group['members']:
            shared[code].append(key)
    result['overlap'] = {code: ids for code,ids in shared.items() if len(ids)>1}
    result['unique_members'] = len(shared)
    halted = load_suspensions(conn, day)
    result['verified_full_day_halt_evidence'] = {c: halted[c] for c in shared if c in halted}
    samples, gaps = batches(root/'data/reports/intraday-events', day, set(shared))
    result['gaps'].extend(gaps)
    result['batches'] = [dict(slot=f'{i//60:02d}:{i%60:02d}', **{k:s[k] for k in ('captured_at','batch','sha256','source')})
                         for i,s in sorted(samples.items())]
    result['missing_slots'] = [f'{i//60:02d}:{i%60:02d}' for i in SLOTS if i not in samples]
    for group in groups.values():
        group['series'] = [point(group['members'], halted, samples.get(i-10), s) for i,s in sorted(samples.items())]
    partial_point = any(p['price_status'] != 'complete' or
                        (i not in (SLOTS[0], SLOTS[12]) and p['full_amount_delta_yuan'] is None)
                        for g in groups.values() for i, p in zip(sorted(samples), g['series']))
    result['status'] = 'partial' if result['gaps'] or result['missing_slots'] or not groups or partial_point else 'complete'
    if not samples:
        result['status'] = 'source_failed'
        result['gaps'].append('当日无可核验的十分钟快照')
    return result
