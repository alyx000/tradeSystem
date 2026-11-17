"""Five-session retrospective screen, derived only from verified dated archives.

The v2 archives are immutable evidence, not the new rule's membership. Replay A
on each archive's own universe and prices; B is unchanged and uses its verified
events. A historical match is never presented as a current match.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import sqlite3
from decimal import Decimal
from pathlib import Path

import pandas as pd

import os
import re
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE_VERSION = 'v2-volume-2026-11-02'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic(path, text):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


def verified_base(run):
    """Validate the existing v1 research envelope without importing its code."""
    report, validation, receipt = (read(run/name) for name in
                                  ('report.json', 'validation.json', 'run-receipt.json'))
    assert report['date'] == run.name == receipt['market_date']
    assert report['status'] in ('complete', 'partial') and receipt['delivery_ready']
    assert validation['status'] == 'passed' and validation['report_sha256'] == sha(run/'report.json')
    for name in ('report.json', 'report.md', 'validation.json', 'comparison.json',
                 'comparison.md', 'full-screen.csv', 'evaluate.py'):
        assert receipt['artifact_sha256'][name] == sha(run/name), name
    for name, digest in report['source_sha256'].items():
        path = Path(name)
        if path.name == 'recurring-contract.md' and sha(path) != digest:
            path = run.parent.parent/'contract-history'/(digest+'.md')
        assert sha(path) == digest, str(path)
    return report


def relocate_links(text, original_dir, new_dir):
    def replace(match):
        target = match.group(1)
        if target.startswith(('/', '#')) or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):
            return match.group(0)
        return '](' + os.path.relpath(original_dir/target, new_dir) + ')'
    return re.sub(r'\]\(([^)]+)\)', replace, text)

VERSION = 'recent-five-raised-range-hs-no-limit-down-2026-11-17'
LOOKBACK = 5
DEFINITION = ('最近5个SSE开放日，含事实日；逐日以当日归档的股票范围和行情重算。'
              'A前两根阳线且收盘上升；第三根阳线仍须收盘上升，第三根阴线则须最高/最低价均严格上移。'
              '三根成交量严格递增、各根不低于此前20根均量、三根均量≥2倍、近10根无真实收盘涨停仍须成立。'
              '十字星不作为第三根阴线；B原规则不变。最终名单统一排除北交所及事实日收盘跌停；'
              '收盘/跌停价缺失单列待核验。历史命中不等于今日成立；缺日不补0。')
UNKNOWN = {'target_suspended', 'target_missing_unverified', 'insufficient_history',
           'historical_gap_unverified', 'nontrading_volume', 'limit_data_missing',
           'source_missing', 'missing-data'}
REASONS = {'volume_shape_not_matched': '当日量价形态不满足',
           'recent_limit_up': '近10根已有涨停', 'no_recent_limit_up': '近期无涨停',
           'no_prelimit_buildup': '涨停前堆量不满足', 'volume_not_shrunk': '回调未缩量',
           'not_below_limit_close': '未回到涨停收盘下方',
           'limit_up_today_no_pullback': '当日涨停，尚无回调',
           'structure_invalidated': '当前B结构失效', 'source_missing': '当日未覆盖',
           'target_suspended': '当日停牌', 'target_missing_unverified': '当日行情缺失待核验',
           'insufficient_history': '历史不足', 'historical_gap_unverified': '历史缺日待核验',
           'nontrading_volume': '成交量无效待核验', 'limit_data_missing': '涨停价缺失待核验',
           'missing-data': '量价数据缺失待核验'}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def market_days(db, target, count=LOOKBACK):
    rows = db.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date<=? ORDER BY date DESC LIMIT 90", (target,)).fetchall()[::-1]
    assert rows and rows[-1] == (target, 1), 'target_calendar_not_open'
    dates = [d for d, _ in rows]
    assert dates == pd.date_range(dates[0], target).strftime('%Y-%m-%d').tolist(), 'calendar_gap'
    assert all(flag in (0, 1) for _, flag in rows), 'invalid_calendar_flag'
    opened = [d for d, flag in rows if flag]
    assert len(opened) >= count, 'calendar_insufficient'
    return opened, opened[-count:]


def shape_a(rows):
    """Pure Decimal comparisons in a shared adjusted-price coordinate system."""
    if len(rows) < 23:
        return None
    tail = rows[-23:]
    if len({r['trade_date'] for r in tail}) != 23:
        return None
    keys = ('open', 'high', 'low', 'close', 'vol', 'adj_factor')
    if not all(isinstance(r.get(k), (int, float)) and not isinstance(r[k], bool)
               and math.isfinite(r[k]) and r[k] > 0 for r in tail for k in keys):
        return None
    if any(r['high'] < max(r['open'], r['close'], r['low']) or
           r['low'] > min(r['open'], r['close'], r['high']) for r in tail):
        return None
    s = tail[-3:]
    price = [{k: Decimal(str(r[k])) * Decimal(str(r['adj_factor']))
              for k in ('open', 'high', 'low', 'close')} for r in s]
    first, second, third = price
    prefix = (first['close'] > first['open'] and second['close'] > second['open']
              and second['close'] > first['close'])
    rising_yang = third['close'] > third['open'] and third['close'] > second['close']
    raised_yin = (third['close'] < third['open'] and third['high'] > second['high']
                  and third['low'] > second['low'])
    vols = [Decimal(str(r['vol'])) for r in s]
    baseline_sum = sum(Decimal(str(r['vol'])) for r in tail[:-3])
    volume_ok = (vols[0] < vols[1] < vols[2] and all(v * 20 >= baseline_sum for v in vols)
                 and sum(vols) * 20 >= 6 * baseline_sum)
    if not (prefix and (rising_yang or raised_yin) and volume_ok):
        return None
    return dict(start_date=s[0]['trade_date'], end_date=s[-1]['trade_date'],
                volume_ratio=float(sum(vols) * 20 / (3 * baseline_sum)),
                third_bar='raised_yin' if raised_yin else 'rising_yang')


def classify_a(rows, limits):
    event = shape_a(rows)
    if event is None:
        return None, 'volume_shape_not_matched'
    flags = []
    for r in rows[-10:]:
        up = limits.get((r['trade_date'], r['ts_code']))
        if up is None or not math.isfinite(up) or up <= 0 or r['close'] > up + .005:
            flags.append(None)
        else:
            flags.append(abs(r['close'] - up) < 1e-8)
    if True in flags:
        return None, 'recent_limit_up'
    if None in flags:
        return None, 'limit_data_missing'
    return event, 'matched'


def load_archive(run):
    """Offline integrity gate; explicitly no today-only delivery gate bypass."""
    base = verified_base(run)
    out = run / 'enhanced-v2'
    report, validation = read(out/'report.json'), read(out/'validation.json')
    assert report['date'] == run.name == validation['date'], 'archive_date_mismatch'
    assert report['rule_version'] == BASE_VERSION and report['status'] in ('partial', 'complete')
    assert validation['status'] == 'passed' and validation['report_sha256'] == sha(out/'report.json')
    assert validation['markdown_sha256'] == sha(out/'report.md')
    sources = dict(base['source_sha256'], **report['source_sha256'])
    for name, digest in sources.items():
        path = Path(name)
        if path.name == 'recurring-contract.md' and sha(path) != digest:
            path = run.parent.parent/'contract-history'/(digest+'.md')
        assert sha(path) == digest, f'source_drift:{path}'
    for path in (out/'report.json', out/'report.md', out/'validation.json'):
        sources[str(path)] = sha(path)
    screen_path = out/'v2-screen.json'
    assert str(screen_path) in report['source_sha256'], 'screen_not_bound'
    states = read(screen_path)
    assert len({x['code'] for x in states}) == len(states), 'duplicate_screen_code'
    daily_path = run/'cache/daily.pkl'
    assert str(daily_path) in sources, 'daily_not_bound'
    daily = pd.read_pickle(daily_path)
    assert not daily.duplicated(['ts_code', 'trade_date']).any(), 'duplicate_daily'
    assert daily.trade_date.max() == run.name, 'future_or_stale_daily'
    limits, down_limits = {}, {}
    # Only load source-bound limit files. An unbound extra file cannot fill a gap.
    for name in base['source_sha256']:
        path = Path(name)
        if path.parent == run and path.name.startswith('limits-') and path.suffix == '.pkl':
            day = path.stem[7:]
            if day > run.name:
                continue
            records = pd.read_pickle(path)
            assert len({x['ts_code'] for x in records}) == len(records), 'duplicate_limit_code'
            for x in records:
                assert x['trade_date'].replace('-', '') == day.replace('-', ''), 'limit_date_mismatch'
                if x.get('up_limit') is not None:
                    limits[(day, x['ts_code'])] = float(x['up_limit'])
                if x.get('down_limit') is not None:
                    down_limits[(day, x['ts_code'])] = float(x['down_limit'])
    return dict(report=report, states=states, daily=daily, limits=limits,
                down_limits=down_limits, sources=sources)


def replay(archive):
    r = archive['report']
    groups = {code: g.sort_values('trade_date').tail(31).to_dict('records')
              for code, g in archive['daily'].groupby('ts_code')}
    states, candidates = [], []
    industries = pd.read_pickle(Path(next(p for p in r['source_sha256'] if p.endswith('/industries.pkl'))))
    for old in archive['states']:
        row = dict(code=old['code'], name=old['name'], A=old['A'], B=old['B'])
        # These states carry the base universe/history gates. Never turn gaps into negatives.
        if old['A'] in UNKNOWN - {'limit_data_missing'}:
            states.append(row)
            continue
        rows = groups.get(old['code'], [])
        if not rows or rows[-1]['trade_date'] != r['date']:
            row['A'] = 'source_missing'
        elif len(rows)<23 or not all(isinstance(x.get(k),(int,float)) and math.isfinite(x[k]) and x[k]>0
                                    for x in rows[-23:] for k in ('open','high','low','close','vol','adj_factor')):
            row['A'] = 'missing-data'
        else:
            event, row['A'] = classify_a(rows, archive['limits'])
            if event:
                info = industries.get(row['code'], industries.get(row['code'].split('.')[0], {}))
                candidates.append(dict(code=row['code'], name=row['name'], stage='A',
                                       industry=info.get('sw_l2', '未分类'), **event))
        states.append(row)
    candidates += [dict(x) for x in r['candidates'] if x['stage'] == 'B']
    old_a = {x['code'] for x in r['candidates'] if x['stage'] == 'A'}
    assert old_a <= {x['code'] for x in candidates if x['stage'] == 'A'}, 'old_A_lost'
    unknown={s:[dict(code=x['code'],name=x['name'],reason=x[s]) for x in states if x[s] in UNKNOWN] for s in ('A','B')}
    return dict(date=r['date'], candidates=candidates, states=states,
                uncomputed_A=unknown['A'], uncomputed_B=unknown['B'],
                status='partial' if any(unknown.values()) else r['status'], base_coverage=r['base_coverage'], base_exclusions=r['base_exclusions'])


def b_structure(event, rows, opened, target):
    """Preserve the original B anchor; an intervening break never revives."""
    start, limit = event['start_date'], event['limit_date']
    relevant = [r for r in rows if start <= r['trade_date'] <= target]
    bydate = {r['trade_date']: r for r in relevant}
    needed = [d for d in opened if start <= d <= target]
    if (not needed or needed[0] != start or len(bydate) != len(relevant)
            or set(needed) - bydate.keys() or target not in bydate or limit not in bydate):
        return dict(status='待核验', reason='事件后行情缺日（含停牌）或重复')
    if not all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) and r[k] > 0
               for r in relevant for k in ('low', 'adj_factor')):
        return dict(status='待核验', reason='事件后价格/因子无效')
    anchor = Decimal(str(bydate[start]['low'])) * Decimal(str(bydate[start]['adj_factor']))
    broken = [r['trade_date'] for r in relevant if r['trade_date'] > limit
              and Decimal(str(r['low'])) * Decimal(str(r['adj_factor'])) < anchor]
    return dict(status='结构已失效' if broken else '未跌破原结构低点',
                first_broken_date=min(broken) if broken else None)


def combine(days, scans, gaps, daily, opened):
    assert days == sorted(set(days)) and len(days) == LOOKBACK
    target = days[-1]
    assert target in scans, 'current_scan_missing'
    current = {(x['code'], x['stage']) for x in scans[target]['candidates']}
    state_map = {x['code']: x for x in scans[target]['states']}
    hits = collections.defaultdict(list)
    for day in days:
        if day in scans:
            for x in scans[day]['candidates']:
                hits[x['code']].append(dict(x, occurrence_date=day))
    groups = {code: g.sort_values('trade_date').to_dict('records')
              for code, g in daily[daily.ts_code.isin(hits)].groupby('ts_code')}
    rows = []
    for code, events in sorted(hits.items()):
        dates = sorted({x['occurrence_date'] for x in events})
        stages = sorted({x['stage'] for x in events})
        now = sorted(s for c, s in current if c == code)
        state = state_map.get(code, {})
        reasons = {s:state.get(s,'source_missing') for s in ('A','B')}
        unknown = any(v in UNKNOWN for v in reasons.values())
        label = '今日仍满足'+ '/'.join(now) if now else '当前待核验' if unknown else '仅历史命中；今日不满足'
        b_events = {}
        for e in events:
            if e['stage'] == 'B':
                key = (e['start_date'], e['end_date'], e['limit_date'])
                b_events[key] = dict(start_date=e['start_date'], limit_date=e['limit_date'],
                                     **b_structure(e, groups.get(code, []), opened, target))
        rows.append(dict(code=code, name=events[-1]['name'], industry=events[-1].get('industry', '未分类'),
                         stages=stages, occurrence_dates=dates, first_date=dates[0], last_date=dates[-1],
                         age_open_days=len(days)-1-days.index(dates[-1]), current_stages=now,
                         current_status=label, current_reasons=reasons,
                         events=events, B_structures=list(b_events.values())))
    return dict(version=VERSION, base_rule_version=BASE_VERSION, date=target, days=days,
                lookback=LOOKBACK, definition=DEFINITION, status='partial' if gaps or any(s['status']=='partial' for s in scans.values()) else 'complete',
                verified_dates=[d for d in days if d in scans], gaps=gaps,
                counts=dict(union_observed=len(rows), current_A=sum(s=='A' for c,s in current),
                            current_B=sum(s=='B' for c,s in current),
                            historical_only_observed=sum(not x['current_stages'] for x in rows),
                            current_unknown_observed=sum(x['current_status']=='当前待核验' for x in rows)),
                rows=rows, daily_coverage={d:dict(status=s['status'], coverage=s['base_coverage'], exclusions=s['base_exclusions'], uncomputed_A=s.get('uncomputed_A',[]), uncomputed_B=s.get('uncomputed_B',[])) for d,s in scans.items()})


def final_universe(result, daily, down_limits):
    """Apply the user's final filters to current AND historical observations."""
    target=result['date']
    today=daily[daily.trade_date==target]
    quotes={code:group.to_dict('records') for code,group in today.groupby('ts_code')}
    kept,beijing,down,pending=[],[],[],[]
    for row in result['rows']:
        code=row['code'];number=code.split('.')[0]
        if code.endswith('.BJ') or number.startswith(('4','8','920')):
            beijing.append(row)
            continue
        records=quotes.get(code,[])
        close=records[0].get('close') if len(records)==1 else None
        floor=down_limits.get((target,code))
        valid=lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>0
        evidence=dict(date=target,close=close if valid(close) else None,
                      down_limit=floor if valid(floor) else None)
        if not valid(close) or not valid(floor) or close<floor-1e-8:
            row['limit_down_check']=dict(evidence,status='unverified')
            pending.append(row)
        elif abs(close-floor)<1e-8:
            row['limit_down_check']=dict(evidence,status='limit_down')
            down.append(row)
        else:
            row['limit_down_check']=dict(evidence,status='clear')
            kept.append(row)
    result.update(rows=kept,excluded_beijing=beijing,excluded_limit_down=down,pending_limit_down=pending)
    counts=result['counts'];counts['before_final_filter_observed']=counts['union_observed']
    counts.update(union_observed=len(kept),current_A=sum('A' in x['current_stages'] for x in kept),
                  current_B=sum('B' in x['current_stages'] for x in kept),
                  historical_only_observed=sum(not x['current_stages'] for x in kept),
                  current_unknown_observed=sum(x['current_status']=='当前待核验' for x in kept),
                  excluded_beijing=len(beijing),excluded_limit_down=len(down),pending_limit_down=len(pending))
    if pending:result['status']='partial'
    return result


