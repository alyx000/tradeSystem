from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from analyzers import theme_launch as t
from analyzers import sector_increment as si
from providers.base import DataResult
from providers.tushare_provider import TushareProvider

DAY, PREV = '2026-11-02', '2026-10-30'
CODES = ['600000.SH', '300001.SZ', '688001.SH']
THEME = '885001.TI'


def inputs():
    def up(heights):
        return dict(_source='tushare:limit_list_d', count=len(heights), stocks=[
            dict(code=CODES[i], name='股票'+str(i), limit_times=h, limit_times_verified=True)
            for i, h in enumerate(heights)])
    quotes = lambda d, factor: [dict(ts_code=c, trade_date=d, amount=(i+1)*1e5*factor) for i, c in enumerate(CODES)]
    inc = si.calculate(quotes(DAY, 2), quotes(PREV, 1),
        [dict(ts_code=c, list_date='20000101') for c in CODES],
        {c: {'sw_l2': '行业'} for c in CODES}, DAY, PREV, min_count=3)
    members = [dict(ts_code=THEME, con_code=c, con_name='股票'+str(i), is_new='Y') for i,c in enumerate(CODES)]
    return dict(ranking=dict(_source='tushare:limit_cpt_list', data=[dict(ts_code=THEME,name='题材甲',rank=1,trade_date='20261102',pct_chg=3)]),
        membership=dict(date=DAY, fetched_at=DAY+'T20:00:00+08:00', themes={THEME: dict(status='complete',rows=members,pages=[3,0])}),
        membership_source=t.SOURCE, limit_up_date=DAY, previous_limit_up_date=PREV,
        limit_up=up([2,1,1]), previous_limit_up=up([1]), increment=inc,
        indices={k:dict(code=c,trade_date=DAY,change_pct=1,_source='tushare:index_daily')
                 for k,c in (('shanghai','000001.SH'),('chinext','399006.SZ'))})


def calc(e=None):
    return t.calculate(DAY, PREV, inputs() if e is None else e)


def test_complete_counts_lead_amount_and_recompute():
    b=calc();r=b['rows'][0]
    assert b['status']=='complete'
    assert r['counts']=={'10':1,'20':2,'30':0,'10+20':3}
    assert r['lead_10cm']==[dict(code=CODES[0],name='股票0',height=2)]
    assert r['amount']['delta_yi']==6 and r['amount']['share_delta_pp']==0
    assert t.validate(b,DAY)==b
    assert '题材启动证据卡' in t.render_markdown(b,DAY)


def _html_default_text(fragment):
    import re
    from html import unescape
    return unescape(re.sub('<[^>]+>', '', re.sub(r'<details\b.*?</details>', '', fragment, flags=re.S)))


@pytest.mark.parametrize('status,expected',[
    ('not_computed', '本日未计算，暂无可判断的题材启动证据'),
    ('source_failed', '题材成员或行情来源未核验'),
    ('skipped', '本日未采集题材启动证据'),
])
def test_html_missing_results_are_plain_language_with_folded_provenance(status,expected):
    block=t.missing(DAY,'原始缺口保留',status)
    before=deepcopy(block);markdown=t.render_markdown(block,DAY)
    fragment,gap=t.render_html(block,DAY);visible=_html_default_text(fragment)
    assert expected in visible and status not in visible and '状态：' not in visible
    assert '原始缺口保留' not in visible and '原始缺口保留' in fragment
    assert t.NOTE not in visible and t.NOTE in fragment
    assert f'data-as-of="{DAY}" data-items="3"' in fragment and '（3项）' in fragment
    assert '<details class="evidence"' in fragment and '<details open' not in fragment
    assert block==before and t.render_markdown(block,DAY)==markdown and gap


