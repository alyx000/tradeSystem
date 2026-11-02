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
    assert '<details class="evidence" open data-evidence-kind="teacher-level"' in html
    assert html.count('<details class="evidence" open') == 1
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
