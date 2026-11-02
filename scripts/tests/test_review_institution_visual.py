"""Institution charts project explicit main-table net amounts, never infer missing flow."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.agents/skills/daily-review/references/html-report-template/review_visuals.js'
HEADERS = ['榜段/股票', '机构买／卖／净亿', '当日涨幅%／换手%／成交亿', '全榜买／卖／净亿', '上榜原因']
META = {'institutionFlow': 'v1', 'asOf': '2026-11-02', 'sourceCount': 1}
# Exact displayed main-table facts from the November 2 report, without whole-market totals.
ACTUAL_ROWS = [
    ['单日／捷成股份 300182.SZ', '1.325/0.317/+1.008', '+20.00/17.34/22.059'],
    ['单日／果麦文化 301052.SZ', '1.967/1.282/+0.684', '+19.99/41.59/11.992'],
    ['单日／粤传媒 002181.SZ', '1.128/0.536/+0.592', '+9.96/10.27/11.327'],
    ['单日／安集科技 688019.SH', '0.636/3.794/-3.158', '+16.87/5.32/22.215'],
    ['单日／光洋股份 002708.SZ', '1.967/2.864/-0.897', '+7.99/30.29/30.134'],
    ['单日／芒果超媒 300413.SZ', '1.195/2.048/-0.853', '+20.00/7.83/19.656'],
    ['多日／皇台酒业 000995.SZ', '1.001/0.199/+0.803', '+10.01/7.18/1.984'],
    ['多日／迎驾贡酒 603198.SH', '1.658/1.482/+0.177', '+9.13/3.20/12.012'],
    ['多日／华阳国际 002949.SZ', '0.898/0.777/+0.121', '+10.00/17.99/3.613'],
    ['多日／泉阳泉 600189.SH', '0.000/0.754/-0.754', '+9.95/14.22/9.673'],
    ['多日／华是科技 301218.SZ', '5.957/6.589/-0.632', '+19.99/22.56/11.883'],
    ['多日／沈阳机床 000410.SZ', '0.000/0.202/-0.202', '+10.06/10.56/12.063'],
]


def project(rows=None, meta=None, headers=None, date='2026-11-02'):
    args = [META if meta is None else meta, HEADERS if headers is None else headers,
            ACTUAL_ROWS if rows is None else rows, date]
    code = f'const v=require({json.dumps(str(SCRIPT))});process.stdout.write(JSON.stringify(v.institutionFlow(...JSON.parse(process.argv[1]))));'
    result = subprocess.run(['node', '-e', code, json.dumps(args)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_actual_twelve_rows_preserve_net_signs_identity_and_segments():
    result = project()
    assert result['status'] == 'ready'
    assert result['issues'] == []
    assert [r['net'] for r in result['groups']['single']] == [1.008, .684, .592, -3.158, -.897, -.853]
    assert [r['net'] for r in result['groups']['multi']] == [.803, .177, .121, -.754, -.632, -.202]
    row = result['groups']['single'][0]
    assert (row['name'], row['code'], row['buy'], row['sell'], row['net']) == ('捷成股份', '300182.SZ', 1.325, .317, 1.008)
    assert (row['change'], row['turnover'], row['amount']) == (20, 17.34, 22.059)
    # Positive stock return does not change a negative institution net amount.
    assert result['groups']['single'][3]['change'] == 16.87
    assert result['groups']['single'][3]['net'] == -3.158


@pytest.mark.parametrize('flow,net,buy,sell', [
    ('1.325/0.317/—', None, 1.325, .317),
    ('1.325/—/—', None, 1.325, None),
    ('未留存/未留存/+1.008', 1.008, None, None),
    ('0.000/0.000/0.000', 0, 0, 0),
])
def test_missing_leg_never_creates_net_and_explicit_zero_remains_zero(flow, net, buy, sell):
    row = ACTUAL_ROWS[0].copy(); row[1] = flow
    result = project([row]); item = result['groups']['single'][0]
    assert (item['net'], item['buy'], item['sell']) == (net, buy, sell)
    assert result['status'] == ('missing-data' if net is None else 'partial' if buy is None else 'ready')


@pytest.mark.parametrize('change', [
    {'asOf': '2026-10-30'}, {'institutionFlow': 'none'}, {'sourceCount': 2},
    {'sourceStatus': 'source_failed'}, {'sourceStatus': 'missing-data'},
])
def test_stale_failed_or_duplicate_tables_have_no_graph_values(change):
    result = project(meta={**META, **change})
    assert result['status'] == 'missing-data'
    assert result['groups'] == {'single': [], 'multi': []}


def test_unknown_fact_date_and_wrong_header_do_not_read_numbers():
    assert project(date=None)['status'] == 'missing-data'
    assert project(headers=['榜段/股票', '全榜买／卖／净亿', HEADERS[2]])['groups']['single'] == []


@pytest.mark.parametrize('identity,flow,market', [
    ('单日/多日／捷成股份 300182.SZ', '1.325/0.317/+1.008', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ 300413.SZ', '1.325/0.317/+1.008', '+20.00/17.34/22.059'),
    ('未知／捷成股份 300182.SZ', '1.325/0.317/+1.008', '+20.00/17.34/22.059'),
    ('单日／捷成股份', '1.325/0.317/+1.008', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ', '-1.325/0.317/+1.008', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ', '1.325/0.317/-1.008', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ', '1.325亿/0.317亿/+1.008亿', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ', '1.325/0.317/+1.008/2.0', '+20.00/17.34/22.059'),
    ('单日／捷成股份 300182.SZ', '1.325/0.317/+1.008', '+20.00/-17.34/22.059'),
])
def test_ambiguous_or_inconsistent_row_is_rejected_without_hiding_valid_rows(identity, flow, market):
    result = project([[identity, flow, market], ACTUAL_ROWS[1]])
    assert result['status'] == 'partial'
    assert len(result['groups']['single']) == 1
    assert result['groups']['single'][0]['code'] == '301052.SZ'
    assert result['issues']


def test_same_segment_duplicate_identity_rejects_both_but_other_segment_is_independent():
    row = ACTUAL_ROWS[0]
    duplicate = row.copy(); duplicate[1] = '1.000/0.000/+1.000'
    multi = row.copy(); multi[0] = row[0].replace('单日', '多日')
    result = project([row, duplicate, multi])
    assert result['groups']['single'] == []
    assert [x['net'] for x in result['groups']['multi']] == [1.008]
    assert result['status'] == 'partial'


def test_multi_only_does_not_fill_empty_daily_group():
    result = project(ACTUAL_ROWS[6:])
    assert result['groups']['single'] == []
    assert len(result['groups']['multi']) == 6
    assert result['status'] == 'partial'


def test_rounded_buy_sell_net_are_checked_at_display_precision():
    row = ACTUAL_ROWS[1].copy()
    assert project([row])['groups']['single'][0]['net'] == .684  # .685 before independent rounding
    row[1] = '1.967/1.282/+0.680'
    assert project([row])['groups']['single'] == []
