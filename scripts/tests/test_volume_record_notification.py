import argparse
import json
from unittest.mock import Mock

import pytest

from cli import volume_record as cli
from services.volume_record import notification as n, service as s
from tests.test_volume_record import setup, add_amount_fixture, TARGET


def archive(setup):
    kwargs = add_amount_fixture(setup)
    return kwargs, s.run_dual(TARGET, 'test', **kwargs)


def test_archived_partial_push_retains_gaps_and_is_deduplicated(setup):
    kwargs = add_amount_fixture(setup)
    kwargs['registry'].call.return_value = Mock(success=False, error='industry offline')
    report = s.run_dual(TARGET, 'test', **kwargs)
    assert report['status'] == 'partial'
    pusher = Mock(); pusher.initialize.return_value = True; pusher.send_markdown.return_value = True
    receipt = n.push_saved(TARGET, 'test', root=kwargs['root'], pusher=pusher)
    assert receipt['status'] == 'sent'
    text = pusher.send_markdown.call_args.kwargs['content']
    assert '部分个股历史数据尚未核验' in text and '成交量创历史新高' in text and '成交额创历史新高' in text
    assert '600000' in text and '申万二级来源缺失' not in text
    assert 'partial' not in text and '缺口：' not in text
    again = n.push_saved(TARGET, 'test2', root=kwargs['root'], pusher=pusher)
    assert again['status'] == 'already_sent'
    pusher.send_markdown.assert_called_once()
    ledger = json.loads(open(receipt['receipt_path']).read())
    assert ledger['status'] == 'sent' and ledger['input_by'] == 'test'


@pytest.mark.parametrize('mode', ['init_failed', 'send_failed', 'tampered', 'sha_changed'])
def test_push_failures_never_report_sent(setup, mode):
    kwargs, report = archive(setup)
    pusher = Mock(); pusher.initialize.return_value = mode != 'init_failed'
    pusher.send_markdown.return_value = mode != 'send_failed'
    if mode == 'tampered':
        path = n.report_path(TARGET, 'both', kwargs['root'])
        payload = json.loads(path.read_text()); payload['status'] = 'complete_changed';path.write_text(json.dumps(payload))
    receipt = n.push_saved(TARGET, 'test', root=kwargs['root'], pusher=pusher,
                           expected_sha='bad' if mode == 'sha_changed' else None)
    assert receipt['status'] == 'failed' and not receipt['sent']
    if mode in ('tampered', 'sha_changed', 'init_failed'):
        pusher.send_markdown.assert_not_called()


def test_failed_part_retry_skips_confirmed_parts(setup, monkeypatch):
    kwargs, _ = archive(setup)
    monkeypatch.setattr(n, 'split_content', lambda content: ['part1', 'part2'])
    pusher = Mock();pusher.initialize.return_value = True
    pusher.send_markdown.side_effect = [True, False, True]
    assert not n.push_saved(TARGET, 'test', root=kwargs['root'], pusher=pusher)['sent']
    assert n.push_saved(TARGET, 'test', root=kwargs['root'], pusher=pusher)['sent']
    assert [c.kwargs['content'] for c in pusher.send_markdown.call_args_list] == ['part1', 'part2', 'part2']


def test_split_content_preserves_complete_unicode_report():
    content = ('完整名单📈\n' * 5000)
    chunks = n.split_content(content)
    assert ''.join(chunks) == content
    assert len(chunks) > 1 and all(len(c.encode()) <= 15000 for c in chunks)


@pytest.mark.parametrize('result,enabled,status', [
    ({'status': 'partial', 'sha256': 'ok', 'previous_report_preserved': True}, True, 'blocked'),
    ({'status': 'source_failed'}, True, 'blocked'),
    ({'status': 'skipped'}, True, 'skipped'),
    ({'status': 'complete'}, False, 'not_requested')])
def test_suppressed_delivery_never_calls_external_pusher(result, enabled, status, monkeypatch):
    sender = Mock();monkeypatch.setattr(n, 'push_saved', sender)
    assert n.push_result(result, 'test', enabled=enabled)['status'] == status
    sender.assert_not_called()


def test_receipt_write_failure_prevents_external_send(setup, monkeypatch):
    kwargs, _ = archive(setup)
    monkeypatch.setattr(s, 'atomic_write', Mock(side_effect=OSError('disk full')))
    pusher = Mock()
    result = n.push_saved(TARGET, 'test', root=kwargs['root'], pusher=pusher)
    assert not result['sent']
    pusher.initialize.assert_not_called()


def test_push_subcommand_reads_archive_without_collecting(monkeypatch):
    parser = argparse.ArgumentParser();cli.register_subparser(parser.add_subparsers(dest='command'))
    args = parser.parse_args(['volume-record', 'push', '--date', TARGET, '--input-by', 'test'])
    sender = Mock(return_value={'sent': True, 'status': 'sent'})
    monkeypatch.setattr(n, 'push_saved', sender)
    collector = Mock();monkeypatch.setattr(s, 'run_dual', collector)
    cli.handle_command({}, args)
    sender.assert_called_once_with(TARGET, 'test', metric='both')
    collector.assert_not_called()


@pytest.mark.parametrize('flags,enabled', [([], True), (['--no-push'], False), (['--dry-run'], False)])
def test_daily_push_default_and_opt_out(monkeypatch, flags, enabled):
    import main
    parser = argparse.ArgumentParser();cli.register_subparser(parser.add_subparsers(dest='command'))
    args = parser.parse_args(['volume-record', 'daily', '--date', TARGET, '--input-by', 'test', '--json'] + flags)
    monkeypatch.setattr(main, 'setup_providers', Mock(return_value=Mock()))
    result = {'status': 'complete'}
    monkeypatch.setattr(s, 'run_dual', Mock(return_value=result))
    sender = Mock(return_value={'status': 'sent' if enabled else 'not_requested'})
    monkeypatch.setattr(n, 'push_result', sender)
    cli.handle_command({}, args)
    sender.assert_called_once_with(result, 'test', metric='both', enabled=enabled)


def test_notification_omits_diagnostics_but_keeps_local_report(setup):
    kwargs, report = archive(setup)
    report['status'] = 'partial'
    report['metrics']['amount'].update(status='source_failed', matched_count=None, stocks=[],
        error="float() argument must be a string or a number, not NoneType",
        gaps=['600616.SH:历史日线/月线存在整月缺口，未证明停牌'], failed_codes=['600616.SH'])
    report['both_matched_count'] = None
    text = n.render_push(report)
    assert '暂未完成核验' in text and '部分个股历史数据尚未核验' in text
    assert '600616' not in text and 'NoneType' not in text and '整月缺口' not in text
    assert report['metrics']['amount']['error'] and report['metrics']['amount']['gaps']
