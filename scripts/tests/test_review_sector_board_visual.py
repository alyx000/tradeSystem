"""Sector board charts use explicit source labels and independently reconciled days."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / '.agents/skills/daily-review/references/html-report-template/review_visuals.js'


def counts(a=0,b=0,c=0,u=0):
    return {'10':a,'20':b,'30':c,'unknown':u}


def fixture():
    current=[{'code':str(i),'board':board} for i,board in enumerate(['10','10','20','30','unknown'])]
    sector={'code':'industry','current':current,'previous':[{'code':'old','board':'20'}], 'current_count':5,'previous_count':1,'board_counts':counts(2,1,1,1),'previous_board_counts':counts(0,1)}
    return {'sectors':[sector],'totals':{'current':5,'previous':1},'board_totals':{'current':counts(2,1,1,1),'previous':counts(0,1)}}


def project(data):
    script=f'const v=require({json.dumps(str(SCRIPT))});process.stdout.write(JSON.stringify(v.sectorBoardCounts(JSON.parse(process.argv[1]))));'
    return json.loads(subprocess.run(['node','-e',script,json.dumps(data)],check=True,capture_output=True,text=True).stdout)


def test_explicit_boards_and_unknown_reconcile_without_code_inference():
    data=fixture();result=project(data)
    assert result['current']=={'valid':True,'counts':counts(2,1,1,1)}
    assert result['previous']=={'valid':True,'counts':counts(0,1)}
    assert result['sectors'][0]['current']['counts']['unknown']==1


@pytest.mark.parametrize('bad',[None,-1,1.5,'2',True])
def test_bad_count_does_not_become_zero(bad):
    data=fixture();data['sectors'][0]['board_counts']['10']=bad
    result=project(data)
    assert not result['current']['valid'] and result['previous']['valid']
    assert result['sectors'][0]['current']['counts'] is None


def test_missing_board_is_not_recovered_from_stock_code():
    data=fixture();data['sectors'][0]['current'][0]={'code':'600001.SH'}
    result=project(data);assert not result['current']['valid']


@pytest.mark.parametrize('change',['missing_key','wrong_global','wrong_total','wrong_labels','duplicate','cross_sector'])
def test_ambiguities_rejected(change):
    data=fixture();sector=data['sectors'][0]
    if change=='missing_key': del sector['board_counts']['30']
    if change=='wrong_global': data['board_totals']['current']=counts(1,2,1,1)
    if change=='wrong_total': data['totals']['current']=6
    if change=='wrong_labels': sector['current'][0]['board']='20'
    if change=='duplicate': sector['current'][1]['code']=sector['current'][0]['code']
    if change=='cross_sector':
        other=copy.deepcopy(sector);other['code']='other';data['sectors'].append(other)
        data['totals']={'current':10,'previous':2};data['board_totals']={'current':counts(4,2,2,2),'previous':counts(0,2)}
    assert not project(data)['current']['valid']


def test_missing_previous_does_not_hide_valid_current():
    data=fixture();del data['sectors'][0]['previous_board_counts']
    result=project(data);assert result['current']['valid'] and not result['previous']['valid']


def test_explicit_zero_day_is_verified():
    data=fixture();s=data['sectors'][0];s['previous']=[];s['previous_count']=0;s['previous_board_counts']=counts()
    data['totals']['previous']=0;data['board_totals']['previous']=counts()
    assert project(data)['previous']=={'valid':True,'counts':counts()}