def mobile_markdown(result, persistence=None):
    """One stock per line; all eligible observations, no duplicate old list."""
    c=result['counts'];persist={x['code']:x for x in (persistence or {}).get('candidates',[])}
    lines=[f"## 量能观察｜{result['date'][5:]}",'',
           f"今日 A {c['current_A']}／B {c['current_B']}；近5日共 {c['union_observed']} 只。",'']
    for current,title in ((True,'今天仍满足'),(False,'仅历史出现')):
        rows=[x for x in result['rows'] if bool(x['current_stages'])==current]
        if not rows:continue
        lines += [f"**{title}（{len(rows)}）**",'']
        for x in rows:
            event=x['events'][-1]
            stage='/'.join(x['current_stages'] if current else x['stages'])
            text=f"- {x['name']} {x['code'].split('.')[0]}｜{x['industry']}｜{stage} {x['last_date'][5:]}｜{event['volume_ratio']:.1f}倍"
            p=persist.get(x['code'])
            if current and 'A' in x['current_stages'] and p and p.get('status')=='complete':
                text+=f"·5日量{p['ratio5']:.1f}倍"
            if x['current_status']=='当前待核验':text+='｜今日待核'
            bad=[b['status'] for b in x['B_structures'] if b['status']!='未跌破原结构低点']
            if bad:text+='｜旧B'+ '/'.join(dict.fromkeys(bad))
            lines.append(text)
        lines.append('')
    filters=[]
    for key,label in (('excluded_beijing','北交所'),('excluded_limit_down','收盘跌停')):
        if c.get(key):filters.append(f"{label}{c[key]}只")
    if filters:lines+=['已排除：'+'、'.join(filters)+'。']
    if c.get('pending_limit_down'):lines += [f"跌停核验缺口{c['pending_limit_down']}只，暂不列入名单。"]
    if result['status']=='partial':
        lines += [f"部分覆盖｜已核验{len(result['verified_dates'])}/5日，个股缺口详见完整报告。"]
    lines += ['完整证据及持续性对比见完整报告。']
    return '\n'.join(lines)+'\n'


