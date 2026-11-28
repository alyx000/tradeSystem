"""影子采样：固定分母、时间顺序、缺源、无推送及不可变归档。"""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from services.intraday_events import analytics as a, sources as src, service as svc
from services.intraday_events.evidence import digest

NOW = datetime.fromisoformat('2026-11-16T10:00:00+08:00')
CODES = [f'60000{i}.SH' for i in range(10)]


def context():
    return dict(trade_date='2026-11-16', previous_date='2026-11-13', universe=CODES,
                industry_map={c:'行业甲' for c in CODES}, cohorts={'昨日涨停':dict(codes=CODES)},
                cores=[dict(code=CODES[0], name='核心甲')], gaps=[], sources={})


def snapshot(at=NOW, *, changes=None, ctx=None):
    ctx = ctx or context()
    changes = changes or {}
    return dict(trade_date=at.date().isoformat(), captured_at=at.isoformat(),
                context_sha256=digest(ctx), rejected=[], stocks={c:dict(name='样本', price=100+changes.get(c,0),
                    pct_chg=changes.get(c,0), quote_at=at.isoformat(),
                    amount=100000+max(0,(at-NOW).total_seconds())*100) for c in CODES})


def test_sector_cluster_and_dedupe():
    old = snapshot()
    new = snapshot(NOW+timedelta(minutes=5), changes={c:1 for c in CODES})
    result = a.analyze(old,new,context())
    events = [e for e in result['events'] if e['kind']=='sector_cluster']
    assert len(events)==1 and events[0]['metrics']['median_pct']==pytest.approx(1)
    assert result['sectors'][0]['amount_delta_yuan']==300000
    assert not [e for e in a.analyze(old,new,context(),active=result['active'])['events'] if e['kind']=='sector_cluster']


def test_sector_missing_member_blocks_event_even_with_high_coverage():
    old, new = snapshot(), snapshot(NOW+timedelta(minutes=5), changes={c:1 for c in CODES})
    del new['stocks'][CODES[-1]]
    result = a.analyze(old,new,context())
    assert result['sectors'][0]['status']=='partial'
    assert not [e for e in result['events'] if e['kind']=='sector_cluster']


def test_single_stock_jump_is_not_sector_cluster():
    result=a.analyze(snapshot(),snapshot(NOW+timedelta(minutes=5),changes={CODES[0]:8}),context())
    assert not [e for e in result['events'] if e['kind']=='sector_cluster']


@pytest.mark.parametrize('minutes', [0,3,8,60,1440])
def test_gap_or_wrong_day_resets_baseline(minutes):
    result=a.analyze(snapshot(),snapshot(NOW+timedelta(minutes=minutes)),context())
    assert result['baseline_only'] and result['events']==[]


def test_changed_context_and_lunch_never_connect():
    new=snapshot(NOW+timedelta(minutes=5)); new['context_sha256']='changed'
    assert not a.comparable(snapshot(),new)
    assert not a.comparable(snapshot(NOW.replace(hour=11,minute=30)),snapshot(NOW.replace(hour=13,minute=0)))


def test_feedback_fixed_cohort_and_missing_not_zero():
    old=snapshot(changes={c:2 for c in CODES})
    new=snapshot(NOW+timedelta(minutes=5),changes={c:-2 for c in CODES})
    result=a.analyze(old,new,context())
    event=next(e for e in result['events'] if e['kind']=='strong_feedback_change')
    assert event['median_change_pp']==-4 and event['up_ratio_change_pp']==-100
    del new['stocks'][CODES[-1]]
    result=a.analyze(old,new,context())
    assert result['feedback'][0]['status']=='partial'
    assert not [e for e in result['events'] if e['kind']=='strong_feedback_change']


def test_missing_and_empty_cohorts_are_distinct():
    ctx=context(); ctx['cohorts']={'missing':dict(codes=None,error='缺源'),'empty':dict(codes=[])}
    result=a.analyze(None,snapshot(ctx=ctx),ctx)
    assert [r['status'] for r in result['feedback']]==['source_failed','empty']
    assert all(r['metrics']['median_pct'] is None for r in result['feedback'])


def test_core_following_requires_next_window_and_excludes_already_moving():
    old=snapshot()
    first=snapshot(NOW+timedelta(minutes=5), changes={CODES[0]:2,CODES[1]:1})
    result=a.analyze(old,first,context())
    assert not [e for e in result['events'] if e['kind']=='core_following']
    pending=result['pending_following']
    assert CODES[1] not in pending[0]['eligible_followers']
    second=snapshot(NOW+timedelta(minutes=10), changes={CODES[0]:2, CODES[1]:2, **{c:1 for c in CODES[2:5]}})
    follow=a.analyze(first,second,context(),pending=pending,active=result['active'])
    event=next(e for e in follow['events'] if e['kind']=='core_following')
    assert event['followers']==CODES[2:5] and event['causality'].endswith('不证明核心带动板块')


def test_following_gap_expires_and_missing_blocks():
    first=snapshot(NOW+timedelta(minutes=5),changes={CODES[0]:2})
    pending=a.analyze(snapshot(),first,context())['pending_following']
    result=a.analyze(first,snapshot(NOW+timedelta(minutes=20)),context(),pending=pending)
    assert result['following'][0]['status']=='partial' and not result['pending_following']
    new=snapshot(NOW+timedelta(minutes=10),changes={c:2 for c in CODES})
    del new['stocks'][CODES[-1]]
    result=a.analyze(first,new,context(),pending=pending)
    assert not [e for e in result['events'] if e['kind']=='core_following']


