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
    parts=[f'<div {MARKER}="{escape(day)}"><p>{escape(_summary(report))}</p>']
    for n,w in report.get('windows',{}).items():
        m=w['market']
        parts.append('<p>'+escape(f"近{n}开放日：≥5%股票日占比{number(m['up_pct'],True)}，≤-5%占比{number(m['down_pct'],True)}；可统计{w['valid_days']}/{n}日，其中完整{w['complete_days']}日。")+'</p>')
    for title,heads,rows in tables(report):
        if not rows:
            parts.append(f'<p>{escape(title)}：未计算。</p>');continue
        parts.append(f'<details class="evidence" data-as-of="{escape(day)}" data-items="{len(rows)}" data-evidence-kind="market-strength"><summary>{escape(title)}（{len(rows)}项）</summary><div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in heads)+'</tr></thead><tbody>')
        parts.extend('<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)
        parts.append('</tbody></table></div></div></details>')
    parts.append('<p>'+escape(NOTE)+'</p></div>')
    gap='每日涨跌广度存在缺日、未分类或来源缺口' if report.get('status')!='complete' else ''
    return ''.join(parts),gap
