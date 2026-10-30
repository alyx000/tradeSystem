"""Hot-theme aggregate ranking boundaries: absent is unknown, never zero."""
import hashlib
import json
from copy import deepcopy

import pytest

from services.review_theme_visuals import theme_heatmap
from tests.test_review_sector_visuals import inputs
from tests.test_review_identity_flows import DAY


def receipt(day, rows):
    payload=dict(biz_date=day,interface_name='limit_cpt_list',provider='tushare:limit_cpt_list',params=dict(trade_date=day.replace('-','')),rows=rows,summary=dict(row_count=len(rows)))
    text=json.dumps(payload,ensure_ascii=False)
    return dict(id=1,status='success',interface_name='limit_cpt_list',provider='tushare:limit_cpt_list',biz_date=day,target_date=day,row_count=len(rows),payload_json=text,payload_hash=hashlib.sha256(text.encode()).hexdigest())


def fixture():
    raw,m=inputs()
    for day in m['trade_dates']:
        rows=[dict(ts_code='885001.TI',name='主题甲',trade_date=day.replace('-',''),up_nums=5,cons_nums=2,rank='1'),dict(ts_code='885002.TI',name='主题乙',trade_date=day.replace('-',''),up_nums=0,cons_nums=0,rank='2')]
        raw[day.replace('-','')+'_theme_ranking']=receipt(day,rows)
    return raw,m


def test_ranked_counts_retained_and_no_constituents_fabricated():
    raw,m=fixture();before=deepcopy(raw);out=theme_heatmap(raw,m,DAY)
    assert out['status']=='partial' and out['scope']=='archived-ranking-only'
    assert out['constituents_available'] is False and out['selected']==['885001.TI','885002.TI']
    assert out['days'][-1]['counts']=={'885001.TI':5,'885002.TI':0}
    assert out['days'][-1]['entries']['885001.TI']['consecutive']==2
    assert raw==before


def test_absent_theme_is_absent_key_not_zero_and_failed_day_is_null():
    raw,m=fixture();day=m['trade_dates'][0];r=raw[day.replace('-','')+'_theme_ranking'];rows=json.loads(r['payload_json'])['rows'][:1];raw[day.replace('-','')+'_theme_ranking']=receipt(day,rows)
    raw[m['trade_dates'][1].replace('-','')+'_theme_ranking']['status']='source_failed'
    out=theme_heatmap(raw,m,DAY)
    assert '885002.TI' not in out['days'][0]['counts']
    assert out['days'][1]['counts'] is None and out['days'][1]['status']=='source_failed'
    assert out['days'][-1]['counts']['885002.TI']==0


@pytest.mark.parametrize('change',['hash','date','provider','count','duplicate','rank','negative','bool','consecutive'])
def test_corrupt_current_ranking_fails_closed(change):
    raw,m=fixture();key='20261029_theme_ranking';r=raw[key];payload=json.loads(r['payload_json'])
    if change=='hash':r['payload_hash']='0'*64
    elif change=='date':r['target_date']='2026-10-28'
    elif change=='provider':r['provider']='other'
    elif change=='count':r['row_count']=99
    else:
        if change=='duplicate':payload['rows'][1]['ts_code']='885001.TI'
        if change=='rank':payload['rows'][1]['rank']='3'
        if change=='negative':payload['rows'][0]['up_nums']=-1
        if change=='bool':payload['rows'][0]['up_nums']=True
        if change=='consecutive':payload['rows'][0]['cons_nums']=6
        raw[key]=receipt(DAY,payload['rows'])
    out=theme_heatmap(raw,m,DAY)
    assert out['status']=='source_failed' and 'sectors' not in out


def test_missing_source_and_bad_calendar_do_not_reuse_other_days():
    raw,m=fixture()
    assert theme_heatmap(None,m,DAY)['status']=='missing-data'
    del raw['20261029_theme_ranking']
    assert theme_heatmap(raw,m,DAY)['status']=='source_failed'
    raw,m=fixture();m['trade_dates'].append(DAY)
    assert theme_heatmap(raw,m,DAY)['status']=='source_failed'


