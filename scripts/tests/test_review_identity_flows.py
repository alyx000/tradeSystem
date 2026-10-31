"""Identity intersections and fixed-cohort ladder transitions, offline fixtures."""
from copy import deepcopy

import pytest

from services.review_identity_flows import build_identity_flows, high_flows, ladder_flows, sector_limit_counts

DAY = '2026-10-29'
PREV = '2026-10-28'


def manifest():
    return dict(status='complete', complete=True, report_date=DAY, as_of=DAY,
                prev_as_of=PREV, basis='rolling-adjusted-high', trade_dates=[PREV, DAY],
                previous_codes={w:['600000.SH','600001.SH'] for w in ['60','120','250']},
                current_codes={w:['600001.SH','600002.SH'] for w in ['60','120','250']},
                counts={w:dict(previous=2,current=2) for w in ['60','120','250']}, sixty_day_overlap=1)


def source(api, day, rows, kind=None):
    pages = [dict(offset=i, count=min(2000,len(rows)-i)) for i in range(0,len(rows),2000)]
    pages.append(dict(offset=len(pages)*2000,count=0))
    params = dict(trade_date=day)
    if kind: params['limit_type']=kind
    return dict(api=api,params=params,status='complete',rows=rows,pages=pages)


def raw():
    before, today = PREV.replace('-',''), DAY.replace('-','')
    def limit(code, height, day):
        return dict(ts_code=code,name='股票'+code[:6],trade_date=day,limit_times=height,limit='U')
    p = [limit(f'{600000+i}.SH',height,before) for i,height in enumerate([1,2,3,5])]
    c = [limit('600000.SH',2,today),limit('600001.SH',3,today),limit('600004.SH',1,today)]
    q = [dict(ts_code=f'{600000+i}.SH',name=f'股票{600000+i}',trade_date=today,open=10,close=10,pre_close=10,vol=100,amount=1000) for i in range(4000)]
    return {before+'_U':source('limit_list_d',before,p,'U'),today+'_U':source('limit_list_d',today,c,'U'),
            before+'_st':source('stock_st',before,[dict(ts_code='688999.SH',trade_date=before)]),
            today+'_st':source('stock_st',today,[dict(ts_code='688999.SH',trade_date=today)]),
            today+'_daily':source('daily',today,q)}


def test_high_identity_partition_not_count_difference():
    r=high_flows(manifest(),DAY)
    assert r['status']=='complete'
    assert r['windows']['60']['retained']==['600001.SH']
    assert r['windows']['60']['exited']==['600000.SH']
    assert r['windows']['60']['added']==['600002.SH']


@pytest.mark.parametrize('key,value', [('as_of',PREV),('status','partial'),('basis','all-time'),('sixty_day_overlap',2)])
def test_invalid_high_source_is_not_empty(key,value):
    m=manifest();m[key]=value
    assert high_flows(m,DAY)['status']=='source_failed'


def test_duplicate_codes_and_inconsistent_counts_rejected():
    m=manifest();m['current_codes']['60']=['600001.SH','600001.SH']
    assert high_flows(m,DAY)['status']=='source_failed'
    m=manifest();m['counts']['60']['current']=3
    assert high_flows(m,DAY)['status']=='source_failed'


def test_ladder_keeps_yesterday_cohort_and_excludes_new_first_board():
    r=ladder_flows(raw(),manifest(),DAY)
    assert r['status']=='complete' and r['sample']==4
    assert [x['outcome'] for x in r['records']]==['promoted','promoted','broken','broken']
    assert '600004.SH' not in [x['code'] for x in r['records']]
    assert all(g['total']==sum(g[k] for k in ['promoted','broken','unknown','excluded']) for g in r['groups'])


def test_untraded_quote_st_change_and_inconsistent_height_are_not_breaks():
    r=raw();r['20261029_daily']['rows'][2]['vol']=0
    r['20261029_st']=source('stock_st','20261029',[dict(ts_code='600003.SH',trade_date='20261029')])
    r['20261029_U']['rows'][0]['limit_times']=3
    out=ladder_flows(r,manifest(),DAY)
    assert out['status']=='partial'
    assert [x['outcome'] for x in out['records']]==['unknown','promoted','unknown','excluded']


def test_missing_quote_is_unknown_even_with_full_market_source():
    r=raw();q=r['20261029_daily']['rows'];q[2]['ts_code']='688888.SH'
    out=ladder_flows(r,manifest(),DAY)
    assert out['records'][2]['outcome']=='unknown' and out['status']=='partial'


def test_current_retired_identity_not_in_up_list_is_excluded():
    r=raw();r['20261029_daily']['rows'][2]['name']='某某退'
    out=ladder_flows(r,manifest(),DAY)
    assert out['records'][2]['outcome']=='excluded' and out['status']=='partial'