@pytest.mark.parametrize('kind',['overlap','missing','no_progress'])
def test_following_requires_ordered_quote_times(kind):
    first=snapshot(NOW+timedelta(minutes=5),changes={CODES[0]:2})
    pending=a.analyze(snapshot(),first,context())['pending_following']
    second=snapshot(NOW+timedelta(minutes=10),changes={c:2 for c in CODES})
    if kind=='overlap':
        first['stocks'][CODES[1]]['quote_at']=(NOW+timedelta(minutes=4)).isoformat()
    elif kind=='missing':
        del first['stocks'][CODES[1]]['quote_at']
    else:
        second['stocks'][CODES[1]]['quote_at']=first['stocks'][CODES[1]]['quote_at']
    result=a.analyze(first,second,context(),pending=pending)
    assert result['following'][0]['status']=='partial'
    assert CODES[1] in result['following'][0]['unverified_followers']
    assert not [e for e in result['events'] if e['kind']=='core_following']


def realtime_rows(at=NOW):
    return [dict(code=c,name='普通股',price=10,pct_chg=1,amount=100,
                 quote_date=at.date().isoformat(),quote_time=at.strftime('%H:%M:%S')) for c in CODES]


class Registry:
    providers=[]
    def __init__(self, payloads): self.payloads=payloads; self.calls=[]
    def call(self, capability, *args):
        self.calls.append((capability,args))
        data=self.payloads[capability]
        if isinstance(data,Exception): raise data
        return SimpleNamespace(data=data,source='test:source',success=True)


@pytest.mark.parametrize('mutation', ['duplicate','stale','future','nan','bad_amount','missing_name','wrong_date','bad_row'])
def test_capture_invalid_data_fails_closed(mutation):
    rows=realtime_rows()
    if mutation=='duplicate': rows.append(dict(rows[0]))
    elif mutation=='stale': rows[0]['quote_time']='09:57:00'
    elif mutation=='future': rows[0]['quote_time']='10:00:01'
    elif mutation=='nan': rows[0]['price']=float('nan')
    elif mutation=='bad_amount': rows[0]['amount']=-1
    elif mutation=='missing_name': rows[0]['name']=''
    elif mutation=='wrong_date': rows[0]['quote_date']='2026-11-13'
    elif mutation=='bad_row': rows[0]=None
    with pytest.raises((ValueError, RuntimeError)):
        src.capture(Registry({'get_realtime_quotes':rows}), context(), NOW)


def test_capture_uses_completion_clock_and_evidence():
    rows=realtime_rows(NOW+timedelta(seconds=20))
    result=src.capture(Registry({'get_realtime_quotes':rows}),context(),NOW,clock=lambda:NOW+timedelta(seconds=30))
    assert result['valid']==10 and result['raw_sha256']==digest(rows)
    assert result['captured_at'].endswith('10:00:30+08:00')


def test_index_capture_checks_identity_freshness_and_isolates_missing_codes():
    targets = [dict(kind='index', code=c) for c in ('000001.SH', '000688.SH', '000852.SH', '399006.SZ', '880003.TDX')]
    def quote(code, name, time='10:00:00'):
        return dict(code=code, name=name, price=3550.0, quote_date='2026-11-16', quote_time=time)
    rows = [quote('000001.SH', '上证指数'), quote('000688.SH', '科创50', '09:57:00'),
            quote('000852.SH', '中证红利'), quote('399006.SZ', '创业板指'), quote('399006.SZ', '创业板指')]
    reg = Registry({'get_realtime_quotes': rows})
    result = src.capture_level_indices(reg, targets, NOW, lambda: NOW)
    assert set(result['indices']) == {'000001.SH'}
    assert set(result['index_source']['errors']) == {'000688.SH', '000852.SH', '399006.SZ', '880003.TDX'}
    assert result['index_source']['raw_sha256'] == digest(rows)
    assert '880003.TDX' not in reg.calls[0][1][0]
    reg.payloads['get_realtime_quotes'] = ValueError('源失败')
    failed = src.capture_level_indices(reg, targets, NOW, lambda: NOW)
    assert not failed['indices'] and len(failed['index_source']['errors']) == 5


def test_update_of_frozen_index_levels_binds_to_same_archive(monkeypatch):
    source = dict(date='2026-11-13', recorded_at='2026-11-13T19:00:00+08:00',
                  revision=0, source_sha256='raw', supplement_sha256='existing')
    stock = dict(kind='stock', code=CODES[0], level=10)
    index = dict(kind='index', code='000688.SH', level=960.25)
    ctx = dict(context(), sources=dict(universe=dict(rows=[{}])),
               anchors=dict(key_levels=[stock], sources=[source], stocks=[dict(code=CODES[0])]))
    parsed = dict(key_levels=[index], sources=[source])
    monkeypatch.setattr(src, 'load_anchors', lambda *args: parsed)
    before = deepcopy(ctx)
    result = src.anchors_for_level_watch(ctx)
    assert result['key_levels'] == [index, stock]
    assert result['stocks'] == ctx['anchors']['stocks'] and ctx == before
    parsed['sources'] = [dict(source, supplement_sha256='changed')]
    assert src.anchors_for_level_watch(ctx) is ctx['anchors']


def limit_block(day, rows):
    return dict(date=day,limit_up=dict(count=len(rows),stocks=rows,_source='tushare:limit_list_d'))


@pytest.fixture
def context_env(monkeypatch):
    monkeypatch.setattr(src,'MIN_UNIVERSE',8)
    monkeypatch.setattr(src,'load_anchors',lambda *args:dict(status='complete',stocks=[],unresolved=[],sources=[],errors=[]))
    prior=[dict(code=c,name='样本',limit_times=2 if i==0 else 1) for i,c in enumerate(CODES[:4])]
    older=[dict(code=CODES[4],name='断板候选',limit_times=2)]
    archives={'2026-11-13':limit_block('2026-11-13',prior),'2026-11-12':limit_block('2026-11-12',older)}
    monkeypatch.setattr(src,'archive',lambda conn,day:(archives[day],dict(date=day,source='db',sha256=digest(archives[day]))))
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE daily_reviews(date TEXT,step5_leaders TEXT,updated_at TEXT)')
    block=dict(top_leaders=[dict(stock_code='600000',stock='核心甲',attribute_type='前排活跃')])
    conn.execute('INSERT INTO daily_reviews VALUES(?,?,?)',('2026-11-13',json.dumps(block),'2026-11-13 14:00:00'))
    reg=Registry({'get_stock_basic_list':[dict(ts_code=c,name='普通股') for c in CODES],
                  'get_stock_sw_industry_map':{c:{'sw_l2':'行业甲'} for c in CODES},
                  'get_market_daily_quotes':[dict(ts_code=CODES[4],trade_date='20261113',close=10,vol=200)]})
    cal=dict(previous='2026-11-13',history=['2026-11-12','2026-11-13'])
    yield conn, reg, cal
    conn.close()