def stock_sources():
    raw,m=fixture();day=DAY.replace('-','');code=raw[day+'_U']['rows'][0]['ts_code']
    raw[day+'_U']['rows'][0]['name']='样本股'
    raw[day+'_theme_causes']=dict(api='limit_list_ths',params=dict(trade_date=day,limit_type='涨停池'),status='complete',pages=[dict(offset=0,count=1),dict(offset=2000,count=0)],rows=[dict(trade_date=day,ts_code=code,name='样本股',limit_type='涨停池',lu_desc='主题甲+其他原因')])
    raw[day+'_theme_member_samples']=[dict(api='ths_member',params=dict(con_code=code),status='complete',fetched_at=DAY+'T23:00:00+08:00',pages=[1,0],rows=[dict(ts_code='885001.TI',con_code=code,con_name='样本股',is_new='Y')])]
    return raw,m


def test_names_keep_sample_membership_and_causes_separate_from_count():
    raw,m=stock_sources();out=theme_heatmap(raw,m,DAY);e=out['days'][-1]['stock_details']
    assert e['membership_status']=='partial' and e['sampled_stocks']==1
    assert e['members']['885001.TI'][0]['name']=='样本股'
    assert e['reasons']['885001.TI'][0]['cause']=='主题甲+其他原因'
    assert out['days'][-1]['counts']['885001.TI']==5
    assert out['constituents_available'] is False
    assert out['days'][-2]['stock_details']['membership_status']=='missing-data'


@pytest.mark.parametrize('kind',['date','name','pagination','duplicate','api','naive'])
def test_invalid_membership_never_leaks_names(kind):
    raw,m=stock_sources();source=raw['20261029_theme_member_samples'][0]
    if kind=='date':source['fetched_at']='2026-10-30T00:01:00+08:00'
    if kind=='name':source['rows'][0]['con_name']='另一股'
    if kind=='pagination':source['pages']=[1]
    if kind=='duplicate':source['rows']*=2;source['pages']=[2,0]
    if kind=='api':source['api']='other'
    if kind=='naive':source['fetched_at']='2026-10-29T23:00:00'
    e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert e['membership_status']=='source_failed' and e['members']=={}
    assert e['reasons']['885001.TI']


def test_wrong_date_cause_and_failed_receipt_do_not_become_empty_members():
    raw,m=stock_sources();raw['20261029_theme_causes']['rows'][0]['trade_date']='20261028'
    e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert e['reason_status']=='source_failed' and e['reasons']=={}
    assert e['members']['885001.TI']


def test_unmatched_tags_are_not_membership_and_robot_alias_keeps_original_reason():
    raw,m=stock_sources();r=raw['20261029_theme_ranking'];rows=json.loads(r['payload_json'])['rows'];rows[0]['name']='机器人概念';raw['20261029_theme_ranking']=receipt(DAY,rows)
    raw['20261029_theme_causes']['rows'][0]['lu_desc']='焊接机器人+主题乙扩展'
    e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert e['reasons']['885001.TI'][0]['matched_tags']==['焊接机器人']
    assert '885002.TI' not in e['reasons']


def test_unknown_membership_is_a_lead_and_explicitly_inactive_is_excluded():
    raw,m=stock_sources();r=raw['20261029_theme_member_samples'][0]['rows'][0];del r['is_new']
    e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert not e['members'] and e['unverified_members']['885001.TI'][0]['name']=='样本股'
    r['is_new']='N';e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert not e['members'] and not e['unverified_members']
    assert e['reasons']['885001.TI']
    r['is_new']='unknown';e=theme_heatmap(raw,m,DAY)['days'][-1]['stock_details']
    assert e['membership_status']=='source_failed' and not e['unverified_members']