def markdown(result, compact=False, persistence=None):
    if compact:return mobile_markdown(result,persistence)
    c=result['counts']
    lines=[f"## 近5日形态观察｜{result['date']}", '',
           f"**已核验新口径：今日A {c['current_A']}只／B {c['current_B']}只；近5日曾命中{c['union_observed']}只，历史记录补充{c['historical_only_observed']}只（其中当日待核验{c['current_unknown_observed']}只）。**", '',
           '窗口：'+'、'.join(result['days'])+f"；归档核验{len(result['verified_dates'])}/5日。状态{result['status']}。", '',
           '第三根阴线允许高低点严格上移；其余量能和涨停条件保留。以下日期为按新规则回算的形态日，行业沿用最近形态日归档。', '']
    for x in result['rows']:
        last=x['events'][-1]
        lines += [f"**{x['name']} {x['code'].split('.')[0]}｜{x['industry']}**", '',
                  f"{'/'.join(x['stages'])}出现："+'、'.join(d[5:] for d in x['occurrence_dates'])+
                  f"；最近距今{x['age_open_days']}个交易日；当时堆量{last['volume_ratio']:.2f}倍。", '',
                  x['current_status']+('。' if x['current_stages'] else '：'+'；'.join(s+' '+REASONS.get(v, '待核验：'+v) for s,v in x['current_reasons'].items())+'。'), '']
        for b in x['B_structures']:
            lines += [f"旧B事件（涨停{b['limit_date']}）：{b['status']}"+(f"，首次跌破{b['first_broken_date']}" if b.get('first_broken_date') else '')+'。', '']
        if not compact:
            lines += ['|形态日|阶段|堆量区间|均量倍数|第三根|', '|---|---|---|---:|---|']
            for e in x['events']:
                kind='高低点上移阴线' if e.get('third_bar')=='raised_yin' else '阳线' if e['stage']=='A' else 'B原事件'
                lines += [f"|{e['occurrence_date']}|{e['stage']}|{e['start_date']}～{e['end_date']}|{e['volume_ratio']:.4f}|{kind}|"]
            lines += ['']
    if result['gaps']:
        lines += ['缺口：'+'；'.join(g['date']+' '+g['reason'] for g in result['gaps'])+'；名单仅代表已核验日期，不表示缺失日期无形态。', '']
    for key,label in (('excluded_beijing','北交所排除'),('excluded_limit_down','事实日收盘跌停排除'),('pending_limit_down','收盘/跌停价待核验，暂不列入名单')):
        if result.get(key):
            lines += [label+'：'+'、'.join(x['name']+' '+x['code'] for x in result[key])+'。','']
    coverage=[]
    for day,block in result['daily_coverage'].items():
        for stage in ('A','B'):
            reasons=collections.Counter(x['reason'] for x in block.get('uncomputed_'+stage,[]))
            if reasons:
                coverage.append(day[5:]+' '+stage+' '+ '、'.join(f'{REASONS.get(k,k)} {n}只' for k,n in sorted(reasons.items())))
    if coverage:
        lines += ['阶段计算缺口：'+'；'.join(coverage)+'；逐票原因保存在证据附件。', '']
    lines += ['各日个股覆盖缺口仍适用，历史无命中不代表历史全市场完整阴性。A不新增结构失效阈值；B按原事件低点检查，缺日待核验。未回测。', '']
    return '\n'.join(lines)