def test_context_uses_exact_prior_cohorts_and_cores(context_env):
    conn,reg,cal=context_env
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert result['cohorts']['昨日连板']['codes']==CODES[:1]
    assert result['cohorts']['昨日断板（前日连板）']['codes']==CODES[4:5]
    assert result['cores'][0]['code']==CODES[0] and not result['gaps']
    assert reg.calls[-1]==('get_market_daily_quotes',('2026-11-13',))


def test_context_rejects_post_open_edits_and_unverified_broken(context_env):
    conn,reg,cal=context_env
    conn.execute("UPDATE daily_reviews SET updated_at='2026-11-16 02:00:00'")
    reg.payloads['get_market_daily_quotes']=[]
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert not result['cores']
    assert result['cohorts']['昨日断板（前日连板）']['codes'] is None
    assert result['cohorts']['昨日涨停']['codes']==CODES[:4]


def test_yesterday_st_limit_up_is_not_misclassified_broken(context_env,monkeypatch):
    conn,reg,cal=context_env
    original=src.archive
    def archived(conn,day):
        raw,evidence=original(conn,day)
        raw=deepcopy(raw)
        if day=='2026-11-13':
            raw['limit_up']['stocks'].append(dict(code=CODES[4],name='*ST样本',limit_times=3))
            raw['limit_up']['count']+=1
        return raw,evidence
    monkeypatch.setattr(src,'archive',archived)
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert result['cohorts']['昨日断板（前日连板）']['codes']==[]


def test_context_bad_industry_blocks_sectors_only(context_env):
    conn,reg,cal=context_env
    reg.payloads['get_stock_sw_industry_map']={CODES[0]:'行业甲'}
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert not result['industry_map'] and result['cohorts']['昨日涨停']['codes']


def test_industry_error_mid_iteration_never_leaks_partial_map(context_env):
    conn,reg,cal=context_env
    reg.payloads['get_stock_sw_industry_map']={**{c:'行业甲' for c in CODES[:8]},'invalid':'行业乙'}
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert result['industry_map']=={} and result['gaps']


def test_full_industry_map_ignores_audited_non_stock_rows(context_env):
    conn,reg,cal=context_env
    reg.payloads['get_stock_sw_industry_map']={**{c:'行业甲' for c in CODES},'T00018.SH':'非普通股票'}
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert set(result['industry_map'])==set(CODES)
    assert result['sources']['industry']['outside_universe']==['T00018.SH']
    assert not result['gaps']


def test_industry_95_percent_is_disclosed_and_blocks_candidates(context_env):
    conn,reg,cal=context_env
    codes=[f'6000{i:02d}.SH' for i in range(20)]
    reg.payloads['get_stock_basic_list']=[dict(ts_code=c,name='样本') for c in codes]
    reg.payloads['get_stock_sw_industry_map']={c:'行业甲' for c in codes[:19]}
    result=src.load_context(reg,conn,'2026-11-16',cal,NOW)
    assert result['industry_coverage_pct']==95
    assert result['unmapped_codes']==codes[19:]
    assert result['industry_map']=={}


@pytest.fixture
def service_env(monkeypatch):
    monkeypatch.setattr(svc,'get_readonly_connection',lambda path:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(svc,'calendar',lambda conn,day:dict(is_open=True,previous='2026-11-13'))
    monkeypatch.setattr(svc,'load_context',lambda *args:context())
    monkeypatch.setattr(svc,'capture',lambda registry,ctx,now,clock,**kwargs:snapshot(now,changes={c:(now-NOW).total_seconds()/300 for c in CODES},ctx=ctx))


def test_run_archives_freezes_dedupes_and_dry_run_does_not_write(service_env,tmp_path,monkeypatch):
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW)
    assert result['status']=='partial' and result['baseline_only'] and result['pushed'] is False
    first=Path(result['report_path']).with_suffix('.json').read_bytes()
    monkeypatch.setattr(svc,'load_context',lambda *args:pytest.fail('不得刷新固定样本'))
    assert svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW)['status']=='skipped'
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW+timedelta(minutes=5))
    assert result['status']=='complete' and result['event_count']>=1
    assert len(list(tmp_path.rglob('report.json')))==2
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    svc.run(None,input_by='USER',root=tmp_path,dry_run=True,clock=lambda:NOW+timedelta(minutes=10))
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert first in [p.read_bytes() for p in tmp_path.rglob('report.json')]


def test_failure_breaks_continuity_and_never_reports_zero_events(service_env,tmp_path,monkeypatch):
    svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW)
    good=svc.capture
    monkeypatch.setattr(svc,'capture',lambda *args,**kwargs: (_ for _ in ()).throw(ValueError('failed')))
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW+timedelta(minutes=5))
    assert result['status']=='source_failed' and result['event_count'] is None
    monkeypatch.setattr(svc,'capture',good)
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW+timedelta(minutes=10))
    assert result['baseline_only']


def test_bad_hash_does_not_fallback_or_overwrite(service_env,tmp_path):
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW)
    Path(result['report_path']).with_suffix('.json').write_text('{}')
    result=svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW+timedelta(minutes=5))
    assert result['status']=='source_failed' and '哈希' in result['reason']
    assert len(list(tmp_path.rglob('report.json')))==1