def test_html_result_counts_remain_visible_without_technical_status():
    block=calc();before=deepcopy(block);fragment,gap=t.render_html(block,DAY)
    visible=_html_default_text(fragment)
    assert '已完整核验1个题材' in visible
    assert '10cm 1只、20cm 2只、30cm 0只；10+20cm合计3只' in visible
    assert 'complete' not in visible and t.NOTE not in visible
    assert '股票0（600000.SH）2板' in fragment and t.NOTE in fragment
    assert not gap and block==before


def test_html_failed_theme_never_shows_zero_counts():
    evidence=inputs();evidence['membership']['themes'][THEME]['status']='source_failed'
    fragment,gap=t.render_html(calc(evidence),DAY);visible=_html_default_text(fragment)
    assert '题材成员或行情来源未核验' in visible and '0只' not in visible
    assert '题材甲' not in visible and '题材甲' in fragment
    assert 'source_failed' not in visible and 'source_failed' in fragment and gap


def test_html_partial_keeps_known_counts_and_folds_failed_theme_rows():
    evidence=inputs();failed='885002.TI'
    evidence['ranking']['data'].append(dict(ts_code=failed,name='未核验题材',rank=2,trade_date='20261102',pct_chg=2))
    evidence['membership']['themes'][failed]={'status':'source_failed'}
    fragment,gap=t.render_html(calc(evidence),DAY);visible=_html_default_text(fragment)
    assert '2个题材中1个完整核验' in visible
    assert '题材甲：10cm 1只、20cm 2只、30cm 0只' in visible
    assert '未核验题材' not in visible and '未核验题材' in fragment and gap


@pytest.mark.parametrize('mutation',[
    lambda e:e['membership'].update(date=PREV),
    lambda e:e['membership'].update(fetched_at=PREV+'T20:00:00+08:00'),
    lambda e:e['membership'].update(fetched_at=DAY+'T20:00:00'),
    lambda e:e['membership']['themes'][THEME].update(pages=[3]),
    lambda e:e['membership']['themes'][THEME].update(status='source_failed'),
    lambda e:e['membership']['themes'][THEME]['rows'][0].update(is_new=None),
    lambda e:e['membership']['themes'][THEME]['rows'][0].update(con_code='600000.SZ'),
    lambda e:e['membership']['themes'][THEME]['rows'].append(e['membership']['themes'][THEME]['rows'][0]),
])
def test_bad_members_never_publish_zero(mutation):
    e=inputs();mutation(e);b=calc(e)
    assert b['status']=='source_failed' and b['rows'][0]['counts'] is None


@pytest.mark.parametrize('mutation',[
    lambda e:e['ranking']['data'][0].update(trade_date='20261030'),
    lambda e:e['ranking']['data'][0].update(rank=2),
    lambda e:e['ranking']['data'].append(e['ranking']['data'][0]),
    lambda e:e.update(membership_source='unknown'),
])
def test_bad_ranking_or_source_fails_closed(mutation):
    e=inputs();mutation(e);b=calc(e)
    assert b['status']=='source_failed' and b['rows']==[]


def test_up_failure_and_true_empty_distinct():
    e=inputs();e['limit_up']={'error':'offline'}
    r=calc(e)['rows'][0]
    assert r['counts'] is None and r['amount'] is not None
    e['limit_up']=dict(_source='verified',count=0,stocks=[])
    assert calc(e)['rows'][0]['counts']['10+20']==0


@pytest.mark.parametrize('mode',['prior_failed','prior_missing','unknown_height','default_height','mismatch'])
def test_lead_requires_verified_continuity(mode):
    e=inputs()
    if mode=='prior_failed':e['previous_limit_up']={'error':'failed'}
    if mode=='prior_missing':e['previous_limit_up']['stocks']=[];e['previous_limit_up']['count']=0
    if mode=='unknown_height':e['limit_up']['stocks'][0]['limit_times_verified']=False
    if mode=='default_height':e['limit_up']['stocks'][0].update(limit_times=1,limit_times_verified=False)
    if mode=='mismatch':e['previous_limit_up']['stocks'][0]['limit_times']=2
    r=calc(e)['rows'][0]
    assert r['lead_10cm']==[] and r['lead_unknown']==[CODES[0]] and r['counts']['10']==1


