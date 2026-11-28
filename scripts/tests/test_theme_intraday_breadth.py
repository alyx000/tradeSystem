from datetime import datetime
import json
import sqlite3

import pytest

from analyzers import tracked_theme_members as tracked
from services.board_break_intraday.data import TZ, digest
from services import theme_intraday_breadth as b
from tools.theme_intraday_breadth import save


DAY, PRIOR = '2026-11-27', '2026-11-26'
CUTOFF = datetime.fromisoformat(DAY+'T09:30:00+08:00')
CODE, A, B = '885959.TI', '600001.SH', '600002.SH'


def raw_members(day):
    return dict(date=day, fetched_at=day+'T20:01:00+08:00',
                themes={CODE: dict(status='complete', pages=[2, 0], rows=[
                    dict(ts_code=CODE, con_code=code, con_name=name, is_new='Y') for code,name in ((A,'甲'), (B,'乙'))])})


def raw_concept(snapshot_day):
    return dict(date=PRIOR, concept_moneyflow_ths={'_source': 'tushare:moneyflow_cnt_ths',
                'data': [dict(trade_date='20261126', name='PCB概念', ts_code=CODE)]},
                theme_launch=dict(trade_date=PRIOR, evidence=dict(membership_source=tracked.SOURCE,
                                                               membership=raw_members(snapshot_day))))


def sample(clock, data, source='sina'):
    at = DAY+'T'+clock+':00+08:00'
    return dict(trade_date=DAY, captured_at=at, batch=clock, sha256='x', source=source,
                context_sha256='same', interval_minutes=10,
                stocks={c:dict(price=p, pre_close=10, amount=a, quote_at=at) for c,(p,a) in data.items()})


def test_only_preopen_identities_and_previous_dated_members():
    pre = raw_concept(PRIOR)
    rows, groups = b.concept_groups(pre, PRIOR, [dict(requested='PCB'), dict(requested='机器人')], CUTOFF)
    assert 'ths:'+CODE in groups and groups['ths:'+CODE]['members']=={A:'甲', B:'乙'}
    assert [r['status'] for r in rows] == ['verified', 'identity_unverified']
    rows, groups = b.concept_groups(raw_concept(DAY), PRIOR, [dict(requested='PCB')], CUTOFF)
    assert not groups and rows[0]['status']=='members_unavailable'
    with pytest.raises(ValueError):
        b.concept_groups(pre, DAY, [], CUTOFF)
    pre['theme_launch']['evidence']['membership']['fetched_at'] = DAY+'T10:30:00+08:00'
    assert not b.concept_groups(pre, PRIOR, [dict(requested='PCB')], CUTOFF)[1]
    pre['theme_launch']['evidence']['membership'].pop('fetched_at')
    assert not b.concept_groups(pre, PRIOR, [dict(requested='PCB')], CUTOFF)[1]


def test_late_created_or_regenerated_prior_envelope_is_ineligible():
    conn = sqlite3.connect(':memory:')
    conn.execute('create table daily_market (date text, raw_data text, created_at text)')
    data = dict(date=PRIOR, generated_at=PRIOR+'T22:00:00', raw_data=raw_concept(PRIOR))
    conn.execute('insert into daily_market values (?,?,?)', (PRIOR, json.dumps(data), DAY+'T02:00:00'))
    with pytest.raises(ValueError):
        b.previous_archive(conn, PRIOR, CUTOFF)
    conn.execute('update daily_market set created_at=?', (PRIOR+'T14:00:00',))
    assert b.previous_archive(conn, PRIOR, CUTOFF)[0]['date']==PRIOR
    data['generated_at']=DAY+'T20:01:00'
    conn.execute('update daily_market set raw_data=?',(json.dumps(data),))
    with pytest.raises(ValueError):
        b.previous_archive(conn, PRIOR, CUTOFF)


def test_direction_and_amount_have_separate_denominators():
    first = sample('09:32', {A:(10,100), B:(10, 200)})
    second = sample('09:42', {A:(11,120), B:(9, 190)})
    p = b.point({A:'甲', B:'乙'}, {}, first, second)
    assert p['up_ratio_pct']==50 and p['price_valid']==2 and p['median_pct']==0
    assert p['amount_valid']==1 and p['observed_amount_delta_yuan']==20 and p['full_amount_delta_yuan'] is None
    second['stocks'][B]['amount'] = 250
    assert b.point([A,B],{},first,second)['full_amount_delta_yuan']==70
    second['source']='other'
    assert b.point([A,B],{},first,second)['observed_amount_delta_yuan'] is None