def test_outside_hours_and_calendar_block_no_network_or_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(svc,'get_readonly_connection',lambda *args:pytest.fail('不应连库'))
    assert svc.run(None,input_by='USER',root=tmp_path,clock=lambda:NOW.replace(hour=16))['status']=='skipped'
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError): svc.run(None,input_by=' ',root=tmp_path)


@pytest.mark.parametrize('hour,minute', [(11, 30), (15, 0)])
def test_manual_sampling_stops_immediately_after_session_end(hour, minute, tmp_path, monkeypatch):
    from services.intraday_events.alerts import in_sampling_session
    close = NOW.replace(hour=hour, minute=minute, second=0, microsecond=0)
    assert in_sampling_session(close)
    monkeypatch.setattr(svc, 'get_readonly_connection', lambda *args: pytest.fail('休市后不应连库'))
    result = svc.run(None, input_by='USER', root=tmp_path, clock=lambda: close + timedelta(microseconds=1))
    assert result['reason_code'] == 'outside_session'
    assert not list(tmp_path.iterdir())


def test_cli_contract():
    from tools.intraday_events import main
    with pytest.raises(SystemExit) as exc: main([])
    assert exc.value.code==2
    with pytest.raises(SystemExit): main(['--input-by','USER','--dry-run','--watch'])


@pytest.mark.parametrize('responses',[
    [dict(status='partial'),dict(status='skipped',reason_code='too_soon',retry_after_seconds=10),dict(status='complete')],
    [dict(status='skipped',reason_code='too_soon',retry_after_seconds=200),dict(status='complete'),dict(status='complete')],
])
def test_watch_short_interval_skip_does_not_end_sampling(monkeypatch,responses):
    from tools import intraday_events as cli
    import main
    calls=[]
    monkeypatch.setattr(main,'load_config',lambda:{})
    monkeypatch.setattr(main,'setup_providers',lambda config:None)
    monkeypatch.setattr(cli,'run',lambda *args,**kwargs:(calls.append(1) or responses[len(calls)-1]))
    sleeps=[]
    monkeypatch.setattr(cli.time,'sleep',sleeps.append)
    cli.main(['--input-by','USER','--watch','--samples','3'])
    assert len(calls)==3 and len(sleeps)==2


def test_watch_outside_session_stops(monkeypatch):
    from tools import intraday_events as cli
    import main
    monkeypatch.setattr(main,'load_config',lambda:{})
    monkeypatch.setattr(main,'setup_providers',lambda config:None)
    monkeypatch.setattr(cli,'run',lambda *args,**kwargs:dict(status='skipped',reason_code='outside_session'))
    monkeypatch.setattr(cli.time,'sleep',lambda seconds:pytest.fail('不应继续等待'))
    assert cli.main(['--input-by','USER','--watch'])==0


def test_watch_slow_capture_preserves_comparable_intervals(monkeypatch):
    from tools import intraday_events as cli
    import main
    monkeypatch.setattr(main,'load_config',lambda:{})
    monkeypatch.setattr(main,'setup_providers',lambda config:None)
    seconds=[0.0]
    captures=[]
    statuses=[]
    def sleep(delay): seconds[0]+=delay
    def slow_run(*args,**kwargs):
        if captures and seconds[0]-captures[-1]<240:
            return dict(status='skipped',reason_code='too_soon',retry_after_seconds=240-(seconds[0]-captures[-1]))
        seconds[0]+=70
        linked=bool(captures) and 240<=seconds[0]-captures[-1]<=420
        captures.append(seconds[0])
        statuses.append(linked)
        return dict(status='complete' if linked else 'partial',baseline_only=not linked)
    monkeypatch.setattr(cli,'run',slow_run)
    monkeypatch.setattr(cli.time,'monotonic',lambda:seconds[0])
    monkeypatch.setattr(cli.time,'sleep',sleep)
    cli.main(['--input-by','USER','--watch','--samples','6'])
    assert captures==[70,380,690]
    assert statuses==[False,True,True]


def test_anchor_observation_is_independent_of_core_and_industry():
    ctx=context(); ctx['cores']=[]; ctx['industry_map']={}
    ctx['anchors']=dict(stocks=[dict(code=CODES[2],name='手写锚点',source_date='2026-11-05',roles=['负反馈'])])
    old=snapshot(ctx=ctx)
    new=snapshot(NOW+timedelta(minutes=5),ctx=ctx,changes={CODES[2]:-2})
    result=a.analyze(old,new,ctx)
    assert result['anchor_watch'][0]['pct_chg']==-2
    event=next(e for e in result['events'] if e['kind']=='anchor_move')
    assert event['code']==CODES[2] and event['direction']=='down'
    assert not [e for e in a.analyze(old,new,ctx,active=result['active'])['events'] if e['kind']=='anchor_move']
    del new['stocks'][CODES[2]]
    result=a.analyze(old,new,ctx)
    assert result['anchor_watch'][0]['status']=='partial'
    assert result['anchor_watch'][0]['pct_chg'] is None


def test_handwritten_anchor_also_gets_following_observation():
    ctx=context(); ctx['cores']=[]
    ctx['anchors']=dict(stocks=[dict(code=CODES[0],name='锚点',source_date='2026-11-05')])
    new=snapshot(NOW+timedelta(minutes=5),ctx=ctx,changes={CODES[0]:2})
    result=a.analyze(snapshot(ctx=ctx),new,ctx)
    assert result['pending_following'][0]['code']==CODES[0]


def test_ten_minute_trial_links_only_adjacent_same_cadence(service_env, tmp_path):
    def sample(minutes, cadence):
        return svc.run(None, input_by='USER', root=tmp_path, interval_minutes=cadence,
                       clock=lambda: NOW+timedelta(minutes=minutes))

    assert sample(0, 5)['baseline_only']
    assert sample(10, 10)['baseline_only']
    linked = sample(20, 10)
    assert not linked['baseline_only'] and linked['event_count'] > 0
    assert sample(40, 10)['baseline_only']


