"""当晚只修复已发布盘后信封中就绪检查点名的失败来源。"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import yaml

from db.dual_write import parse_post_market_envelope
from services.daily_leaders.readiness import (
    DATED_BLOCKS, DataNotReady, is_open_day, now_shanghai, validate_source_block,
)
from services.daily_leaders.retry import check_window
from services.low_price_effect_history import DEFAULT_DAILY_DIR
from utils.post_archive import archive_lock, atomic_write_archive, read_archive

logger = logging.getLogger(__name__)
DATED_SOURCES = {
    'limit_step': 'tushare:limit_step', 'limit_cpt_list': 'tushare:limit_cpt_list',
    'sector_moneyflow_ths': 'tushare:moneyflow_ind_ths', 'concept_moneyflow_ths': 'tushare:moneyflow_cnt_ths',
    'sector_moneyflow_dc': 'tushare:moneyflow_ind_dc', 'concept_moneyflow_dc': 'tushare:moneyflow_ind_dc:concept',
}
RANKING_SOURCES = {'sector_industry': 'tushare:sw_daily', 'sector_concept': 'tushare:ths_daily'}


def verified_recovery_block(conn, date, key, block):
    if key not in (*DATED_SOURCES, *RANKING_SOURCES):
        return False
    try:
        validate_source_block(conn, date, key, block)
    except DataNotReady:
        return False
    if key in RANKING_SOURCES:
        bottom = block.get('bottom')
        top = block['data']
        if (block['_source'] != RANKING_SOURCES[key] or not isinstance(bottom, list)
                or len(top) != 30 or len(bottom) != 5
                or not isinstance(block.get('universe_count'), int) or block['universe_count'] < 35):
            return False
        try:
            validate_source_block(conn, date, key, {**block, 'data': bottom})
        except DataNotReady:
            return False
        rows = top + bottom
        codes = [r.get('ts_code') for r in rows]
        gains = [float(r['change_pct']) for r in top]
        losses = [float(r['change_pct']) for r in bottom]
        return (all(isinstance(code, str) and code.strip() for code in codes) and len(set(codes)) == 35
                and gains == sorted(gains, reverse=True) and losses == sorted(losses) and min(gains) >= max(losses)
                and all(isinstance(row.get('name'), str) and row['name'].strip() for row in rows)
                and all(str(row.get('trade_date', '')).replace('-', '') == date.replace('-', '') for row in rows))
    return block['_source'] == DATED_SOURCES[key]


def _fetch(registry, date, key):
    provider = registry.get_provider('tushare') if registry else None
    if provider is None or not provider.initialize():
        return None
    if key in RANKING_SOURCES:
        result = provider.get_sector_rankings(date, key.removeprefix('sector_'), require_dated=True)
        if result.success and isinstance(result.data, dict) and result.source == RANKING_SOURCES[key]:
            return {'_source': result.source, 'data': result.data.get('top'), 'bottom': result.data.get('bottom'),
                    'universe_count': result.data.get('universe_count')}
    elif key in DATED_BLOCKS:
        result = getattr(provider, 'get_' + key)(date)
        if result.success and result.source:
            return {'_source': result.source, 'data': result.data}
    return None


def _matching_version(envelope, date, issue):
    return (isinstance(envelope, dict) and envelope.get('date') == date
            and envelope.get('generated_at') == issue.generated_at
            and isinstance(envelope.get('raw_data'), dict) and envelope['raw_data'].get('date') == date)


def recover_source(conn, date, issue, registry, *, input_by, daily_dir=DEFAULT_DAILY_DIR, now=now_shanghai):
    """锁外定向取数，锁内核对两端旧版本；已有成功侧先用于补齐失败侧。"""
    key = issue.key
    if input_by != 'launchd' or key not in (*DATED_BLOCKS, *RANKING_SOURCES):
        return False
    check_window(date, now())
    path = Path(daily_dir) / date / 'post-market.yaml'
    try:
        original = read_archive(path)
        if not _matching_version(original, date, issue):
            return False
        file_block = original['raw_data'].get(key)
        if verified_recovery_block(conn, date, key, file_block):
            block = file_block
        elif file_block != issue.block:
            return False
        else:
            block = _fetch(registry, date, key)
            if not verified_recovery_block(conn, date, key, block):
                return False
            block['_recovery'] = {'fact_date': date, 'input_by': input_by, 'fetched_at': now().isoformat()}
        with archive_lock(path), conn:
            conn.execute('BEGIN IMMEDIATE')
            current = read_archive(path)
            row = conn.execute('SELECT raw_data FROM daily_market WHERE date=?', (date,)).fetchone()
            db_env = parse_post_market_envelope(row[0]) if row else None
            if not (_matching_version(current, date, issue) and _matching_version(db_env, date, issue)
                    and is_open_day(conn, date)):
                return False
            if db_env['raw_data'].get(key) != issue.block or current['raw_data'].get(key) != file_block:
                return False
            check_window(date, now())
            if block != file_block:
                current['raw_data'][key] = block
                atomic_write_archive(path, current)
            db_env['raw_data'][key] = block
            conn.execute('UPDATE daily_market SET raw_data=? WHERE date=?', (json.dumps(db_env, ensure_ascii=False), date))
    except (OSError, ValueError, sqlite3.Error, yaml.YAMLError) as exc:
        if isinstance(exc, TimeoutError):
            raise
        logger.warning('每日最票补采未发布 date=%s source=%s error=%s', date, key, type(exc).__name__)
        return False
    logger.info('每日最票已补齐当日来源 date=%s source=%s input_by=%s', date, key, input_by)
    return True
