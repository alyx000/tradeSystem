"""指数缺口修复事实：复用择时日线，存证后只读重算，不生成右侧结论。"""
from __future__ import annotations

from contextlib import closing
from datetime import date
from html import escape
import json
import math
from pathlib import Path
import sqlite3

from services.volume_concentration.capacity_history import _open_days
from services.review_visual_context import load_visual_context, validate_visual_context
from .constants import INDEX_LIST

KEY = 'index_repair'
SCHEMA = 'index-repair-v1'
MARKER = 'data-index-repair'
WINDOW = 60  # 工程观察窗；另取前一开放日作为第一天缺口的比较基准。
NOTE = ('[事实] 观察最近60个开放日新形成的日线向下缺口，不代表全部历史缺口。'
        '形成条件为当日最高价低于前日最低价；触及边界算回补，开盘低开不等于日线缺口。'
        '盘中完全回补、首次收盘回补及当前收盘位置分别记录，不等同阳包阴或右侧确认。'
        '经过天数不含形成日，按开放日计；缺少任一日行情或日历则不计算。'
        '出处：鞠磊笔记#1239；两天回补、1.8万亿元不作为自动判强弱阈值。')


def failed(day, code, reason, status='source_failed'):
    return dict(schema=SCHEMA, trade_date=day, index_code=code, status=status, reason=reason)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('指数OHLC须为有限正数')
    return float(value)


def calculate(evidence, day, code):
    """仅从61日完整同源OHLC重算。保留原始边界，不四舍五入后比较。"""
    if not isinstance(evidence, dict) or evidence.get('trade_date') != day or evidence.get('index_code') != code:
        raise ValueError('指数证据日期或身份不符')
    dates, bars = evidence['open_dates'], evidence['bars']
    if (not isinstance(evidence.get('source'), str) or not evidence['source'].strip()
            or not isinstance(dates, list) or len(dates) != WINDOW + 1
            or any(not isinstance(d, str) or date.fromisoformat(d).isoformat() != d for d in dates)
            or dates != sorted(set(dates)) or dates[-1] != day
            or not isinstance(bars, list) or [b['trade_date'] for b in bars] != dates):
        raise ValueError('日线窗口缺失、重复、错序或来源未核验')
    clean = []
    for b in bars:
        values = {k: _number(b[k]) for k in ('open', 'high', 'low', 'close')}
        if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
            raise ValueError('指数OHLC关系非法')
        clean.append(values)
    gaps = []
    for i in range(1, len(clean)):
        lower, upper = clean[i]['high'], clean[i-1]['low']
        if lower >= upper:
            continue
        after = list(range(i+1, len(clean)))
        filled = next((j for j in after if clean[j]['high'] >= upper), None)
        close_filled = next((j for j in after if clean[j]['close'] >= upper), None)
        reached = max([lower] + [clean[j]['high'] for j in after])
        state = 'filled' if filled is not None else 'partial' if reached > lower else 'unfilled'
        gaps.append(dict(formed_date=dates[i], previous_date=dates[i-1], lower=lower, upper=upper,
            state=state, elapsed_open_days=len(clean)-1-i,
            remaining_lower=None if filled is not None else max(lower, reached),
            fill_date=dates[filled] if filled is not None else None,
            days_to_fill=filled-i if filled is not None else None,
            close_fill_date=dates[close_filled] if close_filled is not None else None,
            current_close_above=clean[-1]['close'] >= upper))
    ma20 = sum(b['close'] for b in clean[-20:])/20
    prior_ma20 = sum(b['close'] for b in clean[-21:-1])/20
    close = clean[-1]['close']
    return dict(schema=SCHEMA, trade_date=day, index_code=code, status='complete',
        window_start=dates[1], gaps=gaps, unfilled_count=sum(g['state'] != 'filled' for g in gaps),
        close=close, ma20=ma20, ma20_position='上方' if close > ma20 else '下方' if close < ma20 else '贴线',
        ma20_direction='向上' if ma20 > prior_ma20 else '向下' if ma20 < prior_ma20 else '持平',
        evidence=evidence)