@pytest.mark.parametrize('accepted', [True, False])
def test_ten_minute_push_receipt_and_no_duplicate(service_env, monkeypatch, tmp_path, accepted):
    from pushers.dingtalk_pusher import DingTalkPusher
    sent = []
    monkeypatch.setattr(DingTalkPusher, 'initialize', lambda self: True)
    monkeypatch.setattr(DingTalkPusher, 'send_markdown',
                        lambda self, title, content: sent.append(content) or accepted)
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=CODES[0], name='锚点甲', source_date=ctx['previous_date'],
                                       roles=['核心'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    capture = svc.capture
    monkeypatch.setattr(svc, 'capture', lambda *args, **kwargs: dict(capture(*args, **kwargs), source='test'))
    def sample(minutes):
        return svc.run(None, root=tmp_path, input_by='USER', push=True, interval_minutes=10,
                       clock=lambda: NOW + timedelta(minutes=minutes))
    assert sample(0)['delivery'] == 'no_comparison' and not sent
    second = sample(10)
    assert second['pushed'] is accepted and len(sent) == 1
    folder = Path(second['report_path']).parent
    receipt = json.loads((folder/'delivery.json').read_bytes())
    assert receipt['status'] == ('accepted' if accepted else 'unconfirmed')
    assert '锚点甲' in sent[0] and '行业甲' not in sent[0]
    assert '10:00–10:10' in sent[0] and '区间 +2.00%，当日 +2.00%' in sent[0]
    assert sample(15)['reason_code'] == 'duplicate_slot'
    last = sample(20)
    assert last['delivery'] == 'quiet' and len(sent) == 1
    assert all(label not in sent[0] for label in ('同时段倍数', '回吐', '下一批表现'))
    (folder / 'key-levels.json').unlink()
    rebuilt = svc.write_day_summary(folder.parent, 'USER').read_text()
    assert f'{folder.name} 未纳入' in rebuilt
    if accepted:
        assert '共核实 1 条。延续 1' in rebuilt
    else:
        assert '投递未确认' in rebuilt and '共核实 1 条' not in rebuilt


def test_levels_archive_crossing_and_turnover_without_alerting(service_env, monkeypatch, tmp_path):
    from pushers.dingtalk_pusher import DingTalkPusher
    ctx = context()
    ctx['anchors'] = dict(sources=[dict(date=ctx['previous_date'])], key_levels=[
        dict(kind='stock', code=CODES[0], name='锚点甲', level=100, labels=['复盘对比价']),
        dict(kind='index', code='000001.SH', name='上证指数', level=4000, labels=['缺口'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    def capture(registry, ctx, now, clock):
        prices = {0: -.1, 10: .1, 20: .2, 30: .3, 40: .4}
        minutes = (now - NOW).total_seconds() / 60
        return dict(snapshot(now, changes={CODES[0]: prices[minutes]}, ctx=ctx), source='test')
    monkeypatch.setattr(svc, 'capture', capture)
    monkeypatch.setattr(DingTalkPusher, 'initialize', lambda self: pytest.fail('影子关键位不可进入发送'))
    def sample(at, index_price):
        registry = Registry({'get_realtime_quotes': [
            dict(code='000001.SH', name='上证指数', price=index_price, quote_date='2026-11-16',
                 quote_time=at.strftime('%H:%M:%S'))]})
        return svc.run(registry, root=tmp_path, input_by='USER', push=True, interval_minutes=10, clock=lambda: at)
    baseline = sample(NOW, 4010)
    assert '没有可比点位，未计算' in Path(baseline['level_summary_path']).read_text()
    followed = sample(NOW + timedelta(minutes=10), 3994)
    assert baseline['delivery'] == 'no_comparison' and followed['delivery'] == 'quiet'
    path = Path(followed['report_path']).with_name('key-levels.md')
    report = json.loads(path.with_suffix('.json').read_bytes())
    receipt = json.loads(path.with_name('receipt.json').read_bytes())
    assert report['report_sha256'] == receipt['sha256']
    assert report['indices_sha256'] == digest(report['indices'])
    stock, index = report['rows']
    assert stock['crossed'] == 'up' and stock['amount_yuan'] == 60000
    assert index['crossed'] == 'down' and index['amount_yuan'] is None
    assert all(row['confirmation']['status'] == 'pending' for row in report['rows'])
    assert '向下穿越' in path.read_text() and '6.00 万元' in path.read_text()

    confirmed_stock = sample(NOW + timedelta(minutes=20), 3996)
    summary = Path(confirmed_stock['report_path']).with_name('key-levels.md')
    record = json.loads(summary.with_suffix('.json').read_bytes())
    assert record['level_rows_sha256'] == digest(record['rows'])
    assert record['rows'][0]['confirmation']['status'] == 'confirmed'
    assert record['rows'][0]['confirmation']['distance_pct'] == .2
    assert record['rows'][0]['confirmation']['crossing_amount_yuan'] == 60000
    assert record['rows'][1]['confirmation']['status'] == 'pending'
    assert '首次穿越区间成交 6.00 万元' in summary.read_text()
    assert confirmed_stock['delivery'] == 'quiet'

    confirmed_index = sample(NOW + timedelta(minutes=30), 3990)
    record = json.loads(Path(confirmed_index['report_path']).with_name('key-levels.json').read_bytes())
    assert 'confirmation' not in record['rows'][0]
    assert record['rows'][1]['confirmation']['status'] == 'confirmed'
    assert record['rows'][1]['confirmation']['crossed_at'] == (NOW + timedelta(minutes=10)).isoformat()
    assert confirmed_index['delivery'] == 'quiet'

    stable = sample(NOW + timedelta(minutes=40), 3980)
    record = json.loads(Path(stable['report_path']).with_name('key-levels.json').read_bytes())
    assert all('confirmation' not in row for row in record['rows'])
    combined = Path(stable['level_summary_path']).read_text()
    assert combined.count('|锚点甲 ') == combined.count('|上证指数 ') == 1
    assert '10:20↑' in combined and '6.00 万' in combined and '10:30↓' in combined
    assert '已核验 5 批' in combined
    assert f"报价区间 [10:00:00]({Path(baseline['report_path']).parent.name}/report.md)" in combined
    assert f"[10:10:00]({path.parent.name}/report.md)，本次 0.60 万元/分" in combined
    delivery_file = summary.with_name('delivery.json')
    delivery = json.loads(delivery_file.read_bytes())
    delivery['report_sha256'] = 'invalid'
    delivery_file.write_text(json.dumps(delivery))
    rebuilt = svc.write_day_summary(path.parent.parent, 'USER').read_text()
    assert '10:20↑' in rebuilt and '10:30↓' in rebuilt
    assert '投递回执缺失或未通过核验' in rebuilt
    # 缺少首次穿越批，只能显示缺口，不能借后续确认行补齐。
    path.with_suffix('.json').unlink()
    rebuilt = svc.write_day_summary(path.parent.parent, 'USER').read_text()
    assert '10:20↑' not in rebuilt and '10:30↓' not in rebuilt
    assert '确认链无法逐批复核' in rebuilt


def test_index_sidecar_delay_cannot_expire_original_alert(service_env, monkeypatch, tmp_path):
    from pushers.dingtalk_pusher import DingTalkPusher
    code = CODES[0]
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'], sources=[{}],
        stocks=[dict(code=code, name='锚点甲', source_date=ctx['previous_date'])],
        key_levels=[dict(kind='index', code='000001.SH', name='上证指数', level=4000, labels=['缺口'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    tick = [NOW.replace(hour=9, minute=56, second=58)]
    def capture(registry, ctx, now, clock):
        return snapshot(now, changes={code: 3 if now > NOW else 0}, ctx=ctx)
    monkeypatch.setattr(svc, 'capture', capture)
    calls = []
    monkeypatch.setattr(DingTalkPusher, 'initialize', lambda self: True)
    monkeypatch.setattr(DingTalkPusher, 'send_markdown', lambda *args: calls.append('send') or True)
    def slow_source(*args):
        calls.append('index')
        tick[0] += timedelta(seconds=4)
        return dict(indices={}, index_source=dict(expected=['000001.SH'], errors={'000001.SH': '源失败'}))
    monkeypatch.setattr(svc, 'capture_level_indices', slow_source)
    def sample():
        return svc.run(None, root=tmp_path, input_by='USER', push=True, interval_minutes=10, clock=lambda: tick[0])
    assert sample()['delivery'] == 'no_comparison'
    tick[0] = NOW.replace(minute=6, second=58)
    result = sample()
    assert result['delivery'] == 'accepted' and result['level_watch_status'] == 'partial'
    assert calls == ['index', 'send', 'index']


@pytest.mark.parametrize('linked,source,amount,advance,expected_amount', [
    (True, 'sina', 200, True, 100),
    (True, 'other', 200, True, None),
    (True, 'sina', 50, True, None),
    (True, 'sina', 200, False, None),
    (False, 'sina', 200, True, None),
])
def test_level_turnover_requires_comparable_advancing_same_source(
        linked, source, amount, advance, expected_amount):
    from services.intraday_events.levels import observe_levels
    code = CODES[0]
    before = dict(source='sina', stocks={code: dict(price=10, amount=100, quote_at=NOW.isoformat())})
    stamp = NOW + timedelta(minutes=10) if advance else NOW
    current = dict(source=source, stocks={code: dict(price=11, amount=amount, quote_at=stamp.isoformat())})
    anchors = dict(sources=[{}], key_levels=[dict(kind='stock', code=code, level=10)])
    row = observe_levels(before, current, anchors, linked)['rows'][0]
    assert row['amount_yuan'] == expected_amount
    assert row['crossed'] is None  # 等于点位后离开不构成双侧穿越


@pytest.mark.parametrize('changed', [
    {'status': 'baseline'}, {'previous_quote_at': 'different'},
    {'side': 'at'}, {'supplement_sha256': 'revised'},
])
def test_key_level_confirmation_cannot_continue_without_same_adjacent_evidence(changed):
    from services.intraday_events.levels import confirm_crossings

    old_at = NOW.isoformat()
    new_at = (NOW + timedelta(minutes=10)).isoformat()
    previous = [dict(kind='stock', code=CODES[0], level=100, supplement_sha256='frozen',
                     side='above', quote_at=old_at, confirmation=dict(status='pending', crossed_at=old_at))]
    current = dict(kind='stock', code=CODES[0], level=100, supplement_sha256='frozen',
                   side='above', price=101, previous_side='above', previous_quote_at=old_at,
                   quote_at=new_at, status='compared', crossed=None)
    current.update(changed)
    confirm_crossings([current], previous, new_at)
    assert 'confirmation' not in current


def test_anchor_cooldown_uses_receipts_and_preserves_research_candidates(service_env, monkeypatch, tmp_path):
    from pushers.dingtalk_pusher import DingTalkPusher

    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=CODES[0], name='锚点甲', source_date=ctx['previous_date'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    changes = {0: 0, 10: 1.8, 20: 4.1, 30: 1.7, 40: 5.0, 50: 9.0, 60: 6.5, 70: 4.0}
    def capture(registry, ctx, now, clock):
        minutes = (now - NOW).total_seconds() / 60
        return snapshot(now, changes={CODES[0]: changes[minutes]}, ctx=ctx)
    monkeypatch.setattr(svc, 'capture', capture)
    monkeypatch.setattr(DingTalkPusher, 'initialize', lambda self: True)
    monkeypatch.setattr(DingTalkPusher, 'send_markdown', lambda *args: True)

    def sample(minutes, *, push=True):
        return svc.run(None, root=tmp_path, input_by='USER', push=push, interval_minutes=10,
                       clock=lambda: NOW + timedelta(minutes=minutes))

    assert sample(0)['delivery'] == 'no_comparison'
    assert sample(10)['delivery'] == 'quiet'
    assert sample(20)['delivery'] == 'accepted'
    suppressed = sample(30)
    assert suppressed['delivery'] == 'quiet'
    report = json.loads(Path(suppressed['report_path']).with_suffix('.json').read_bytes())
    assert any(row['kind'] == 'anchor_move' for row in report['analysis']['events'])
    assert sample(40, push=False)['delivery'] is None
    day_root = tmp_path / NOW.date().isoformat()
    def history(minutes):
        return svc.load_anchor_history(day_root, NOW + timedelta(minutes=minutes), {CODES[0]})
    assert history(49)['recent_codes'] == {CODES[0]}
    assert not history(50)['recent_codes']
    assert history(50)['last_pct'] == {f'anchor:{CODES[0]}:up': 4.1}
    assert history(50)['ranges'] == {CODES[0]: (100, 105)}
    assert sample(50)['delivery'] == 'quiet'  # 连续同向即使超过3%仍去重
    assert sample(60)['delivery'] == 'accepted'  # 本地批次不启动冷却
    assert sample(70)['delivery'] == 'quiet'


def test_anchor_cooldown_allows_large_new_move():
    from services.intraday_events.alerts import select_alerts
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=CODES[0], source_date=ctx['previous_date'])])
    def calc(change, recent):
        analysis = dict(baseline_only=False, active=[], sectors=[],
                        anchor_watch=[dict(code=CODES[0], interval_pct=change)])
        return select_alerts(analysis, {CODES[0]: change}, ctx, {},
                             anchor_history=dict(recent_codes=recent))
    assert not calc(-2.99, {CODES[0]})['anchors']
    assert calc(-3.0, {CODES[0]})['anchors']
    assert calc(2.0, set())['anchors']
    assert calc((16.83 / 16.50 - 1) * 100, set())['anchors']
    assert calc((2.45 / 2.50 - 1) * 100, set())['anchors']
    assert calc((71.07 / 69.0 - 1) * 100, {CODES[0]})['anchors']


@pytest.mark.parametrize('change,price,pct,prior_pct,expected', [
    (3.51, 22.00, 4.53, 1.71, False),
    (3.51, 22.01, 4.56, 1.71, True),
    (-2.56, 20.50, -2.79, 1.77, True),
    (-2.56, 20.50, -2.79, -1.77, False),
    (-2.56, 19.99, -3.97, -1.77, True),
    (2.5, 21.01, 0.03, -0.2, True),
    (-2.47, 21.00, 0.0, 0.0, False),
])
def test_same_direction_reminder_requires_new_sample_extreme_or_prior_close_cross(
        change, price, pct, prior_pct, expected):
    from services.intraday_events.alerts import select_alerts
    code = CODES[0]
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=code, source_date=ctx['previous_date'])])
    analysis = dict(baseline_only=False, active=[], sectors=[],
                    anchor_watch=[dict(code=code, interval_pct=change, price=price, pct_chg=pct)])
    key = f"anchor:{code}:{'up' if change > 0 else 'down'}"
    history = dict(ranges={code: (20.00, 22.00)}, last_pct={key: prior_pct})
    assert bool(select_alerts(analysis, {code: change}, ctx, {}, anchor_history=history)['anchors']) is expected


@pytest.mark.parametrize('damaged_json', [True, False])
def test_invalid_delivery_preserves_snapshot_and_pauses_anchor_push(
        service_env, monkeypatch, tmp_path, damaged_json):
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=CODES[0], name='锚点甲', source_date=ctx['previous_date'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    initial = svc.run(None, root=tmp_path, input_by='USER', push=True, interval_minutes=10,
                      clock=lambda: NOW)
    path = Path(initial['report_path']).with_name('delivery.json')
    receipt = json.loads(path.read_bytes())
    path.write_text('{' if damaged_json else json.dumps(dict(receipt, status='unknown')))
    second = svc.run(None, root=tmp_path, input_by='USER', push=True, interval_minutes=10,
                     clock=lambda: NOW + timedelta(minutes=10))
    report = json.loads(Path(second['report_path']).with_suffix('.json').read_bytes())
    assert second['status'] == 'partial' and second['gaps']
    assert report['snapshot'] and not report['analysis']['baseline_only']
    assert report['analysis']['alert']['anchor_history_error'] in second['gaps']


def test_reverse_sector_alert_and_focused_widening():
    from services.intraday_events.alerts import select_alerts
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=CODES[0], source_date=ctx['previous_date'])])
    row = dict(sector='行业甲', metrics=dict(median_pct=.8, count=10, up_ratio_pct=90))
    a = dict(baseline_only=False, active=['sector:行业甲:up'], sectors=[row], events=[])
    def calc(market, state):
        return select_alerts(a, {'样本': market}, ctx, state)
    opposite = calc(-.3, {})
    assert len(opposite['sectors']) == 1
    assert not calc(-.3, opposite['sector_state'])['sectors']
    same = calc(.5, {})
    assert not same['sectors']
    widened = calc(.1, same['sector_state'])
    assert widened['sectors'][0]['reason'] == '连续两窗分化扩大'
    assert not calc(.1, widened['sector_state'])['sectors']


def test_timed_launch_receipt_and_repeat_slot(service_env, tmp_path):
    planned = NOW.replace(minute=2)
    actual = planned + timedelta(seconds=8)
    def sample(clock):
        return svc.run(None, root=tmp_path, input_by='launchd', interval_minutes=10,
                       push=True, scheduled_at=planned, clock=lambda: clock)
    assert sample(planned - timedelta(seconds=1))['reason_code'] == 'outside_scheduled_slot'
    assert sample(planned + timedelta(minutes=3))['reason_code'] == 'outside_scheduled_slot'
    first = sample(actual)
    receipt = json.loads(Path(first['report_path']).with_name('receipt.json').read_bytes())
    assert receipt['scheduled_at'] == planned.isoformat()
    assert receipt['started_at'] == receipt['captured_at'] == actual.isoformat()
    assert sample(actual + timedelta(seconds=5))['reason_code'] == 'duplicate_slot'


@pytest.mark.parametrize('price,pct,previous_pct,expected_reason', [
    (22.20, 1.5, 1.0, '此前盘中采样高点 22.00'),
    (19.80, -1.5, -1.0, '此前盘中采样低点 20.00'),
    (21.01, .03, -.20, '现已站上昨收'),
    (21.00, -.03, .20, '现已跌破昨收'),
])
def test_dingtalk_repeat_explains_selected_evidence(price, pct, previous_pct, expected_reason):
    from services.intraday_events.alerts import select_alerts, format_alert
    code = CODES[0]
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=code, source_date=ctx['previous_date'])])
    change = 2.5 if pct > 0 else -2.5
    row = dict(code=code, name='锚点甲', interval_pct=change, price=price, pct_chg=pct)
    key = f"anchor:{code}:{'up' if change > 0 else 'down'}"
    alert = select_alerts(dict(baseline_only=False, active=[], sectors=[], anchor_watch=[row]),
                          {code: change}, ctx, {},
                          anchor_history=dict(ranges={code: (20.00, 22.00)}, last_pct={key: previous_pct}))
    _, message = format_alert(NOW.isoformat(), alert)
    assert expected_reason in message and f'{price:.2f} 元' in message


