from copy import deepcopy
from datetime import datetime
import json
import sqlite3
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import yaml

from providers.base import DataResult
from services.consecutive_pool_history import backfill, select_dates
from tests.test_consecutive_pool import DAY, calendar, evidence


def setup(tmp_path):
    db = calendar(tmp_path)
    path = tmp_path/DAY/'post-market.yaml'
    path.parent.mkdir()
    original = dict(date=DAY, raw_data={'unrelated': {'test': '保持原值'}}, holdings_data=[{'name': '不变'}])
    path.write_text(yaml.safe_dump(original, allow_unicode=True))
    reg = MagicMock()
    def call(method, day):
        e = evidence(day)
        if method == 'get_limit_up_list': return DataResult(data=e['limit_up'], source='tushare:limit_list_d')
        if method == 'get_market_daily_quotes': return DataResult(data=e['quotes'], source='tushare:daily')
        raise AssertionError('不允许其他请求')
    reg.call.side_effect = call
    sync = MagicMock(return_value=True)
    kwargs = dict(input_by='USER', daily_dir=tmp_path, report_dir=tmp_path/'reports', db_path=db,
                  sync_fn=sync, now=datetime(2026, 11, 11, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    return db, path, original, reg, sync, kwargs


def test_backfill_only_target_block_backups_audit_dual_write_and_idempotency(tmp_path):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    before = path.read_bytes()
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['status'] == 'complete' and result['trading_days'] == 1
    assert result['push_status'] == 'not_requested'
    updated = yaml.safe_load(path.read_text())
    snapshot = updated['raw_data'].pop('consecutive_pool')
    assert updated == original and snapshot['count'] == 2
    assert snapshot['collection_receipt']['input_by'] == 'USER'
    assert sync.call_count == 1 and reg.call.call_count == 2
    receipt = result['receipts'][0]
    from pathlib import Path
    assert Path(receipt['backup_path']).read_bytes() == before
    assert json.loads(Path(receipt['source_path']).read_text())['amount_yi'] == pytest.approx(6.23456)
    after = path.read_bytes()
    kwargs['now'] = kwargs['now'].replace(microsecond=1)
    second = backfill(reg, DAY, DAY, **kwargs)
    assert second['status'] == 'complete' and second['receipts'][0]['status'] == 'already_complete'
    assert path.read_bytes() == after and reg.call.call_count == 2 and sync.call_count == 2


def test_source_failure_no_archive_mutation(tmp_path):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    before = path.read_bytes()
    reg.call.side_effect = lambda *args: DataResult(data=None, source='test', error='source down')
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['status'] == 'partial' and result['receipts'][0]['status'] == 'source_failed'
    assert before == path.read_bytes() and not sync.called


def test_calendar_hole_rejects_before_fetch_or_write(tmp_path):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    with pytest.raises(ValueError, match='不完整'):
        backfill(reg, '2026-11-09', DAY, **kwargs)
    assert not reg.call.called and not sync.called
    with pytest.raises(ValueError, match='input_by'):
        backfill(reg, DAY, DAY, **{**kwargs, 'input_by': ' '})


def test_sync_failure_is_reported_and_retried_without_refetch(tmp_path):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    sync.return_value = False
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['status'] == 'partial' and result['receipts'][0]['status'] == 'sync_failed'
    sync.return_value = True
    kwargs['now'] = kwargs['now'].replace(microsecond=1)
    assert backfill(reg, DAY, DAY, **kwargs)['status'] == 'complete'
    assert reg.call.call_count == 2


def test_concurrent_archive_change_not_overwritten(tmp_path):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    original_call = reg.call.side_effect
    def call(method, day):
        if method == 'get_market_daily_quotes':
            changed = deepcopy(original)
            changed['concurrent_update'] = True
            path.write_text(yaml.safe_dump(changed))
        return original_call(method, day)
    reg.call.side_effect = call
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['receipts'][0]['status'] == 'failed'
    assert yaml.safe_load(path.read_text())['concurrent_update'] is True and not sync.called


@pytest.mark.parametrize('data', [{}, {'count': 0}])
def test_structurally_empty_limit_result_never_persists_false_zero(tmp_path, data):
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    before = path.read_bytes()
    original_call = reg.call.side_effect
    reg.call.side_effect = lambda method, day: (DataResult(data=data, source='tushare:limit_list_d')
        if method == 'get_limit_up_list' else original_call(method, day))
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['receipts'][0]['status'] == 'source_failed'
    assert path.read_bytes() == before and not sync.called


def test_partial_details_preserved_when_retry_loses_amount_coverage(tmp_path):
    from analyzers.consecutive_pool import calculate
    db, path, original, reg, sync, kwargs = setup(tmp_path)
    e = evidence()
    e['quotes'][1]['amount'] = None
    block = calculate(DAY, e)
    assert block['count'] == 2 and block['amount_count'] == 1 and block['amount_yi'] is None
    original['raw_data']['consecutive_pool'] = block
    path.write_text(yaml.safe_dump(original))
    before = path.read_bytes()
    original_call = reg.call.side_effect
    reg.call.side_effect = lambda method, day: (DataResult(data=None, source='tushare:daily', error='failed')
        if method == 'get_market_daily_quotes' else original_call(method, day))
    result = backfill(reg, DAY, DAY, **kwargs)
    assert result['receipts'][0]['status'] == 'fallback_preserved'
    assert path.read_bytes() == before and not sync.called
