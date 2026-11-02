"""课程波段证据卡：独立于既有波段标签、候选和排序的只读证据。"""
from __future__ import annotations

from datetime import date
from html import escape
import math

from utils.qfq import OHLC_PRICE_KEYS, apply_qfq

KEY = 'wave_evidence'
SCHEMA = 'wave-evidence-v1'
MARKER = 'data-wave-evidence'
NOTE = ('出处：鞠磊#1089，归入10月31日技术课，原文10月30日小组讨论00:38—07:05。'
        '前高锚点为本生命周期截至昨日最高价，平高取最近一根；不代表已识别第一波。'
        '间隔为前高后、今日前的有效K线根数，不等于连续市场交易日。'
        '短分歧证据采用1—3根间隔、今日阳线实体包住期间全部阴线实体；不自动判为一波延续。'
        '量比=今日成交量/此前5或13根均量（不含今日），5/13为工程窗口；量不是成交额。'
        'MA20按有效收盘计算，守线仅核验前高后至今日收盘，不能证明盘中未跌破。'
        '板块仅引用成交Top20容量样本，不代表全行业行情或个股带动板块。'
        '一波延续／二波重启保留人工定性；原有机械或人工波段标签独立保留，不影响入池和排序。')


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def missing(day, reason, status='source_failed'):
    return dict(schema=SCHEMA, date=day, status=status, gaps=[reason], facts={}, sector=None,
                interpretation='待人工定性')


def calculate(bars, factors, launch, day):
    """完整验证本卡输入；失败不覆盖原生命周期指标，也不退回未复权。"""
    try:
        date.fromisoformat(day)
        date.fromisoformat(launch)
        if launch > day or not bars or not factors:
            raise ValueError('行情或复权缺失')
        ordered = sorted(bars, key=lambda b: b['trade_date'])
        dates = [b['trade_date'] for b in ordered]
        if len(set(dates)) != len(dates) or dates[-1] != day:
            raise ValueError('行情日期重复、未来或缺目标日')
        for b in ordered:
            if date.fromisoformat(b['trade_date']).isoformat() != b['trade_date']:
                raise ValueError('行情日期非法')
            prices = [_num(b.get(k)) for k in OHLC_PRICE_KEYS]
            if any(p is None or p <= 0 for p in prices):
                raise ValueError('OHLC非正或缺失')
            o, c, h, l = prices
            if l > min(o, c) or h < max(o, c) or l > h:
                raise ValueError('OHLC顺序非法')
        factor_map = {}
        for f in factors:
            d = f['trade_date']
            if date.fromisoformat(d).isoformat() != d or d > day or d in factor_map:
                raise ValueError('因子日期重复、未来或非法')
            value = _num(f.get('adj_factor'))
            if value is None or value <= 0:
                raise ValueError('复权因子非法')
            factor_map[d] = value
        adjusted = apply_qfq(ordered, factors, keys=OHLC_PRICE_KEYS)
        if adjusted is None or any(b[k] <= 0 for b in adjusted for k in OHLC_PRICE_KEYS):
            raise ValueError('复权因子缺失、错位或复权价格非法')
        life_indexes = [i for i, b in enumerate(adjusted[:-1]) if b['trade_date'] >= launch]
        if not life_indexes:
            return missing(day, '启动后尚无前日锚点', 'partial')
        peak_i = max(life_indexes, key=lambda i: (adjusted[i]['high'], adjusted[i]['trade_date']))
        peak, today, previous = adjusted[peak_i], adjusted[-1], adjusted[-2]
        between = adjusted[peak_i+1:-1]
        after = adjusted[peak_i+1:]
        low = min(after, key=lambda b: (b['low'], b['trade_date']))
        mas = [sum(b['close'] for b in adjusted[i-19:i+1])/20 if i >= 19 else None
               for i in range(len(adjusted))]
        if any(m is not None and (not math.isfinite(m) or m <= 0) for m in mas):
            raise ValueError('MA20数值非法')
        ma = mas[-1]
        gaps = []
        if ma is None:
            gaps.append('MA20不足20根有效K线')
        direction = None
        if ma is not None and len(mas) > 1 and mas[-2] is not None:
            direction = '向上' if ma > mas[-2] else '向下' if ma < mas[-2] else '持平'
        else:
            gaps.append('MA20方向不足21根有效K线')
        hold_mas = mas[peak_i+1:]
        hold = (all(b['close'] >= m for b, m in zip(after, hold_mas))
                if all(m is not None for m in hold_mas) else None)
        if hold is None:
            gaps.append('前高后守MA20覆盖不足')
        ratios = {}
        for n in (5, 13):
            window = ordered[-n-1:]
            values = [_num(b.get('vol')) for b in window]
            if (len(window) != n+1 or any(v is None or v < 0 for v in values)
                    or sum(values[:-1]) <= 0 or not math.isfinite(sum(values[:-1]))):
                ratios[str(n)] = None
                gaps.append(f'成交量{n}根窗口缺失或基准为零')
            elif len({factor_map[b['trade_date']] for b in window}) != 1:
                ratios[str(n)] = None
                gaps.append(f'成交量{n}根窗口跨复权因子变化，量能不可比')
            else:
                ratio = values[-1] / (sum(values[:-1]) / n)
                ratios[str(n)] = ratio if math.isfinite(ratio) else None
                if ratios[str(n)] is None:
                    gaps.append(f'成交量{n}根比值非法')
        bearish = [b for b in between if b['close'] < b['open']]
        engulf = bool(1 <= len(between) <= 3 and bearish and today['close'] > today['open']
                      and today['open'] <= min(b['close'] for b in bearish)
                      and today['close'] >= max(b['open'] for b in bearish))
        facts = dict(peak_date=peak['trade_date'], interval_bars=len(between),
                     low_date=low['trade_date'], pullback_pct=100*(min(low['low']/peak['high'], 1)-1),
                     close_from_peak_pct=100*(today['close']/peak['high']-1),
                     recovery_from_low_pct=100*(today['close']/low['low']-1),
                     touched_prior_peak=today['high'] >= peak['high'],
                     bearish_bars=len(bearish), short_engulf=engulf,
                     ma20_position=('上方' if today['close'] > ma else '下方' if today['close'] < ma else '持平') if ma else None,
                     ma20_direction=direction, held_ma20=hold, volume_ratios=ratios,
                     rising=today['close'] > previous['close'])
        if any(not math.isfinite(facts[k]) for k in ('pullback_pct', 'close_from_peak_pct', 'recovery_from_low_pct')):
            raise ValueError('价格比值非法')
        return dict(schema=SCHEMA, date=day, status='partial' if gaps else 'complete', gaps=gaps,
                    facts=facts, sector=None, interpretation='待人工定性')
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return missing(day, str(exc))