@pytest.mark.parametrize('move,daily,position', [
    (2.80, -1.20, '仍低于昨收 1.20%'),
    (-3.10, 2.40, '仍高于昨收 2.40%'),
    (-3.10, .002, '仍高于昨收（不足 0.01%）'),
    (2.01, 0.0, '当日 +0.00%'),
    (2.01, 1.01, '当日 +1.01%'),
])
def test_dingtalk_price_position_is_relative_to_previous_close(move, daily, position):
    from services.intraday_events.alerts import format_alert
    row = dict(code=CODES[0], name='锚点甲', price=30, pct_chg=daily, interval_pct=move)
    _, content = format_alert(NOW.isoformat(), dict(anchors=[row], sectors=[]))
    assert f'区间 {move:+.2f}%，{position}' in content
    assert '上次已接收' not in content


@pytest.mark.parametrize('reference,minutes,expected', [
    (30, -10, '现价较当时持平'), (31, -10, '现价较当时低 3.23%'),
    (29.9999, -10, '现价较当时高（不足 0.01%）'), (0, -10, None), (float('nan'), -10, None),
    (True, -10, None), (30, 1, None), (30, -1440, None),
])
def test_dingtalk_price_comparison_needs_valid_earlier_same_day_reference(reference, minutes, expected):
    from services.intraday_events.alerts import format_alert
    row = dict(code=CODES[0], name='锚点甲', price=30, pct_chg=1, interval_pct=2,
               previous_accepted=dict(captured_at=(NOW+timedelta(minutes=minutes)).isoformat(), price=reference))
    _, content = format_alert(NOW.isoformat(), dict(anchors=[row], sectors=[]))
    if expected is None:
        assert '上次已接收' not in content
    else:
        assert '上次已接收的同向提醒在 09:50' in content and expected in content


