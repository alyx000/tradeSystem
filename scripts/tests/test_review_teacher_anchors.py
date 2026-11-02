from html import escape
from pathlib import Path
import sqlite3

import pytest

from services.review_teacher_anchors import load, render, validate
from tests.test_daily_review_html_report import _load_assembler, _write_chunks


DAY = '2026-11-01'
QUOTE = '上证指数顺势击破3574点位置也可以满足等比跌幅条件'


def source(tmp_path, *, day=DAY):
    db = tmp_path/'notes.db'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE teachers(id INTEGER,name TEXT);'
                           'CREATE TABLE teacher_notes(id INTEGER,teacher_id INTEGER,date TEXT,raw_content TEXT);')
        conn.execute('INSERT INTO teachers VALUES(1,?)',('老师甲',))
        conn.execute('INSERT INTO teacher_notes VALUES(10,1,?,?)',(day,QUOTE))
    attrs = {'kind':'level','report-date':DAY,'note-id':'10','source-date':day,
             'subject':'上证指数','level':'3574','status':'pending','quote':QUOTE}
    return db, attrs


def write_input(tmp_path, attrs, text='前低观察，仍待量能确认'):
    p = tmp_path/f'teacher_anchors_{DAY}.html'
    p.write_text('<p '+ ' '.join('data-'+k+'="'+escape(v,quote=True)+'"' for k,v in attrs.items())+'>'+escape(text)+'</p>')
    return p


def test_source_attribution_old_date_and_exact_quote(tmp_path):
    db, attrs=source(tmp_path,day='2026-10-30')
    p=write_input(tmp_path,attrs);payload=load(p,DAY,db)
    assert payload['status']=='partial'
    html=render(payload,DAY);validate(html,DAY,payload)
    assert '<details class="evidence" data-evidence-kind="teacher-level"' in html
    assert html.count('<details class="evidence" open') == 0
    assert '历史观点' in html and '2026-10-30' in html and '老师甲' in html
    assert 'teacher_notes#10' in html and QUOTE in html
    assert 'data-teacher-core-anchors' in html and '本次来源未整理此项' not in html
    assert '缺失不等于' in html
    assert 'href="#teacher-anchor-source-10"' in html
    with pytest.raises(ValueError):validate(html.replace('3574','9999'),DAY,payload)
    with pytest.raises(ValueError):validate(html+html,DAY,payload)


@pytest.mark.parametrize('field,value', [('quote','并不存在的原文'),('source-date','2026-10-30'),
    ('source-date','2026-11-02'),('report-date','2026-11-02'),('note-id','99'),('status','confirmed')])
def test_wrong_source_date_quote_or_status_fails_closed(tmp_path,field,value):
    db,attrs=source(tmp_path);attrs[field]=value
    payload=load(write_input(tmp_path,attrs),DAY,db)
    assert payload['status']=='source_failed' and not payload['rows']
    assert 'source_failed' in render(payload,DAY)


def test_missing_file_and_db_failure_are_distinct(tmp_path):
    assert load(tmp_path/'missing.html',DAY,tmp_path/'absent.db')['status']=='missing-data'
    db,attrs=source(tmp_path)
    assert load(write_input(tmp_path,attrs),DAY,tmp_path/'absent.db')['status']=='source_failed'


@pytest.mark.parametrize('field', ['kind','report-date','note-id','source-date','subject','level','status','quote'])
def test_valueless_attributes_preserve_failure_state(tmp_path,field):
    db,attrs=source(tmp_path);p=write_input(tmp_path,attrs)
    p.write_text(p.read_text().replace('data-'+field+'="'+escape(attrs[field],quote=True)+'"','data-'+field))
    payload=load(p,DAY,db)
    assert payload['status']=='source_failed' and not payload['rows']
    asm=_load_assembler();_write_chunks(tmp_path,date=DAY)
    html=asm.render_report(tmp_path,DAY,teacher_anchors=payload)
    asm.validate_report(html,teacher_anchors=payload)
    assert '老师核心锚点与指数关键点位：source_failed' in html


@pytest.mark.parametrize('note_id', ['0','9223372036854775808','²'])
def test_invalid_database_ids_preserve_failure_state(tmp_path,note_id):
    db,attrs=source(tmp_path);attrs['note-id']=note_id
    payload=load(write_input(tmp_path,attrs),DAY,db)
    assert payload['status']=='source_failed' and not payload['rows']


def test_html_input_cannot_inject_scripts_and_output_escapes_text(tmp_path):
    db,attrs=source(tmp_path);p=write_input(tmp_path,attrs,'条件 <script>alert(1)</script>')
    payload=load(p,DAY,db)
    assert payload['status']=='partial' and '<script>' not in render(payload,DAY)
    p.write_text(p.read_text().replace('&lt;script&gt;','<script>'))
    assert load(p,DAY,db)['status']=='source_failed'


def test_daily_renderer_keeps_both_slots_and_rejects_tampering(tmp_path):
    asm=_load_assembler();_write_chunks(tmp_path,date=DAY)
    html=asm.render_report(tmp_path,DAY)
    asm.validate_report(html)
    assert html.count('data-teacher-core-anchors=')==1
    assert html.count('data-teacher-index-levels=')==1
    assert '老师核心锚点与指数关键点位：missing-data' in html
    with pytest.raises(asm.ReportValidationError,match='老师核心锚点'):
        asm.validate_report(html.replace('<!-- teacher-anchors:start -->',''))


