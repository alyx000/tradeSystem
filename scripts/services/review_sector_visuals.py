"""Pure report projections: dated sector counts, breadth and sealing timestamps."""
from __future__ import annotations

import re
from collections import Counter

from services.review_identity_flows import _codes, _eligible, _height, _identity_names, _number, _source


def _context(raw, manifest, day):
    if not isinstance(raw, dict):
        raise ValueError('缺少板块图形原始回执')
    dates = manifest.get('trade_dates', [])
    if (manifest.get('as_of') != day or len(dates) < 5 or dates[-1] != day or
            dates != sorted(set(dates))):
        raise ValueError('缺少截至报告日的严格五个开放日依据')
    compact = day.replace('-', '')
    names = _identity_names(raw, compact, day)
    identity = raw.get(compact + '_identity')
    if not identity or not names:
        raise ValueError('缺少同日申万成分与名称快照')
    candidates, labels = {}, {}
    for row in identity['rows']:
        code, sector, label = row.get('ts_code'), row.get('l2_code'), row.get('l2_name')
        if code not in names:
            continue
        if isinstance(sector, str) and re.fullmatch(r'\d{6}\.SI', sector) and isinstance(label, str) and label.strip():
            candidates.setdefault(code, set()).add((sector, label.strip()))
    mapping = {code: next(iter(matches)) for code, matches in candidates.items() if len(matches) == 1 and len(names[code]) == 1}
    for sector, label in mapping.values():
        if sector in labels and labels[sector] != label:
            raise ValueError('行业代码对应多个名称')
        labels[sector] = label
    if not mapping:
        raise ValueError('申万二级分类无有效映射')
    return dates[-5:], names, mapping, labels


def _limits(raw, day):
    compact = day.replace('-', '')
    ups = _source(raw, compact + '_U', 'limit_list_d', compact, limit_type='U')
    st = _codes([r['ts_code'] for r in _source(raw, compact + '_st', 'stock_st', compact, allow_empty=False)])
    for row in ups:
        if row.get('limit') != 'U' or not isinstance(row.get('name'), str) or not row['name'].strip():
            raise ValueError('涨停明细类别或名称无效')
        _height(row.get('limit_times'))
    return [r for r in ups if _eligible(r, st)], st


def _rank_current(ups, mapping):
    counts = Counter(mapping[r['ts_code']][0] for r in ups if r['ts_code'] in mapping)
    labels = {value[0]: value[1] for value in mapping.values()}
    return sorted(counts, key=lambda code: (-counts[code], labels[code], code))


def sector_heatmap(raw, manifest, day):
    dates, _, mapping, labels = _context(raw, manifest, day)
    current, _ = _limits(raw, day)
    ranked = _rank_current(current, mapping)
    days = []
    for target in dates:
        try:
            ups, _ = _limits(raw, target)
            records = [dict(code=r['ts_code'], name=r['name'], sector=mapping.get(r['ts_code'], ('UNCLASSIFIED',))[0]) for r in ups]
            unknown = [r['code'] for r in records if r['sector'] == 'UNCLASSIFIED']
            counts = Counter(r['sector'] for r in records)
            days.append(dict(date=target, status='partial' if unknown else 'complete', total=len(ups),
                             unclassified=unknown, counts=dict(counts), records=records))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            days.append(dict(date=target, status='source_failed', reason=str(exc), counts=None, total=None))
    return dict(status='complete' if all(d['status'] == 'complete' for d in days) else 'partial',
                date=day, classification_as_of=day, dates=dates, days=days,
                sectors=[dict(code=c, name=labels[c]) for c in ranked], selected=ranked[:10])


