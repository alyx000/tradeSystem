"""Offline visual projections: missingness, units and repeat-safe attachment."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / '.agents/skills/daily-review/references/html-report-template'


def js(expression):
    code = 'const v=require(' + json.dumps(str(TEMPLATE / 'review_visuals.js')) + '); console.log(JSON.stringify(' + expression + '));'
    return json.loads(subprocess.run(['node', '-e', code], check=True, text=True, capture_output=True).stdout)


@pytest.mark.parametrize('value,expected', [
    (None, None), ('', None), (' ', None), ('未计算', None), ('—', None),
    ('source_failed', None), ('Infinity', None), ('NaN', None),
    ('12abc', None), ('2026-10-29', None), ('12/15', None),
    ('0', 0), ('0.00%', 0), ('−8.94%', -8.94), ('+7.53pp', 7.53),
    ('15,565.17亿', 15565.17),
])
def test_numeric_parser_rejects_missing_and_non_values(value, expected):
    assert js('v.num(' + json.dumps(value) + ')') == expected


def test_missingness_never_becomes_below_or_zero():
    assert js('[v.compare(null,5),v.compare(5,null),v.compare(5,5),v.compare(4,5),v.compare(6,5)]') == ['missing','missing','equal','below','above']
    assert js('v.scale([null,-4,6],true)') == {'min':-6,'max':6}
    assert js('v.scale([117,102,49,27],false)') == {'min':0,'max':117}
    assert js('v.fmt(null)') == '未计算'
    assert js('v.compareDisplayed(10.00,10.00)') == 'rounded-equal'
    assert js('v.compareDisplayed(null,10.00)') == 'missing'
    assert js('v.compareDisplayed(10.01,10.00)') == 'above'


def test_combined_heatmap_does_not_turn_total_failure_into_partial():
    assert js('v.coverageState(["source_failed","source_failed"])') == 'source_failed'
    assert js('v.coverageState(["missing-data","missing-data"])') == 'missing-data'
    assert js('v.coverageState(["source_failed","missing-data"])') == 'source_failed'
    assert js('v.coverageState(["complete","source_failed"])') == 'partial'
    assert js('v.coverageState(["complete","partial"])') == 'partial'
    assert js('v.coverageState(["complete","complete"])') == 'ready'


def test_sector_fragments_require_explicit_signed_percentage():
    text = '[事实] 申万二级124方向中，航天装备Ⅱ+6.84%、国有大行Ⅱ+2.39%，通信设备−8.94%、元件−8.07%、半导体−5.13%；阶段未知'
    result = js('v.percentItems(' + json.dumps(text) + ')')
    assert [r['value'] for r in result] == [6.84,2.39,-8.94,-8.07,-5.13]
    assert js('v.percentItems("行业5/20日涨幅，样本12只，未计算")') == []


@pytest.mark.parametrize('change,source', [
    ({}, '[判断] 明日若回流，半导体+5.00%、通信设备+3.00%'),
    ({'vizAsOf':'2026-10-28'}, '[事实] 前日，半导体+5.00%'),
    ({'vizMeasure':'sector-5d-return-pct'}, '[事实] 近5日，半导体+5.00%'),
    ({'vizSourceStatus':'source_failed'}, '[事实] 板块，半导体+5.00%'),
    ({'vizMeasure':None}, '[事实] 板块，半导体+5.00%'),
])
def test_sector_contract_rejects_forecast_stale_multi_day_and_failed(change, source):
    meta = dict(vizMeasure='sector-daily-return-pct', vizAsOf='2026-10-29', vizKind='fact', vizSourceStatus='complete')
    meta.update(change)
    assert js('v.sectorFacts(' + json.dumps(meta) + ',' + json.dumps(source) + ',"2026-10-29")') == []


def test_sector_contract_accepts_same_day_facts_without_judgement_tail():
    meta = dict(vizMeasure='sector-daily-return-pct', vizAsOf='2026-10-29', vizKind='fact', vizSourceStatus='complete')
    source = '[事实] 今日，半导体-5.13%；[判断] 明日，通信设备+3.00%'
    assert js('v.sectorFacts(' + json.dumps(meta) + ',' + json.dumps(source) + ',"2026-10-29")') == [{'label':'半导体','value':-5.13}]


def test_distribution_statistics_keep_missing_and_zero_distinct():
    assert js('v.stats([null,0,-6,3])') == {'n':3,'mean':-1,'median':0}
    assert js('v.stats([null,null])') == {'n':0,'mean':None,'median':None}
    assert js('v.stats([-10,-8,2,4])') == {'n':4,'mean':-3,'median':-3}


def test_identity_attachment_escapes_script_text_and_is_idempotent(assembler):
    source='<html><head></head><body>evidence</body></html>'
    first=assembler.attach_identity_flows(source,'2026-10-29')
    assert 'review-identity-data' in first and 'missing-data' in first
    assert assembler.attach_identity_flows(first,'2026-10-29') == first


@pytest.fixture(scope='module')
def assembler():
    spec = importlib.util.spec_from_file_location('review_visuals_test_assembler', TEMPLATE / 'assemble_report.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_attachment_is_repeat_safe_and_preserves_evidence(assembler, tmp_path):
    table = '<table data-source-status="partial"><tr><td>未计算</td></tr></table>'
    source = '<html><head></head><body><div id="tldr">速览</div>' + table + '<script>window.reader=true;</script></body></html>'
    first = assembler.attach_visuals(source, '2026-10-29', illustration_dir=tmp_path)
    second = assembler.attach_visuals(first, '2026-10-29', illustration_dir=tmp_path)
    assert second == first
    assert table in first
    assert first.count('review-visuals-script:start') == 1
    assert first.index('review-visuals-script:start') < first.index('window.reader=true')
    assert 'data-review-illustration' not in first
    assert 'fetch(' not in (TEMPLATE / 'review_visuals.js').read_text()
    assert 'localStorage' not in (TEMPLATE / 'review_visuals.js').read_text()


def test_illustrations_are_optional_and_invalid_files_not_embedded(assembler, tmp_path):
    (tmp_path / '01-复盘摘要.png').write_bytes(b'not an image')
    source = '<html><head></head><body><div id="tldr">x</div></body></html>'
    out = assembler.attach_visuals(source, '2026-10-29', illustration_dir=tmp_path)
    assert 'data-review-illustration' not in out
    # Signature fixture: attachment tests bytes transport, browser QA tests decoding.
    (tmp_path / '01-复盘摘要.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    out = assembler.attach_visuals(source, '2026-10-29', illustration_dir=tmp_path)
    assert 'data:image/png;base64,' in out
    assert 'data-review-illustration="v1"' in out
    assert '另外0张' not in out
    assert assembler.attach_visuals(out, '2026-10-29', illustration_dir=tmp_path) == out