def test_authorized_payload_reaches_formal_validator(tmp_path):
    asm=_load_assembler();_write_chunks(tmp_path,date=DAY)
    db,attrs=source(tmp_path);p=write_input(tmp_path,attrs)
    payload=load(p,DAY,db)
    html=asm.render_report(tmp_path,DAY,teacher_anchors=payload)
    asm.validate_report(html,teacher_anchors=payload)
    with pytest.raises(asm.ReportValidationError):asm.validate_report(html)
    payload['rows'] = [dict(payload['rows'][0],subject='指数'+str(i)) for i in range(41)]
    too_long=asm.render_report(tmp_path,DAY,teacher_anchors=payload)
    with pytest.raises(asm.ReportValidationError,match='独立证据预算'):
        asm.validate_report(too_long,teacher_anchors=payload)


def _default_teacher_text(fragment):
    import re
    from html import unescape
    return unescape(re.sub('<[^>]+>','',re.sub(r'<details\b.*?</details>','',fragment,flags=re.S)))


def test_default_summary_retains_actual_conditions_status_and_folded_sources(tmp_path):
    from copy import deepcopy
    db,attrs=source(tmp_path,day='2026-10-30')
    condition='需突破3574且持续放量；未满足前不能视为已确认。'
    payload=load(write_input(tmp_path,attrs,condition),DAY,db);before=deepcopy(payload)
    html=render(payload,DAY);visible=_default_teacher_text(html)
    assert '上证指数' in visible and '3574' in visible and condition in visible
    assert '老师甲' in visible and '待盘面验证' in visible and '历史归档 2026-10-30' in visible
    assert QUOTE not in visible and QUOTE in html and 'teacher_notes#10' not in visible
    assert '归档日不等于原始发言日' not in visible and '归档日不等于原始发言日' in html
    assert html.count('data-teacher-index-levels=')==1 and html.count('data-teacher-core-anchors=')==1
    assert '<details class="evidence" open' not in html
    assert f'data-as-of="{DAY}" data-items="1"' in html
    assert payload==before
    validate(html,DAY,payload)
    with pytest.raises(ValueError):validate(html.replace('持续放量','无需放量',1),DAY,payload)


@pytest.mark.parametrize('status,label',[
    ('pending','待盘面验证'),('historical','历史观点'),('uncertain','待核原文'),('not-stated','未给具体点位'),
])
def test_default_teacher_summary_preserves_each_source_status(tmp_path,status,label):
    db,attrs=source(tmp_path);attrs.update(status=status,kind='anchor',level='')
    payload=load(write_input(tmp_path,attrs,'只观察条件，不认定已兑现。'),DAY,db)
    html=render(payload,DAY);visible=_default_teacher_text(html)
    assert label in visible and '只观察条件，不认定已兑现' in visible
    assert 'data-teacher-summary="anchor"' in html and 'flex-wrap:wrap' in html
    assert '<script' not in html and '<link' not in html


def test_summary_keeps_conflicting_index_sources_separate(tmp_path):
    db,attrs=source(tmp_path);payload=load(write_input(tmp_path,attrs),DAY,db)
    payload['rows'].append(dict(payload['rows'][0],teacher='老师乙',subject='上证指数·另一来源',level='3680',summary='仅当再次放量才验证；不能与3574合并。',status='uncertain'))
    html=render(payload,DAY);visible=_default_teacher_text(html)
    assert '老师甲' in visible and '老师乙' in visible and '3574' in visible and '3680' not in visible
    assert '3680' in html and '摘录1/2条' in visible and '另1条来源观察' in visible
    assert '不能与3574合并' in html and '待核原文' in html and '不合并点位或确认条件' in visible
    assert html.count('id="teacher-anchor-source-10"')==1
    validate(html,DAY,payload)


def test_teacher_summary_groups_by_teacher_without_claiming_full_consensus(tmp_path):
    db,attrs=source(tmp_path);attrs.update(kind='anchor',level='')
    payload=load(write_input(tmp_path,attrs,'首条完整条件；还须量能确认。'),DAY,db)
    payload['rows'].append(dict(payload['rows'][0],subject='另一个对象',summary='第二条完整条件，不能略去。'))
    html=render(payload,DAY);visible=_default_teacher_text(html)
    assert '首条完整条件；还须量能确认' in visible
    assert '第二条完整条件' not in visible and '第二条完整条件' in html
    assert '摘录1/2条' in visible and '摘录不代表全部观点' in visible
    validate(html,DAY,payload)


def test_unstated_levels_share_one_visible_line_but_keep_all_source_conditions(tmp_path):
    db,attrs=source(tmp_path);attrs.update(status='not-stated',level='未提供具体点位')
    payload=load(write_input(tmp_path,attrs,'没有给出明确数字，不将估值作为点位。'),DAY,db)
    payload['rows'].append(dict(payload['rows'][0],teacher='老师乙',subject='另一老师指数',summary='只说观察条件，暂无具体点位。'))
    html=render(payload,DAY);visible=_default_teacher_text(html)
    assert '老师甲、老师乙：所列来源未给具体点位（2条' in visible
    assert '没有给出明确数字' not in visible and '没有给出明确数字' in html
    assert '只说观察条件' in html
    validate(html,DAY,payload)