def sector_context(history, industry):
    """只对申万二级同名组聚合，不把人工题材或未分类当作行业。"""
    result = dict(industry=industry or '未分类', status='missing-data', points=[], delta_pp=None,
                  reason='申万二级身份或完整5开放日日历缺失')
    if not industry or industry in {'未分类', 'UNCLASSIFIED'} or history.get('calendar_status') != 'complete':
        return result
    for p in history.get('history', []):
        group = p.get('sectors', {}).get(industry)
        usable = p.get('status') in {'complete', 'partial'} and group is not None
        result['points'].append(dict(date=p['date'], status=p['status'],
                                    total=group['total'] if usable else None,
                                    valid=group['valid'] if usable else None,
                                    above_pct=group['above_pct'] if usable else None))
    if len(result['points']) != 5:
        return result
    result['status'] = ('complete' if all(p['status'] == 'complete' and p['above_pct'] is not None
                                         for p in result['points']) else 'partial')
    result['reason'] = '仅成交Top20同申万二级样本；缺榜/缺源不是行业走弱'
    comparison = history['history'][-1].get('sector_comparisons', {}).get(industry, {})
    if comparison.get('status') == 'complete' and all(p['status'] == 'complete' for p in result['points'][-2:]):
        result['delta_pp'] = comparison.get('daily_delta_pp')
    return result


def attach_sector(card, history, industry):
    card = dict(card)
    context = sector_context(history, industry)
    card['sector'] = context
    if context['status'] != 'complete':
        card['gaps'] = [*card['gaps'], '板块5日容量背景覆盖不完整']
        if card['status'] == 'complete':
            card['status'] = 'partial'
    return card