def test_st_excluded_from_limits_but_amount_retains_st_scope():
    e=inputs();e['limit_up']['stocks'][0]['name']='ST测试';e['membership']['themes'][THEME]['rows'][0]['con_name']='ST测试'
    r=calc(e)['rows'][0]
    assert r['counts']['10']==0 and r['amount']['delta_yi']==6


def test_overlap_not_summed_and_partial_member_isolated():
    e=inputs();other='885002.TI'
    e['ranking']['data'].append(dict(ts_code=other,name='题材乙',rank=2,trade_date='20261102',pct_chg=2))
    e['membership']['themes'][other]=deepcopy(e['membership']['themes'][THEME])
    for r in e['membership']['themes'][other]['rows']:r['ts_code']=other
    b=calc(e)
    assert [r['counts']['10+20'] for r in b['rows']]==[3,3]
    assert 'total_count' not in b
    e['membership']['themes'][other]['status']='source_failed'
    b=calc(e)
    assert b['status']=='partial' and b['rows'][0]['status']=='complete' and b['rows'][1]['counts'] is None


def test_amount_tamper_missing_and_partial_never_create_full_share():
    e=inputs();e['increment']['components'][0]['amount_yi']=100
    r=calc(e)['rows'][0];assert r['amount'] is None and r['counts']['20']==2
    e=inputs();e['increment'].update(status='partial',gaps=['未覆盖'])
    r=calc(e)['rows'][0];assert r['amount']['share_delta_pp'] is None
    e=inputs();e['membership']['themes'][THEME]['rows'].append(dict(ts_code=THEME,con_code='000002.SZ',con_name='缺行情',is_new='Y'))
    e['membership']['themes'][THEME]['pages']=[4,0]
    r=calc(e)['rows'][0];assert r['amount'] is None and r['amount_coverage']['missing']==['000002.SZ']


def test_snapshot_tamper_wrong_date_and_old_missing():
    b=calc();b['rows'][0]['counts']['20']=4
    assert t.validate(b,DAY)['status']=='source_failed'
    assert t.validate(calc(),PREV)['status']=='source_failed'
    assert t.validate(None,DAY)['status']=='not_computed'


def calendar(tmp_path):
    db=tmp_path/'calendar.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE trade_calendar (date TEXT,exchange TEXT,is_open INT)')
        c.executemany('INSERT INTO trade_calendar VALUES (?,"SSE",?)',[(d,f) for d,f in [(PREV,1),('2026-10-31',0),('2026-11-01',0),(DAY,1)]])
    return db


def save(daily,day,raw):
    p=daily/day/'post-market.yaml';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(yaml.safe_dump(dict(date=day,raw_data=raw),allow_unicode=True))


def test_collect_and_history_do_not_backfill_current_members(tmp_path):
    db=calendar(tmp_path);e=inputs();calls=[]
    reg=SimpleNamespace(call=lambda *args: (calls.append(args) or DataResult(data=e['membership'],source=t.SOURCE)))
    raw=dict(date=DAY,limit_cpt_list=e['ranking'],limit_up=e['limit_up'],sector_increment=e['increment'],indices=e['indices'])
    save(tmp_path,PREV,dict(limit_up=e['previous_limit_up']))
    now=datetime(2026,11,2,20,tzinfo=ZoneInfo('Asia/Shanghai'));before=db.read_bytes()
    b=t.collect(reg,DAY,raw,daily_dir=tmp_path,db_path=db,now=now)
    assert b['status']=='complete' and len(calls)==1 and db.read_bytes()==before
    save(tmp_path,DAY,{t.KEY:b})
    later=now+timedelta(days=1)
    assert t.collect(reg,DAY,raw,daily_dir=tmp_path,db_path=db,now=later)==b
    assert len(calls)==1
    assert t.collect(reg,DAY,raw,daily_dir=tmp_path,db_path=db,now=now.replace(hour=15))['status']=='skipped'
    assert len(calls)==1
    with sqlite3.connect(db) as c:c.execute('DELETE FROM trade_calendar WHERE date="2026-11-01"')
    assert t.collect(reg,DAY,raw,daily_dir=tmp_path,db_path=db,now=now)['status']=='source_failed'
    assert len(calls)==1


