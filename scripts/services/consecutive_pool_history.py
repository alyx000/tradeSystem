"""连板池受控历史补采：仅补目标事实块、备份原文、标准双写，无推送。"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from analyzers import consecutive_pool as pool
from db.connection import get_readonly_connection
from services.low_price_effect_history import _atomic_text, DEFAULT_DAILY_DIR

ROOT = Path(__file__).resolve().parents[2]


def select_dates(conn, start_date, end_date):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if not 0 <= (end-start).days <= 92:
        raise ValueError('补采窗口须为1至93个自然日')
    rows = conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ? ORDER BY date", (start_date, end_date)).fetchall()
    expected = [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    if [r[0] for r in rows] != expected or any(r[1] not in (0, 1) for r in rows):
        raise ValueError('补采区间SSE自然日历不完整')
    days = [r[0] for r in rows if r[1] == 1]
    if not days:
        raise ValueError('补采区间没有开放日')
    return days


def backfill(registry, start_date, end_date, *, input_by, daily_dir=DEFAULT_DAILY_DIR,
             report_dir=ROOT/'data/reports/consecutive-pool', db_path=None, sync_fn=None, now=None):
    if not isinstance(input_by, str) or not input_by.strip():
        raise ValueError('input_by不能为空')
    current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if current.tzinfo is None:
        raise ValueError('运行时刻无时区')
    current = current.astimezone(ZoneInfo('Asia/Shanghai'))
    if end_date > current.date().isoformat() or (end_date == current.date().isoformat() and current.hour < 16):
        raise ValueError('目标日尚未完成盘后窗口')
    with closing(get_readonly_connection(db_path)) as conn:
        dates = select_dates(conn, start_date, end_date)
    if sync_fn is None:
        from db.dual_write import sync_daily_market_to_db
        sync_fn = lambda day, envelope: sync_daily_market_to_db(day, envelope, db_path=db_path)
    run_dir = Path(report_dir)/'runs'/current.strftime('%Y%m%dT%H%M%S%f')
    run_dir.mkdir(parents=True, exist_ok=False)
    receipts = []
    for day in dates:
        receipt = dict(trade_date=day, input_by=input_by.strip())
        try:
            path = Path(daily_dir)/day/'post-market.yaml'
            original = path.read_bytes()
            envelope = yaml.load(original, Loader=yaml.CSafeLoader)
            if (not isinstance(envelope, dict) or str(envelope.get('date')) != day
                    or not isinstance(envelope.get('raw_data'), dict)):
                raise ValueError('既有盘后归档日期或结构无效，拒绝构造或替换')
            old = pool.from_archive(envelope, day)
            if old['status'] == 'complete' and pool.KEY in envelope['raw_data']:
                if path.read_bytes() != original:
                    raise ValueError('归档已并发变化，拒绝同步')
                synced = bool(sync_fn(day, envelope))
                receipt.update(status='already_complete' if synced else 'sync_failed', db_synced=synced,
                               source_status='complete', count=old['count'], amount_yi=old['amount_yi'])
            else:
                # 旧证据已完整时复用；否则仅补两项日期键控来源。
                if old['status'] == 'complete':
                    snapshot, mode = old, 'archived_recalculation'
                else:
                    limit = registry.call('get_limit_up_list', day)
                    quotes = registry.call('get_market_daily_quotes', day)
                    # 保留缺失字段供严格校验；不能将{}或仅count=0合成为完整空名单。
                    limit_section = deepcopy(limit.data) if limit.success and isinstance(limit.data, dict) else {'error': limit.error or '涨停名单结构非法'}
                    limit_section['_source'] = limit.source
                    snapshot = pool.collect(day, limit_up=limit_section,
                                            quote_result=quotes, db_path=db_path, now=current)
                    mode = 'historical_backfill'
                snapshot['collection_receipt'] = dict(mode=mode, input_by=input_by.strip(),
                    collected_at=current.isoformat(), original_sha256=hashlib.sha256(original).hexdigest())
                source_path = run_dir/f'{day}.sources.json'
                source_text = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
                _atomic_text(source_path, source_text)
                receipt.update(source_path=str(source_path), source_sha256=hashlib.sha256(source_text.encode()).hexdigest(),
                               source_status=snapshot['status'], mode=mode, gaps=snapshot['gaps'])
                if snapshot['status'] not in {'complete', 'partial'}:
                    receipt['status'] = 'source_failed'
                elif ((old['count'] is not None and snapshot['count'] is None)
                      or (old['amount_yi'] is not None and snapshot['amount_yi'] is None)
                      or (snapshot['status'] != 'complete' and
                          {r['code'] for r in old['rows'] if r['amount_yi'] is not None} -
                          {r['code'] for r in snapshot['rows'] if r['amount_yi'] is not None})):
                    receipt['status'] = 'fallback_preserved'
                else:
                    if path.read_bytes() != original:
                        raise ValueError('归档在补采期间变化，拒绝覆盖，请重试')
                    backup = run_dir/f'{day}.post-market.before.yaml'
                    _atomic_text(backup, original.decode('utf-8'))
                    updated = deepcopy(envelope)
                    updated['raw_data'][pool.KEY] = snapshot
                    _atomic_text(path, yaml.dump(updated, Dumper=yaml.CSafeDumper, allow_unicode=True, sort_keys=False))
                    synced = bool(sync_fn(day, updated))
                    receipt.update(status='persisted' if synced else 'sync_failed', db_synced=synced,
                                   backup_path=str(backup), original_sha256=hashlib.sha256(original).hexdigest(),
                                   count=snapshot['count'], amount_yi=snapshot['amount_yi'])
        except Exception as exc:
            receipt.update(status='failed', error=str(exc))
        receipts.append(receipt)
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
        _atomic_text(run_dir/'receipt.json', json.dumps(receipts, ensure_ascii=False, indent=2))
    result = dict(status='complete' if all(r.get('db_synced') and r.get('source_status') == 'complete' for r in receipts) else 'partial',
                  start_date=start_date, end_date=end_date, trading_days=len(dates), run_dir=str(run_dir),
                  receipts=receipts, push_status='not_requested')
    _atomic_text(run_dir/'result.json', json.dumps(result, ensure_ascii=False, indent=2))
    return result