def _valid_card(card, day):
    if (not isinstance(card, dict) or card.get('schema') != SCHEMA or card.get('date') != day
            or card.get('status') not in {'complete', 'partial', 'source_failed', 'not_computed'}
            or not isinstance(card.get('facts'), dict) or not isinstance(card.get('gaps'), list)
            or any(not isinstance(g, str) for g in card['gaps'])):
        return False
    facts = card['facts']
    if not facts:
        return card['status'] != 'complete'
    for key in ('interval_bars', 'bearish_bars'):
        if type(facts.get(key)) is not int or facts[key] < 0:
            return False
    for key in ('pullback_pct', 'close_from_peak_pct', 'recovery_from_low_pct'):
        if type(facts.get(key)) not in (int, float) or not math.isfinite(facts[key]):
            return False
    for key in ('short_engulf', 'touched_prior_peak', 'rising', 'held_ma20'):
        if facts.get(key) is not None and type(facts[key]) is not bool:
            return False
    if facts.get('ma20_position') not in (None, '上方', '下方', '持平') or facts.get('ma20_direction') not in (None, '向上', '向下', '持平'):
        return False
    ratios = facts.get('volume_ratios')
    if not isinstance(ratios, dict) or set(ratios) != {'5', '13'}:
        return False
    if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0) for v in ratios.values()):
        return False
    try:
        if any(date.fromisoformat(facts[k]).isoformat() != facts[k] or facts[k] > day for k in ('peak_date', 'low_date')):
            return False
    except (ValueError, TypeError, KeyError):
        return False
    breadth = card.get('breadth')
    if breadth is not None:
        from analyzers.market_strength import valid_context
        if not valid_context(breadth, day):
            return False
    sector = card.get('sector')
    if sector is not None:
        if not isinstance(sector, dict) or not isinstance(sector.get('points'), list):
            return False
        points = sector['points']
        if any(not isinstance(p, dict) for p in points):
            return False
        if points and (len(points) != 5 or points[-1].get('date') != day):
            return False
        for point in points:
            if not isinstance(point, dict):
                return False
            for k in ('total', 'valid'):
                if point.get(k) is not None and (type(point[k]) is not int or point[k] < 0):
                    return False
            value = point.get('above_pct')
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100):
                return False
        value = sector.get('delta_pp')
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            return False
    return True


def cards(payload, day, max_rows=12):
    """使用现有分区与限额；旧卡、缓存、停牌绝不冒充当天证据。"""
    from services.emotion_leader.tracking import tracking_view
    if not isinstance(payload, dict) or payload.get('date') != day or payload.get('status') not in {'ok', 'partial'}:
        return []
    if any(not isinstance(payload.get(k), list) or any(not isinstance(r, dict) for r in payload[k])
           for k in ('active', 'archived')):
        return []
    view = tracking_view(payload)
    rows = []
    for group in ('current', 'history', 'unverified'):
        for row in view[group][:max_rows]:
            card = row.get(KEY)
            if row.get('metric_as_of') != day:
                card = missing(day, '当日行情未刷新，不沿用旧证据', 'not_computed')
            elif row.get('metric_status') == 'source_failed':
                card = missing(day, str(row.get('metric_error') or '当日行情来源失败'))
            elif row.get('metric_status') != 'ok':
                card = missing(day, '当日指标不可用或已核验停牌', 'not_computed')
            elif not isinstance(card, dict) or card.get('schema') != SCHEMA or card.get('date') != day:
                card = missing(day, '旧快照未计算或卡片日期不匹配', 'not_computed')
            elif not _valid_card(card, day):
                card = missing(day, '卡片结构损坏')
            if card.get('facts') and (not card.get('breadth') or card['breadth'].get('status') != 'complete' or not card['breadth'].get('stats')):
                card = dict(card, status='partial' if card['status']=='complete' else card['status'],
                            gaps=[*card['gaps'], '板块涨跌广度缺失或覆盖不完整'])
            rows.append(dict(code=str(row.get('code') or ''), name=str(row.get('name') or ''), card=card))
    return rows


def _pct(value):
    return f'{value:+.2f}%' if type(value) in (int, float) and math.isfinite(value) else '未计算'


def _yes(value):
    return '是' if value is True else '否' if value is False else '未计算'