@pytest.mark.parametrize('first_accepted', [True, False])
def test_formal_delivery_uses_accepted_same_direction_price(service_env, monkeypatch, tmp_path, first_accepted):
    from pushers.dingtalk_pusher import DingTalkPusher
    code = CODES[0]
    ctx = context()
    ctx['anchors'] = dict(expected_source_date=ctx['previous_date'],
                          stocks=[dict(code=code, name='锚点甲', source_date=ctx['previous_date'])])
    monkeypatch.setattr(svc, 'load_context', lambda *args: ctx)
    changes = {0: -8, 10: -5, 20: -4, 30: -1}
    def capture(registry, ctx, now, clock):
        offset = (now - NOW).total_seconds() / 60
        return dict(snapshot(now, changes={code: changes[offset]}, ctx=ctx), source='test')
    monkeypatch.setattr(svc, 'capture', capture)
    messages = []
    monkeypatch.setattr(DingTalkPusher, 'initialize', lambda self: True)
    def send(self, title, content):
        messages.append(content)
        return first_accepted if len(messages) == 1 else True
    monkeypatch.setattr(DingTalkPusher, 'send_markdown', send)
    def sample(minutes):
        return svc.run(None, root=tmp_path, input_by='USER', push=True, interval_minutes=10,
                       clock=lambda: NOW + timedelta(minutes=minutes))
    assert sample(0)['delivery'] == 'no_comparison'
    assert sample(10)['delivery'] == ('accepted' if first_accepted else 'unconfirmed')
    assert sample(20)['delivery'] == 'quiet'
    last = sample(30)
    assert last['delivery'] == 'accepted' and len(messages) == 2
    receipt = json.loads(Path(last['report_path']).with_name('delivery.json').read_bytes())
    assert messages[1] == receipt['content']
    assert '仍低于昨收 5.00%' in messages[0] and '仍低于昨收 1.00%' in messages[1]
    assert '此前盘中采样高点' in messages[1]
    assert ('上次已接收的同向提醒在 10:10，现价较当时高 4.21%' in messages[1]) == first_accepted


def test_weekday_launch_calendar_matches_the_24_shanghai_slots():
    import plistlib
    folder = Path(__file__).resolve().parents[2] / 'deploy/launchd'
    plist = plistlib.loads((folder/'com.alyx.tradesystem.intraday-events.plist').read_bytes())
    intervals = plist['StartCalendarInterval']
    assert len(intervals) == 120
    expected = {(day, t // 60, t % 60) for day in range(1, 6)
                for start in [572, 782] for t in range(start, start + 120, 10)}
    assert {(i['Weekday'], i['Hour'], i['Minute']) for i in intervals} == expected
    assert not plist['KeepAlive'] and not plist['RunAtLoad'] and 'StartInterval' not in plist
    assert Path(plist['ProgramArguments'][0]).name == 'intraday-events-runner.sh'