def capture(conn, bars, day, code, source):
    """只读本地日历，复用已请求的行情；独立失败不改变原择时信号。"""
    try:
        dates = _open_days(conn, day, WINDOW+1)
        selected = []
        for b in bars:
            if dates[0] <= b['trade_date'] <= day:
                for key in ('ts_code', 'code'):
                    if key in b and b[key] != code:
                        raise ValueError('日线响应指数身份不符')
                selected.append({k: b[k] for k in ('trade_date', 'open', 'high', 'low', 'close')})
        return calculate(dict(trade_date=day, index_code=code, source=source, open_dates=dates, bars=selected), day, code)
    except (ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
        return failed(day, code, str(exc))


def validate(block, day, code):
    if block is None:
        return failed(day, code, '旧快照未保存缺口日线依据', 'not_computed')
    try:
        if (block.get('schema') != SCHEMA or block.get('trade_date') != day or block.get('index_code') != code):
            raise ValueError('缺口快照身份或日期不符')
        if block.get('status') in ('source_failed', 'not_computed'):
            if not isinstance(block.get('reason'), str) or not block['reason']:
                raise ValueError('失败快照缺少原因')
            return failed(day, code, block['reason'], block['status'])
        expected = calculate(block['evidence'], day, code)
        if block != expected:
            raise ValueError('缺口结果与日线依据不一致')
        return expected
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return failed(day, code, str(exc))


def load_for_review(db_path, report_date):
    context = load_visual_context(db_path, report_date)
    day = context['fact_date']
    result = dict(report_date=report_date, context=context, rows=[])
    try:
        if day is None:
            raise ValueError(context['reason'])
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            records = dict(conn.execute('SELECT index_code, fractal_json FROM market_timing_signal WHERE trade_date=?', (day,)))
            # 独立日历核验：存证中的日期序列也必须与本次报告所选数据库一致。
            dates = _open_days(conn, day, WINDOW+1)
            for item in INDEX_LIST:
                code = item['code']
                try:
                    state = json.loads(records[code]) if code in records and records[code] is not None else {}
                    if not isinstance(state, dict):
                        raise ValueError('指数证据快照损坏')
                    block = validate(state.get(KEY), day, code)
                    if block['status'] == 'complete' and block['evidence']['open_dates'] != dates:
                        raise ValueError('缺口存证与本地开放日历不符')
                except (ValueError, TypeError) as exc:
                    block = failed(day, code, str(exc))
                result['rows'].append(dict(name=item['name'], block=block))
    except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
        result['rows'] = [dict(name=i['name'], block=failed(day or report_date, i['code'], str(exc))) for i in INDEX_LIST]
    return result


def _fmt(value):
    return f'{value:.2f}'


def render_html(payload, report_date):
    context = validate_visual_context(payload['context'], report_date)
    if payload.get('report_date') != report_date:
        raise ValueError('缺口报告日期不符')
    day = context['fact_date'] or report_date
    expected = [(i['name'], i['code']) for i in INDEX_LIST]
    if [(r['name'], r['block']['index_code']) for r in payload['rows']] != expected:
        raise ValueError('指数缺口清单不完整或身份不符')
    rows = [(r['name'], validate(r['block'], day, r['block']['index_code'])) for r in payload['rows']]
    if context['status'] != 'complete':
        rows = [(name, failed(day, b['index_code'], context['reason'])) for name, b in rows]
    fact_label = f'事实日{day}' if context['status'] == 'complete' else '事实日未核验'
    parts = [f'<div {MARKER}="{escape(report_date)}" data-as-of="{escape(day)}"><h3>指数缺口修复</h3>',
             f'<p>[事实] 报告日{escape(report_date)}；{escape(fact_label)}。近60个开放日形成的缺口，未回补数仅限此窗口。</p>']
    if context['status'] == 'complete' and day != report_date:
        parts.append('<p>[事实] 休市展示最近开放日历史，非当日新增。</p>')
    gaps, detail = [], []
    labels = dict(filled='已完全回补', partial='部分回补', unfilled='尚未回补')
    for name, block in rows:
        if block['status'] != 'complete':
            text = f"{name}：未计算；{block['reason']}"
            gaps.append(text)
            parts.append('<p>[事实] '+escape(text)+'。</p>')
            continue
        latest = block['gaps'][-1] if block['gaps'] else None
        desc = (f"最近缺口{latest['formed_date']}，{labels[latest['state']]}，形成后{latest['elapsed_open_days']}个交易日"
                if latest else '窗口内未形成日线向下缺口')
        parts.append('<p>[事实] '+escape(f"{name}：未完全回补{block['unfilled_count']}个；{desc}；收盘在MA20{block['ma20_position']}，MA20{block['ma20_direction']}。")+'</p>')
        detail.append('<p>'+escape(f"{name}（{block['index_code']}）；{block['window_start']}—{day}；来源{block['evidence']['source']}；收盘{_fmt(block['close'])}，MA20={_fmt(block['ma20'])}。")+'</p>')
        for g in reversed(block['gaps']):
            text = (f"{name} {g['formed_date']}：原缺口{_fmt(g['lower'])}—{_fmt(g['upper'])}；{labels[g['state']]}；"
                    f"首次盘中完全回补{g['fill_date'] or '尚未'}（用时{g['days_to_fill'] if g['days_to_fill'] is not None else '未完成'}个交易日）；"
                    f"首次收盘回补{g['close_fill_date'] or '尚未'}；当前收盘{'已在' if g['current_close_above'] else '未在'}上沿及以上")
            if g['remaining_lower'] is not None:
                text += f"；剩余区间{_fmt(g['remaining_lower'])}—{_fmt(g['upper'])}"
            detail.append('<p>'+escape(text)+'。</p>')
        detail.append('<p>日线依据：'+escape('；'.join(f"{b['trade_date']} 开{b['open']} 高{b['high']} 低{b['low']} 收{b['close']}" for b in block['evidence']['bars']))+'</p>')
    parts += [f'<details class="evidence" data-as-of="{escape(day)}" data-items="{max(1,len(detail))}"><summary>逐个缺口、MA20与日线来源（{max(1,len(detail))}项）</summary>', '<p>'+escape(NOTE)+'</p>', *detail, '</details></div>']
    return ''.join(parts), ('指数缺口修复部分或全部未计算；'+ '；'.join(gaps)) if gaps else ''
