"""Regression checks for resuming 开盘啦 evidence without re-opening known gaps."""

import csv
import json
from automations import kaipanla_checkpoint as checkpoint


FACT = '2026-11-27'
TIME = FACT + 'T21:05:00+08:00'
LATER = FACT + 'T21:06:00+08:00'


def quote(view, name='甲', pct='--', stamp=TIME):
    return dict(date=FACT, view=view, name=name, category='化工', raw_pct=pct, read_at=stamp)


def tail(view, count):
    return dict(date=FACT, view=view, unique_rows=str(count), tail='true', read_at=TIME)


def registry():
    return {'date': FACT, 'ordered_categories': '化工、能源', 'read_at': TIME}


def test_only_unique_view_blank_requires_all_tails_and_recheck():
    both = [tail('精选', 1), tail('化工', 0)]
    data = {
        'commodity': [quote('精选')],
        'category-list': [registry()],
        'tail': both,
        'one-view-recheck': [quote('精选', stamp=LATER)],
    }
    assert checkpoint.reconcile(data, FACT)[1][0]['name'] == '甲'
    both.append(tail('能源', 0))
    resolved, pending, count = checkpoint.reconcile(data, FACT)
    assert not pending
    assert count == 1
    assert resolved[0]['status'] == 'source_pct_unavailable'
    assert resolved[0]['pct'] == '' and resolved[0]['raw_pct'] == '--'


def test_matching_confirmed_views_preserve_blank_and_numeric_conflict_reopens():
    data = {
        'commodity': [quote('精选'), quote('能源')],
        'category-list': [registry()],
        'tail': [tail('精选', 1), tail('能源', 1)],
    }
    rows, pending, _ = checkpoint.reconcile(data, FACT)
    assert not pending
    assert rows[0]['pct'] == ''
    data['commodity'].append(quote('能源', pct='0.00%', stamp=LATER))
    rows, pending, _ = checkpoint.reconcile(data, FACT)
    assert not pending
    assert rows[0]['pct'] == '0.00' and rows[0]['status'] == 'observed'
    data['commodity'].append(quote('精选', pct='0.01%', stamp=LATER))
    rows, pending, _ = checkpoint.reconcile(data, FACT)
    assert rows == []
    assert len(pending) == 1 and '冲突' in pending[0]['reason']


def test_committed_batch_survives_interrupted_projection_and_idempotent_replay(tmp_path, monkeypatch, capsys):
    day = tmp_path / FACT
    payload = tmp_path / 'payload.json'
    data = {
        'commodity': [quote('精选'), quote('能源')],
        'category-list': [registry()],
        'tail': [tail('精选', 1), tail('化工', 0), tail('能源', 1)],
    }
    payload.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    common = ['--day-dir', str(day), '--date', FACT]
    write = [*common, '--input-by', 'pytest']
    args = ['record', *write, '--batch-id', 'test-batch', '--input', str(payload)]
    original = checkpoint.write_projection

    def interrupted(*_):
        raise OSError('模拟投影失败')

    monkeypatch.setattr(checkpoint, 'write_projection', interrupted)
    assert checkpoint.main(args) == 2
    capsys.readouterr()
    assert checkpoint.main(['status', *common]) == 0
    status = json.loads(capsys.readouterr().out)
    assert 'test-batch' in status['recorded_batches']
    assert '商品:能源' in status['completed_slots']
    assert not status['pending_commodity']
    assert not (day / 'ui-resume-evidence').exists()

    monkeypatch.setattr(checkpoint, 'write_projection', original)
    assert checkpoint.main(['materialize', *write]) == 0
    assert checkpoint.main(args) == 0
    with (day / 'pending-verification.csv').open() as handle:
        assert list(csv.DictReader(handle)) == []
    assert checkpoint.checkpoint_data(day, FACT)[1] == status['recorded_batches']


def test_same_slot_rejects_different_payload_and_tampering(tmp_path, capsys):
    day = tmp_path / FACT
    payload = tmp_path / 'payload.pipe'
    payload.write_text(f'commodity|精选|甲|化工|--|{TIME}', encoding='utf-8')
    common = ['--day-dir', str(day), '--date', FACT]
    args = ['record', *common, '--input-by', 'pytest', '--batch-id', 'test-batch', '--input', str(payload),
            '--format', 'pipe']
    assert checkpoint.main(args) == 0
    capsys.readouterr()
    payload.write_text(f'commodity|精选|甲|化工|0.00%|{TIME}', encoding='utf-8')
    assert checkpoint.main(args) == 2
    assert '该批次编号已有不同证据' in capsys.readouterr().err
    record = day / 'checkpoints' / 'test-batch.json'
    record.write_text(record.read_text().replace('"--"', '"0.00%"'), encoding='utf-8')
    assert checkpoint.main(['status', *common]) == 2
    assert '哈希不符' in capsys.readouterr().err


