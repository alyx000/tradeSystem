"""Read-only report projections from verified identity lists; no fetching or persistence."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date


def _codes(values):
    if not isinstance(values, list) or any(not isinstance(v, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', v) for v in values):
        raise ValueError('股票身份非法')
    if len(values) != len(set(values)):
        raise ValueError('股票身份重复')
    return set(values)


def high_flows(manifest, report_date):
    if not isinstance(manifest, dict):
        return {'status': 'missing-data', 'reason': '缺少滚动新高身份清单'}
    try:
        previous = manifest['prev_as_of']
        if (manifest.get('status') != 'complete' or manifest.get('complete') is not True or
                manifest.get('as_of') != report_date or manifest.get('report_date') != report_date or
                manifest.get('basis') != 'rolling-adjusted-high' or previous >= report_date or
                manifest.get('trade_dates', [])[-2:] != [previous, report_date]):
            raise ValueError('日期、窗口或完整性不符')
        windows = {}
        for window in ('60', '120', '250'):
            before, after = _codes(manifest['previous_codes'][window]), _codes(manifest['current_codes'][window])
            counts = manifest['counts'][window]
            if counts['previous'] != len(before) or counts['current'] != len(after):
                raise ValueError('名单与计数不一致')
            windows[window] = dict(previous=sorted(before), current=sorted(after), retained=sorted(before & after), exited=sorted(before - after), added=sorted(after - before))
        if manifest.get('sixty_day_overlap') != len(windows['60']['retained']):
            raise ValueError('60日交集与正式统计不一致')
        return dict(status='complete', date=report_date, previous_date=previous, windows=windows)
    except (KeyError, TypeError, ValueError) as exc:
        return {'status': 'source_failed', 'reason': str(exc)}


def _source(raw, key, api, day, *, allow_empty=True, limit_type=None):
    source = raw.get(key)
    if not isinstance(source, dict) or source.get('status') != 'complete' or source.get('api') != api:
        raise ValueError(key + ' 来源不完整')
    if source.get('params', {}).get('trade_date') != day:
        raise ValueError(key + ' 请求日期不符')
    if limit_type and source.get('params', {}).get('limit_type') != limit_type:
        raise ValueError(key + ' 涨停口径不符')
    rows, pages = source.get('rows'), source.get('pages')
    if not isinstance(rows, list) or (not allow_empty and not rows) or not isinstance(pages, list) or not pages:
        raise ValueError(key + ' 缺少完整分页')
    if any(p.get('offset') != i * 2000 or type(p.get('count')) is not int or p['count'] < 0 or p['count'] > 2000 for i, p in enumerate(pages)):
        raise ValueError(key + ' 分页错位')
    if pages[-1]['count'] != 0 or sum(p['count'] for p in pages) != len(rows) or any(p['count'] == 0 for p in pages[:-1]):
        raise ValueError(key + ' 分页未闭合')
    _codes([r.get('ts_code') for r in rows])
    if any(r.get('trade_date') != day for r in rows):
        raise ValueError(key + ' 行情日期不符')
    return rows


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _height(value):
    value = _number(value)
    if value is None or value < 1 or not value.is_integer():
        raise ValueError('连板高度非法')
    return int(value)


def _eligible(row, st):
    return row['ts_code'] not in st and not row['ts_code'].startswith(('200', '201', '900')) and 'ST' not in str(row.get('name', '')).upper() and '退' not in str(row.get('name', ''))


def _identity_names(raw, day, report_date):
    source = raw.get(day + '_identity')
    if source is None:
        return {}
    if (not isinstance(source, dict) or source.get('api') != 'index_member_all' or source.get('status') != 'complete' or
            source.get('as_of') != report_date or not str(source.get('fetched_at', '')).startswith(report_date) or
            not isinstance(source.get('rows'), list) or not source['rows']):
        raise ValueError('当日名称快照缺失、错日或来源失败')
    names = {}
    for row in source['rows']:
        code, name = row.get('ts_code'), row.get('name')
        # SW retains legacy delisted T-series identities outside today's A-share universe.
        if isinstance(code, str) and re.fullmatch(r'T\d{5}\.SH', code) and isinstance(name, str) and '退市' in name:
            continue
        _codes([code])
        if row.get('is_new') != 'Y' or not isinstance(name, str) or not name.strip():
            raise ValueError('名称快照非当期成员或名称无效')
        names.setdefault(code, set()).add(name.strip())
    return names


def ladder_flows(raw, manifest, report_date):
    """Prior non-ST/non-delisted U cohort -> current identity-matched outcomes.

    A complete current U list alone cannot prove a break: an absent/invalid
    current daily quote remains unknown. Changed eligibility is separate.
    """
    if not isinstance(raw, dict):
        return {'status': 'missing-data', 'reason': '缺少双日涨停/ST及当日日线原始收据'}
    try:
        previous = manifest['prev_as_of']
        if manifest.get('trade_dates', [])[-2:] != [previous, report_date] or manifest.get('as_of') != report_date:
            raise ValueError('缺少严格相邻开放日依据')
        date.fromisoformat(previous); date.fromisoformat(report_date)
        before, today = previous.replace('-', ''), report_date.replace('-', '')
        p_up = _source(raw, before + '_U', 'limit_list_d', before, limit_type='U')
        c_up = _source(raw, today + '_U', 'limit_list_d', today, limit_type='U')
        p_st = _codes([r['ts_code'] for r in _source(raw, before + '_st', 'stock_st', before, allow_empty=False)])
        c_st = _codes([r['ts_code'] for r in _source(raw, today + '_st', 'stock_st', today, allow_empty=False)])
        quotes = _source(raw, today + '_daily', 'daily', today, allow_empty=False)
        if len(quotes) < 4000:
            raise ValueError('全市场日线不足4000行')
        quote_map = {r['ts_code']: r for r in quotes}
        current = {r['ts_code']: r for r in c_up}
        identity_names = _identity_names(raw, today, report_date)
        for r in p_up + c_up:
            _height(r.get('limit_times'))
            if r.get('limit') != 'U' or not str(r.get('name', '')).strip():
                raise ValueError('涨停行身份或类别非法')
        cohort = [r for r in p_up if _eligible(r, p_st)]
        records = []
        for prior in sorted(cohort, key=lambda r: (_height(r['limit_times']), r['ts_code'])):
            code, height = prior['ts_code'], _height(prior['limit_times'])
            quote, now = quote_map.get(code), current.get(code)
            record = dict(code=code, name=prior['name'], previous_height=height, current_height=None)
            names = set(identity_names.get(code, set()))
            for row in (quote, now):
                if row and isinstance(row.get('name'), str) and row['name'].strip():
                    names.add(row['name'].strip())
            if code in c_st or any('退' in n or 'ST' in n.upper() for n in names):
                outcome, reason = 'excluded', '当日ST/退市资格变化'
            elif len(names) != 1:
                outcome, reason = 'unknown', '当日名称身份缺失或冲突，ST/退市资格未核实'
            elif quote is None or any(_number(quote.get(k)) is None or _number(quote[k]) <= 0 for k in ('open', 'close', 'pre_close', 'vol', 'amount')):
                outcome, reason = 'unknown', '缺少有效当日日线，停牌等原因未核实'
            elif now is None:
                outcome, reason = 'broken', '有有效日线，未在完整当日涨停名单'
            elif _height(now['limit_times']) == height + 1:
                outcome, reason = 'promoted', '同股涨停且连板高度加一'
                record['current_height'] = height + 1
            else:
                outcome, reason = 'unknown', '涨停身份存在但高度不连续，待核验'
                record['current_height'] = _height(now['limit_times'])
            record.update(outcome=outcome, reason=reason)
            record['current_names'] = sorted(names)
            records.append(record)
        groups = []
        for height in sorted({_height(r['limit_times']) for r in cohort}):
            group = [r for r in records if r['previous_height'] == height]
            counts = Counter(r['outcome'] for r in group)
            groups.append(dict(height=height, total=len(group), **{k: counts[k] for k in ('promoted', 'broken', 'unknown', 'excluded')}))
        return dict(status='partial' if any(r['outcome'] in ('unknown', 'excluded') for r in records) else 'complete', date=report_date, previous_date=previous, sample=len(records), groups=groups, records=records)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {'status': 'source_failed', 'reason': str(exc)}


def sector_limit_counts(raw, manifest, report_date):
    """Disjoint SW L2 counts, both days mapped through the same dated snapshot."""
    if not isinstance(raw, dict):
        return {'status': 'missing-data', 'reason': '缺少涨停明细与同日申万二级成分快照'}
    try:
        previous = manifest['prev_as_of']
        if manifest.get('trade_dates', [])[-2:] != [previous, report_date]:
            raise ValueError('板块统计日期非相邻开放日')
        day = report_date.replace('-', '')
        _identity_names(raw, day, report_date)
        identity = raw.get(day + '_identity')
        if identity is None:
            raise ValueError('缺少同日申万二级成分快照')
        mapping = {}
        for row in identity['rows']:
            code, name = row.get('l2_code'), row.get('l2_name')
            if isinstance(code, str) and code and isinstance(name, str) and name:
                mapping.setdefault(row['ts_code'], set()).add((code, name))
        if not mapping:
            raise ValueError('快照没有申万二级分类字段')
        sectors, totals, unknown = {}, {}, {}
        for slot, iso in [('previous', previous), ('current', report_date)]:
            compact = iso.replace('-', '')
            ups = _source(raw, compact + '_U', 'limit_list_d', compact, limit_type='U')
            st = _codes([r['ts_code'] for r in _source(raw, compact + '_st', 'stock_st', compact, allow_empty=False)])
            eligible = [r for r in ups if _eligible(r, st)]
            totals[slot], unknown[slot] = len(eligible), []
            for row in eligible:
                height = _height(row.get('limit_times'))
                if row.get('limit') != 'U':
                    raise ValueError('涨停统计混入其他类型')
                matches = mapping.get(row['ts_code'], set())
                if len(matches) == 1:
                    sector_code, sector_name = next(iter(matches))
                else:
                    sector_code, sector_name = 'UNCLASSIFIED', '未分类（缺失或冲突）'
                    unknown[slot].append(row['ts_code'])
                sector = sectors.setdefault(sector_code, dict(code=sector_code, name=sector_name, previous=[], current=[]))
                if sector['name'] != sector_name:
                    raise ValueError('同一行业代码名称冲突')
                sector[slot].append(dict(code=row['ts_code'], name=row['name'], height=height))
        result = []
        for sector in sectors.values():
            result.append(dict(**sector, previous_count=len(sector['previous']), current_count=len(sector['current']),
                               first_count=sum(r['height'] == 1 for r in sector['current']),
                               consecutive_count=sum(r['height'] >= 2 for r in sector['current'])))
        result.sort(key=lambda s: (-s['current_count'], s['name'], s['code']))
        return dict(status='partial' if any(unknown.values()) else 'complete', date=report_date, previous_date=previous,
                    classification_as_of=report_date, totals=totals, unclassified=unknown, sectors=result)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {'status': 'source_failed', 'reason': str(exc)}


def build_identity_flows(manifest, raw, report_date):
    high = high_flows(manifest, report_date)
    if high['status'] == 'complete':
        codes = {code for window in high['windows'].values() for key in ('previous', 'current') for code in window[key]}
        try:
            source_names = _identity_names(raw if isinstance(raw, dict) else {}, report_date.replace('-', ''), report_date)
            high['names'] = {code: next(iter(source_names[code])) for code in sorted(codes) if len(source_names.get(code, set())) == 1}
            high['name_status'] = 'complete' if len(high['names']) == len(codes) else 'partial'
            high['name_reason'] = '' if high['name_status'] == 'complete' else '部分当日名称缺失或冲突，保留代码并标名称待核'
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            high.update(names={}, name_status='source_failed', name_reason=str(exc))
    ladder = ladder_flows(raw, manifest, report_date) if high['status'] == 'complete' else {'status': 'missing-data', 'reason': '严格开放日依据未通过核验'}
    sectors = sector_limit_counts(raw, manifest, report_date) if high['status'] == 'complete' else {'status': 'missing-data', 'reason': '严格开放日依据未通过核验'}
    from services.review_sector_visuals import build_sector_visuals
    from services.review_theme_visuals import theme_heatmap
    visuals = build_sector_visuals(raw, manifest, report_date) if high['status'] == 'complete' else {
        key: dict(status='missing-data', reason='严格开放日依据未通过核验')
        for key in ('sector_heatmap', 'sector_breadth', 'limit_timeline')}
    themes = theme_heatmap(raw, manifest, report_date) if high['status'] == 'complete' else dict(status='missing-data',reason='严格开放日依据未通过核验')
    return dict(schema='review-identity-flows-v1', report_date=report_date, new_high=high, ladder=ladder, sector_limits=sectors, theme_heatmap=themes, **visuals)
