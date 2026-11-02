"""全市场/申万二级大涨大跌数量与历史的同源报告。"""
from html import escape

from analyzers.market_strength import NOTE, UNKNOWN

MARKER='data-market-strength'


def number(value, percent=False):
    return '未计算' if value is None else f'{value:.2f}%' if percent else str(value)


def _summary(report):
    current=report.get('current') or {};m=current.get('market') or {}
    return (f"[事实] ≥5%／≤-5%每日广度 · {report['date']}；当日{current.get('status','not_computed')}；"
            f"有效样本{number(m.get('sample_count'))}只；≥5% {number(m.get('up_count'))}只（{number(m.get('up_pct'),True)}）；"
            f"≤-5% {number(m.get('down_count'))}只（{number(m.get('down_pct'),True)}）。")


def tables(report):
    points=report.get('points',[]);last=points[-1] if points else {}
    current=report.get('current') or {};sectors=current.get('sectors',{})
    sector_rows=[]
    for name,m in sorted(sectors.items(),key=lambda kv:(kv[0]==UNKNOWN,-kv[1]['up_count'],kv[0])):
        delta=last.get('sector_deltas',{}).get(name) or {}
        windows=[]
        for n in ('5','10','20'):
            w=report.get('windows',{}).get(n,{}).get('sectors',{}).get(name,{})
            windows.append(f"{number(w.get('up_pct'),True)}／{w.get('valid_days',0)}/{n}日")
        sector_rows.append([name,number(m['sample_count']),f"{m['up_count']}／{number(m['up_pct'],True)}",
                            f"{m['down_count']}／{number(m['down_pct'],True)}",number(delta.get('up_count')),
                            *windows])
    daily_rows=[]
    for p in points:
        m=p['market'] or {};d=p.get('delta') or {};coverage=p.get('coverage') or {}
        daily_rows.append([p['date'],p['status'],number(m.get('sample_count')),number(coverage.get('eligible_count')),
                           f"{number(m.get('up_count'))}／{number(m.get('up_pct'),True)}",
                           f"{number(m.get('down_count'))}／{number(m.get('down_pct'),True)}",number(d.get('up_count')),
                           p.get('comparison_status','未计算')])
    return [('申万二级板块', ['板块','有效样本','≥5%数量／占比','≤-5%数量／占比','≥5%较前日增减只数','5日≥5%股票日占比／覆盖','10日占比／覆盖','20日占比／覆盖'],sector_rows),
            ('近20开放日全市场', ['日期','状态','有效样本','上市宇宙','≥5%数量／占比','≤-5%数量／占比','≥5%较前日增减只数','日差覆盖状态'],daily_rows)]


def render_markdown(report):
    lines=[_summary(report)]
    for n,w in report.get('windows',{}).items():
        m=w['market'];lines.append(f"- 近{n}开放日：≥5%股票日占比{number(m['up_pct'],True)}，≤-5%占比{number(m['down_pct'],True)}；可统计{w['valid_days']}/{n}日，其中完整{w['complete_days']}日。")
    _,_,rows=tables(report)[0]
    lines += ['- 板块≥5%数量前5（非排名建议）：'+ ('；'.join(f'{r[0]} {r[2]}，有效样本{r[1]}' for r in rows[:5]) or '未计算')]
    lines += ['[事实] 缺源不补0，partial仅代表已覆盖样本；日差受样本及归类变化影响。完整行业及20日明细见同日信封和正式复盘②。']
    gaps=[*report.get('gaps',[]),*(report.get('current') or {}).get('gaps',[])]
    if gaps:lines.append('缺口：'+'；'.join(map(str,gaps[:4])))
    return '\n'.join(lines)+'\n'