def test_replacing_batch_resolves_conflict_and_preserves_prior_evidence(tmp_path, capsys):
    day = tmp_path / FACT
    payload = tmp_path / 'payload.json'
    common = ['--day-dir', str(day), '--date', FACT]

    def submit(batch, rows, *extra):
        payload.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        rc = checkpoint.main(['record', *common, '--input-by', 'pytest', '--batch-id', batch,
                              '--input', str(payload), *extra])
        output = capsys.readouterr()
        return rc, json.loads(output.out) if rc == 0 else output.err

    declared = {
        'category-list': [registry()],
        'tail': [tail('精选', 1), tail('化工', 1), tail('能源', 0)],
        'commodity': [quote('精选', pct='0.00%')],
    }
    assert submit('01-summary', declared)[0] == 0
    old_rows = {'commodity': [quote('化工', pct='0.01%')]}
    rc, status = submit('02-category', old_rows)
    assert rc == 0 and status['pending_commodity'][0]['name'] == '甲'
    old = day / 'checkpoints' / '02-category.json'
    original = old.read_bytes()
    new_rows = {'commodity': [quote('化工', pct='0.00%', stamp=LATER)]}

    args = ('--supersedes', '02-category', '--reason', '检查同日不同入口后更正看错的涨幅')
    rc, status = submit('03-correction', new_rows, *args)
    assert rc == 0 and status['pending_commodity'] == []
    correction = status['batch_corrections']['02-category']
    assert correction['replacement'] == '03-correction' and correction['input_by'] == 'pytest'
    assert correction['sha256'] == status['recorded_batches']['03-correction']
    assert old.read_bytes() == original
    current, pending, _ = checkpoint.commodity_reconcile(day / 'ui-resume-evidence', FACT)
    assert not pending and current[0]['pct'] == '0.00'
    assert submit('03-correction', new_rows, *args)[0] == 0
    rc, message = submit('04-branch', new_rows, *args)
    assert rc == 2 and '当前有效批次' in message
    next_args = ('--supersedes', '03-correction', '--reason', '再次确认同日原值')
    rc, status = submit('04-correction', new_rows, *next_args)
    assert rc == 0 and status['pending_commodity'] == []
    assert status['batch_corrections']['03-correction']['replacement'] == '04-correction'


def test_etf_status_and_final_gate_reject_the_same_missing_price(monkeypatch):
    import pytest
    from automations import kaipanla_evidence as evidence

    scope = '宽基、行业'
    header = dict(date=FACT, scope=scope, read_at=TIME)
    data = {
        'etf-market': [
            dict(header, bucket='涨幅高', name='科创芯片ETF', code='589190', price_raw='', change_pct_raw='6.00%', turnover_raw='1亿'),
            dict(header, bucket='涨幅低', name='银行ETF', code='512800', price_raw='1.20', change_pct_raw='-3.00%', turnover_raw='2亿'),
        ],
        'etf-flows': [],
        'etf-trends': [],
        'etf-meta': [
            dict(header, view='涨幅榜', boundary='分页条数:1/1 已核对到尾页'),
            dict(header, view='11月27日申赎列', boundary='分页条数:0/0/0/0 来源未披露'),
            dict(header, view='跨境参考', boundary='已核实来源未披露'),
            dict(header, view='动向', boundary='今天21:00更新，连续净申购0条、连续净赎回0条，已验收尾页'),
        ],
    }
    status = checkpoint.resume_status(data, {}, {}, FACT)
    assert 'ETF:涨幅榜' in status['remaining_slots']
    assert 'ETF:涨幅榜' not in status['completed_slots']
    issue, = status['field_issues']
    assert (issue['page'], issue['identity'], issue['field']) == ('涨幅榜', '589190', 'price_raw')
    assert 'ETF:动向' in status['completed_slots']

    from pathlib import Path
    monkeypatch.setattr(evidence, 'read', lambda path: data[path.stem])
    with pytest.raises(ValueError, match='涨幅榜/589190/price_raw'):
        evidence.etf_gate(Path('.'), FACT, '2026-11-26', '2026-11-25')

    data['etf-meta'][0]['boundary'] += ' 源缺字段 589190/price_raw_extra'
    assert checkpoint.resume_status(data, {}, {}, FACT)['field_issues']
    data['etf-meta'][0]['boundary'] += ' 源缺字段 589190/price_raw 价格栏源页显示--'
    status = checkpoint.resume_status(data, {}, {}, FACT)
    assert 'ETF:涨幅榜' in status['completed_slots']
    assert not status['field_issues']
    assert evidence.etf_gate(Path('.'), FACT, '2026-11-26', '2026-11-25') == 2
