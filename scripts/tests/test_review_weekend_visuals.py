"""Closed-day report projections must retain dates and real gaps, never blank valid facts."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from services.review_visual_context import load_visual_context, visual_context, validate_visual_context
from services.review_identity_flows import build_identity_flows
from services.review_chart_market import build_chart_market, load_chart_market
from tests.test_review_sector_visuals import inputs
from tests.test_daily_review_html_report import _index_intraday_manifest, _write_chunks, _load_assembler
from tests.test_daily_review_visuals import js

R, F = '2026-11-01', '2026-10-30'
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT/'.agents/skills/daily-review/references/html-report-template'


def closed_context(report=R, fact=F):
    days=(date.fromisoformat(report)-date.fromisoformat(fact)).days
    return visual_context(report, fact, dict(report_date=report, calendar=[
        [(date.fromisoformat(fact)+timedelta(days=i)).isoformat(),int(i==0)] for i in range(days+1)]))


def make_db(path, records):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE trade_calendar(date TEXT PRIMARY KEY,is_open INTEGER,exchange TEXT)')
        conn.executemany('INSERT INTO trade_calendar VALUES(?,?,?)', [(d,n,'SSE') for d,n in records])


def fixtures():
    raw, manifest = inputs()
    text=json.dumps([raw,manifest])
    for before,after in [('29','30'),('28','29'),('27','28'),('26','27'),('23','26')]:
        text=text.replace('2026-10-'+before,'2026-10-'+after).replace('202610'+before,'202610'+after)
    raw,manifest=json.loads(text)
    manifest['report_date']=R
    identity=raw.pop('20261030_identity')
    identity.update(as_of=R, fetched_at=R+'T21:00:00+08:00')
    raw['20261101_identity']=identity
    return raw,manifest


@pytest.mark.parametrize('report,fact', [(R,F),('2026-10-07','2026-09-30'),(F,F)])
def test_calendar_resolves_weekend_long_holiday_and_open_day(tmp_path,report,fact):
    ctx=closed_context(report,fact) if report!=fact else visual_context(report)
    rows=ctx['review_context']['calendar'] if report!=fact else [[report,1]]
    db=tmp_path/'calendar.db';make_db(db,rows)
    assert load_visual_context(db,report)==ctx
    assert js('v.chartDate('+json.dumps(ctx)+','+json.dumps(report)+')')==fact


@pytest.mark.parametrize('rows', [[[F,1],[R,0]], [[F,1],['2026-10-31',2],[R,0]], [[F,1]]])
def test_missing_or_invalid_calendar_does_not_use_old_facts(tmp_path,rows):
    db=tmp_path/'calendar.db';make_db(db,rows)
    ctx=load_visual_context(db,R)
    assert ctx['status']=='source_failed' and ctx['fact_date'] is None
    assert js('v.chartDate('+json.dumps(ctx)+','+json.dumps(R)+')') is None
    raw,m=fixtures();out=build_identity_flows(m,raw,R,visual_context=ctx)
    assert out['new_high']['status']=='source_failed' and 'windows' not in out['new_high']


@pytest.mark.parametrize('edit', ['missing','open_between','wrong_report','future','bool_status'])
def test_forged_closed_window_rejected_by_python_and_js(edit):
    ctx=closed_context()
    if edit=='missing':ctx['review_context']['calendar'].pop(1)
    if edit=='open_between':ctx['review_context']['calendar'][1][1]=1
    if edit=='wrong_report':ctx['review_context']['report_date']=F
    if edit=='future':ctx['fact_date']='2026-11-02'
    if edit=='bool_status':ctx['review_context']['calendar'][0][1]=True
    with pytest.raises(ValueError):validate_visual_context(ctx,R)
    assert js('v.chartDate('+json.dumps(ctx)+','+json.dumps(R)+')') is None


def test_open_day_source_failure_never_reuses_prior_facts():
    raw,m=fixtures();m['report_date']='2026-11-02'
    out=build_identity_flows(m,raw,'2026-11-02',visual_context=visual_context('2026-11-02'))
    assert out['new_high']['status']=='source_failed'
    assert out['sector_limits']['status']=='missing-data'


def test_weekend_identity_flow_uses_fact_day_and_separate_current_classification():
    raw,m=fixtures();before=deepcopy((raw,m));out=build_identity_flows(m,raw,R,visual_context=closed_context())
    assert (raw,m)==before
    assert out['fact_date']==F and out['classification_as_of']==R
    assert out['new_high']['status']=='complete' and out['new_high']['date']==F
    assert out['new_high']['windows']['60']['retained']==['600001.SH']
    assert out['new_high']['name_as_of']==R
    for key in ('sector_limits','sector_heatmap','sector_breadth','limit_timeline'):
        assert out[key]['status']=='partial' and out[key]['classification_as_of']==R
        assert out[key]['date']==F
    assert out['sector_limits']['totals']=={'previous':4,'current':3}
    assert out['sector_heatmap']['dates']==m['trade_dates']
    assert out['limit_timeline']['sample']==3


def test_invalid_fact_day_members_are_not_replaced_with_current_snapshot():
    raw,m=fixtures();raw['20261030_identity']=dict(status='source_failed')
    out=build_identity_flows(m,raw,R,visual_context=closed_context())
    assert out['classification_as_of']==F
    assert out['sector_limits']['status']=='source_failed'
    assert out['sector_heatmap']['status']=='source_failed'


def test_current_names_cannot_prove_historical_ladder_eligibility():
    raw,m=fixtures()
    for r in raw['20261030_daily']['rows']:r.pop('name',None)
    out=build_identity_flows(m,raw,R,visual_context=closed_context())
    assert [r['outcome'] for r in out['ladder']['records']]==['promoted','promoted','unknown','unknown']


def test_capacity_labels_parse_without_erasing_real_missingness():
    value='收盘196.000；MA5 234.094 / MA10 258.977 / MA20 308.135 / MA60 382.158 / MA144 — / MA233 未计算'
    assert js('v.capacityValues('+json.dumps(value)+')')==dict(close=196,averages=[234.094,258.977,308.135,382.158,None,None])
    assert js('v.capacityValues("10;11/12/13/14/15/16")')==dict(close=10,averages=[11,12,13,14,15,16])
    assert js('v.capacityValues("未计算；MA10 20 / MA5 10")')==dict(close=None,averages=[None]*6)


def market_snapshot():
    return dict(date=F,source='daily/'+F+'/post-market.yaml',premium_source=dict(
        date='2026-10-29',source='daily/2026-10-29/post-market.yaml',backfill=dict(
            prev_date='2026-10-29',trade_date=F,capacity_top10=dict(count=10,premium_mean=-5,premium_median=-4))),raw_data=dict(
        board_break_feedback=dict(status='ok',source_connected_date='2026-10-28',break_date='2026-10-29',outcome_date=F,
            sample_count=1,open_mean_pct=-2,open_median_pct=-2,close_mean_pct=3,close_median_pct=3,
            details=[dict(code='600000',name='测试',feedback_open_pct=-2,feedback_close_pct=3)]),
        style_factors=dict(popularity_provenance=dict(source_trade_date='2026-10-29',outcome_trade_date=F),
            premium_snapshot={'capacity_top10':dict(count=10,premium_mean=-5,premium_median=-4)})))


def test_archive_chart_projection_keeps_missing_groups_and_is_source_checked(tmp_path):
    raw,m=fixtures();snapshot=market_snapshot();out=build_chart_market(snapshot,m,F)
    assert out['board_feedback']['status']=='complete'
    assert out['board_feedback']['points']==[dict(code='600000',label='测试 600000',open=-2,close=3)]
    assert out['style_feedback']['status']=='partial'
    assert out['style_feedback']['rows'][1]['mean'] is None
    snapshot['raw_data']['board_break_feedback']['close_mean_pct']=8
    assert build_chart_market(snapshot,m,F)['board_feedback']['status']=='source_failed'
    assert load_chart_market(tmp_path,F)['status']=='source_failed'


@pytest.mark.parametrize('report,wrong_day', [(R,F),(R,'2026-10-31'),(R,R),(F,F)])
def test_calendar_rejects_non_sse_and_mixed_market_windows(tmp_path,report,wrong_day):
    db=tmp_path/'calendar.db';make_db(db,[[F,1],['2026-10-31',0],[R,0]])
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE trade_calendar SET exchange=? WHERE date=?',('NYSE',wrong_day))
    ctx=load_visual_context(db,report)
    assert ctx['status']=='source_failed' and ctx['fact_date'] is None


def test_premium_dates_are_independent_of_popularity_and_stale_style_snapshot(tmp_path):
    import yaml
    raw,m=fixtures();snapshot=market_snapshot()
    snapshot['raw_data']['style_factors']={}
    day=tmp_path/F;day.mkdir()
    (day/'post-market.yaml').write_text(yaml.safe_dump(dict(date=F,raw_data=snapshot['raw_data'])))
    prev=tmp_path/'2026-10-29';prev.mkdir()
    original=dict(date='2026-10-29',premium_backfill=snapshot['premium_source']['backfill'])
    path=prev/'post-market.yaml';path.write_text(yaml.safe_dump(original))
    loaded=load_chart_market(tmp_path,F,previous_date='2026-10-29')
    out=build_chart_market(loaded,m,F)
    assert out['style_feedback']['status']=='partial'
    assert out['style_feedback']['rows'][0]['mean']==-5
    assert out['style_feedback']['source']=='daily/2026-10-29/post-market.yaml'
    original['premium_backfill']['trade_date']='2026-10-28'
    path.write_text(yaml.safe_dump(original))
    out=build_chart_market(load_chart_market(tmp_path,F,previous_date='2026-10-29'),m,F)
    assert out['style_feedback']['status']=='source_failed'
    assert out['board_feedback']['status']=='complete'
    path.unlink()
    out=build_chart_market(load_chart_market(tmp_path,F,previous_date='2026-10-29'),m,F)
    assert out['style_feedback']['status']=='source_failed'
    assert out['board_feedback']['status']=='complete'


def test_minute_sidecar_and_formal_render_accept_verified_weekend(tmp_path):
    assembler=_load_assembler();ctx=closed_context();payload=_index_intraday_manifest(report_date=F)
    payload.update(report_date=R,visual_context=ctx)
    assembler._validate_index_intraday_manifest_payload(payload,R)
    _write_chunks(tmp_path,date=R)
    html=assembler.render_report(tmp_path,R,index_intraday_manifest=payload,visual_context=ctx)
    parsed=assembler._parse_report(html)
    assert parsed.visual_context==ctx
    assert 'data-as-of="'+F+'" data-reviewed-through="'+R+'"' in html
    assert html.count('review-visual-context"')==1
    # Altering a complete row to the report date cannot satisfy the fact-day gate.
    payload['rows'][0]['last_bar_at']=R+' 15:00'
    with pytest.raises(assembler.ReportValidationError):assembler._validate_index_intraday_manifest_payload(payload,R)


def test_historical_minutes_need_calendar_proof():
    assembler=_load_assembler();payload=_index_intraday_manifest(report_date=F);payload['report_date']=R
    with pytest.raises(assembler.ReportValidationError):assembler._validate_index_intraday_manifest_payload(payload,R)


def test_minute_builder_uses_fact_day_and_failed_calendar_never_fetches(monkeypatch):
    spec=importlib.util.spec_from_file_location('weekend_builder',TEMPLATE/'build_index_intraday_manifest.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    import pytdx.hq
    class Api:
        def __init__(self,**kw):pass
        def connect(self,*a,**kw):return True
        def disconnect(self):pass
    monkeypatch.setattr(pytdx.hq,'TdxHq_API',Api)
    seen=[]
    def fetch(*a,**kw):seen.append(kw['report_date']);return [{}]
    monkeypatch.setattr(builder,'_fetch_bars_through',fetch)
    monkeypatch.setattr(builder,'calculate_row',lambda bars,**kw:dict(last_bar_at=kw['report_date']+' 15:00'))
    out=builder.build_manifest(R,servers=[('fixture',1)],visual_context=closed_context())
    assert seen==[F]*18 and out['as_of']==F and out['report_date']==R and out['complete']
    ctx=dict(schema='review-visual-context-v1',report_date=R,fact_date=None,status='source_failed',reason='日历缺失')
    seen.clear();out=builder.build_manifest(R,visual_context=ctx)
    assert out['status']=='failed' and seen==[]


def test_explicit_archived_minutes_bind_review_date_without_mutating_source(tmp_path):
    assembler=_load_assembler();path=tmp_path/'historical.json'
    original=_index_intraday_manifest(report_date=F);path.write_text(json.dumps(original))
    before=path.read_bytes()
    bound=assembler.load_index_intraday_manifest(path,R,visual_context=closed_context())
    assert path.read_bytes()==before
    assert bound['report_date']==R and bound['as_of']==F and bound['source_report_date']==F
    assert bound['rows']==original['rows'] and bound['generated_at']==original['generated_at']
    assert len(bound['source_manifest_sha256'])==64
    with pytest.raises(assembler.ReportValidationError):assembler.load_index_intraday_manifest(path,'2026-11-02',visual_context=visual_context('2026-11-02'))


def test_context_json_round_trip_and_tampering_do_not_bypass_calendar(tmp_path):
    assembler=_load_assembler();ctx=closed_context();_write_chunks(tmp_path,date=R)
    minute=_index_intraday_manifest(report_date=F);minute.update(report_date=R,visual_context=ctx)
    html=assembler.render_report(tmp_path,R,index_intraday_manifest=minute,visual_context=ctx)
    with pytest.raises(assembler.ReportValidationError,match='独立核验'):
        assembler.validate_report(html)
    broken=html.replace('data-as-of="'+F+'" data-reviewed-through="'+R+'"','data-reviewed-through="'+R+'"',1)
    with pytest.raises(assembler.ReportValidationError):assembler._parse_report(broken)
    ctx=dict(schema='review-visual-context-v1',report_date=R,fact_date=None,status='source_failed',reason='路径<a>失败')
    fragment=assembler.attach_visual_context('<head></head>',R,ctx)
    import re
    encoded=re.search(r'<script[^>]*>(.*?)</script>',fragment).group(1)
    assert '<a>' not in encoded and json.loads(encoded)==ctx


@pytest.mark.parametrize('status,reason,expected', [('ok','no_board_breaks','complete'),('ok',None,'source_failed'),('partial',None,'partial')])
def test_empty_feedback_is_distinct_from_missing_or_failed(status,reason,expected):
    raw,m=fixtures();snapshot=market_snapshot();feedback=snapshot['raw_data']['board_break_feedback']
    feedback.update(status=status,empty_reason=reason,details=[],sample_count=0,break_count=0)
    out=build_chart_market(snapshot,m,F)['board_feedback']
    assert out['status']==expected
    if expected=='complete':assert out['empty'] is True and out['points']==[]
    if expected=='partial':assert out['empty'] is False and out['points']==[]


def test_feedback_duplicate_identity_and_boolean_count_fail_closed():
    raw,m=fixtures();snapshot=market_snapshot();feedback=snapshot['raw_data']['board_break_feedback']
    feedback['details'].append(dict(feedback['details'][0],code='600000.SH'));feedback['sample_count']=2
    assert build_chart_market(snapshot,m,F)['board_feedback']['status']=='source_failed'
    feedback['details'].pop();feedback['sample_count']=True
    assert build_chart_market(snapshot,m,F)['board_feedback']['status']=='source_failed'


def test_cli_uses_selected_calendar_and_explicit_archived_minutes(tmp_path):
    assembler = _load_assembler()
    chunks = tmp_path / 'chunks'
    paths = _write_chunks(chunks, date=R)
    from tests.test_daily_review_html_report import _new_high_manifest, _write_new_high_manifest, _capacity_manifest, _write_capacity_manifest
    _write_new_high_manifest(chunks, date=R, payload=_new_high_manifest(report_date=R, as_of=F))
    _write_capacity_manifest(chunks, date=R, payload=_capacity_manifest(report_date=R, as_of=F))
    import re
    section = paths['s456'].read_text()
    section = re.sub(r'(data-new-high-structure="[^"]+" data-as-of=")'+R, r'\g<1>'+F, section)
    section = re.sub(r'(data-capacity-health="[^"]+" data-as-of=")'+R, r'\g<1>'+F, section)
    paths['s456'].write_text(section)
    db = tmp_path / 'calendar.db'
    make_db(db, [(F, 1), ('2026-10-31', 0), (R, 0)])
    source = tmp_path / 'minutes-fact-day.json'
    source.write_text(json.dumps(_index_intraday_manifest(report_date=F)))
    before = source.read_bytes()
    output = tmp_path / 'review.html'
    assert assembler.main([str(chunks), R, '--trade-db', str(db),
                           '--index-intraday-manifest', str(source),
                           '--daily-dir', str(tmp_path/'empty-daily'),
                           '--output', str(output)]) == 0
    html = output.read_text()
    assert 'data-as-of="'+F+'" data-reviewed-through="'+R+'"' in html
    assert source.read_bytes() == before
    assert '六指数 15/30/60 分钟结构（18/18 组）' in html


@pytest.mark.parametrize('text,expected', [
    ('+5.00% → -14.19% / 216.08亿', -14.19),
    ('-5.00% → +2.00% / 10亿', 2),
    ('+5.00% → 未计算', None),
    ('0.00% / 10亿', 0), ('+2.00% / 10亿', 2), ('未计算', None),
])
def test_capacity_today_change_never_reuses_previous_value(text, expected):
    assert js('v.capacityChange('+json.dumps(text)+')') == expected