def sector_breadth(raw, manifest, day):
    _, names, mapping, labels = _context(raw, manifest, day)
    ups, st = _limits(raw, day)
    compact = day.replace('-', '')
    quotes = _source(raw, compact + '_daily', 'daily', compact, allow_empty=False)
    if len(quotes) < 4000:
        raise ValueError('全市场日线不足4000行')
    quote_map = {r['ts_code']: r for r in quotes}
    quote_codes = set(quote_map)
    named_st = {code for code, values in names.items() if any('ST' in name.upper() for name in values)} & quote_codes
    listed_st = st & quote_codes
    eligibility = dict(st_list_only=sorted(listed_st - named_st), name_st_only=sorted(named_st - listed_st),
                       st_union_count=len(listed_st | named_st))
    groups = {code: dict(code=code, name=label, records=[]) for code, label in labels.items()}
    up_codes = {r['ts_code'] for r in ups}
    for code, (sector, _) in sorted(mapping.items()):
        name = next(iter(names[code]))
        if not _eligible(dict(ts_code=code, name=name), st):
            continue
        quote = quote_map.get(code)
        valid = quote is not None and all(_number(quote.get(k)) is not None and _number(quote[k]) > 0 for k in ('close', 'pre_close', 'vol', 'amount'))
        record = dict(code=code, name=name, pct=None, outcome='unknown', limit_up=code in up_codes)
        if valid:
            delta = float(quote['close']) - float(quote['pre_close'])
            record.update(outcome='up' if delta > 0 else 'down' if delta < 0 else 'flat', pct=delta / float(quote['pre_close']) * 100)
        groups[sector]['records'].append(record)
    result = []
    for group in groups.values():
        counts = Counter(r['outcome'] for r in group['records'])
        valid = sum(counts[k] for k in ('up', 'flat', 'down'))
        result.append(dict(**group, sample=len(group['records']), valid=valid, **{k: counts[k] for k in ('up','flat','down','unknown')},
                           limit_up=sum(r['limit_up'] for r in group['records'])))
    # A quote outside the named classification cannot be silently treated as covered.
    unmapped = sorted(r['ts_code'] for r in quotes if r['ts_code'] not in mapping and _eligible(r, st))
    result.sort(key=lambda s: (-s['limit_up'], s['name'], s['code']))
    return dict(status='partial' if unmapped or eligibility['st_list_only'] or eligibility['name_st_only'] or any(s['unknown'] for s in result) else 'complete', date=day,
                classification_as_of=day, selected=_rank_current(ups, mapping)[:10], unmapped_quotes=unmapped,
                sectors=result, quote_count=len(quotes), eligibility=eligibility)


def _seal_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{5,6}', value):
        return None
    value = value.zfill(6)
    hour, minute, second = int(value[:2]), int(value[2:4]), int(value[4:])
    if minute > 59 or second > 59:
        return None
    total = hour * 3600 + minute * 60 + second
    # Provider auction records can carry seconds after 09:25 (e.g. 09:25:02).
    # They are shown as auction/opening records, not continuous trading.
    if not (9*3600+25*60 <= total <= 11*3600+30*60 or 13*3600 <= total <= 15*3600):
        return None
    return total


def limit_timeline(raw, manifest, day):
    _, _, mapping, labels = _context(raw, manifest, day)
    ups, _ = _limits(raw, day)
    records = []
    for row in ups:
        first, last = _seal_time(row.get('first_time')), _seal_time(row.get('last_time'))
        issues = []
        if first is None or last is None:
            issues.append('首次或最后封板时刻缺失/非法')
        if first is not None and last is not None and first > last:
            first = last = None
            issues.append('首次封板晚于最后封板，待核')
        opened = _number(row.get('open_times'))
        if opened is None or opened < 0 or not opened.is_integer():
            opened = None
            issues.append('开板次数未核实')
        else:
            opened = int(opened)
        sector, label = mapping.get(row['ts_code'], ('UNCLASSIFIED','未分类'))
        if sector == 'UNCLASSIFIED':
            issues.append('申万二级归属未核实')
        records.append(dict(code=row['ts_code'], name=row['name'], sector=sector, sector_name=label,
                            first=first, last=last, open_times=opened, height=_height(row['limit_times']), issues=issues))
    records.sort(key=lambda r: (r['first'] is None, r['first'] or 0, r['code']))
    ranked = _rank_current(ups, mapping)
    return dict(status='partial' if any(r['issues'] for r in records) else 'complete', date=day,
                selected=ranked[:10], sectors=[dict(code=c,name=labels[c]) for c in ranked], records=records, sample=len(records))


def build_sector_visuals(raw, manifest, day):
    result = {}
    for key, build in [('sector_heatmap',sector_heatmap),('sector_breadth',sector_breadth),('limit_timeline',limit_timeline)]:
        if not isinstance(raw, dict):
            result[key] = dict(status='missing-data',reason='缺少板块图形原始回执')
            continue
        try:
            result[key] = build(raw, manifest, day)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            result[key] = dict(status='source_failed',reason=str(exc))
    return result
