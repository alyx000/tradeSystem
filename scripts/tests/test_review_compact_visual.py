"""Reject stale or inconsistent narrative numbers before creating comparison bars."""
import json
from pathlib import Path
import subprocess
import pytest

SCRIPT=Path(__file__).resolve().parents[2]/'.agents/skills/daily-review/references/html-report-template/review_visuals.js'
FACT='[事实] 非ST 10月30日→11月2日：涨停47→125；跌停82→8；炸板31→28；首板38→114；连板9→11；最高板5→6；封板率60.26%→81.70%。封板率=涨停/(涨停+炸板)。'
def project(text=FACT,asof='2026-11-02',day='2026-11-02',previous='2026-10-30'):
    code='process.stdout.write(JSON.stringify(require(process.argv[1]).emotionComparison(...JSON.parse(process.argv[2]))));'
    return json.loads(subprocess.run(['node','-e',code,str(SCRIPT),json.dumps([text,asof,day,previous])],check=True,text=True,capture_output=True).stdout)

def test_verified_source_has_correct_units_and_before_after_values():
    value=project()
    assert [(r['before'],r['after']) for r in value['rows']]==[(47,125),(82,8),(31,28),(38,114),(9,11),(5,6)]
    assert value['rows'][-1]['unit']=='板'
    assert value['seal']==[60.26,81.70]

@pytest.mark.parametrize('text',[FACT.replace('[事实]','[判断]'),FACT.replace('10月30日','10月29日'),FACT.replace('11月2日','11月3日'),FACT.replace('114','113'),FACT.replace('81.70','91.70'),FACT.replace('涨停47','涨停未计算'),FACT.replace('涨停47','涨停-1'),FACT.replace('跌停82','跌停9007199254740993')])
def test_no_chart_from_unsupported_or_conflicting_evidence(text):
    assert project(text) is None

@pytest.mark.parametrize('asof,day,previous',[('2026-11-01','2026-11-02','2026-10-30'),('2026-11-02',None,'2026-10-30'),('2026-11-02','2026-11-02',None),('2026-11-02','2026-11-02','2026-11-02')])
def test_date_proof_required(asof,day,previous):
    assert project(asof=asof,day=day,previous=previous) is None

def test_zero_denominator_never_promotes_a_source_to_chart():
    zero=FACT.replace('涨停47','涨停0').replace('炸板31','炸板0').replace('首板38','首板0').replace('连板9','连板0').replace('最高板5','最高板0').replace('60.26','50.00')
    assert project(zero) is None
    assert project(zero.replace('50.00','0.00')) is None

@pytest.mark.parametrize('content,source,expected',[(None,'partial','partial'),('ready','partial','partial'),('partial','complete','partial'),('missing-data','partial','missing-data'),('ready','complete','ready')])
def test_panel_keeps_source_coverage_visible(content,source,expected):
    code='process.stdout.write(JSON.stringify(require(process.argv[1]).visualStatus(...JSON.parse(process.argv[2]))));'
    value=json.loads(subprocess.run(['node','-e',code,str(SCRIPT),json.dumps([content,source])],check=True,text=True,capture_output=True).stdout)
    assert value==expected
