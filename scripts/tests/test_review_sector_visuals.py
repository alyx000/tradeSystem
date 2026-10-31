"""Offline boundaries for sector report projections; no provider calls."""
from copy import deepcopy

import pytest

from services.review_sector_visuals import build_sector_visuals, sector_heatmap, sector_breadth, limit_timeline, _seal_time
from tests.test_review_identity_flows import classified_raw, manifest, source, DAY


def inputs():
    r,m=classified_raw(),manifest()
    m['trade_dates']=['2026-10-23','2026-10-26','2026-10-27','2026-10-28',DAY]
    for day in ['20261023','20261026','20261027']:
        rows=deepcopy(r['20261028_U']['rows'])
        for row in rows:row['trade_date']=day
        r[day+'_U']=source('limit_list_d',day,rows,'U')
        r[day+'_st']=source('stock_st',day,[dict(ts_code='688999.SH',trade_date=day)])
    for i,row in enumerate(r['20261029_U']['rows']):
        row.update(first_time=['92500','92502','130000'][i],last_time=['92500','150000','140000'][i],open_times=i)
    return r,m


def test_five_day_heatmap_uses_fixed_current_classification_and_cohort():
    r,m=inputs();out=sector_heatmap(r,m,DAY)
    assert out['dates']==m['trade_dates']
    assert [d['total'] for d in out['days']]==[4,4,4,4,3]
    assert out['selected']==['801001.SI','801002.SI']
    assert out['days'][0]['counts']=={'801001.SI':2,'801002.SI':2}
    assert out['days'][-1]['counts']=={'801001.SI':2,'801002.SI':1}
    assert out['status']=='complete'


def test_failed_day_is_null_while_verified_empty_day_is_zero():
    r,m=inputs();r['20261023_U']['status']='source_failed'
    r['20261026_U']=source('limit_list_d','20261026',[],'U')
    out=sector_heatmap(r,m,DAY)
    assert out['status']=='partial'
    assert out['days'][0]['counts'] is None and out['days'][0]['total'] is None
    assert out['days'][1]['counts']=={} and out['days'][1]['total']==0 and out['days'][1]['status']=='complete'


def test_unknown_industry_keeps_daily_total_and_marks_lower_bound():
    r,m=inputs();r['20261029_identity']['rows']=[x for x in r['20261029_identity']['rows'] if x['ts_code']!='600002.SH']
    out=sector_heatmap(r,m,DAY)
    assert out['days'][0]['total']==4 and out['days'][0]['status']=='partial'
    assert out['days'][0]['unclassified']==['600002.SH']
    assert sum(out['days'][0]['counts'].values())==4


def test_breadth_denominator_excludes_zero_volume_missing_quotes_and_uses_close():
    r,m=inputs();q=r['20261029_daily']['rows']
    q[0]['close']=11;q[1]['close']=9;q[2]['close']=10;q[3]['vol']=0;q[4]['ts_code']='688888.SH'
    out=sector_breadth(r,m,DAY);a,b=out['sectors']
    assert (a['up'],a['down'],a['flat'],a['unknown'],a['valid'],a['sample'])==(1,1,0,0,2,2)
    assert (b['up'],b['down'],b['flat'],b['unknown'],b['valid'],b['sample'])==(0,0,1,2,1,3)
    assert all(s['sample']==s['valid']+s['unknown'] for s in out['sectors'])
    assert all(s['valid']==s['up']+s['flat']+s['down'] for s in out['sectors'])
    assert out['status']=='partial'


def test_breadth_st_union_exclusion_is_visible_and_does_not_silently_mix():
    r,m=inputs();r['20261029_identity']['rows'][2]['name']='*ST测试'
    r['20261029_st']=source('stock_st','20261029',[dict(ts_code='600003.SH',trade_date='20261029')])
    out=sector_breadth(r,m,DAY)
    assert out['eligibility']==dict(st_list_only=['600003.SH'],name_st_only=['600002.SH'],st_union_count=2)
    assert not {'600002.SH','600003.SH'} & {x['code'] for s in out['sectors'] for x in s['records']}


@pytest.mark.parametrize('value,expected',[('92500',33900),('92502',33902),('093000',34200),('113000',41400),('130000',46800),('150000',54000),('120000',None),('156000',None),('150001',None),('092459',None),('',None),(None,None),(92500,None),('9:25',None)])
def test_seal_timestamps_normalize_five_digits_and_reject_invalid(value,expected):
    assert _seal_time(value)==expected


def test_timeline_preserves_all_stock_names_and_auction_events():
    r,m=inputs();out=limit_timeline(r,m,DAY)
    assert out['status']=='complete' and out['sample']==3
    assert [x['first'] for x in out['records']]==[33900,33902,46800]
    assert [x['open_times'] for x in out['records']]==[0,1,2]
    assert all(x['name'] and x['code'] for x in out['records'])


def test_invalid_times_and_counts_stay_unknown_not_zero_or_dropped():
    r,m=inputs();r['20261029_U']['rows'][0].update(first_time='145900',last_time='093000',open_times=True)
    out=limit_timeline(r,m,DAY);record=next(x for x in out['records'] if x['code']=='600000.SH')
    assert out['status']=='partial' and out['sample']==3
    assert record['first'] is None and record['last'] is None and record['open_times'] is None
    assert len(record['issues'])==2


def test_duplicate_quotes_fail_only_breadth_not_other_views():
    r,m=inputs();r['20261029_daily']['rows'][1]['ts_code']='600000.SH'
    out=build_sector_visuals(r,m,DAY)
    assert out['sector_breadth']['status']=='source_failed'
    assert out['sector_heatmap']['status']==out['limit_timeline']['status']=='complete'


def test_calendar_and_snapshot_fail_closed_and_inputs_not_mutated():
    r,m=inputs();before=deepcopy((r,m));build_sector_visuals(r,m,DAY);assert (r,m)==before
    m['trade_dates'].append(DAY)
    assert all(v['status']=='source_failed' for v in build_sector_visuals(r,m,DAY).values())
    assert all(v['status']=='missing-data' for v in build_sector_visuals(None,m,DAY).values())
