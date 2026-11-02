"""Pure HTML projections of the already validated style payload; no I/O or inference."""
from __future__ import annotations

import math
import re
from html import escape

COLORS = ('#087f73', '#526faf', '#9871a9')
STATUS = {'complete': '已核验', 'partial': '部分覆盖', 'ok': '已核验', 'latest_available': '历史可用'}
TRACK = 'display:block;position:relative;height:12px;background:#e9eceb;border-radius:3px;overflow:hidden'


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _bar(value, bound, color):
    if _number(value) is None:
        return '<span>未计算</span>'
    width = abs(value) / bound * 50
    left = 50 - width if value < 0 else 50
    return (f'<span style="{TRACK}"><span style="position:absolute;left:50%;height:12px;border-left:1px solid #738179"></span>'
            f'<span style="position:absolute;left:{left:.4f}%;width:{width:.4f}%;height:12px;background:{color}"></span></span>')


def _fold(body, day, items, title='完整数据与口径'):
    return (f'<details class="evidence" data-as-of="{escape(day, quote=True)}" data-items="{items}">'
            f'<summary>{title}（{items}项）</summary>{body}</details>')


def _split(html):
    end = html.index('</h3>') + len('</h3>')
    return html[:end], html[end:html.rfind('</div>')]


def low_price(html, snapshot, report_date):
    """Keep original contract and full numeric source text; add two distinct scales."""
    head, original = _split(html)
    day = snapshot['date']
    # Closed-day renderer already has an evidence container. Keep one flat layer.
    original = re.sub(r'<details\b[^>]*>\s*<summary>.*?</summary>', '', original, flags=re.S)
    original = original.replace('</details>', '')
    rows = [('≤5元', snapshot['bands']['price_le_5']),
            ('5～10元', snapshot['bands']['price_5_to_10']), ('全市场', snapshot['market'])]
    bound = max(1, *[abs(_number(r.get('pct_chg_median')) or 0) for _, r in rows])
    status = snapshot['status']
    out = [head, f'<p>[事实] 来源日{escape(day)}'+(f' · 复核日{escape(report_date)}' if day != report_date else '')+f' · {escape(STATUS.get(status, status))} · 低价样本{snapshot["low_price"]["sample_count"]}只</p>',
           '<div data-style-chart="low-price" style="display:flex;flex-wrap:wrap;gap:16px">']
    for metric in ('median', 'advance'):
        out.append('<div style="flex:1 1 240px;min-width:0">')
        out.append('<h4>'+('涨跌幅中位数' if metric == 'median' else '上涨比例')+'</h4>')
        out.append(f'<p style="font-size:12px;color:var(--muted)">'+(f'共同横轴 −{bound:.2f}% ～ +{bound:.2f}%' if metric == 'median' else '共同横轴 0% ～ 100%')+'</p>')
        for index, (label, item) in enumerate(rows):
            n = item['sample_count']
            value = _number(item.get('pct_chg_median' if metric == 'median' else 'advance_rate'))
            shown = '未计算' if value is None else (f'{value:+.2f}%' if metric == 'median' else f'{value*100:.1f}%')
            out.append(f'<div data-style-metric="{metric}" data-value="{value if value is not None else "unavailable"}" style="margin:12px 0">'
                       f'<div style="display:flex;justify-content:space-between;gap:8px"><span>{label} <small>n={n}</small></span><strong>{shown}</strong></div>')
            if metric == 'median':
                out.append(_bar(value, bound, COLORS[index]))
            elif value is None:
                out.append('<span>未计算</span>')
            else:
                out.append(f'<span style="{TRACK}"><span style="display:block;width:{value*100:.4f}%;height:12px;background:{COLORS[index]}"></span></span>')
            out.append('</div>')
        out.append('</div>')
    out.append('</div>')
    folded = _fold(original, day, 4)
    if day != report_date:
        folded = folded.replace('完整数据与口径（4项）', '完整统计与口径（4组）')
    out.append(folded)
    return '\n'.join(out) + '</div>'