def prepare(run, r, input_by, base_report=None, db_path=None):
    assert input_by.strip() and run.name == r['date']
    # The archive may be in a separate checkout from the installed code.
    database=Path(db_path) if db_path else run.parents[4]/'data/trade.db'
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        opened, days = market_days(db, run.name)
    scans, gaps, sources, latest = {}, [], {}, None
    for day in days:
        try:
            archive = load_archive(run.parent/day)
            local_scan = replay(archive)
            local_sources = {}
            # A changed shared source is rejected by load_archive rather than silently rebound.
            for path, digest in archive['sources'].items():
                actual = Path(path)
                if actual.name == 'recurring-contract.md' and sha(actual) != digest:
                    actual=run.parent.parent/'contract-history'/(digest+'.md')
                local_sources[str(actual)] = digest
            # Publish the date only after every dependency has been collected.
            # A failed date must contribute neither hits nor verified coverage.
            scans[day] = local_scan
            sources.update(local_sources)
            if day == run.name:
                latest=archive
        except (AssertionError, OSError, ValueError, KeyError, TypeError) as exc:
            if day == run.name:
                raise
            gaps.append(dict(date=day, reason='source_failed', detail=str(exc)))
    assert latest is not None and latest['report'] == r, 'current_report_binding_mismatch'
    result = combine(days, scans, gaps, latest['daily'], opened)
    result.update(input_by=input_by, sources=sources,
                  program_sha256=sha(__file__), calendar_open_days=opened,
                  current_rule_exclusions=scans[run.name]['states'])
    # Verify set arithmetic independently from aggregation and retain every hit date.
    expected={(d,x['code'],x['stage'],x['start_date'],x['end_date']) for d,s in scans.items() for x in s['candidates']}
    actual={(e['occurrence_date'],x['code'],e['stage'],e['start_date'],e['end_date']) for x in result['rows'] for e in x['events']}
    assert expected == actual and len(result['rows']) == len({k[1] for k in expected})
    result=final_universe(result,latest['daily'],latest.get('down_limits',{}))
    parent=Path(base_report) if base_report else run/'enhanced-v2/report.md'
    assert parent.resolve().is_relative_to(run.resolve()), 'base_report_outside_run'
    result['sources'][str(parent)] = sha(parent)
    digest=hashlib.sha256(encode(result).encode()).hexdigest()
    out=run/'enhanced-v2/recent-shapes';out.mkdir(exist_ok=True)
    def preserve(path, text):
        if path.exists():
            assert path.read_text() == text
        else:
            atomic(path, text)
    md=markdown(result)
    preserve(out/(digest+'.json'), encode(result))
    preserve(out/(digest+'.md'), md)
    preserve(out/(digest+'.py'), Path(__file__).read_text())
    previous=relocate_links(parent.read_text(),parent.parent,out)
    full=md+'\n\n---\n\n以下保留原严格v2口径当日对照；其名单不代表上方新口径。\n\n'+previous
    preserve(out/(digest+'.full.md'),full)
    for path,h in result['sources'].items():
        assert sha(path)==h, 'source_changed_during_build:'+path
    assert sha(__file__)==result['program_sha256'], 'program_changed_during_build'
    validation=dict(status='passed',version=VERSION,date=run.name,input_by=input_by,
                    verified_dates=result['verified_dates'],counts=result['counts'],
                    evidence_sha256=sha(out/(digest+'.json')),markdown_sha256=sha(out/(digest+'.md')),
                    full_report_sha256=sha(out/(digest+'.full.md')),
                    checks=['逐日来源/规则/日期/SHA门','原A集合包含核验','B沿用已核验原事件','逐日命中与并集合完整性','旧B低点固定及失效不复活'])
    preserve(out/(digest+'.validation.json'), encode(validation))
    return result, str(out/(digest+'.full.md'))



def main():
    parser=argparse.ArgumentParser(description='只读已核验逐日底稿，重算最近5个开放日堆量形态；仅写研究附件。')
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--input-by',required=True)
    parser.add_argument('--db-path',type=Path,help='只读SSE日历库；默认归档所属仓库的data/trade.db')
    args=parser.parse_args()
    run=args.run_dir.resolve()
    dt.date.fromisoformat(run.name)
    if not args.input_by.strip() or run.parent.name!='runs' or run.parent.parent.name!='volume-buildup':
        parser.error('须提供请求者及volume-buildup/runs/YYYY-MM-DD目录')
    report=load_archive(run)['report']
    result,path=prepare(run,report,args.input_by,db_path=args.db_path)
    print(encode(dict(status=result['status'],counts=result['counts'],report=path,gaps=result['gaps'])))


if __name__=='__main__':
    main()