def test_missing_current_names_are_unknown_not_breaks():
    r=raw()
    for row in r['20261029_daily']['rows']:row.pop('name')
    out=ladder_flows(r,manifest(),DAY)
    assert [x['outcome'] for x in out['records']]==['promoted','promoted','unknown','unknown']
    r['20261029_identity']=dict(api='index_member_all',status='complete',as_of=DAY,fetched_at=DAY+'T23:00:00+08:00',rows=[dict(ts_code=f'{600000+i}.SH',name=f'股票{600000+i}',is_new='Y') for i in range(4)])
    assert ladder_flows(r,manifest(),DAY)['status']=='complete'
    r['20261029_identity']['as_of']=PREV
    assert ladder_flows(r,manifest(),DAY)['status']=='source_failed'


@pytest.mark.parametrize('change', ['duplicate','wrong_date','failed','incomplete_page','small_market','bad_limit','bool_height'])
def test_ladder_rejects_broken_receipts(change):
    r=raw()
    if change=='duplicate':r['20261029_U']['rows'][1]['ts_code']='600000.SH'
    elif change=='wrong_date':r['20261029_U']['rows'][1]['trade_date']='20261028'
    elif change=='failed':r['20261029_U']['status']='source_failed'
    elif change=='incomplete_page':r['20261029_U']['pages'].pop()
    elif change=='small_market':r['20261029_daily']=source('daily','20261029',r['20261029_daily']['rows'][:3])
    elif change=='bad_limit':r['20261029_U']['params']['limit_type']='D'
    elif change=='bool_height':r['20261029_U']['rows'][0]['limit_times']=True
    assert ladder_flows(r,manifest(),DAY)['status']=='source_failed'


def test_missing_inputs_and_non_adjacent_calendar_never_make_zero_counts():
    out=build_identity_flows(None,None,DAY)
    assert out['new_high']['status']=='missing-data' and out['ladder']['status']=='missing-data'
    m=manifest();m['trade_dates']=[PREV,'2026-10-28x',DAY]
    assert ladder_flows(raw(),m,DAY)['status']=='source_failed'


def test_source_input_objects_are_not_mutated():
    r,m=raw(),manifest();before=deepcopy((r,m))
    build_identity_flows(m,r,DAY)
    assert (r,m)==before


def classified_raw():
    r=raw()
    r['20261029_identity']=dict(api='index_member_all',status='complete',as_of=DAY,fetched_at=DAY+'T23:00:00+08:00',rows=[
        dict(ts_code=f'{600000+i}.SH',name=f'股票{600000+i}',is_new='Y',l2_code='801001.SI' if i<2 else '801002.SI',l2_name='行业甲' if i<2 else '行业乙') for i in range(5)])
    return r


def test_sector_partition_conserves_both_days_and_board_types():
    r=classified_raw();r['20261029_identity']['rows'].append(deepcopy(r['20261029_identity']['rows'][0]))
    out=sector_limit_counts(r,manifest(),DAY)
    assert out['status']=='complete' and out['totals']=={'previous':4,'current':3}
    assert [(s['name'],s['previous_count'],s['current_count'],s['first_count'],s['consecutive_count']) for s in out['sectors']]==[('行业甲',2,2,0,2),('行业乙',2,1,1,0)]
    assert out['classification_as_of']==DAY


def test_sector_mapping_conflict_is_once_unclassified_not_dropped():
    r=classified_raw();extra=deepcopy(r['20261029_identity']['rows'][0]);extra['l2_code']='801099.SI';extra['l2_name']='冲突行业';r['20261029_identity']['rows'].append(extra)
    out=sector_limit_counts(r,manifest(),DAY)
    assert out['status']=='partial'
    assert out['unclassified']=={'previous':['600000.SH'],'current':['600000.SH']}
    assert sum(s['current_count'] for s in out['sectors'])==3
    assert sum(s['previous_count'] for s in out['sectors'])==4


def test_sector_missing_snapshot_is_failure_not_zero():
    out=sector_limit_counts(raw(),manifest(),DAY)
    assert out['status']=='source_failed' and 'totals' not in out


def test_legacy_delisted_sw_identity_does_not_block_current_universe():
    r=classified_raw();r['20261029_identity']['rows'].append(dict(ts_code='T00018.SH',name='上港集箱(退市)',is_new='Y',l2_code='801992.SI',l2_name='航运港口'))
    assert sector_limit_counts(r,manifest(),DAY)['status']=='complete'
    assert ladder_flows(r,manifest(),DAY)['status']=='complete'
    r['20261029_identity']['rows'][-1]['name']='未知'
    assert sector_limit_counts(r,manifest(),DAY)['status']=='source_failed'


def test_high_names_cover_all_windows_and_missing_names_do_not_change_counts():
    r=classified_raw();m=manifest()
    high=build_identity_flows(m,r,DAY)['new_high']
    assert high['name_status']=='complete'
    assert high['names']=={'600000.SH':'股票600000','600001.SH':'股票600001','600002.SH':'股票600002'}
    r['20261029_identity']['rows']=[row for row in r['20261029_identity']['rows'] if row['ts_code']!='600002.SH']
    missing=build_identity_flows(m,r,DAY)['new_high']
    assert missing['name_status']=='partial' and '600002.SH' not in missing['names']
    assert missing['windows']==high['windows']
