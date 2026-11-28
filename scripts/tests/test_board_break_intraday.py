"""严格时点、分钟源、批次交接、模型与发送边界；全部隔离外网。"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from services.board_break_intraday import data, llm, service, store

DAY='2026-11-12'
NEXT='2026-11-13'
CODE='600715.SH'


def minutes(day=DAY,cutoff='09:40',price=10.5):
    return [dict(ts_code=CODE,trade_time=t,open=price,close=price,high=price,low=price,vol=100,amount=100*price)
            for t in data.expected_minutes(day,cutoff)]


def test_calendar_full_year_and_strict_monday():
    c=sqlite3.connect(':memory:')
    c.execute('create table trade_calendar(date text primary key,is_open int,exchange text)')
    first=date(2026,1,1)
    rows=[((first+timedelta(days=n)).isoformat(),int((first+timedelta(days=n)).weekday()<5),'SSE') for n in range(365)]
    c.executemany('insert into trade_calendar values(?,?,?)',rows)
    assert data.calendar(c,'2026-11-16')['previous']=='2026-11-13'
    c.execute("delete from trade_calendar where date='2026-04-01'")
    with pytest.raises(data.EvidenceError,match='完整SSE'):
        data.calendar(c,'2026-11-16')


def test_complete_minutes_cut_future_and_auction():
    rows=minutes()+[dict(minutes()[0],trade_time=DAY+' 09:30:00'),dict(minutes()[0],trade_time=DAY+' 09:41:00')]
    x=data.normalize_minutes(rows,CODE,DAY,'09:40')
    assert len(x['rows'])==10 and x['excluded_rows']==2
    assert len(data.expected_minutes(DAY,'14:50'))==230


@pytest.mark.parametrize('kind',['missing','duplicate','wrong_day','wrong_code','nan','invalid_ohlc','empty'])
def test_minutes_reject_invalid(kind):
    rows=minutes()
    if kind=='missing':rows.pop()
    if kind=='duplicate':rows.append(rows[0])
    if kind=='wrong_day':rows[0]['trade_time']='2026-11-11 09:31:00'
    if kind=='wrong_code':rows[0]['ts_code']='600000.SH'
    if kind=='nan':rows[0]['close']=float('nan')
    if kind=='invalid_ohlc':rows[0]['high']=1
    if kind=='empty':rows=[]
    with pytest.raises(data.EvidenceError):data.normalize_minutes(rows,CODE,DAY,'09:40')


@pytest.fixture
def wired(monkeypatch):
    context=dict(as_of='2026-11-11',feedback=[],handwritten=dict(raw='文投控股是短剧前排'),gaps=[],nodes=[])
    monkeypatch.setattr(data,'calendar',lambda conn,d:dict(is_open=True,previous='2026-11-11' if d==DAY else DAY,history=[],dates={}))
    monkeypatch.setattr(data,'context',lambda *a:deepcopy(context))
    monkeypatch.setattr(data,'archive',lambda *a:({},dict(source='test',date=a[-1],sha256=data.digest({}))))
    monkeypatch.setattr(data,'baseline',lambda *a:[dict(code=CODE,name='文投控股',height=3,industry='传媒',previous_close=10)])
    monkeypatch.setattr(data,'limit_rows',lambda raw,*a:[])
    class Source:
        def __init__(self):
            self.provider=SimpleNamespace(
                get_market_daily_quotes=lambda d:SimpleNamespace(success=True,source='test',data=[dict(ts_code=CODE,trade_date=d,close=10.5)]),
                get_stock_limit_prices=lambda d:SimpleNamespace(success=True,data=[
                    dict(ts_code='920309.BJ',trade_date=d,up_limit=99999.99,down_limit=0),
                    dict(ts_code=CODE,trade_date=d,up_limit=11,down_limit=9),
                ]),
            )
        limits=data.MarketSource.limits
        def suspensions(self,d):return data.normalize_suspensions(SimpleNamespace(success=True,source='tushare:suspend_d',data=[]),d)
        def reference_prices(self,codes,d,mode,**options):return {c:dict(pre_close=10,source='test',date=d) for c in codes}
        def minutes(self,code,d,cutoff,mode,**options):
            raw = minutes(d,cutoff)
            return dict(data.normalize_minutes(raw,code,d,cutoff,**options),
                        source='test', raw_sha256=data.digest(raw))
    def evaluator(payload):
        obj=dict(environment='谨慎',summary='证据研究观察',candidates=[dict(code=r['code'],assessment='观察',reason='前排观察',front_quote='文投控股是短剧前排') for r in payload['candidates']],evidence_sha256=data.digest(payload))
        return dict(status='complete',response=llm.validate(obj,payload))
    return Source,evaluator,context


def run(wired,tmp_path,stage='tail',day=DAY,mode='rehearsal',**kwargs):
    Source,evaluator,_=wired
    return service.run(None,stage=stage,day=day,mode=mode,input_by='USER',root=tmp_path,
                       handwritten_root=tmp_path,source_factory=Source,evaluator=evaluator,
                       now=datetime(2026,11,15,18,tzinfo=data.TZ),**kwargs)


def test_tail_to_open_immutable_batch(wired,tmp_path):
    tail=run(wired,tmp_path)
    assert tail['status']=='complete' and tail['selected_codes']==[CODE]
    manifest=store.save(tmp_path,tail,service.render(tail))
    morning=run(wired,tmp_path,'open-review',NEXT,batch=manifest)
    assert morning['status']=='complete' and morning['selected_codes']==[CODE]
    assert morning['parent']['candidate_codes']==[CODE]
    assert morning['parent_validation']['status'] == 'accepted'
    assert morning['parent_validation']['tail_rules_sha256'] == morning['rules_sha256']
    assert morning['parent']['sha256'] == store.load(manifest)[1]
    assert len(morning['candidates'][0]['minute']['rows'])==10
    assert store.load(manifest)[0]==tail
    for report in (tail, morning):
        assert report['pk']['status'] == 'complete'
        assert [(r['code'], r['rank'], r['wins']) for r in report['pk']['rows']] == [(CODE, 1, 0)]


def test_tail_exact_quote_can_exclude_before_minutes(wired, tmp_path):
    Source, evaluator, _ = wired

    class QuoteSource(Source):
        def reference_prices(self, codes, day, mode, *, cutoff):
            assert cutoff == '14:50'
            return {CODE: dict(pre_close=10, source='sina', date=day,
                               cutoff_quote=dict(close=11, kind='realtime_quote'))}

        def minutes(self, *args, **kwargs):
            pytest.fail('已被截止价排除，无需请求分钟路径')

    day = '2026-11-26'
    result = service.run(None, stage='tail', day=day, mode='live', input_by='USER',
                         root=tmp_path, handwritten_root=tmp_path, source_factory=QuoteSource,
                         evaluator=evaluator, now=datetime(2026, 11, 26, 14, 50, 10, tzinfo=data.TZ))
    assert result['status'] == 'complete' and result['candidate_count'] == 0
    assert result['evaluation']['status'] == 'not_needed'
    assert result['excluded'][0]['cutoff_evidence']['kind'] == 'realtime_quote'
    assert result['excluded'][0]['reason'] == '14:50仍涨停'


@pytest.mark.parametrize(('price', 'reason'), [
    (9, '截止时点跌停'), (10.6, None), (10.61, '14:50涨幅超过6%'),
    (11, '14:50仍涨停'), (11.1, None),
])
def test_tail_incomplete_minutes_only_resolve_proven_price_exclusions(wired, tmp_path, price, reason):
    Source, evaluator, ctx = wired

    class IncompleteSource(Source):
        def minutes(self, code, day, cutoff, mode, **options):
            return data.normalize_minutes(minutes(day, cutoff, price)[1:], code, day, cutoff, **options)

    result = run((IncompleteSource, evaluator, ctx), tmp_path)
    assert result['candidates'] == []
    if reason is None:
        assert result['status'] == 'partial' and result['candidate_count'] is None
        assert '229/230' in result['unverified'][0]['reason']
    else:
        assert result['status'] == 'complete' and result['candidate_count'] == 0
        assert result['excluded'][0]['reason'] == reason
        evidence = result['excluded'][0]['cutoff_evidence']
        assert evidence['kind'] == 'closed_minute_bar'
        assert evidence['cutoff_bar']['time'] == DAY + ' 14:50:00'
        assert evidence['verified_minutes'] == 229
        assert 'minute' not in result['excluded'][0]


@pytest.mark.parametrize('fault', ['missing_cutoff', 'all_zero', 'off_session'])
def test_tail_cutoff_exclusion_requires_a_valid_cutoff_bar_and_trading(fault):
    rows = minutes(DAY, '14:50', 11)[1:]
    if fault == 'missing_cutoff':
        rows[-1]['trade_time'] = DAY + ' 14:51:00'
    elif fault == 'all_zero':
        for row in rows:
            row.update(vol=0, amount=0)
    else:
        rows.append(dict(rows[-1], trade_time=DAY + ' 13:00:00'))
    with pytest.raises(data.EvidenceError):
        data.normalize_minutes(rows, CODE, DAY, '14:50', cutoff_exclusion=lambda _: '涨停')


def test_cutoff_exclusion_records_earlier_trade_when_cutoff_bar_has_no_trade():
    rows = minutes(DAY, '14:50', 11)[1:]
    rows[-1].update(vol=0, amount=0)
    result = data.normalize_minutes(rows, CODE, DAY, '14:50', cutoff_exclusion=lambda _: '涨停')
    assert result['cutoff_bar']['vol'] == 0
    assert result['trade_witness']['time'] == DAY + ' 14:49:00'
    assert result['trade_witness']['vol'] == 100


def test_cutoff_price_gate_uses_the_fact_date_of_live_parent(wired, tmp_path, monkeypatch):
    Source, evaluator, _ = wired
    day = '2026-11-26'
    old_day = '2026-11-25'
    new_day = '2026-11-27'
    monkeypatch.setattr(data, 'calendar', lambda conn, target: dict(
        is_open=True, previous={old_day: '2026-11-24', day: old_day, new_day: day}[target]))

    def live(stage, target, time):
        return service.run(None, stage=stage, day=target, mode='live', input_by='USER',
                           root=tmp_path, handwritten_root=tmp_path, source_factory=Source,
                           evaluator=evaluator, now=datetime.fromisoformat(target+'T'+time).replace(tzinfo=data.TZ))

    old = live('tail', old_day, '14:50:05')
    new = live('tail', day, '14:50:05')
    assert old['rules_sha256'] == service.RULES_V1_HASH
    assert new['rules_sha256'] == service.RULES_HASH
    for tail in (old, new):
        store.save(tmp_path, tail, service.render(tail))
    for target, digest in ((day, service.RULES_V1_HASH), (new_day, service.RULES_HASH)):
        review = live('open-review', target, '09:40:05')
        assert review['parent_validation']['status'] == 'accepted'
        assert review['rules_sha256'] == digest
        assert len(review['candidates'][0]['minute']['rows']) == 10


def test_open_reports_old_rule_and_incomplete_parent_separately(wired, tmp_path):
    tail = run(wired, tmp_path)
    tail['rules'] = dict(service.RULES_V1, version='unknown')
    tail.update(rules_sha256=data.digest(tail['rules']), candidate_coverage=False)
    manifest = store.save(tmp_path, tail, '旧尾盘批次')
    morning = run(wired, tmp_path, 'open-review', NEXT)
    assert morning['status'] == 'source_failed' and morning['candidate_count'] is None
    assert morning['parent_validation'] == dict(
        manifest=str(manifest), sha256=store.load(manifest)[1], status='rejected',
        tail_rules_sha256=tail['rules_sha256'], review_rules_sha256=service.RULES_HASH)
    assert '原尾盘批次与本次复核的规则版本不一致' in morning['gaps']
    assert '原尾盘批次失败或候选覆盖不完整' in morning['gaps']
    assert 'parent' not in morning and morning['evaluation']['status'] == 'not_run'


def test_open_rehearsal_can_verify_an_existing_v1_rehearsal_parent(wired, tmp_path):
    tail = run(wired, tmp_path)
    tail.update(rules=service.RULES_V1, rules_sha256=service.RULES_V1_HASH)
    manifest = store.save(tmp_path, tail, service.render(tail))
    morning = run(wired, tmp_path, 'open-review', NEXT, batch=manifest)
    assert morning['parent_validation']['status'] == 'accepted'
    assert morning['rules'] == service.RULES_V1
    assert morning['rules_sha256'] == service.RULES_V1_HASH
    assert len(morning['candidates'][0]['minute']['rows']) == 10


def test_open_ranks_legacy_tail_candidates_without_saved_standings(wired, tmp_path):
    tail = run(wired, tmp_path)
    del tail['pk']
    manifest = store.save(tmp_path, tail, service.render(tail))
    morning = run(wired, tmp_path, 'open-review', NEXT, batch=manifest)
    assert morning['pk']['rows'][0]['code'] == CODE
    assert morning['parent']['baseline_date'] == tail['previous_trade_day']
    assert morning['status'] == 'complete'


def test_incomplete_tail_pk_does_not_replace_or_block_next_window(wired, tmp_path):
    def interrupted(evidence):
        assert evidence['stage'] == 'tail'
        return dict(status='incomplete', reason='对局超时', rows=[], matches=[], total=0)

    tail = run(wired, tmp_path, ranker=interrupted)
    assert tail['status'] == 'partial' and tail['pk']['rows'] == []
    assert tail['evaluation']['status'] == 'complete' and tail['selected_codes'] == []
    manifest = store.save(tmp_path, tail, service.render(tail))
    morning = run(wired, tmp_path, 'open-review', NEXT, batch=manifest)
    assert morning['parent']['status'] == morning['status'] == 'partial'
    assert morning['selected_codes'] == [] and morning['pk']['status'] == 'complete'
    assert morning['pk']['rows'][0]['code'] == CODE


def test_ranking_independent_of_observation_and_no_llm(wired, tmp_path):
    source, _, context = wired
    def failed_evaluator(_):
        return dict(status='failed', error='timeout')
    report = run((source, failed_evaluator, context), tmp_path)
    assert report['status'] == 'partial' and not report['selected_codes']
    assert report['pk']['rows'][0]['code'] == CODE
    disabled = run(wired, tmp_path, no_llm=True, ranker=lambda _: pytest.fail('关闭模型时不运行PK'))
    assert disabled['pk']['status'] == 'skipped' and disabled['pk']['rows'] == []


def test_morning_reverses_tail_rank_from_its_own_ten_bars(wired, tmp_path, monkeypatch):
    source, _, ctx = wired
    other = '600000.SH'
    names = {CODE: '文投控股', other: '浦发银行'}
    monkeypatch.setattr(data, 'baseline', lambda *_: [
        dict(code=code, name=name, height=3, industry='测试') for code, name in names.items()])

    class TwoStocks(source):
        def __init__(self):
            super().__init__()
            self.provider.get_market_daily_quotes = lambda d: SimpleNamespace(success=True, source='test', data=[
                dict(ts_code=code, trade_date=d, close=10.1) for code in names])

        def limits(self, day, codes):
            return {code: dict(up_limit=11, down_limit=9) for code in codes}

        def minutes(self, code, day, cutoff, mode, **options):
            leader = CODE if cutoff == '14:50' else other
            rows = [dict(row, ts_code=code) for row in minutes(day, cutoff, 10.5 if code == leader else 10.1)]
            return dict(data.normalize_minutes(rows, code, day, cutoff), source='test', raw_sha256=data.digest(rows))

    def classify(payload):
        return dict(status='complete', response=dict(environment='谨慎', summary='测试', candidates=[
            dict(code=row['code'], assessment='不纳入', reason='没有手写前排依据', front_quote='')
            for row in payload['candidates']]))

    def compare(request, timeout):
        by_code = {c['code']: c for c in request['candidates']}
        assert {c['bar_count'] for c in by_code.values()} == {230 if request['stage'] == 'tail' else 10}
        pair, = request['pairs']
        winner = 'A' if by_code[pair['a']]['pct_chg'] > by_code[pair['b']]['pct_chg'] else 'B'
        return dict(evidence_sha256=request['evidence_sha256'],
                    matches=[dict(id=pair['id'], winner=winner, fields=['pct_chg'])])

    components = (TwoStocks, classify, ctx)
    def ranker(evidence):
        return service.pk.run(evidence, runner=compare)
    tail = run(components, tmp_path, ranker=ranker)
    parent = store.save(tmp_path, tail, service.render(tail))
    morning = run(components, tmp_path, 'open-review', NEXT, batch=parent, ranker=ranker)
    for result, top in ((tail, CODE), (morning, other)):
        assert result['status'] == 'complete' and result['selected_codes'] == []
        assert result['pk']['rows'][0]['code'] == top
        assert names[top] in result['pk']['rows'][1]['reason']
        assert result['pk']['total'] == len(result['pk']['matches']) == 1


def test_open_excludes_close_reseal(wired,tmp_path,monkeypatch):
    tail=run(wired,tmp_path);manifest=store.save(tmp_path,tail,'tail')
    Source,evaluator,ctx=wired
    class Resealed(Source):
        def __init__(self):
            super().__init__()
            self.provider.get_market_daily_quotes=lambda d:SimpleNamespace(success=True,source='test',data=[dict(ts_code=CODE,trade_date=d,close=11)])
        def minutes(self,*args):raise AssertionError('回封者不能进入分钟评估')
    monkeypatch.setattr(data,'limit_rows',lambda raw,*a:[dict(code=CODE)])
    r=run((Resealed,evaluator,ctx),tmp_path,'open-review',NEXT,batch=manifest)
    assert r['status']=='complete' and r['candidate_count']==0
    assert r['excluded'][0]['close_check']['resealed'] is True


def test_open_does_not_reapply_six_percent(wired,tmp_path):
    tail=run(wired,tmp_path);manifest=store.save(tmp_path,tail,'tail')
    Source,evaluator,ctx=wired
    class Strong(Source):
        def minutes(self,code,d,cutoff,mode):
            raw = minutes(d,cutoff,10.8)
            return dict(data.normalize_minutes(raw,code,d,cutoff), source='test', raw_sha256=data.digest(raw))
    r=run((Strong,evaluator,ctx),tmp_path,'open-review',NEXT,batch=manifest)
    assert r['candidate_count']==1 and r['candidates'][0]['pct_chg']==8


def test_source_failure_not_empty_and_no_llm(wired,tmp_path):
    Source,evaluator,ctx=wired
    class Missing(Source):
        def minutes(self,*args,**kwargs):raise data.EvidenceError('权限不足')
    r=run((Missing,lambda p:pytest.fail('不能调用LLM'),ctx),tmp_path)
    assert r['status']=='partial' and r['candidate_count'] is None
    assert len(r['unverified'])==1 and not r['selected_codes']
    assert r['pk']['status'] == 'not_run' and not r['pk']['rows']


def test_model_failure_no_template_or_selection(wired,tmp_path):
    Source,_,ctx=wired
    r=run((Source,lambda p:dict(status='failed',error='timeout'),ctx),tmp_path)
    assert r['status']=='partial' and r['candidate_count']==1 and r['selected_codes']==[]


def test_outside_time_no_source(wired,tmp_path):
    r=run((lambda:pytest.fail('时点外不能取行情'),wired[1],wired[2]),tmp_path,mode='live')
    assert r['status']=='skipped'


def test_stale_context_blocks_formal_selection(wired,tmp_path):
    wired[2]['gaps'].append('手写复盘过期')
    r=run(wired,tmp_path)
    assert r['status']=='partial' and r['selected_codes']==[]


def suspension(day=DAY, **changes):
    return dict(dict(ts_code=CODE,trade_date=day.replace('-',''),suspend_type='S',suspend_timing=None),**changes)


def suspension_response(rows):
    return SimpleNamespace(success=True,source='tushare:suspend_d',data=rows)


@pytest.mark.parametrize('kind',['date','identity','duplicate','type','missing_timing','bad_timing','source','failed','truncated'])
def test_suspension_requires_explicit_current_official_evidence(kind):
    rows=[suspension()];response=suspension_response(rows)
    if kind=='date':rows[0]['trade_date']='20261111'
    if kind=='identity':rows[0]['code']='600000.SH'
    if kind=='duplicate':rows.append(dict(rows[0]))
    if kind=='type':rows[0]['suspend_type']='R'
    if kind=='missing_timing':rows[0].pop('suspend_timing')
    if kind=='bad_timing':rows[0]['suspend_timing']=False
    if kind=='source':response.source='unknown'
    if kind=='failed':response.success=False
    if kind=='truncated':response.data=rows*5000
    with pytest.raises(data.EvidenceError):data.normalize_suspensions(response,DAY)


def test_suspension_empty_and_intraday_do_not_exempt_minutes():
    assert data.normalize_suspensions(suspension_response([]),DAY)['full_day']=={}
    r=data.normalize_suspensions(suspension_response([suspension(suspend_timing='09:30-10:30')]),DAY)
    assert r['full_day']=={} and r['raw_sha256']==data.digest(r['rows'])


@pytest.mark.parametrize('stage',['tail','open-review'])
def test_verified_full_day_suspension_excluded_without_fabricated_bars(wired,tmp_path,stage):
    Source,evaluator,ctx=wired
    parent=store.save(tmp_path,run(wired,tmp_path),'tail') if stage=='open-review' else None
    class Suspended(Source):
        def suspensions(self,d):return data.normalize_suspensions(suspension_response([suspension(d)]),d)
        def limits(self,*a):pytest.fail('全天停牌无需请求价格或分钟')
        def reference_prices(self,*a):pytest.fail('全天停牌无需请求价格或分钟')
        def minutes(self,*a):pytest.fail('全天停牌不能伪造分钟')
    r=run((Suspended,lambda p:pytest.fail('无候选不请求模型'),ctx),tmp_path,stage,
          NEXT if stage=='open-review' else DAY,batch=parent)
    assert r['status']=='complete' and r['candidate_coverage'] and r['candidate_count']==0
    assert r['excluded'][0]['suspension_evidence']['date']==r['date']
    assert 'minute' not in r['excluded'][0]


def test_intraday_suspension_missing_minutes_still_blocks(wired,tmp_path):
    Source,evaluator,ctx=wired
    class Intraday(Source):
        def suspensions(self,d):return data.normalize_suspensions(suspension_response([suspension(d,suspend_timing='09:30-10:30')]),d)
        def minutes(self,*a,**kwargs):raise data.EvidenceError('分钟覆盖不完整')
    r=run((Intraday,evaluator,ctx),tmp_path)
    assert r['status']=='partial' and r['candidate_count'] is None and len(r['unverified'])==1


def test_suspension_failure_is_not_no_suspensions(wired,tmp_path):
    Source,evaluator,ctx=wired
    class Missing(Source):
        def suspensions(self,d):raise data.EvidenceError('停牌源失败')
    r=run((Missing,evaluator,ctx),tmp_path)
    assert r['status']=='source_failed' and r['candidate_count'] is None


def test_tamper_detected_no_fallback(wired,tmp_path):
    tail=run(wired,tmp_path);first=store.save(tmp_path,tail,'one');second=store.save(tmp_path,tail,'two')
    (second.parent/'report.md').write_text('tampered')
    r=run(wired,tmp_path,'open-review',NEXT)
    assert r['status']=='source_failed' and r['candidate_count'] is None
    assert store.load(first)[0]==tail


def test_cannot_use_rehearsal_as_live(wired,tmp_path):
    tail=run(wired,tmp_path);manifest=store.save(tmp_path,tail,'tail')
    Source,evaluator,_=wired
    r=service.run(None,stage='open-review',day=NEXT,mode='live',input_by='USER',root=tmp_path,
                  handwritten_root=tmp_path,batch=manifest,source_factory=Source,evaluator=evaluator,
                  now=datetime(2026,11,13,9,40,30,tzinfo=data.TZ))
    assert r['status']=='source_failed'
    with pytest.raises(data.EvidenceError):store.deliver(tmp_path,manifest,'USER')


def test_llm_rejects_invented_quote_and_extra_code(wired,tmp_path):
    r=run(wired,tmp_path);payload=r['model_input'];obj=deepcopy(r['evaluation']['response'])
    obj['candidates'][0]['front_quote']='编造的文投控股前排'
    with pytest.raises(ValueError):llm.validate(obj,payload)
    obj=deepcopy(r['evaluation']['response']);obj['candidates'][0]['code']='600000.SH'
    with pytest.raises(ValueError):llm.validate(obj,payload)


def test_delivery_once_and_failures_observable(wired,tmp_path):
    r=run(wired,tmp_path);r['mode']='live'
    manifest=store.save(tmp_path,r,service.render(r));sent=[]
    class Pusher:
        def initialize(self):return True
        def send_markdown(self,*a):sent.append(a);return False
    clock=datetime(2026,11,12,14,51,tzinfo=data.TZ)
    first=store.deliver(tmp_path,manifest,'USER',pusher_factory=Pusher,now=clock)
    second=store.deliver(tmp_path,manifest,'USER',pusher_factory=Pusher,now=clock)
    assert first['status']=='failed' and second['status']=='already_attempted' and len(sent)==1


def test_new_cli_requires_audit_and_historical_never_pushes():
    from main import build_parser
    from cli.board_break_intraday import handle
    p=build_parser()
    with pytest.raises(SystemExit):p.parse_args(['board-break','tail'])
    a=p.parse_args(['board-break','tail','--input-by','USER','--rehearsal','--push'])
    with pytest.raises(SystemExit,match='历史演练'):handle(a)


def test_source_uses_distinct_live_and_history_api():
    calls=[]
    class Pro:
        def query(self,api,**kw):
            calls.append((api,kw))
            return SimpleNamespace(to_dict=lambda orient:minutes())
    source=object.__new__(data.MarketSource);source.provider=SimpleNamespace(pro=Pro())
    source._minute_now=lambda:datetime.fromisoformat(DAY+'T09:40:01').replace(tzinfo=data.TZ)
    source.minutes(CODE,DAY,'09:40','rehearsal')
    source.minutes(CODE,DAY,'09:40','live')
    assert calls[0][0]=='stk_mins' and calls[0][1]['end_date']==DAY+' 09:40:00'
    assert calls[1]==('rt_min_daily',dict(ts_code=CODE,freq='1MIN'))


def test_permission_failure_stops_repeated_requests():
    calls=[]
    class Pro:
        def query(self,*a,**kw):
            calls.append(1)
            raise RuntimeError('没有访问接口的权限')
    source=object.__new__(data.MarketSource);source.provider=SimpleNamespace(pro=Pro())
    for _ in range(3):
        with pytest.raises(data.EvidenceError,match='权限不足'):
            source.minutes(CODE,DAY,'09:40','rehearsal')
    assert len(calls)==1


def test_limits_without_pre_close_and_reference_date_guard():
    source=object.__new__(data.MarketSource)
    source.provider=SimpleNamespace(
        get_stock_limit_prices=lambda day:SimpleNamespace(success=True,data=[dict(ts_code=CODE,trade_date='20261112',up_limit=11,down_limit=9)]),
        get_market_daily_quotes=lambda day:SimpleNamespace(success=True,source='tushare:daily',data=[dict(ts_code=CODE,trade_date='20261111',pre_close=10)]))
    assert source.limits(DAY,[CODE])[CODE]==dict(up_limit=11,down_limit=9)
    with pytest.raises(data.EvidenceError,match='日期'):
        source.reference_prices([CODE],DAY,'rehearsal')


@pytest.mark.parametrize(('quote_time', 'start', 'end', 'volume', 'accepted'), [
    ('14:50:00', '14:50:03', '14:50:04', 500, True),
    ('14:49:59', '14:50:03', '14:50:04', 500, False),
    ('14:50:01', '14:50:03', '14:50:04', 500, False),
    ('14:50:00', '14:53:01', '14:53:02', 500, False),
    ('14:50:00', '14:52:59', '14:53:01', 500, False),
    ('14:50:00', '14:50:03', '14:50:04', 0, False),
])
def test_realtime_reference_only_reuses_exact_cutoff_quote(monkeypatch, quote_time, start, end, volume, accepted):
    from providers.sina_provider import SinaProvider

    source = object.__new__(data.MarketSource)
    times = (datetime.fromisoformat(f'{DAY}T{time}').replace(tzinfo=data.TZ) for time in (start, end))
    source._minute_now = lambda: next(times)
    quote = dict(code=CODE, quote_date=DAY, quote_time=quote_time, price=11,
                 pre_close=10, volume=volume, amount=5500)
    monkeypatch.setattr(SinaProvider, 'get_realtime_quotes',
                        lambda self, codes: SimpleNamespace(success=True, source='sina', data=[quote]))
    reference = source.reference_prices([CODE], DAY, 'live', cutoff='14:50')[CODE]
    assert reference['pre_close'] == 10
    assert ('cutoff_quote' in reference) is accepted
    if accepted:
        assert reference['cutoff_quote']['time'] == DAY + 'T14:50:00+08:00'
        assert reference['cutoff_quote']['observed_at'] == DAY + 'T14:50:04+08:00'
        assert reference['cutoff_quote']['quote_sha256']


@pytest.mark.parametrize(('changes','message'), [
    (dict(down_limit=0), 'down_limit.*数值非法'),
    (dict(up_limit=None), 'up_limit.*数值缺失'),
    (dict(up_limit=8), '区间非法'),
    (dict(trade_date='2026-11-11'), '日期不符'),
])
def test_limits_reject_invalid_requested_rows(changes,message):
    row=dict(ts_code=CODE,trade_date=DAY,up_limit=11,down_limit=9)
    row.update(changes)
    source=object.__new__(data.MarketSource)
    source.provider=SimpleNamespace(
        get_stock_limit_prices=lambda day:SimpleNamespace(success=True,data=[row]))
    with pytest.raises(data.EvidenceError,match=rf'{CODE}.*{message}'):
        source.limits(DAY,[CODE])


def test_limits_reject_duplicate_requested_rows():
    row=dict(ts_code=CODE,trade_date=DAY,up_limit=11,down_limit=9)
    source=object.__new__(data.MarketSource)
    source.provider=SimpleNamespace(
        get_stock_limit_prices=lambda day:SimpleNamespace(success=True,data=[row,row]))
    with pytest.raises(data.EvidenceError,match=rf'{CODE}.*重复'):
        source.limits(DAY,[CODE])


def test_legacy_daily_supports_audit():
    from main import build_parser
    a=build_parser().parse_args(['board-break','daily','--input-by','USER','--no-push'])
    assert a.input_by=='USER'


def test_minute_check_no_push_or_model_arguments():
    from main import build_parser
    p=build_parser()
    a=p.parse_args(['board-break','minute-check','--date',DAY,'--code',CODE,'--source','historical','--input-by','USER'])
    assert a.board_break_command=='minute-check'
    with pytest.raises(SystemExit):p.parse_args(['board-break','minute-check','--date',DAY,'--code',CODE,'--source','historical','--input-by','USER','--push'])


def environment(day):
    return dict(date=day,node_signals=[],
                indices={k:dict(_source='tushare:index_daily',trade_date=day,close=100,change_pct=1)
                         for k in ('shanghai','shenzhen','chinext','star50')},
                breadth=dict(_source='tushare:daily',advance=2000,decline=2000,flat=1,total=4001),
                sector_industry=dict(_source='tushare:sw_daily',data=[dict(name='传媒',change_pct=1)]),
                limit_up=dict(_source='tushare:limit_list_d',stocks=[],count=0),
                limit_down=dict(_source='tushare:limit_list_d',stocks=[],count=0))


@pytest.fixture
def context_source(monkeypatch,tmp_path):
    days=[(date(2026,10,1)+timedelta(days=n)).isoformat() for n in range(25)]
    archives={d:environment(d) for d in days}
    for i,d in enumerate(days[2:],2):
        archives[d]['board_break_feedback']=dict(status='ok',outcome_date=d,break_date=days[i-1],source_connected_date=days[i-2],
            details=[dict(code=CODE,feedback_close_pct=1)],sample_count=1,break_count=1,break_candidate_count=1,
            break_coverage_pct=100,feedback_coverage_pct=100,close_mean_pct=1,close_median_pct=1,errors=[])
    monkeypatch.setattr(data,'archive',lambda conn,d:(deepcopy(archives[d]),dict(date=d,sha256='test')))
    (tmp_path/days[-1]).mkdir()
    monkeypatch.setattr(data,'load_entry',lambda p:dict(raw='手写原文',meta={}))
    return dict(previous=days[-1],dates={d:1 for d in days}),archives


@pytest.mark.parametrize('field',['node_signals','indices','breadth','sector_industry','limit_down'])
def test_missing_environment_is_gap(context_source,monkeypatch,tmp_path,field):
    cal,archives=context_source
    assert data.context(None,cal,tmp_path)['gaps']==[]
    archives[cal['previous']].pop(field)
    assert data.context(None,cal,tmp_path)['gaps']


@pytest.mark.parametrize('field',['indices','breadth','sector_industry','limit_down'])
def test_failed_environment_is_gap(context_source,tmp_path,field):
    cal,archives=context_source
    archives[cal['previous']][field]['status']='source_failed'
    assert data.context(None,cal,tmp_path)['gaps']


@pytest.mark.parametrize('status',['source_failed','partial'])
def test_failed_empty_limit_list_never_means_zero(status):
    raw=environment(DAY);raw['limit_up']['status']=status
    with pytest.raises(data.EvidenceError):data.limit_rows(raw,DAY)


def test_wrong_day_limit_list_rejected():
    raw=environment(DAY);raw['limit_up'].update(count=1,stocks=[dict(code=CODE,trade_date='19990101')])
    with pytest.raises(data.EvidenceError):data.limit_rows(raw,DAY)


@pytest.mark.parametrize('reason,count',[('no_connected_candidates',0),('no_board_breaks',1)])
def test_verified_zero_feedback_not_failed(context_source,tmp_path,reason,count):
    cal,archives=context_source
    fb=archives[cal['previous']]['board_break_feedback']
    fb.update(empty_reason=reason,connected_count=count,details=[],sample_count=0,break_count=0,
              break_candidate_count=0,dirty_source_count=0,break_coverage_pct=None,feedback_coverage_pct=None,
              close_mean_pct=None,close_median_pct=None)
    if count:
        for d in (fb['source_connected_date'],fb['break_date']):
            archives[d]['limit_up'].update(count=1,stocks=[dict(code=CODE,name='文投控股',limit_times=2)])
    assert data.context(None,cal,tmp_path)['gaps']==[]
    fb['status']='source_failed'
    assert data.context(None,cal,tmp_path)['gaps']
    fb['status']='ok'
    archives[fb['break_date']]['limit_up']['status']='source_failed'
    assert data.context(None,cal,tmp_path)['gaps']


def test_recovered_feedback_requires_matching_archive_and_hash(context_source, tmp_path):
    from services.board_break_intraday import feedback_recovery

    cal, archives = context_source
    days = sorted(cal['dates'])[-3:]
    day = days[-1]
    raw = archives[day]
    block = deepcopy(raw['board_break_feedback'])
    raw['board_break_feedback'] = dict(status='source_failed', details=[])
    raw['limit_up'].update(count=1, stocks=[dict(code=CODE, name='文投控股')])
    directory = tmp_path / 'recovery'
    directory.mkdir()
    meta = dict(status='complete', original_status='source_failed', input_by='USER',
                recovered_at='2026-11-05T00:00:00+08:00', feedback_sha256=data.digest(block),
                original_feedback_sha256=data.digest(raw['board_break_feedback']),
                archive_limits=feedback_recovery._archive_sources(None, days, raw),
                quote_sources=[dict(date=d, source='tushare:daily', rows=4000, sha256='a'*64) for d in days[1:]])
    payload = dict(schema=feedback_recovery.SCHEMA, date=day, recovery=meta,
                   raw_data=dict(board_break_feedback=block))
    path = directory / f'{day}.json'
    path.write_bytes(store.encode(payload))
    result = data.context(None, cal, tmp_path, directory)
    assert result['gaps'] == []
    assert len(result['stock_history_feedback']) == 20
    assert result['sources'][-2]['sha256'] == store.sha(path.read_bytes())
    assert result['sources'][-2]['original_status'] == 'source_failed'

    block['close_mean_pct'] = 2
    path.write_bytes(store.encode(payload))
    assert data.context(None, cal, tmp_path, directory)['gaps'] == [f'{day}断板反馈未通过核验']
    block['close_mean_pct'] = 1
    path.write_bytes(store.encode(payload))
    raw['limit_up']['stocks'][0]['name'] = '变动后的归档'
    assert data.context(None, cal, tmp_path, directory)['gaps'] == [f'{day}断板反馈未通过核验']
    raw['board_break_feedback'] = block
    assert data.context(None, cal, tmp_path, directory)['gaps'] == []


def test_skipped_does_not_shadow_tail_but_failed_does(wired,tmp_path):
    tail=run(wired,tmp_path);first=store.save(tmp_path,tail,'valid')
    skip=deepcopy(tail);skip.update(status='skipped',candidate_coverage=False,candidates=[],selected_codes=[])
    store.save(tmp_path,skip,'outside window')
    assert store.latest(tmp_path,DAY,'rehearsal')==first
    morning=run(wired,tmp_path,'open-review',NEXT)
    assert morning['status']=='complete' and morning['parent']['manifest']==str(first)
    skip['status']='source_failed';latest=store.save(tmp_path,skip,'failed')
    assert store.latest(tmp_path,DAY,'rehearsal')==latest
    assert run(wired,tmp_path,'open-review',NEXT)['status']=='source_failed'


@pytest.mark.parametrize('status,code',[('skipped',0),('partial',1),('source_failed',1)])
def test_cli_push_noncomplete_preserves_receipt(wired,tmp_path,monkeypatch,capsys,status,code):
    from cli import board_break_intraday as cli
    from main import build_parser
    result=run(wired,tmp_path);result.update(status=status,mode='live',selected_codes=[])
    monkeypatch.setattr(cli,'DEFAULT_OUTPUT',tmp_path)
    monkeypatch.setattr(cli,'get_readonly_connection',lambda:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(cli.service,'run',lambda *a,**k:result)
    monkeypatch.setattr(cli.store,'deliver',lambda *a:pytest.fail('不能发送不完整结果'))
    args=build_parser().parse_args(['board-break','tail','--date',DAY,'--push','--input-by','USER','--json'])
    if code:
        with pytest.raises(SystemExit) as ex:cli.handle(args)
        assert ex.value.code==code
    else:cli.handle(args)
    response=json.loads(capsys.readouterr().out)
    assert response['status']==status and Path(response['manifest']).exists()
    assert response['pk']['rows'] == result['pk']['rows']
    assert response['delivery']['status']=='not_sent'


def test_large_llm_evidence_transmitted_via_stdin(wired,tmp_path,monkeypatch):
    from utils.llm_cli import LlmCliConfig
    r=run(wired,tmp_path);payload=r['model_input'];payload['large_evidence']='完整证据'*300000
    obj=deepcopy(r['evaluation']['response']);obj['evidence_sha256']=data.digest(payload)
    monkeypatch.setattr(llm,'resolve_config',lambda **k:LlmCliConfig('', 'unchanged-model',180,'openai'))
    def launch(command,**kw):
        assert command[-1]=='--prompt-stdin' and sum(map(len,command))<1000
        assert len(kw['input'].encode())>1048576
        assert json.loads(kw['input'].split('输入证据：',1)[1])==payload
        return SimpleNamespace(returncode=0,stdout=json.dumps(obj))
    monkeypatch.setattr(llm.subprocess,'run',launch)
    assert llm.evaluate(payload)['status']=='complete'


@pytest.mark.parametrize('module_name',['openai_responses','lmstudio'])
def test_model_adapters_stdin_and_legacy_prompt(monkeypatch,capsys,module_name):
    import importlib,io
    adapter=importlib.import_module('utils.'+module_name)
    calls=[]
    monkeypatch.setattr(adapter,'generate',lambda prompt,**kw:calls.append((prompt,kw)) or 'ok')
    monkeypatch.setattr(adapter.sys,'stdin',io.StringIO('完整证据'*300000))
    provider='openai' if module_name=='openai_responses' else 'lmstudio'
    assert adapter.main(['--provider',provider,'--model','original','--prompt-stdin'])==0
    assert adapter.main(['--provider',provider,'--model','original','--prompt','legacy'])==0
    assert calls[0][0]=='完整证据'*300000 and calls[1][0]=='legacy'
    assert calls[0][1]['model']=='original'


def test_zero_trading_window_not_candidate_after_protocol_decode(wired,tmp_path):
    Source,evaluator,ctx=wired
    class NoTrades(Source):
        def minutes(self,code,d,cutoff,mode,**options):
            rows=minutes(d,cutoff)
            for row in rows:row.update(vol=0,amount=0)
            return data.normalize_minutes(rows,code,d,cutoff,**options)
    r=run((NoTrades,lambda p:pytest.fail('无成交窗口不能评估'),ctx),tmp_path)
    assert r['candidate_count'] is None and r['status']=='partial'
    assert not r['candidates'] and r['unverified'][0]['reason']=='窗口无成交，不能判断承接或反包'


@pytest.mark.parametrize('advance,decline,flat,total,legacy,share,multiple', [
    (586, 4926, 61, 5573, .12, 10.51, .119),
    (4658, 783, 129, 5570, 5.95, 83.63, 5.9489),
    (4658, 0, 100, 4758, float('inf'), 97.90, None),
    (0, 4150, 13, 4163, 0, 0.00, 0),
])
def test_environment_model_receives_two_distinct_breadth_denominators(
        context_source, tmp_path, monkeypatch, advance, decline, flat, total, legacy, share, multiple):
    cal, archives = context_source
    day = cal['previous']
    archives[day]['breadth'].update(advance=advance, decline=decline, flat=flat, total=total, advance_ratio=legacy)
    ctx = data.context(None, cal, tmp_path)
    facts = ctx['market']['breadth']
    assert facts['fact_date'] == day and facts['period'] == '收盘' and facts['source'] == 'tushare:daily'
    assert facts['advance_share_pct'] == share and facts['advance_decline_multiple'] == multiple
    assert facts['status'] == 'complete' and ctx['gaps'] == []
    assert f'{advance}/{total}' in facts['summary'] and f'{advance}/{decline}' in facts['summary']
    assert (facts['gap'] is None) == bool(decline)
    assert 'advance_ratio' not in facts and 'advance_ratio' in archives[day]['breadth']

    payload = {'context': ctx, 'candidates': []}
    response = dict(environment='谨慎', summary='已核对收盘广度。', candidates=[], evidence_sha256=data.digest(payload))
    monkeypatch.setattr(llm, 'resolve_config', lambda **kwargs: SimpleNamespace(provider='openai', model='fake', timeout_seconds=1))
    monkeypatch.setattr(llm, 'build_prompt_command', lambda config,prompt: ['never-run', '--prompt', prompt])
    def capture(command, **kwargs):
        prompt = kwargs['input']
        assert facts['summary'] in prompt and 'advance_ratio' not in prompt
        assert 'advance_decline_multiple' in prompt and 'advance_share_pct' in prompt
        return SimpleNamespace(returncode=0, stdout=json.dumps(response))
    monkeypatch.setattr(llm.subprocess, 'run', capture)
    audit = llm.evaluate(payload)
    assert audit['status'] == 'complete' and audit['prompt_sha256'] == data.digest(llm.PROMPT)


@pytest.mark.parametrize('update', [
    {'status':'partial'}, {'status':{}}, {'error':'timeout'}, {'_source':''}, {'_source':float('nan')}, {'advance':None}, {'flat':False},
    {'total':5572}, {'trade_date':'2020-01-01'}, {'advance':1, 'decline':2, 'flat':0, 'total':3},
])
def test_bad_market_breadth_preserves_gap_in_model_input(context_source, tmp_path, update):
    cal, archives = context_source
    day = cal['previous']
    archives[day]['breadth'].update(advance=586, decline=4926, flat=61, total=5573, advance_ratio=.12)
    archives[day]['breadth'].update(update)
    ctx = data.context(None, cal, tmp_path)
    facts = ctx['market']['breadth']
    assert facts['status'] == 'source_failed' and facts['gap']
    assert facts['advance_share_pct'] is None and facts['advance_decline_multiple'] is None
    assert 'advance_ratio' not in facts and ctx['gaps']
    data.digest(ctx)


def test_operator_and_model_get_same_breadth_evidence(wired, tmp_path):
    # Original incident receipt values. Only fixture data is used, with a fake evaluator.
    facts = data.breadth_evidence(dict(_source='tushare:daily', advance=586, decline=4926, flat=61,
                                       total=5573, advance_ratio=.12), '2026-11-25')
    wired[2].setdefault('market', {})['breadth'] = facts
    batch = run(wired,tmp_path)
    assert batch['model_input']['context']['market']['breadth'] == facts
    assert facts['summary'] in service.render(batch)
