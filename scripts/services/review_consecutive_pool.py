"""连板池历史图：20个开放日、数量/金额分面、来源重算、缺值断线。"""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
from html import escape
from pathlib import Path
import sqlite3
import math

from analyzers.consecutive_pool import NOTE, from_archive, summary
from services.review_feedback_trends import _read


def _days(rows, report_date, count=20):
    if type(count) is not int or not 1 <= count <= 60:
        raise ValueError('趋势窗口必须为1至60个开放日')
    expected, days = date.fromisoformat(report_date), []
    for day, is_open in rows:
        if day != expected.isoformat() or is_open not in (0, 1):
            raise ValueError('交易日历缺失或非法，历史趋势未计算')
        if is_open:
            days.append(day)
        if len(days) == count:
            return days
        expected -= timedelta(days=1)
    raise ValueError(f'完整{count}个开放日历不足')


def load(daily_dir, db_path, report_date, *, days=20):
    result = dict(report_date=report_date, fact_date=None, points=[], gaps=[], window_days=days)
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            rows = conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date<=? ORDER BY date DESC LIMIT 120", (report_date,)).fetchall()
        days = _days(rows, report_date, days)
        result['calendar'] = rows
        result['fact_date'] = days[0]
        for day in reversed(days):
            block = from_archive(_read(Path(daily_dir)/day/'post-market.yaml', yaml_file=True), day)
            result['points'].append(block)
        incomplete = sum(p['status'] != 'complete' for p in result['points'])
        if incomplete:
            result['gaps'].append(f'连板池趋势：{incomplete}/{len(days)}日存在未核验数据，缺失不补零')
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        result['gaps'] = [str(exc)]
    return result


def _fmt(value, *, amount=False):
    return '—' if value is None else (f'{value:.2f}' if amount else str(value))


