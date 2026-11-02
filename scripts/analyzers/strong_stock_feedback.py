"""昨日收盘涨停股的次日事实反馈；与T-2连板→T-1断板→T反馈独立。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
import math
from pathlib import Path
import re
from statistics import mean, median
from zoneinfo import ZoneInfo

import yaml

from utils import is_st_stock
from utils.limit_identity import positive_streak as _limit_times, verified_streak

KEY = 'strong_stock_feedback'
SCHEMA = 'strong-stock-feedback-v1'
NOTE = ('[事实] 样本为严格上一开放日涨停名单中的非ST、非退市A股；不是断板股样本。'
        '触及前高=次日最高价×次日复权因子≥前日最高价×前日复权因子，含恰好相等。'
        '开盘涨跌按次日行情昨收，收盘涨跌用次日pct_chg；收阴=收盘<开盘，与收盘下跌不同。'
        '比例只以对应可计算样本为分母，缺失不补零；未确认停牌仍记行情缺口。'
        '出处：鞠磊笔记#1089，经验观点尚未转为胜率或筛选规则。')


def _base(day, **extra):
    return dict(schema=SCHEMA, trade_date=day, source_note_id=1089, status='source_failed',
                source_date=None, cohort_count=None, details=[], errors=[], sources={}, **extra)


def _number(value, positive=False):
    if isinstance(value, bool):
        raise ValueError('布尔值不是行情数值')
    n = float(value)
    if not math.isfinite(n) or (positive and n <= 0):
        raise ValueError('非法行情数值')
    return n


def _code(value):
    text = str(value or '').strip().upper()
    bare, dot, suffix = text.partition('.')
    if len(bare) != 6 or not bare.isascii() or not bare.isdigit():
        raise ValueError('股票代码非法')
    exchange = 'SH' if bare.startswith(('6', '900')) else 'BJ' if bare.startswith(('4', '8', '92')) else 'SZ'
    if dot and suffix != exchange:
        raise ValueError('股票交易所非法或冲突')
    return bare + '.' + exchange


def _row_code(row):
    values = [_code(row[k]) for k in ('code', 'ts_code') if row.get(k) is not None]
    if not values or len(set(values)) != 1:
        raise ValueError('股票身份缺失或冲突')
    return values[0]


def _index(rows, day):
    if not isinstance(rows, list) or not rows:
        raise ValueError('来源无有效数据')
    output = {}
    for row in rows:
        code = _row_code(row)
        if code in output or str(row.get('trade_date', '')).replace('-', '') != day.replace('-', ''):
            raise ValueError('来源代码重复或日期错位')
        output[code] = row
    return output




def _cohort(section):
    # 旧provider把空返回也标error，无法区分真空名单与未到数/失败。
    # 不把这种错误文本反推为已核验0；只有来源明确成功且count/名单一致才允许empty。
    if (not isinstance(section, dict) or section.get('error')
            or section.get('status') not in {None, 'complete', 'ok', 'success', 'empty'}
            or not isinstance(section.get('_source'), str) or not section['_source'].strip()):
        raise ValueError('上一开放日涨停来源缺失或失败')
    rows = section.get('stocks')
    if not isinstance(rows, list) or type(section.get('count')) is not int or section['count'] != len(rows):
        raise ValueError('涨停名单计数缺失或不完整')
    result, seen = [], set()
    for row in rows:
        code = _row_code(row)
        if code in seen:
            raise ValueError('涨停名单代码重复')
        seen.add(code)
        name = row.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('涨停名单名称缺失，无法核验ST身份')
        bare = code[:6]
        if is_st_stock(name) or '退' in name or bare.startswith(('200', '201', '900')):
            continue
        if not bare.startswith(('0', '3', '6', '4', '8', '92')):
            raise ValueError('无法确认A股身份')
        # 保留来源日身份，次日是否再涨停不能改变首板/连板分组；缺失不默认首板。
        height, evidence = verified_streak(row, section['_source'])
        result.append(dict(code=code, name=name, limit_times=height,
                           limit_times_verified=height is not None, limit_times_evidence=evidence))
    return result


def _ohlc(row):
    q = {k: _number(row[k], True) for k in ('open', 'high', 'low', 'close')}
    if q['low'] > min(q['open'], q['close']) or q['high'] < max(q['open'], q['close']) or q['low'] > q['high']:
        raise ValueError('OHLC范围矛盾')
    previous = _number(row['pre_close'], True)
    # 仅拦截数量级/单位损坏，不冒充板型涨跌停规则：名单未提供IPO/复牌无涨跌幅限制身份。
    # 宽松100倍界限保留特殊交易日；所有派生比值仍必须有限，极小昨收不能溢出。
    for price in q.values():
        ratio = _number(price / previous, True)
        if not 0.01 <= ratio <= 100:
            raise ValueError('OHLC相对昨收数量级异常')
    return q


def _summary(rows):
    quotes = [r for r in rows if r.get('quote_status') == 'complete']
    touches = [r for r in rows if type(r.get('touched_previous_high')) is bool]
    n, t = len(quotes), len(touches)
    def pct(count, total):
        return 100 * count / total if total else None
    hit = sum(r['touched_previous_high'] for r in touches)
    bearish = sum(r['bearish_candle'] for r in quotes)
    return dict(quote_count=n, touch_count=t,
                touched_count=hit if t else None, not_touched_count=t-hit if t else None,
                touched_rate_pct=pct(hit, t), bearish_count=bearish if n else None,
                bearish_rate_pct=pct(bearish, n),
                no_touch_bearish_count=sum(not r['touched_previous_high'] and r['bearish_candle'] for r in touches) if t else None,
                open_mean_pct=mean(r['open_pct'] for r in quotes) if n else None,
                open_median_pct=median(r['open_pct'] for r in quotes) if n else None,
                close_mean_pct=mean(r['close_pct'] for r in quotes) if n else None,
                close_median_pct=median(r['close_pct'] for r in quotes) if n else None)


def calculate(day, previous, section, source_data, *, source_errors=None, sources=None):
    """四个批量源；次日行情独立于跨日高点缺口保留，分母分别计数。"""
    result = _base(day)
    result.update(source_date=previous, sources=sources or {}, errors=list(source_errors or []))
    try:
        if date.fromisoformat(previous) >= date.fromisoformat(day):
            raise ValueError('来源日期必须早于反馈日')
        cohort = _cohort(section)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result['errors'].append(str(exc))
        return result
    result.update(cohort_count=len(cohort), source_count=section['count'], excluded_count=section['count']-len(cohort))
    if not cohort:
        result.update(status='empty', summary=_summary([]))
        return result
    indexed = {}
    for key in ('previous_quotes', 'current_quotes', 'previous_factors', 'current_factors'):
        try:
            indexed[key] = _index(source_data.get(key), previous if key.startswith('previous') else day)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            indexed[key] = {}
            result['errors'].append(f'{key}: {exc}')
    for stock in cohort:
        code = stock['code']
        row = dict(**stock, quote_status='source_failed', touched_previous_high=None, errors=[])
        try:
            raw = indexed['current_quotes'][code]
            q = _ohlc(raw)
            pre_close = _number(raw['pre_close'], True)
            close_pct = _number(raw['pct_chg'])
            if abs((q['close']/pre_close-1)*100-close_pct) > 0.03:
                raise ValueError('次日涨跌幅与行情昨收不一致')
            row.update(quote_status='complete', open_pct=(q['open']/pre_close-1)*100,
                       close_pct=close_pct, bearish_candle=q['close'] < q['open'],
                       candle='阴' if q['close'] < q['open'] else '阳' if q['close'] > q['open'] else '平',
                       current_high=q['high'], current_open=q['open'], current_close=q['close'], pre_close=pre_close)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            row['errors'].append(f'次日行情不可用:{exc}')
            result['details'].append(row)
            continue
        try:
            prior = _ohlc(indexed['previous_quotes'][code])
            prev_factor = _number(indexed['previous_factors'][code]['adj_factor'], True)
            cur_factor = _number(indexed['current_factors'][code]['adj_factor'], True)
            # 比较乘积而不先四舍五入换算价，恰好触及也算；除权两日处于同一坐标。
            old = Decimal(str(prior['high'])) * Decimal(str(prev_factor))
            new = Decimal(str(q['high'])) * Decimal(str(cur_factor))
            aligned = _number(float(old / Decimal(str(cur_factor))), True)
            row.update(touched_previous_high=new >= old, previous_high=prior['high'],
                       previous_high_in_current_basis=aligned, previous_factor=prev_factor, current_factor=cur_factor)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            row['errors'].append(f'前高比较不可用:{exc}')
        result['details'].append(row)
    result['summary'] = summary = _summary(result['details'])
    result['status'] = ('source_failed' if not summary['quote_count'] else
                        'complete' if summary['touch_count'] == len(cohort) and not result['errors'] else 'partial')
    return result


def _previous_open(conn, day):
    target = date.fromisoformat(day)
    rows = conn.execute('SELECT date,is_open FROM trade_calendar WHERE exchange=? AND date BETWEEN ? AND ? ORDER BY date DESC',
                        ('SSE', (target-timedelta(days=30)).isoformat(), day)).fetchall()
    expected = target
    for d, flag in rows:
        if d != expected.isoformat() or flag not in (0, 1):
            raise ValueError('交易日历缺口')
        if d == day and flag == 0:
            return None
        if d != day and flag == 1:
            return d
        expected -= timedelta(days=1)
    raise ValueError('交易日历不足，无法确认上一开放日')


def collect(registry, day, *, daily_dir, db_path=None, now=None):
    """随post采集，不自行写库/补日历/推送；异常隔离不阻断主流程。"""
    result = _base(day)
    try:
        now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if now.tzinfo is None:
            raise ValueError('运行时刻缺少时区')
        now = now.astimezone(ZoneInfo('Asia/Shanghai'))
        target = date.fromisoformat(day)
        if target > now.date():
            raise ValueError('目标日尚未到达')
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            previous = _previous_open(conn, day)
        finally:
            conn.close()
        if previous is None or (target == now.date() and now.hour < 16):
            return {**result, 'status': 'skipped', 'errors': ['非开放日或尚未收盘']}
        result['source_date'] = previous
        envelope = yaml.safe_load((Path(daily_dir)/previous/'post-market.yaml').read_text())
        if not isinstance(envelope, dict) or envelope.get('date') != previous:
            raise ValueError('上一开放日归档缺失或日期错位')
        section = envelope['raw_data']['limit_up']
        cohort = _cohort(section)
        data, errors = {}, []
        sources = {'limit_up': {'date': previous, 'source': section.get('_source')}}
        if cohort:
            for key, method, source_day in [('previous_quotes','get_market_daily_quotes',previous),
                                            ('current_quotes','get_market_daily_quotes',day),
                                            ('previous_factors','get_adj_factor',previous),
                                            ('current_factors','get_adj_factor',day)]:
                try:
                    response = registry.call(method, source_day)
                    sources[key] = dict(date=source_day, source=response.source, status='complete' if response.success else 'source_failed')
                    if response.success:
                        data[key] = response.data
                    else:
                        errors.append(f'{key}:来源失败')
                except Exception:
                    sources[key] = dict(date=source_day, status='source_failed')
                    errors.append(f'{key}:来源调用异常')
        return calculate(day, previous, section, data, source_errors=errors, sources=sources)
    except Exception as exc:
        result['errors'].append(f'次日反馈不可用:{type(exc).__name__}:{exc}')
        return result


def load_snapshot(daily_dir, day):
    """正式HTML只读同日信封，旧归档未计算，不回退其他日期。"""
    try:
        envelope = yaml.safe_load((Path(daily_dir)/day/'post-market.yaml').read_text())
        if not isinstance(envelope, dict) or envelope.get('date') != day:
            raise ValueError('归档日期错位')
        block = envelope.get('raw_data', {}).get(KEY)
        return block if block is not None else {**_base(day), 'status': 'not_computed'}
    except FileNotFoundError:
        return {**_base(day), 'status': 'not_computed'}
    except Exception:
        return {**_base(day), 'errors': ['盘后归档不可读']}


def _view(block, day):
    """展示前校验计数和日期；损坏块不冒充完整统计。"""
    if block is None:
        return {**_base(day), 'status': 'not_computed'}
    try:
        if block['schema'] != SCHEMA or block['trade_date'] != day or block['status'] not in {'complete','partial','source_failed','empty','skipped','not_computed'}:
            raise ValueError('状态或日期非法')
        rows = block['details']
        if not isinstance(rows, list) or not isinstance(block['errors'], list) or any(not isinstance(e, str) for e in block['errors']):
            raise ValueError('明细或错误列表非法')
        for row in rows:
            if not isinstance(row['name'], str) or not row['name'].strip() or row['quote_status'] not in {'complete','source_failed'}:
                raise ValueError('逐股身份或状态非法')
            if not isinstance(row['errors'], list) or any(not isinstance(e,str) for e in row['errors']):
                raise ValueError('逐股缺口非法')
            touch = row['touched_previous_high']
            if touch is not None and type(touch) is not bool:
                raise ValueError('触及状态非法')
            if row['quote_status'] == 'complete':
                for key in ('open_pct','close_pct'):
                    _number(row[key])
                if type(row['bearish_candle']) is not bool or row['candle'] not in {'阴','阳','平'}:
                    raise ValueError('阴阳线状态非法')
                if row['bearish_candle'] != (row['candle'] == '阴'):
                    raise ValueError('阴阳线矛盾')
            elif touch is not None:
                raise ValueError('缺行情不能判断触及')
        if rows or block['status'] in {'complete','partial','empty'}:
            if type(block['cohort_count']) is not int or block['cohort_count'] != len(rows):
                raise ValueError('样本计数矛盾')
            if len({_row_code(r) for r in rows}) != len(rows):
                raise ValueError('重复股票')
            if block['summary'] != _summary(rows):
                raise ValueError('汇总与明细不一致')
            n = block['summary']['touch_count']
            if (block['status'] == 'complete' and (not rows or n != len(rows) or block['errors'])
                    or block['status'] == 'empty' and rows
                    or block['status'] == 'partial' and (not block['summary']['quote_count'] or (n == len(rows) and not block['errors']))
                    or block['status'] == 'source_failed' and block['summary']['quote_count']):
                raise ValueError('完整状态矛盾')
            if date.fromisoformat(block['source_date']) >= date.fromisoformat(day):
                raise ValueError('来源日期非法')
        return block
    except (ValueError, TypeError, KeyError, AttributeError):
        return {**_base(day), 'errors': ['快照内容或统计口径损坏']}


def _table(block):
    def number(v):
        return '未计算' if v is None else f'{v:+.2f}%'
    rows = []
    for r in block.get('details', []):
        touch = r.get('touched_previous_high')
        rows.append([f"{r['name']}（{r['code']}）", '未计算' if touch is None else '触及' if touch else '未触及',
                     number(r.get('open_pct')), number(r.get('close_pct')), r.get('candle','未计算'),
                     '；'.join(r.get('errors', [])) or '完整'])
    return ['股票', '次日触及前高', '开盘涨跌', '收盘涨跌', 'K线阴阳', '缺口'], rows


def _description(block):
    state = block['status']
    text = f"[事实] 昨日涨停股次日反馈：{state}；观察日{block['trade_date']}，来源日{block.get('source_date') or '未核验'}。"
    s = block.get('summary')
    if state == 'empty':
        return text + '来源名单已核验，符合口径样本为0。'
    if state not in {'complete','partial'} or not s:
        return text + '未计算，不代表没有样本或反馈为0。'
    def val(k, suffix=''):
        return '未计算' if s[k] is None else f'{s[k]:.2f}{suffix}'
    return (text + f"应有{block['cohort_count']}只，次日行情{ s['quote_count']}只，前高比较{s['touch_count']}只；"
            f"触及率{val('touched_rate_pct','%')}，收阴率{val('bearish_rate_pct','%')}，"
            f"未触及且收阴{str(s['no_touch_bearish_count']) if s['no_touch_bearish_count'] is not None else '未计算'}只。"
            f"开盘均值/中位{val('open_mean_pct','%')}/{val('open_median_pct','%')}；"
            f"收盘均值/中位{val('close_mean_pct','%')}/{val('close_median_pct','%')}。")


def render_markdown(block, day, *, history=None):
    if history is not None and history.get('date') != day:
        raise ValueError('历史统计观察日与报告日期不一致')
    block = _view(block, day)
    def cell(v):
        return re.sub(r'([\\`*_{}\[\]()#+.!|>-])', r'\\\1', escape(str(v), quote=False).replace('\n',' ').replace('\r',' '))
    lines = [cell(_description(block)), cell(NOTE)]
    lines += [cell('缺口：'+'；'.join(block.get('errors', [])))] if block.get('errors') else []
    headers, rows = _table(block)
    if rows:
        lines += ['| '+' | '.join(headers)+' |', '|'+'---|'*len(headers)]
        lines += ['| '+' | '.join(map(cell,row))+' |' for row in rows[:10]]
        if len(rows)>10:
            lines.append('仅列前10只；完整逐股明细保存在同日盘后归档及复盘HTML。')
    if history is not None:
        from analyzers.strong_stock_feedback_history import render_markdown as render_history
        lines.append(render_history(history))
    return '\n'.join(lines)


def _visual_progress(label, numerator, denominator, *, key, suffix='只'):
    """Independent denominator per measure; no filled bar for an unknown rate."""
    known = (type(numerator) is int and type(denominator) is int
             and denominator > 0 and 0 <= numerator <= denominator)
    pct = 100 * numerator / denominator if known else None
    value = f'{numerator}/{denominator}{suffix} · {pct:.1f}%' if known else '未计算'
    attrs = (f' role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.6f}"'
             f' aria-label="{escape(label, quote=True)}"' if known else '')
    mark = f'<span style="display:block;height:100%;width:{pct:.6f}%;background:#287b79"></span>' if known else ''
    return (f'<div data-feedback-metric="{escape(key, quote=True)}" data-numerator="{numerator if numerator is not None else ""}" '
            f'data-denominator="{denominator if denominator is not None else ""}" style="min-width:0;margin:10px 0">'
            f'<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><span>{escape(label)}</span><strong>{value}</strong></div>'
            f'<div{attrs} style="height:9px;background:rgba(128,128,128,.14);margin-top:6px">{mark}</div></div>')


def _visual_returns(summary):
    metrics = [('open_mean_pct', '开盘均值'), ('close_mean_pct', '收盘均值'),
               ('open_median_pct', '开盘中位'), ('close_median_pct', '收盘中位')]
    values = [summary.get(key) for key, _ in metrics]
    bound = max(1, *(abs(v) for v in values if v is not None))
    parts = ['<div data-feedback-returns="v1" style="min-width:0;margin-top:16px"><strong>开盘 / 收盘涨跌</strong>',
             f'<div style="display:flex;justify-content:space-between;font-size:11px;margin-top:7px"><span>−{bound:.2f}%</span><span>0</span><span>+{bound:.2f}%</span></div>']
    for key, label in metrics:
        value = summary.get(key)
        display = '未计算' if value is None else f'{value:+.2f}%'
        attr = '' if value is None else f' data-value="{value:.10f}"'
        mark = ''
        if value is not None:
            endpoint = 50 + 50 * value / bound
            color = '#4c6fa9' if key.startswith('open') else '#287b79'
            mark = (f'<span style="position:absolute;left:{min(50, endpoint):.6f}%;width:{abs(endpoint-50):.6f}%;height:100%;background:{color}"></span>')
        parts.append(f'<div data-feedback-return="{key}"{attr} style="margin:8px 0">'
                     f'<div style="display:flex;justify-content:space-between;gap:8px"><span>{label}</span><strong>{display}</strong></div>'
                     '<div style="position:relative;height:9px;background:rgba(128,128,128,.14);margin-top:4px">'
                     +mark+'<span style="position:absolute;left:50%;height:100%;border-left:1px solid #777"></span></div></div>')
    return ''.join(parts)+'</div>'


def render_html(block, day, *, history=None):
    if history is not None and history.get('date') != day:
        raise ValueError('历史统计观察日与报告日期不一致')
    block = _view(block, day)
    description = _description(block)
    html = [f'<div data-strong-stock-feedback="v1" data-as-of="{escape(day,quote=True)}" data-source-status="{block["status"]}" style="min-width:0;max-width:100%">',
            '<h4>昨日涨停股 · 次日反馈</h4>',
            f'<p style="font-size:12px">观察日 {escape(day)} · 名单来源日 {escape(block.get("source_date") or "未核验")}</p>']
    summary = block.get('summary')
    if block['status'] in {'complete', 'partial'} and summary:
        n = block['cohort_count']
        html.append(f'<p>已核验行情 {summary["quote_count"]}/{n}只 · 前高比较 {summary["touch_count"]}/{n}只</p>')
        html.append(_visual_progress('触及前高（含等于）', summary['touched_count'], summary['touch_count'], key='touched'))
        html.append(_visual_progress('收阴（收盘低于开盘）', summary['bearish_count'], summary['quote_count'], key='bearish'))
        html.append('<p style="font-size:12px">两项分母独立；收阴不等于当日下跌。</p>')
        html.append(_visual_returns(summary))
        if block['status'] == 'partial':
            html.append(f'<p>待核验：行情 {n-summary["quote_count"]}只 · 前高 {n-summary["touch_count"]}只；其他来源缺口见依据。</p>')
    else:
        html.append('<p>名单已核验，符合口径样本为0。</p>' if block['status'] == 'empty' else '<p>反馈未计算，来源尚未核验。</p>')
    headers, rows = _table(block)
    disclosure = (f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{len(rows)}" data-evidence-kind="strong-stock-feedback"><summary>逐股反馈与统计依据（{len(rows)}项）</summary>'
                  if rows else '<details class="rv-subdetail"><summary>来源与统计依据</summary>')
    html += [disclosure, f'<p>{escape(description)}</p><p>{escape(NOTE)}</p>']
    if block.get('errors'):
        html += ['<p>'+escape('；'.join(block['errors']))+'</p>']
    if rows:
        html += ['<div style="overflow-x:auto;max-width:100%"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>']
        html += ['<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows]
        html += ['</tbody></table></div>']
    html.append('</details>')
    history_gap = None
    if history is not None:
        from analyzers.strong_stock_feedback_history import render_html as render_history
        fragment, history_gap = render_history(history)
        html.append(fragment)
    html += ['</div>']
    gaps = ([description] if block['status'] not in {'complete','empty'} else []) + ([history_gap] if history_gap else [])
    return '\n'.join(html), '；'.join(gaps) if gaps else None


def html_signature(html, marker="data-strong-stock-feedback"):
    """只签名解析器实际遇到的模块；注释/script中的原文不能替代显示模块。"""
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks, self.active, self.depth = [], None, 0

        def handle_starttag(self, tag, attrs):
            if self.active is None and any(k == marker for k, _ in attrs):
                self.active = []
                self.blocks.append(self.active)
            if self.active is not None:
                self.active.append(('start', tag, tuple(sorted(attrs))))
                if tag == 'div':
                    self.depth += 1

        def handle_endtag(self, tag):
            if self.active is not None:
                self.active.append(('end', tag))
                if tag == 'div':
                    self.depth -= 1
                    if self.depth == 0:
                        self.active = None

        def handle_data(self, data):
            if self.active is not None:
                self.active.append(('text', data))

        def handle_comment(self, data):
            if self.active is not None:
                self.active.append(('comment', data))

    parser = Parser()
    parser.feed(html)
    return parser.blocks