def render_html(report,day):
    if not isinstance(report,dict) or report.get('date')!=day:raise ValueError('广度报告日期不匹配')
    current=report.get('current') or {};market=current.get('market') or {}
    points=report.get('points',[])[-20:]
    usable=[p for p in points if p.get('status') in {'complete','partial'} and p.get('market')]
    partial=sum(p['status']=='partial' for p in usable)
    parts=[f'<div {MARKER}="{escape(day,quote=True)}"><h3>≥5%／≤-5%每日广度</h3>']
    if market and current.get('status') in {'complete','partial'}:
        parts.append('<p>[事实] '+escape(day)+f' · 当日有效样本 {number(market.get("sample_count"))}只</p>')
        parts.append('<div style="display:flex;flex-wrap:wrap;gap:10px">')
        for key,label,color,background in [('up','≥5%','#a4323c','#fff1f2'),('down','≤-5%','#245986','#edf5fc')]:
            parts.append(f'<div data-strength-current="{key}" style="flex:1 1 120px;min-width:0;box-sizing:border-box;padding:12px;border-radius:8px;background:{background};color:{color}">'+
                         '<div>'+label+'</div><strong style="font-size:26px">'+number(market.get(key+'_count'))+
                         '只</strong><div>占当日样本 '+number(market.get(key+'_pct'),True)+'</div></div>')
        parts.append('</div>')
        if current.get('status')=='partial':
            parts.append('<p>[事实] 当日仅统计已覆盖样本，仍有行情或行业缺口。</p>')
    else:
        parts.append('<p>[事实] '+escape(day)+' · 当日来源尚未核验，暂无可展示的广度计数。</p>')
    parts.append('<h4>近20开放日 · 每日数量（只）</h4>')
    if usable:
        parts.append(f'<p style="font-size:13px">可绘 {len(usable)}/{len(points)}日'+
                     (f'，其中 {partial}日仅为已覆盖样本' if partial else '')+
                     ('；缺日留空，不连线。' if len(usable)<len(points) else '。')+'</p>')
        parts.append('<div style="display:flex;flex-wrap:wrap;gap:8px 18px;font-size:13px">'+
                     '<span style="color:#a4323c">● 实线：≥5%</span><span style="color:#245986">■ 虚线：≤-5%</span></div>')
        peak=max(p['market'][key] for p in usable for key in ('up_count','down_count'))
        ceiling=max(10,((peak+9)//10)*10)
        left,right,top,bottom=40,348,24,174
        xpos=lambda i:left+(right-left)*i/max(1,len(points)-1)
        ypos=lambda value:bottom-(bottom-top)*value/ceiling
        chart_label=f'{points[0]["date"]}至{points[-1]["date"]}，≥5%与≤-5%每日股票数量，同轴单位只；可绘{len(usable)}/{len(points)}日，缺日留空。完整数值见近20开放日全市场表。'
        parts.append('<svg data-strength-history="counts" role="img" aria-label="'+escape(chart_label,quote=True)+
                     '" viewBox="0 0 360 210" style="display:block;width:100%;max-width:760px;height:auto;color:inherit">'+
                     '<title>近20开放日大涨大跌数量</title><desc>'+escape(chart_label)+'</desc>')
        for tick in (0,ceiling//2,ceiling):
            y=ypos(tick)
            parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#cbd5e1" stroke-width="0.6"></line>'+
                         f'<text x="34" y="{y+4:.2f}" text-anchor="end" font-size="11" fill="currentColor">{tick}</text>')
        parts.append('<text x="8" y="13" font-size="11" fill="currentColor">只</text>')
        label_indices={round(i*(len(points)-1)/4) for i in range(5)}
        for i,p in enumerate(points):
            x=xpos(i)
            if p not in usable:
                parts.append(f'<line data-strength-gap="{escape(p["date"],quote=True)}" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="#d1d5db" stroke-dasharray="2 4"><title>'+escape(p['date'])+'：来源缺口，未绘制数值</title></line>')
            if i in label_indices:
                anchor='start' if i==0 else 'end' if i==len(points)-1 else 'middle'
                parts.append(f'<text x="{x:.2f}" y="195" text-anchor="{anchor}" font-size="11" fill="currentColor">'+escape(p['date'][5:])+'</text>')
        for key,color,dash in [('up_count','#a4323c',''),('down_count','#245986','4 3')]:
            commands=[];connected=False
            for i,p in enumerate(points):
                if p not in usable:
                    connected=False;continue
                x,y=xpos(i),ypos(p['market'][key])
                commands.append(f'{"L" if connected else "M"}{x:.2f},{y:.2f}');connected=True
            parts.append(f'<path data-strength-series="{key}" d="'+ ' '.join(commands)+f'" fill="none" stroke="{color}" stroke-width="1.8" stroke-dasharray="{dash}"></path>')
            for i,p in enumerate(points):
                if p not in usable:continue
                x,y=xpos(i),ypos(p['market'][key]);value=p['market'][key]
                label=('≥5%' if key=='up_count' else '≤-5%')
                tooltip=f'{p["date"]} {label} {value}只；有效样本{p["market"]["sample_count"]}只'+('；仅已覆盖样本' if p['status']=='partial' else '')
                attrs=f'data-strength-point="{key}" data-date="{escape(p["date"],quote=True)}" data-value="{value}" tabindex="0" aria-label="{escape(tooltip,quote=True)}"'
                if key=='up_count':
                    parts.append(f'<circle {attrs} cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="{color}"></circle>')
                else:
                    parts.append(f'<rect {attrs} x="{x-2.5:.2f}" y="{y-2.5:.2f}" width="5" height="5" fill="{color}"></rect>')
        parts.append('</svg><p style="font-size:12px">逐日数值可展开下方全市场表查看。</p>')
    else:
        parts.append('<p>[事实] 历史来源尚未核验，暂不能绘制趋势；缺失值不补零。</p>')
    windows=report.get('windows',{})
    if windows:
        parts.append(f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{len(windows)}" data-evidence-kind="market-strength"><summary>5／10／20日股票日占比（{len(windows)}项）</summary>'+
                     '<p>[事实] 分母为窗口内有效样本累计股票日，不是当日股票数。</p><div style="overflow-x:auto"><table><thead><tr><th>开放日窗口</th><th>≥5%股票日占比</th><th>≤-5%股票日占比</th><th>分母（股票日）</th><th>可统计／窗口日</th><th>完整日</th></tr></thead><tbody>')
        for n,w in windows.items():
            m=w['market'];cells=[n+'日',number(m['up_pct'],True),number(m['down_pct'],True),number(m.get('stock_days')),f'{w["valid_days"]}/{n}',w['complete_days']]
            parts.append('<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in cells)+'</tr>')
        parts.append('</tbody></table></div></details>')
    for title,heads,rows in tables(report):
        if not rows:continue
        parts.append(f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{len(rows)}" data-evidence-kind="market-strength"><summary>{escape(title)}（{len(rows)}项）</summary><div class="evidence-body"><div class="table-scroll-shell" style="overflow-x:auto;max-width:100%"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in heads)+'</tr></thead><tbody>')
        parts.extend('<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)
        parts.append('</tbody></table></div></div></details>')
    gaps=[*report.get('gaps',[]),*current.get('gaps',[])]
    items=len(gaps)+2
    parts.append(f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{items}" data-evidence-kind="market-strength"><summary>统计口径与来源缺口（{items}项）</summary><p>'+escape(NOTE)+'</p>')
    parts.append('<p>'+escape(f'报告状态：{report.get("status")}；当日状态：{current.get("status","not_computed")}')+'</p>')
    parts.extend('<p>'+escape(str(g))+'</p>' for g in gaps)
    parts.append('</details></div>')
    gap='每日涨跌广度存在缺日、未分类或来源缺口' if report.get('status')!='complete' else ''
    return ''.join(parts),gap