def _shock(html, shock):
    """Rewrite only a valid visible p; preserve its attributes and tested phrases."""
    match = re.search(r'(<p\b[^>]*data-style-shock-feedback="v1"[^>]*>)(.*?)</p>', html, re.S)
    if not match:
        return html
    horizons = shock['horizons']
    bound = max(1, *[abs(float(horizons[str(h)]['median_pct'])) for h in (1, 3, 5, 10)])
    out = [match[1], f'<span style="display:block;font-weight:600">[判断] 严重异动已实现反馈（事件{shock["event_count"]}例）</span>',
           f'<span style="display:block;font-size:12px">截至{escape(str(shock.get("date", "")))} · {escape(STATUS.get(shock["status"], str(shock["status"])))}；各窗口只含已走满样本，分母不同，不连成同一收益曲线。</span>',
           f'<span style="display:block;font-size:12px">中位数共同横轴 −{bound:.1f}% ～ +{bound:.1f}%</span>']
    for h in (1, 3, 5, 10):
        row = horizons[str(h)];v = float(row['median_pct'])
        out.append(f'<span data-style-horizon="{h}" aria-label="T+{h} 中位{v:+.1f}%、正收益{row["positive_count"]}/{row["sample_count"]}（{row["shown_rate_pct"]}%）" style="display:block;margin:12px 0">'
                   f'<span style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><strong>T+{h} 中位{v:+.1f}%</strong>'
                   f'<span>正收益{row["positive_count"]}/{row["sample_count"]}（{row["shown_rate_pct"]}%）</span></span>')
        out.append(_bar(v, bound, '#b34146' if v < 0 else COLORS[0]))
        out.append('</span>')
    out.append('</p>')
    return html[:match.start()] + ''.join(out) + html[match.end():]


def market_effect(html, market, shock, report_date):
    head, body = _split(html)
    # The shock p is a separate default-visible contract; never fold it with methods.
    match = re.search(r'<p\b[^>]*data-style-shock-feedback="[^"]+"[^>]*>.*?</p>', body, re.S)
    shock_html = match[0] if match else ''
    original = body[:match.start()] + body[match.end():] if match else body
    original = original.replace('本日无北交所涨停', '现有来源样本未含北交所涨停；不证明全市场无30cm涨停')
    current = market['current'];board = current['board'];day = market['as_of'];history = market['history']
    bound = max(1, *[board[str(w)]['count'] for w in (10, 20, 30)])
    out = [head, f'<p>[事实] 来源日{escape(day)}'+(f' · 复核日{escape(report_date)}' if day != report_date else '')+f' · {escape(STATUS.get(market["status"], market["status"]))} · 来源涨停{current["count"]}只，最高{current["max_height"]}板</p>',
           '<div data-style-chart="board-width"><p style="font-size:12px">青色：首板；金色：连板。常规板型，不是涨幅筛选。</p>']
    for w in (10, 20, 30):
        r = board[str(w)]
        out.append(f'<div data-style-board="{w}" style="margin:12px 0"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px"><strong>{w}cm · {r["count"]}只</strong><span>首板{r["first"]} / 连板{r["continuation"]}</span></div>'
                   f'<div style="{TRACK}"><span style="position:absolute;width:{100*r["first"]/bound:.4f}%;height:12px;background:{COLORS[0]}"></span>'
                   f'<span style="position:absolute;left:{100*r["first"]/bound:.4f}%;width:{100*r["continuation"]/bound:.4f}%;height:12px;background:#a67927"></span></div></div>')
    out.append('<p style="font-size:12px">30cm统计仅限本来源样本，不能判断北交所全市场。</p></div>')
    out.append(f'<h4>最近{len(history)}份原始样本 · 板型变化</h4><p style="font-size:12px">原归档日期，不补缺日；各图采用共同数量刻度。</p>')
    trend_bound = max(1, *[r[f'board_{w}_count'] for r in history for w in (10, 20, 30)])
    out.append('<div data-style-chart="board-history" style="display:flex;flex-wrap:wrap;gap:12px">')
    for i,w in enumerate((10, 20, 30)):
        out.append(f'<div style="flex:1 1 210px;min-width:0"><strong>{w}cm</strong><div style="display:flex;gap:5px;margin-top:8px">')
        for r in history:
            v = r[f'board_{w}_count'];d=r['date']
            out.append(f'<div data-history-board="{w}" data-date="{escape(d,quote=True)}" data-value="{v}" style="flex:1;min-width:0;text-align:center;font-size:11px">'
                       f'<div>{v}</div><div style="height:64px;display:flex;align-items:flex-end;background:var(--subtle,#f1f3f2)">'
                       f'<div style="width:100%;height:{64*v/trend_bound:.4f}px;background:{COLORS[i]}"></div></div><div>{escape(d[5:])}</div></div>')
        out.append('</div></div>')
    out.append('</div>')
    out.append(_shock(shock_html, shock))
    out.append(_fold(original, day, len(history)+3))
    return '\n'.join(out) + '</div>'