def test_closed_day_projection_and_no_open_day_fallback(tmp_path):
    db=calendar(tmp_path);b=calc();save(tmp_path,DAY,{t.KEY:b})
    with sqlite3.connect(db) as c:c.execute('INSERT INTO trade_calendar VALUES ("2026-11-03","SSE",0)')
    block=t.load_for_review(tmp_path,'2026-11-03',db)
    html,gap=t.render_html(block,'2026-11-03')
    assert '休市展示' in html and 'data-as-of="2026-11-02"' in html and not gap
    with sqlite3.connect(db) as c:c.execute('UPDATE trade_calendar SET is_open=1 WHERE date="2026-11-03"')
    assert t.load_for_review(tmp_path,'2026-11-03',db)['status']=='not_computed'


def test_provider_paginates_actual_rows_and_rejects_partial(monkeypatch):
    import providers.tushare_provider as p
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,2,20,tzinfo=tz)
    monkeypatch.setattr(p,'datetime',Clock)
    provider=TushareProvider({});rows=inputs()['membership']['themes'][THEME]['rows'];calls=[]
    def query(api,**kw):
        calls.append(kw)
        return pd.DataFrame(rows[kw['offset']:kw['offset']+2])
    provider.pro=SimpleNamespace(query=query)
    out=provider.get_theme_member_snapshot(DAY,[THEME])
    assert out.success and out.data['themes'][THEME]['pages']==[2,1,0]
    assert [x['offset'] for x in calls]==[0,2,3]
    assert not provider.get_theme_member_snapshot(PREV,[THEME]).success
    provider.pro.query=lambda *a,**k:pd.DataFrame(rows)
    assert provider.get_theme_member_snapshot(DAY,[THEME]).data['themes'][THEME]['status']=='source_failed'
    provider.pro.query=lambda *a,**k:pd.DataFrame()
    assert provider.get_theme_member_snapshot(DAY,[THEME]).data['themes'][THEME]['status']=='source_failed'


def test_html_source_guard_and_escape(tmp_path):
    spec=importlib.util.spec_from_file_location('html_tests',Path(__file__).with_name('test_daily_review_html_report.py'))
    f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
    a=f._load_assembler();f._write_chunks(tmp_path,DAY)
    e=inputs();e['ranking']['data'][0]['name']='<script>x</script>|题材';b=calc(e)
    html=a.render_report(tmp_path,DAY,theme_launch=b)
    a.validate_report(html,theme_launch=b)
    fragment,_=t.render_html(b,DAY)
    assert '&lt;script&gt;' in fragment and '<script>x' not in fragment
    for changed in (html.replace(fragment,fragment.replace('股票0','假股票')),
                    html.replace('</article>',fragment+'</article>'),html.replace(fragment,'<!--'+fragment+'-->')):
        with pytest.raises(a.ReportValidationError):a.validate_report(changed,theme_launch=b)
    s2=tmp_path/f'b{DAY}_s2.html';s2.write_text(s2.read_text()+f'<div {t.MARKER}="{DAY}">假证据</div>')
    with pytest.raises(a.ReportValidationError,match='duplicate_theme_launch'):a.render_report(tmp_path,DAY,theme_launch=b)