def _chart(points, key, label, unit, color):
    maximum = max([1, *(p[key] for p in points if p.get(key) is not None)])
    maximum *= 1.15
    if key == 'count':
        maximum = math.ceil(maximum)
    x = lambda i: 62 + 786*i/max(1, len(points)-1)
    y = lambda v: 180-126*v/maximum
    parts = [f'<svg viewBox="0 0 900 230" role="img" aria-label="{label}趋势，单位{unit}">',
             f'<text x="62" y="24" fill="{color}">{label}（{unit}）</text>']
    ticks = sorted({0, maximum//2, maximum}) if key == 'count' else (0, maximum/2, maximum)
    for v in ticks:
        label_v = str(int(v)) if key == 'count' else f'{v:.2f}'.rstrip('0').rstrip('.')
        parts.append(f'<line x1="62" x2="848" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="currentColor" opacity=".15"/><text x="53" y="{y(v)+4:.1f}" text-anchor="end">{label_v}</text>')
    previous = None
    for i, point in enumerate(points):
        v, day = point.get(key), point['trade_date']
        if v is None:
            previous = None
            parts.append(f'<text x="{x(i):.1f}" y="120" text-anchor="middle" opacity=".5">缺</text>')
        else:
            if previous is not None and previous[2] == point.get('limit_source'):
                parts.append(f'<line data-pool-line="{key}" x1="{previous[0]:.1f}" y1="{previous[1]:.1f}" x2="{x(i):.1f}" y2="{y(v):.1f}" stroke="{color}" stroke-width="2"/>')
            title = f'{day} {label} {_fmt(v, amount=key == "amount_yi")}{unit}'
            parts.append(f'<circle data-pool-point="{key}" data-date="{day}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="{color}"><title>{escape(title)}</title></circle>')
            previous = (x(i), y(v), point.get('limit_source'))
        if i in {0, 4, 9, 14, len(points)-1}:
            parts.append(f'<text x="{x(i):.1f}" y="210" text-anchor="middle">{day[5:]}</text>')
    return ''.join(parts)+'</svg>'


def render(payload, report_date):
    payload = payload or {}
    points = payload.get('points') or []
    if payload.get('report_date') != report_date or not points:
        reason = '；'.join(payload.get('gaps') or ['历史来源或交易日历不足'])
        return ('<p data-consecutive-pool-trend="missing-data">[事实] 连板股票池趋势：'+escape(reason)+'。</p>', [reason])
    fact_date = payload['fact_date']
    try:
        days = _days(payload.get('calendar') or [], report_date, payload.get('window_days', 20))
        if fact_date != days[0] or [p['trade_date'] for p in points] != list(reversed(days)):
            raise ValueError('连板池图点与开放日窗口不符')
    except (ValueError, TypeError, KeyError) as exc:
        return '<p data-consecutive-pool-trend="missing-data">[事实] '+escape(str(exc))+'。</p>', [str(exc)]
    # 外部传入的聚合不可直接成为图点，始终从来源重算。
    checked = []
    for p in points:
        if p.get('status') in {'complete', 'partial'}:
            from analyzers.consecutive_pool import calculate
            q = calculate(p['trade_date'], p.get('evidence'))
            q['historical_recalculation'] = p.get('historical_recalculation', False)
        else:
            from analyzers.consecutive_pool import missing
            q = missing(p['trade_date'], '；'.join(p.get('gaps') or ['来源缺失']), p.get('status', 'source_failed'))
        checked.append(q)
    points = checked
    latest = points[-1]
    gaps = list(payload.get('gaps') or [])
    status = 'complete' if all(p['status'] == 'complete' for p in points) else 'partial'
    if status == 'partial' and not gaps:
        gaps.append('连板池趋势存在未核验数据，缺失不补零')
    rows = []
    states = {'complete': '完整', 'partial': '部分缺失', 'source_failed': '来源失败', 'missing-data': '未归档', 'skipped': '跳过'}
    for p in points:
        names = '；'.join(f"{r['name']} {r['code']} · {r['height']}板 · {_fmt(r['amount_yi'], amount=True)}亿元" for r in p['rows']) or '—'
        notes = '；'.join(p['gaps']) or '已核验'
        if p.get('historical_recalculation'):
            notes += '；旧归档只读重算'
        rows.append('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in (
            p['trade_date'], _fmt(p['count']), _fmt(p['amount_yi'], amount=True),
            f"{p['amount_count']}/{len(p['rows'])}", states.get(p['status'], p['status']), names, notes))+'</tr>')
    count_days = sum(p['count'] is not None for p in points)
    amount_days = sum(p['amount_yi'] is not None for p in points)
    html = f'''<figure class="feedback-trend" data-consecutive-pool-trend="v1" data-as-of="{fact_date}" data-reviewed-through="{report_date}" data-source-status="{status}">
<figcaption><strong>连板股票池：数量与成交额趋势</strong></figcaption>
<p>[事实] {fact_date} · <strong>{escape(summary(latest))}</strong></p>
<p>{points[0]['trade_date']} 至 {fact_date} · 数量 {count_days}/{len(points)}日、成交额 {amount_days}/{len(points)}日可核验；缺值断线。</p>
<p style="font-size:.85em;opacity:.7">窄屏可左右滑动图表；悬停圆点可查看当日数值。</p>
<div style="width:100%;overflow-x:auto"><div style="min-width:620px">{_chart(points, 'count', '连板股数量', '只', '#c04b3c')}{_chart(points, 'amount_yi', '合计成交额', '亿元', '#2779ae')}</div></div>
<details class="evidence" data-as-of="{fact_date}" data-items="{len(points)}" data-evidence-kind="consecutive-pool"><summary>查看逐日数据、股票名单与统计口径（{len(points)}日）</summary>
<p>{escape(NOTE)} 来源：当日涨停名单＋tushare:daily；来源覆盖范围变化时不可直接比较。完整仅指该来源名单及其成交额完整。</p>
<div style="overflow-x:auto"><table><thead><tr><th>交易日</th><th>数量（只）</th><th>合计（亿元）</th><th>金额覆盖/确认连板</th><th>状态</th><th>股票明细</th><th>证据/缺口</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></details></figure>'''
    return html, gaps