def evidence_lines(card):
    f = card.get('facts') or {}
    if not f:
        return ['未计算：' + '；'.join(map(str, card.get('gaps', [])))]
    ratios = f.get('volume_ratios') or {}
    ratio_text = '／'.join(f'{ratios[str(n)]:.2f}倍' if _num(ratios.get(str(n))) is not None else '未计算' for n in (5, 13))
    lines = [f"前高{f.get('peak_date')}；间隔{f.get('interval_bars')}根；前高后最低回撤{_pct(f.get('pullback_pct'))}；现收盘距前高{_pct(f.get('close_from_peak_pct'))}",
             f"阴线{f.get('bearish_bars')}根；短分歧实体反包{_yes(f.get('short_engulf'))}；今日触及前高{_yes(f.get('touched_prior_peak'))}",
             f"MA20{f.get('ma20_position') or '未计算'}／{f.get('ma20_direction') or '未计算'}；前高后收盘均守线{_yes(f.get('held_ma20'))}；今日较前收上涨{_yes(f.get('rising'))}；量比5／13：{ratio_text}"]
    sector = card.get('sector') or {}
    points = sector.get('points') or []
    if points:
        latest = points[-1]
        valid = sum(p.get('above_pct') is not None and p.get('status') == 'complete' for p in points)
        delta = sector.get('delta_pp')
        delta_text = f'{delta:+.2f}个百分点' if _num(delta) is not None else '未计算'
        lines.append(f"板块{sector.get('industry')}：Top20内当日可算{latest.get('valid') if latest.get('valid') is not None else '未计算'}/{latest.get('total') if latest.get('total') is not None else '未计算'}；MA20上方占比{_pct(latest.get('above_pct'))}；较前日{delta_text}；5日完整覆盖{valid}/5")
    else:
        lines.append('板块背景未计算')
    breadth = card.get('breadth') or {}
    stats = breadth.get('stats') or {}
    if stats:
        lines.append(f"板块≥5%广度（{breadth.get('status')}）：{stats.get('up_count')}只／{stats.get('sample_count')}有效样本，占比{_pct(stats.get('up_pct'))}；≤-5% {stats.get('down_count')}只，占比{_pct(stats.get('down_pct'))}")
        delta = breadth.get('delta') or {}
        lines.append('≥5%较前日增减（'+str(breadth.get('comparison_status','未计算'))+'）：' + (str(delta['up_count'])+'只' if delta.get('up_count') is not None else '未计算'))
        for n, window in breadth.get('windows', {}).items():
            lines.append(f"近{n}日≥5%股票日占比{_pct(window.get('up_pct'))}，可统计{window.get('valid_days',0)}/{n}日，其中完整{window.get('complete_days',0)}日")
    else:
        lines.append('板块≥5%／≤-5%广度未计算（旧档或来源不足）')
    lines.append('缺口：' + ('；'.join(map(str, card.get('gaps', []))) or '无'))
    return lines


def render_markdown(payload, day, max_rows=12):
    rows = cards(payload, day, max_rows)
    lines = ['## 一波延续／二波重启证据卡',
             '[事实] 前高按生命周期截至昨日最高价；根数为有效K线。量比用成交量；板块仅Top20容量样本。',
             '[判断] 保留人工定性，不改变原波段标签。完整口径与卡片见同日JSON和正式复盘③。']
    if not rows:
        lines.append('未计算：没有可展示的同日核心来源。')
    shown = 0
    for row in rows:
        clean = lambda s: str(s).replace('|', '／').replace('\n', ' ').replace('<', '＜').replace('>', '＞')
        block = [f"### {clean(row['name'])}（{clean(row['code'])}） · {row['card']['status']} · 待人工定性"]
        block += ['- [事实] ' + clean(s) for s in evidence_lines(row['card'])]
        # 只限新增摘要，按实际UTF-8字节计；整卡加入，避免截断事实或放大既有推送。
        if shown >= 3 or len(('\n'.join(lines + block)).encode('utf-8')) > 5400:
            break
        lines += block
        shown += 1
    lines.append(f'卡片摘要展示{shown}/{len(rows)}只；完整已刷新核心卡片保留在同日JSON。出处：鞠磊#1089。')
    return '\n'.join(lines) + '\n'


def render_html(payload, day):
    rows = cards(payload, day)
    parts = [f'<div {MARKER}="{escape(day)}"><p>[事实] 一波延续／二波重启证据卡；{len(rows)}只；[判断] 待人工定性。</p>',
             f'<details class="evidence" data-as-of="{escape(day)}" data-items="{max(1,len(rows))}" data-evidence-kind="wave-evidence"><summary>展开形态与板块证据（{max(1,len(rows))}项）</summary><div class="evidence-body">',
             ]
    for row in rows:
        parts.append('<p><strong>' + escape(row['name']+'（'+row['code']+'） · '+row['card']['status']) + '</strong><br>' + '<br>'.join(escape(s) for s in evidence_lines(row['card'])) + '</p>')
    if not rows:
        parts.append('<p>未计算：没有可展示的同日核心来源。</p>')
    parts.append('<p>' + escape(NOTE) + '</p></div></details></div>')
    gap = ('波段证据卡存在未计算或覆盖缺口' if not rows or any(r['card']['status'] != 'complete' for r in rows) else '')
    return ''.join(parts), gap