def test_collection_and_report_entry_wired(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from collectors.market import MarketCollector
    from generators.report import ReportGenerator
    from providers.registry import ProviderRegistry
    monkeypatch.setenv('TRADE_DB_PATH',str(tmp_path/'isolated.db'))
    reg=MagicMock(spec=ProviderRegistry)
    reg.call.return_value=DataResult(data=None,source='test',error='offline')
    reg.call_specific.return_value=reg.call.return_value
    reg.get_provider.return_value=None
    monkeypatch.setattr('collectors.market.BASE_DIR',tmp_path)
    # 集成只验证编排与归档，其他独立采集器全部隔离，不能读生产日历/触发重试。
    for module, name in [('analyzers.low_price_effect','collect_low_price_effect'),
                         ('analyzers.ipo_effect','collect_ipo_effect'),
                         ('analyzers.sector_increment','collect'),
                         ('analyzers.board_break_feedback','collect_board_break_feedback'),
                         ('analyzers.strong_stock_feedback','collect'),
                         ('services.sector_adjustment_risk.collector','collect')]:
        import importlib
        monkeypatch.setattr(importlib.import_module(module),name,lambda *a,**k:{'status':'source_failed'})
    sentinel=t.missing(DAY,'test_member_failure')
    calls=[]
    def collect(registry,day,raw,**kwargs):
        calls.append((registry,day,raw.get('date')))
        return sentinel
    monkeypatch.setattr(t,'collect',collect)
    collector=MarketCollector(reg)
    collector._rhythm_analyzer=MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value=[]
    collector._rhythm_analyzer.analyze.return_value=[]
    raw=collector.collect_post_market(DAY)
    assert calls==[(reg,DAY,DAY)] and raw[t.KEY] is sentinel
    assert 'strong_stock_feedback' in raw  # 后续采集继续执行，单块失败不阻断。
    gen=ReportGenerator();gen.daily_dir=tmp_path/'daily'
    markdown,path=gen.generate_post_market(DAY,raw)
    assert '题材启动证据卡' in markdown and 'test_member_failure' in markdown
    saved=yaml.safe_load(Path(path).read_text())
    assert saved['raw_data'][t.KEY]==sentinel


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(code='399006.SZ'),lambda r:r.pop('code'),
    lambda r:r.update(date=PREV),lambda r:r.update(_source='unknown'),
])
def test_index_identity_date_conflicts_and_unverified_source(mutation):
    e=inputs();mutation(e['indices']['shanghai']);b=calc(e)
    assert b['status']=='partial'
    assert [r['name'] for r in b['indices']]==['创业板指']


def test_missing_envelope_cannot_smuggle_values_or_snapshot_date():
    b=t.missing(DAY,'offline');b.update(market={'change_pct':999},indices=[dict(name='假指数',change_pct=999)],membership_date=DAY)
    html,_=t.render_html(b,DAY)
    assert '999' not in html and '假指数' not in html and '成员快照日：未取得' in html
    assert '成员快照日：未取得' in t.render_markdown(None,DAY)
    e=inputs();e['membership']['themes'][THEME]['status']='source_failed'
    assert calc(e)['membership_date'] is None


def test_provider_batch_deadline_preserves_completed_themes(monkeypatch):
    import providers.tushare_provider as p
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,2,20,tzinfo=tz)
    monkeypatch.setattr(p,'datetime',Clock)
    ticks=[0.];monkeypatch.setattr('time.monotonic',lambda:ticks[0])
    provider=TushareProvider({});rows=inputs()['membership']['themes'][THEME]['rows'];calls=[]
    def query(api,**kw):
        calls.append(kw)
        ticks[0]+=5 if kw['ts_code']==THEME else 115
        return pd.DataFrame(rows if kw['offset']==0 else [])
    provider.pro=SimpleNamespace(query=query)
    r=provider.get_theme_member_snapshot(DAY,[THEME,'885002.TI','885003.TI'])
    assert r.data['themes'][THEME]['status']=='complete'
    assert r.data['themes']['885002.TI']['status']=='source_failed'
    assert r.data['themes']['885003.TI']['status']=='source_failed'
    assert len(calls)==3 and all(0<x['_request_timeout']<=15 for x in calls)