def test_no_cross_session_or_sparse_windows_and_suspensions():
    first = sample('11:22', {A:(10,100)})
    second = sample('13:02', {A:(10,500)})
    p = b.point([A,B],{B:dict(full_day=True)},first,second)
    assert p['expected']==2 and p['price_valid']==1 and p['up']==0 and p['verified_full_day_halts']==[B]
    assert p['full_amount_delta_yuan'] is None and not p['adjacent_same_session']
    assert b.point([A,B],{},first,second)['missing_quotes']==[B]
    assert b.point([A],{A:{}},first,second)['conflicting_halt']==[A]
    assert not b.point([A],{},sample('09:32', {A:(10,100)}),sample('09:52', {A:(10,500)}))['adjacent_same_session']


def report(rows):
    clock = '09:32'
    snap = sample(clock,{A:(11,100)},source='sina')
    snap['raw_rows'] = rows
    snap['raw_sha256'] = digest(rows)
    context = {'universe':[A]}
    snap['context_sha256']=digest(context)
    return dict(snapshot=snap, context=context, trade_date=DAY, input_by='USER', captured_at=snap['captured_at'])


def raw_quote():
    return dict(code=A, price=11., pre_close=10., amount=100., quote_date=DAY, quote_time='09:32:00')


def batch(tmp_path, r, label):
    from hashlib import sha256
    folder=tmp_path/DAY/label
    folder.mkdir(parents=True)
    raw = json.dumps(r).encode()
    (folder/'report.json').write_bytes(raw)
    (folder/'receipt.json').write_text(json.dumps(dict(sha256=sha256(raw).hexdigest(),batch=label,trade_date=DAY,input_by='USER')))
    return folder


def test_preserves_real_time_excludes_duplicates_wrong_close_timestamp_and_corrupt_receipt(tmp_path):
    raw=raw_quote()
    valid=report([raw])
    f=batch(tmp_path, valid, '1')
    known,gaps=b.batches(tmp_path,DAY,{A})
    assert not gaps and list(known)==[572] and known[572]['stocks'][A]['pre_close']==10
    double=batch(tmp_path, valid, '2')
    assert not b.batches(tmp_path,DAY,{A})[0]
    (double/'report.json').write_text('bad')
    assert any('哈希' in x for x in b.batches(tmp_path,DAY,{A})[1])
    for invalid_rows in ([raw,raw],[dict(raw,pre_close=0)], [dict(raw,quote_time='09:28:00')]):
        r=report(invalid_rows)
        assert not b._compact(r,dict(batch='1',sha256='x',trade_date=DAY),{A},DAY)[1]['stocks']
    with pytest.raises(ValueError):
        b._compact(valid,dict(batch='1',sha256='x',trade_date=PRIOR),{A},PRIOR)
    valid['snapshot']['raw_sha256']='wrong'
    with pytest.raises(ValueError):
        b._compact(valid,dict(batch='1',sha256='x',trade_date=DAY),{A},DAY)


def test_retains_better_report(tmp_path):
    snapshot=sample('09:32',{A:(10,100)})
    base=dict(schema=b.SCHEMA,trade_date=DAY,input_by='USER',status='partial',gaps=[],groups={},batches=[dict(slot='09:32')])
    group=dict(members={A:'甲'},source={'date':PRIOR},series=[b.point([A],{},None,snapshot)])
    good=dict(base,groups={'named:甲':group})
    path=tmp_path/'day.json'
    assert save(path,good)=='saved'
    assert save(path,base)=='fallback_preserved'
    assert json.loads(path.read_bytes())['sha256']==digest(good)
    replacement=dict(base, groups={'named:甲':dict(group,members={B:'乙'})})
    assert save(path,replacement)=='fallback_preserved'
    assert path.with_suffix('.last-attempt.json').exists()


def test_partial_amount_member_coverage_is_preserved(tmp_path):
    first = sample('09:32',{A:(10,100),B:(10,100)})
    last = sample('09:42',{A:(10,200),B:(10,0)})
    def result():
        g=dict(members={A:'甲',B:'乙'},source={},series=[b.point([A,B],{},first,last)])
        return dict(schema=b.SCHEMA,trade_date=DAY,input_by='USER',batches=[],gaps=[],status='partial',groups={'甲':g})
    path=tmp_path/'x.json'
    assert save(path,result())=='saved'
    last['stocks'][A]['amount']=80
    last['stocks'][B]['amount']=200
    assert save(path,result())=='fallback_preserved'


@pytest.mark.parametrize('open_day,hour', [(False,17),(True,14)])
def test_closed_or_early_day_never_reads_sources(monkeypatch,tmp_path,open_day,hour):
    monkeypatch.setattr(b,'calendar',lambda conn,day:dict(is_open=open_day))
    r=b.build(None,tmp_path,DAY,'tester',now=datetime(2026,11,27,hour,tzinfo=TZ))
    assert r['status']=='skipped' and not r['groups']
