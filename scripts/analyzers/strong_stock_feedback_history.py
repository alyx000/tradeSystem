"""昨日涨停反馈的只读历史与来源日分组；不补采、不补写、不扩大时间窗。"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
from pathlib import Path
import sqlite3

import yaml

from analyzers import strong_stock_feedback as sf
from utils.price_limit import limit_pct_for

SCHEMA = 'strong-feedback-history-v1'
WINDOWS = (5, 10, 20)
GROUPS = (('all','全部'), ('first','首板'), ('consecutive','连板'), ('height_unknown','连板次数未知'),
          ('10cm','10cm'), ('20cm','20cm'), ('30cm','30cm'), ('board_unknown','板型未知'))
NOTE = ('[事实] 分组按来源日涨停身份；板型为常规10/20/30cm分类，不代表当日实际涨停幅度。'
        '窗口锁定最近5/10/20个开放日，只合并complete/empty日期，每个股票每天计1个股票日；'
        '比例按股票日合并计数，均值/中位来自逐股记录，不平均每日比例。'
        '每日样本会变化，日差不是同一批股票的涨跌；缺日、未计算和失败不补零，不向更早日期凑满。')


limit_times = sf._limit_times


def _calendar(conn, day):
    end = date.fromisoformat(day)
    rows = conn.execute('SELECT date,is_open FROM trade_calendar WHERE exchange=? AND date BETWEEN ? AND ? ORDER BY date DESC',
                        ('SSE', (end-timedelta(days=120)).isoformat(), day)).fetchall()
    expected, opened = end, []
    for d, flag in rows:
        if d != expected.isoformat() or flag not in (0,1):
            raise ValueError('日历缺口')
        if d == day and not flag:
            raise ValueError('观察日非开放日')
        if flag:
            opened.append(d)
        if len(opened) == 21:
            return list(reversed(opened))
        expected -= timedelta(days=1)
    raise ValueError('日历不足21个开放日')


def _envelope(daily_dir, day, cache):
    if day not in cache:
        try:
            value = yaml.load((Path(daily_dir)/day/'post-market.yaml').read_text(),
                              Loader=getattr(yaml,'CSafeLoader',yaml.SafeLoader))
            if not isinstance(value, dict) or value.get('date') != day or not isinstance(value.get('raw_data'),dict):
                raise ValueError('归档日期或结构非法')
            cache[day] = value
        except FileNotFoundError:
            cache[day] = 'missing-data'
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            cache[day] = 'source_failed'
    return cache[day]


def _snapshot(daily_dir, day, cache):
    envelope = _envelope(daily_dir,day,cache)
    if isinstance(envelope,str):
        return {**sf._base(day), 'status':envelope}
    block = envelope['raw_data'].get(sf.KEY)
    return {**sf._base(day),'status':'not_computed'} if block is None else sf._view(block,day)


def _groups(rows):
    result = {}
    for key,label in GROUPS:
        group = [r for r in rows if key=='all' or key in (r['height_group'],r['board_group'])]
        result[key] = dict(label=label, sample_count=len(group), **sf._summary(group))
    return result


def _point(block, day, previous, daily_dir, cache):
    if block.get('status') == 'missing-data':
        checked = block
    else:
        checked = sf._view(block,day)
    state = checked['status']
    # 日期必须由日历再次对账，拒绝把两日前甚至未来名单冒充上一开放日。
    if state in {'complete','partial','empty'} and previous is not None and checked.get('source_date') != previous:
        checked = {**sf._base(day),'errors':['来源日不等于严格上一开放日']}
        state = 'source_failed'
    rows = checked.get('details',[])
    prior_map = {}
    if rows and any('limit_times_verified' not in r for r in rows) and previous and checked.get('source_date') == previous:
        envelope = _envelope(daily_dir,previous,cache)
        try:
            prior = sf._cohort(envelope['raw_data']['limit_up'])
            if {r['code'] for r in prior} != {sf._row_code(r) for r in rows}:
                raise ValueError('历史名单身份不一致')
            prior_map = {r['code']:r['limit_times'] for r in prior}
        except (ValueError, TypeError, KeyError, AttributeError):
            pass  # 不用当前名单、股票池或后续涨停次数补历史身份。
    annotated = []
    for r in rows:
        code = sf._row_code(r)
        if 'limit_times_verified' in r:
            height = limit_times(r.get('limit_times')) if r['limit_times_verified'] is True else None
        elif 'limit_times' in r and limit_times(r['limit_times']) is None:
            height = None
        else:
            recovered = prior_map.get(code)
            height = recovered if 'limit_times' not in r or limit_times(r['limit_times']) == recovered else None
        board = limit_pct_for(code,is_st=False)
        # 本模块只声明2022年起的常规板型支持范围；更早日期未核验，不反推分类。
        if (checked.get('source_date') or day) < '2022-01-01':
            board = None
        annotated.append(dict(r, height_group='first' if height==1 else 'consecutive' if height else 'height_unknown',
                              board_group=f'{int(board)}cm' if board in (10,20,30) else 'board_unknown'))
    known = checked.get('cohort_count') is not None
    groups = _groups(annotated) if known else None
    return dict(date=day,source_date=checked.get('source_date'),status=state,cohort_count=checked.get('cohort_count'),
                rows=annotated,groups=groups,delta=None,
                classification_complete=(known and not any(r['height_group']=='height_unknown' or r['board_group']=='board_unknown' for r in annotated)))


def _window(points, n):
    selected = points[-n:]
    valid = [p for p in selected if p['status'] in {'complete','empty'}]
    rows = [r for p in valid for r in p['rows']]
    return dict(days=n,start=selected[0]['date'] if selected else None,end=selected[-1]['date'] if selected else None,
                valid_days=len(valid),status='complete' if len(valid)==n else 'partial' if valid else 'not_computed',
                groups=_groups(rows) if valid else None,
                classified_days=sum(p['classification_complete'] for p in valid))


def build(daily_dir, day, *, current=None, db_path=None):
    """同日尚未落盘的current优先；历史只读同日信封，日历失败不发布来源身份分组。"""
    cache = {}
    block = sf._view(current,day) if current is not None else _snapshot(daily_dir,day,cache)
    result = dict(schema=SCHEMA,date=day,calendar_status='source_failed',history=[],windows=[],note=NOTE)
    # 未经日历确认严格上一开放日，不发布任何来源身份分组。原单日反馈独立保留。
    result['current'] = _point(sf._base(day),day,None,daily_dir,cache)
    try:
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            days = _calendar(conn,day)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        result['reason'] = '完整SSE日历不足21个开放日、存在缺口或目标日休市；当日分组及历史窗口未计算'
        result['windows'] = [_window([],n) for n in WINDOWS]
        return result
    result['calendar_status'] = 'complete'
    for i, target in enumerate(days[1:],1):
        value = block if target==day else _snapshot(daily_dir,target,cache)
        point = _point(value,target,days[i-1],daily_dir,cache)
        prev = result['history'][-1] if result['history'] else None
        if prev and prev['status']=='complete' and point['status']=='complete':
            a,b = prev['groups']['all'],point['groups']['all']
            point['delta'] = {key:b[key]-a[key] if a[key] is not None and b[key] is not None else None
                              for key in ('touched_rate_pct','bearish_rate_pct','close_mean_pct')}
        result['history'].append(point)
    result['current'] = result['history'][-1]
    result['windows'] = [_window(result['history'],n) for n in WINDOWS]
    return result


def _fmt(value, percent=False, signed=False):
    if value is None:
        return '未计算'
    return (f'{value:+.2f}' if signed else f'{value:.2f}') + ('%' if percent else '')


def _cells(group):
    return [str(group['sample_count']),str(group['quote_count']),str(group['touch_count']),
            _fmt(group['touched_rate_pct'],True),_fmt(group['bearish_rate_pct'],True),
            _fmt(group['open_mean_pct'],True),_fmt(group['close_mean_pct'],True),_fmt(group['close_median_pct'],True)]


def _tables(result):
    stat_headers = ['样本/股票日','行情','前高比较','触及率','收阴率','开盘均值','收盘均值','收盘中位']
    current=[]
    for key,label in GROUPS:
        groups=result['current']['groups']
        if groups is not None:
            current.append([label]+_cells(groups[key]))
    windows=[]
    for w in result['windows']:
        for key,label in GROUPS:
            g = w['groups'][key] if w['groups'] else None
            windows.append([f"近{w['days']}日",f"{w['valid_days']}/{w['days']}",label]+(_cells(g) if g else ['未计算']*8))
    days=[]
    for p in result['history']:
        g=p['groups']['all'] if p['groups'] else None
        delta=p['delta'] or {}
        days.append([p['date'],p['status'],str(p['cohort_count']) if p['cohort_count'] is not None else '未计算']+
                    ([_fmt(g[k],True) for k in ('touched_rate_pct','bearish_rate_pct','open_mean_pct','close_mean_pct','close_median_pct')] if g else ['未计算']*5)+
                    [_fmt(delta.get('touched_rate_pct'),signed=True),_fmt(delta.get('bearish_rate_pct'),signed=True),_fmt(delta.get('close_mean_pct'),signed=True)])
    return [('当日来源身份分组',['分组']+stat_headers,current),
            ('固定窗口分组统计',['窗口','完整日覆盖','分组']+stat_headers,windows),
            ('最近20个开放日',['观察日','状态','应有样本','触及率','收阴率','开盘均值','收盘均值','收盘中位','触及日差pp','收阴日差pp','收盘均值日差pp'],days)]


def _gap(result):
    if result['calendar_status']!='complete':
        return result.get('reason','历史日历不可用')
    w=result['windows'][-1]
    current=result['current']
    if w['valid_days']<20 or w['classified_days']<w['valid_days'] or not current['classification_complete']:
        return f"强势股反馈历史：完整日{w['valid_days']}/20；身份完整日{w['classified_days']}/{w['valid_days']}；缺失不补零，未知身份单列"
    return None


def render_markdown(result):
    lines=['\n### 强势股反馈历史与分组 [事实]',NOTE]
    gap=_gap(result)
    if gap: lines.append(gap)
    lines+=['| 窗口 | 完整日 | 股票日 | 触及率 | 收阴率 | 收盘均值 |','|---|---|---|---|---|---|']
    for w in result['windows']:
        g=w['groups']['all'] if w['groups'] else None
        values=[f"近{w['days']}日",f"{w['valid_days']}/{w['days']}",str(g['sample_count']) if g else '未计算']
        values += [_fmt(g[k],True) if g else '未计算' for k in ('touched_rate_pct','bearish_rate_pct','close_mean_pct')]
        lines.append('| '+' | '.join(values)+' |')
    current=result['current']
    if current['groups'] is not None:
        lines+=['| 当日分组 | 样本 | 行情/前高覆盖 | 触及率 | 收阴率 | 收盘均值 |','|---|---|---|---|---|---|']
        for key,label in GROUPS[1:]:
            g=current['groups'][key]
            lines.append(f"| {label} | {g['sample_count']} | {g['quote_count']}/{g['touch_count']} | {_fmt(g['touched_rate_pct'],True)} | {_fmt(g['bearish_rate_pct'],True)} | {_fmt(g['close_mean_pct'],True)} |")
    lines.append('完整逐日及各窗口分组明细见复盘HTML；窗口部分覆盖时，统计仅代表已覆盖日期。')
    return '\n'.join(lines)


def _chart(points, series, title):
    values=[p['groups']['all'][key] for p in points if p['status']=='complete' for key,_,_ in series
            if p['groups']['all'][key] is not None]
    if not values:
        return '<p>'+escape(title)+'：无完整日期，未绘制趋势线。</p>'
    lo,hi=min(0,min(values)),max(1,max(values))
    parts=[f'<svg viewBox="0 0 470 220" role="img" aria-label="{escape(title)}" style="width:100%;max-width:640px"><title>{escape(title)}</title>']
    for value in (lo,hi):
        y=170-(value-lo)/(hi-lo)*120
        parts.append(f'<text x="0" y="{y:.1f}" font-size="14">{value:.1f}%</text><line x1="55" x2="450" y1="{y:.1f}" y2="{y:.1f}" stroke="#ccc"/>')
    for j,(key,label,color) in enumerate(series):
        parts.append(f'<text x="{55+j*200}" y="20" fill="{color}" font-size="16">{escape(label)}</text>')
        previous=None
        for i,p in enumerate(points):
            value=p['groups']['all'][key] if p['status']=='complete' else None
            if value is None:
                previous=None;continue
            x=55+395*i/max(1,len(points)-1);y=170-(value-lo)/(hi-lo)*120
            if previous:
                parts.append(f'<line x1="{previous[0]:.1f}" y1="{previous[1]:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"><title>{p["date"]} {escape(label)} {value:.2f}%</title></circle>')
            previous=(x,y)
    if points:
        parts += [f'<text x="55" y="205" font-size="14">{points[0]["date"]}</text>',f'<text x="450" y="205" text-anchor="end" font-size="14">{points[-1]["date"]}</text>']
    return ''.join(parts)+'</svg>'


def render_html(result):
    tables=_tables(result)
    items=sum(len(rows) for _,_,rows in tables)
    gap=_gap(result)
    parts=[f'<div data-strong-feedback-history="{escape(result["date"])}" style="min-width:0;max-width:100%"><h4>反馈历史 · 已核验覆盖</h4>']
    if result['calendar_status'] != 'complete':
        parts.append('<p>历史窗口未计算：开放日日历未核验。</p>')
    else:
        parts.append('<p style="font-size:12px">仅统计已覆盖日期；缺日不补零，不代表完整趋势。</p>')
        parts.append('<div data-feedback-windows="v1" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:12px">')
        for w in result['windows']:
            parts.append('<div style="min-width:0;padding:10px;border:1px solid rgba(128,128,128,.25)">')
            parts.append(sf._visual_progress(f"近{w['days']}日 · 完整日", w['valid_days'], w['days'], key=f"coverage-{w['days']}", suffix='日'))
            g=w['groups']['all'] if w['groups'] else None
            parts.append('<p style="font-size:12px">'+(f"已覆盖 {g['sample_count']} 股票日 · 收盘均值 {_fmt(g['close_mean_pct'],True,signed=True)}" if g else '样本与反馈未计算')+'</p></div>')
        parts.append('</div>')
        w=result['windows'][-1]
        parts.append('<p style="font-size:12px">来源身份完整 '+f"{w['classified_days']}/{w['valid_days']}日"+'；未知身份单列。</p>')
    parts.append(f'<details class="evidence" data-as-of="{escape(result["date"])}" data-items="{items}" data-evidence-kind="strong-feedback-history"><summary>强势股反馈历史与分组（{items}项）</summary>')
    parts.append('<p>'+escape(NOTE)+'</p>')
    if gap: parts.append('<p>[事实] '+escape(gap)+'</p>')
    parts.append(_chart(result['history'],(('touched_rate_pct','触及前高率','#b54a40'),('bearish_rate_pct','收阴率','#286e9e')),'触及前高率与收阴率'))
    parts.append(_chart(result['history'],(('close_mean_pct','收盘涨跌均值','#667d36'),),'收盘表现'))
    for title,headers,rows in tables:
        if not rows:
            parts.append('<p>'+escape(title)+'：未计算，来源日期或样本未核验。</p>')
            continue
        parts.append('<p>'+escape(title)+'</p><div style="overflow-x:auto;max-width:100%"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>')
        parts += ['<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows]
        parts.append('</tbody></table></div>')
    parts.append('</details></div>')
    return '\n'.join(parts),gap
