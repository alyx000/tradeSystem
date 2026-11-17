from copy import deepcopy
import pytest
from analyzers import theme_volume_context as v, theme_launch as t
from scripts.tests.test_theme_continuity import current_launch, DAY


@pytest.mark.parametrize('factor,outside,text',[(2,3,'题材放量、市场缩量'),(2,12,'市场与题材均放量'),(.5,12,'市场放量、题材缩量'),(.5,3,'市场与题材均缩量'),(1,9,'市场或题材成交额持平')])
def test_all_volume_combinations_do_not_infer_mainline(factor,outside,text):
    block=current_launch(factor,outside); before=deepcopy(block)
    html,gap=t.render_html(block,DAY)
    assert text in html and '板块量能对照' in html and '不自动确认主线' in html
    assert block==before


def test_partial_market_and_independent_index_failure():
    b=current_launch(); e=b['evidence']
    e['increment'].update(status='partial',gaps=['行情缺口'])
    e['indices']['shanghai']['trade_date']='2000-01-01'
    b=t.calculate(DAY,b['previous_date'],e)
    context=v.build(t.validate(b,DAY))
    assert context['market']=='未计算' and '上证指数 未核验' in context['indices']
    assert context['rows'][0][2]=='放量（+100.00%）'
    assert context['rows'][0][3]=='未计算' and context['rows'][0][-1]=='量能对照待核验'


@pytest.mark.parametrize('mode',['price','volume','hide','different_source'])
def test_official_html_protects_added_table(tmp_path,mode):
    from scripts.tests import test_daily_review_html_report as h
    a=h._load_assembler(); h._write_chunks(tmp_path,DAY)
    b=current_launch(); html=a.render_report(tmp_path,DAY,capacity_trend_db_path=tmp_path/"missing.db",theme_launch=b)
    a.validate_report(html,theme_launch=b)
    fragment=v.render_html(t.validate(b,DAY))
    if mode=='price': html=html.replace(fragment,fragment.replace('+3.00%','+99.00%'))
    if mode=='volume': html=html.replace(fragment,fragment.replace('题材放量、市场缩量','市场与题材均放量'))
    if mode=='hide': html=html.replace(fragment,'<div hidden>'+fragment+'</div>')
    if mode=='different_source': b=current_launch(.5,12)
    with pytest.raises(a.ReportValidationError): a.validate_report(html,theme_launch=b)


@pytest.mark.parametrize('raw',[None, [], ['bad'], 'broken', 42])
@pytest.mark.parametrize('location',['increment', 'evidence'])
def test_missing_or_malformed_raw_source_keeps_official_report_available(tmp_path,raw,location):
    from scripts.tests import test_daily_review_html_report as h
    block=current_launch()
    evidence=deepcopy(block['evidence'])
    if location=='increment': evidence['increment']=raw
    else: evidence=raw
    block=t.calculate(DAY,block['previous_date'],evidence)
    html,gap=t.render_html(block,DAY)
    assert '沪深市场成交额：未计算' in html
    if location=='increment':
        assert '量能对照待核验' in html
    else:
        assert '板块量能对照未计算' in html
    a=h._load_assembler();h._write_chunks(tmp_path,DAY)
    full=a.render_report(tmp_path,DAY,capacity_trend_db_path=tmp_path/'missing.db',theme_launch=block)
    a.validate_report(full,theme_launch=block)
    assert '沪深市场成交额：未计算' in full
