"""从已验证题材快照投影量能对照；不把同涨解释为主线或资金净流入。"""
from html import escape


def direction(value):
    if value is None:
        return '未计算'
    return ('放量' if value > 0 else '缩量' if value < 0 else '持平') + f'（{value:+.2f}%）'


def build(value):
    # 调用方必须先用theme_launch.validate重算。partial总额不可当完整市场背景。
    evidence = value.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    increment = evidence.get('increment')
    # Failed sources retain their raw shape in otherwise valid partial snapshots.
    increment = increment if isinstance(increment, dict) else {}
    market = value.get('market') if increment.get('status') == 'complete' else None
    market_change = market.get('change_pct') if market else None
    indices = {r['name']: r['change_pct'] for r in value.get('indices', [])}
    index_text = '；'.join(name+' '+(f'{indices[name]:+.2f}%' if name in indices else '未核验')
                          for name in ('上证指数', '创业板指'))
    rows = []
    for r in value.get('rows', []):
        amount = r.get('amount') or {}
        change, share = amount.get('change_pct'), amount.get('share_delta_pp')
        if change is None or market_change is None:
            summary = '量能对照待核验'
        elif market_change > 0 and change > 0:
            summary = '市场与题材均放量'
        elif market_change < 0 and change > 0:
            summary = '题材放量、市场缩量'
        elif market_change > 0 and change < 0:
            summary = '市场放量、题材缩量'
        elif market_change < 0 and change < 0:
            summary = '市场与题材均缩量'
        else:
            summary = '市场或题材成交额持平'
        rows.append([r['name'], f"{r['change_pct']:+.2f}%" if r['change_pct'] is not None else '未核验',
                     direction(change), f'{share:+.2f}pp' if share is not None else '未计算', summary])
    return dict(indices=index_text, market=direction(market_change), rows=rows)


def render_html(value):
    context = build(value)
    parts = ['<h4>板块量能对照</h4>', '<p>[事实] '+escape(context['indices'])+'；沪深市场成交额：'+escape(context['market'])+'。</p>']
    parts.append('<p>'+escape(f"比较日{value.get('previous_date') or '未核验'}→{value['trade_date']}；成员快照日{value.get('membership_date') or '未取得'}。")+'</p>')
    if context['rows']:
        parts.append('<div class="table-scroll-shell"><table><thead><tr>')
        parts.extend('<th>'+h+'</th>' for h in ('题材', '涨跌', '成交额变化', '份额变化', '量能对照'))
        parts.append('</tr></thead><tbody>')
        for row in context['rows']:
            parts.append('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in row)+'</tr>')
        parts.append('</tbody></table></div>')
    else:
        parts.append('<p>[事实] 题材来源未取得，板块量能对照未计算。</p>')
    parts.append('<p>量能按当日已归档成员的同一集合比较两日；只覆盖当日前十题材。成交额非净流入，同涨或同向放量不自动确认主线；昨日涨停样本反馈另列。</p>')
    return ''.join(parts)
