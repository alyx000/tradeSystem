"""请求者必须在任何采集/写库前校验；从CLI贯穿到快照的集成用例见test_capacity_trend。"""
import argparse
from pathlib import Path
import shlex
from unittest.mock import Mock

import pytest

from cli import volume_watch
from services.volume_concentration import service


def parser():
    p = argparse.ArgumentParser()
    volume_watch.register_subparser(p.add_subparsers(dest='command'))
    return p


@pytest.mark.parametrize('extra', [[], ['--input-by', ''], ['--input-by', ' \t ']])
def test_cli_rejects_missing_or_blank_requester(extra):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(['volume-watch', 'daily', '--no-push'] + extra)
    assert exc.value.code == 2


def test_cli_passes_requester_and_no_push_to_persisted_run(monkeypatch):
    import main
    args = parser().parse_args(['volume-watch', 'daily', '--date', '2026-10-30',
                               '--no-push', '--input-by', ' USER '])
    conn, registry = Mock(), Mock()
    run, push = Mock(return_value='report'), Mock()
    monkeypatch.setattr(main, 'setup_providers', lambda config: registry)
    monkeypatch.setattr(volume_watch, 'get_connection', lambda: conn)
    monkeypatch.setattr(volume_watch, 'migrate', Mock())
    monkeypatch.setattr('utils.trade_date.is_non_trading_day', lambda *a: False)
    monkeypatch.setattr(service, 'run_daily', run)
    monkeypatch.setattr(volume_watch, '_push_to_dingtalk', push)
    volume_watch._run_daily({}, args)
    run.assert_called_once_with(conn, registry, '2026-10-30', persist=True, refetch=False, input_by='USER')
    push.assert_not_called()
    conn.close.assert_called_once()


@pytest.mark.parametrize('requester', [None, '', ' \n '])
def test_direct_calls_reject_bad_requester_before_side_effects(monkeypatch, requester):
    import main
    setup, collect = Mock(), Mock()
    monkeypatch.setattr(main, 'setup_providers', setup)
    monkeypatch.setattr(service.collector, 'build_record', collect)
    with pytest.raises(argparse.ArgumentTypeError):
        volume_watch._run_daily({}, argparse.Namespace(input_by=requester))
    with pytest.raises(ValueError):
        service.run_daily(None, None, '2026-10-30', input_by=requester)
    setup.assert_not_called()
    collect.assert_not_called()


def test_installed_runner_command_contract():
    root = Path(__file__).resolve().parents[2]
    runner = (root/'deploy/launchd/volume-watch-runner.sh').read_text()
    command = next(line for line in runner.splitlines() if line.startswith('exec '))
    argv = shlex.split(command)
    args = parser().parse_args(argv[3:])
    assert args.input_by == 'launchd'
    assert not args.dry_run
    assert not args.no_push  # 保持既有自动报告推送设置；手工回补另带--no-push。
