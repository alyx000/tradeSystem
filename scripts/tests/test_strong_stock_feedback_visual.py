"""Rendered feedback preserves independent denominators, gaps and source signatures."""
from copy import deepcopy

from bs4 import BeautifulSoup
import pytest

from analyzers import strong_stock_feedback as sf
from analyzers import strong_stock_feedback_history as history
import importlib.util
from pathlib import Path


def _fixture(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


_daily=_fixture('test_strong_stock_feedback')
_history=_fixture('test_strong_stock_feedback_history')
inputs,compute,DAY=_daily.inputs,_daily.compute,_daily.DAY
market,block=_history.market,_history.block


def test_independent_denominators_and_return_values():
    section,data=inputs()
    second=deepcopy(section['stocks'][0]);second.update(code='000002.SZ',name='测试乙')
    section['stocks'].append(second);section['count']=2
    for key,rows in data.items():
        if key=='previous_factors':continue
        row=deepcopy(rows[0]);row['ts_code']='000002.SZ';rows.append(row)
    value=compute(section,data);before=deepcopy(value)
    html,gap=sf.render_html(value,DAY);soup=BeautifulSoup(html,'html.parser')
    touch=soup.select_one('[data-feedback-metric="touched"]');bear=soup.select_one('[data-feedback-metric="bearish"]')
    assert (touch['data-numerator'],touch['data-denominator'])==('1','1')
    assert (bear['data-numerator'],bear['data-denominator'])==('2','2')
    for key in ['open_mean_pct','open_median_pct','close_mean_pct','close_median_pct']:
        assert float(soup.select_one('[data-feedback-return="'+key+'"]')['data-value'])==pytest.approx(value['summary'][key])
    assert gap and value==before
    assert '行情 0只 · 前高 1只' in soup.get_text()
    assert soup.select_one('details').find('table')
    assert sf.NOTE in soup.select_one('details').get_text()


def test_missing_touch_has_no_rate_bar_but_preserves_bearish_and_quotes():
    s,d=inputs();d['previous_factors']=[]
    soup=BeautifulSoup(sf.render_html(compute(s,d),DAY)[0],'html.parser')
    touch=soup.select_one('[data-feedback-metric="touched"]')
    assert touch['data-numerator']=='' and touch['data-denominator']=='0'
    assert not touch.select('[role="progressbar"]') and '未计算' in touch.get_text()
    assert soup.select_one('[data-feedback-metric="bearish"] [role="progressbar"]')['aria-valuenow']=='100.000000'


def test_equal_high_and_bearish_positive_return_are_not_mislabeled():
    s,d=inputs();q=d['current_quotes'][0];q.update(open=12,high=12.1,low=11.1,close=11.5,pct_chg=(11.5/11-1)*100)
    soup=BeautifulSoup(sf.render_html(compute(s,d),DAY)[0],'html.parser')
    assert soup.select_one('[data-feedback-metric="bearish"]')['data-numerator']=='1'
    assert float(soup.select_one('[data-feedback-return="close_mean_pct"]')['data-value'])>0
    assert '收阴（收盘低于开盘）' in soup.get_text() and '触及前高（含等于）' in soup.get_text()


def test_failed_source_has_no_numeric_graph_and_no_zero_evidence_count():
    soup=BeautifulSoup(sf.render_html(sf._base(DAY),DAY)[0],'html.parser')
    assert not soup.select('[data-feedback-return], [role="progressbar"], [data-items="0"]')
    assert '反馈未计算' in soup.get_text()


def test_sparse_history_coverage_is_not_a_twenty_day_trend(tmp_path):
    daily,db,days=market(tmp_path);b,_=block(DAY,days[-2])
    value=history.build(daily,DAY,current=b,db_path=db);original=deepcopy(value)
    html,gap=history.render_html(value);soup=BeautifulSoup(html,'html.parser')
    for days in [5,10,20]:
        item=soup.select_one('[data-feedback-metric="coverage-'+str(days)+'"]')
        assert (item['data-numerator'],item['data-denominator'])==('1',str(days))
        assert float(item.select_one('[role="progressbar"]')['aria-valuenow'])==pytest.approx(100/days)
    assert all(svg.find_parent('details') is not None for svg in soup.select('svg'))
    assert history.NOTE in soup.select_one('details').get_text()
    assert '不代表完整趋势' in soup.get_text() and gap and value==original
    # All detailed current/window/day tables remain available in the disclosure.
    assert len(soup.select('details table'))==3
    assert len(sf.html_signature(html,'data-strong-feedback-history'))==1
