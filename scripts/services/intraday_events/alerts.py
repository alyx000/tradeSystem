"""从已验证的相邻窗口挑选一批值得提醒的新变化。"""
from datetime import datetime, time
from math import isclose, isfinite
from statistics import median

from services.intraday_monitor.guards import shanghai_now

ANCHOR_ALERT_PCT = 2.0
ANCHOR_COOLDOWN_SECONDS = 30 * 60
ANCHOR_COOLDOWN_OVERRIDE_PCT = 3.0
POSSIBLY_DELIVERED = ('accepted', 'sending', 'unconfirmed')


def in_sampling_session(now):
    """手动采样和返回的行情须在连续交易时段内，允许上午或下午收盘整点。"""
    current = shanghai_now(now).time()
    return time(9, 30) <= current <= time(11, 30) or time(13) <= current <= time(15)


def alert_window(now):
    """当前交易时段内距计划时点最近的 10 分钟槽，午休和收盘起点排除。"""
    local = shanghai_now(now)
    minutes = local.hour * 60 + local.minute
    for start in (570, 780):
        if start <= minutes < start + 120:
            slot = min(11, (minutes - start + 3) // 10)
            return (local.date(), start + 2 + 10 * slot)
    return None


def _reaches(change, threshold):
    magnitude = abs(change)
    return magnitude >= threshold or isclose(magnitude, threshold, rel_tol=0, abs_tol=1e-10)


def _anchor_moves(watch, anchored):
    moves = {}
    for row in watch:
        change = row['interval_pct']
        if row['code'] in anchored and change is not None and _reaches(change, ANCHOR_ALERT_PCT):
            direction = 'up' if change > 0 else 'down'
            key = f"anchor:{row['code']}:{direction}"
            moves[key] = dict(row, kind='anchor_move', key=key, direction=direction)
    return moves


def _repeat_reason(key, row, last_pct, ranges):
    up = row['direction'] == 'up'
    if key not in last_pct:
        opposite = 'down' if up else 'up'
        if f"anchor:{row['code']}:{opposite}" in last_pct:
            return f"此前入选另一方向提醒，今日首次入选向{'上' if up else '下'}提醒"
        return ''
    bounds = ranges.get(row['code'])
    if bounds and up and row['price'] > bounds[1]:
        return f'突破此前盘中采样高点 {bounds[1]:.2f} 元'
    if bounds and not up and row['price'] < bounds[0]:
        return f'跌破此前盘中采样低点 {bounds[0]:.2f} 元'
    if (last_pct[key] <= 0 < row['pct_chg'] if up else last_pct[key] >= 0 > row['pct_chg']):
        return f"上次同向入选时当日 {last_pct[key]:+.2f}%，现已{'站上' if up else '跌破'}昨收"
    return None


def select_alerts(analysis, intervals, context, previous_state, *,
                  previous_anchor_watch=(), anchor_history=None):
    sectors = []
    state = {}
    result = dict(anchors=[], sectors=sectors, sector_state=state,
                  anchor_rules=dict(min_move_pct=ANCHOR_ALERT_PCT,
                                    cooldown_seconds=ANCHOR_COOLDOWN_SECONDS,
                                    cooldown_override_pct=ANCHOR_COOLDOWN_OVERRIDE_PCT,
                                    repeat_gate='sampled_extreme_or_prior_close_cross'))
    if analysis['baseline_only'] or not intervals:
        return result
    source = context.get('anchors') or {}
    anchored = {r['code'] for r in source.get('stocks', [])
                if context.get('previous_date') and r['source_date'] == context['previous_date']
                and source.get('expected_source_date') == context['previous_date']}
    previous_moves = _anchor_moves(previous_anchor_watch, anchored)
    moves = _anchor_moves(analysis.get('anchor_watch', []), anchored)
    history = anchor_history or {}
    recent_codes = history.get('recent_codes', ())
    last_pct = history.get('last_pct', {})
    ranges = history.get('ranges', {})
    for key, row in moves.items():
        if key in previous_moves or (row['code'] in recent_codes
                and not _reaches(row['interval_pct'], ANCHOR_COOLDOWN_OVERRIDE_PCT)):
            continue
        reason = _repeat_reason(key, row, last_pct, ranges)
        if reason is not None:
            if reason:
                row['repeat_reason'] = reason
            previous_accepted = history.get('last_accepted', {}).get(key)
            if previous_accepted:
                row['previous_accepted'] = previous_accepted
            result['anchors'].append(row)
    focused = {context['industry_map'].get(code) for code in anchored}
    market = median(intervals.values())
    result['market_median_pct'] = market
    by_name = {r['sector']: r['metrics'] for r in analysis['sectors']}
    for key in analysis['active']:
        if not key.startswith('sector:'):
            continue
        _, name, direction = key.split(':')
        sign = 1 if direction == 'up' else -1
        metrics = by_name[name]
        change = metrics['median_pct']
        gap = sign * (change - market)
        last = previous_state.get(key, {})
        streak = last.get('streak', 0) + 1
        opposite = sign * market < 0 and gap >= 1.0
        widened = (name in focused and streak == 2 and gap >= .6
                   and gap - last['gap'] >= .3)
        state[key] = dict(streak=streak, gap=gap, opposite=opposite)
        if (opposite and not last.get('opposite')) or widened:
            sectors.append(dict(name=name, median_pct=change, gap=gap, count=metrics['count'],
                                up_ratio_pct=metrics['up_ratio_pct'], streak=streak,
                                reason='逆市' if opposite else '连续两窗分化扩大'))
    sectors.sort(key=lambda item: (-item['gap'], item['name']))
    return result


def _magnitude_suffix(pct):
    rounded = f'{abs(pct):.2f}'
    return '（不足 0.01%）' if rounded == '0.00' else f' {rounded}%'


def _previous_price_text(row, now):
    previous = row.get('previous_accepted')
    if not isinstance(previous, dict):
        return ''
    try:
        at = datetime.fromisoformat(previous['captured_at'])
        price, reference = row['price'], previous['price']
        if (at.tzinfo is None or at >= now or shanghai_now(at).date() != now.date()
                or any(type(p) not in (int, float) or not isfinite(p) or p <= 0 for p in (price, reference))):
            return ''
        pct = (price - reference) / reference * 100
        if not isfinite(pct):
            return ''
    except (KeyError, TypeError, ValueError, OverflowError):
        return ''
    comparison = '持平' if price == reference else ('高' if price > reference else '低') + _magnitude_suffix(pct)
    return f'。上次已接收的同向提醒在 {shanghai_now(at):%H:%M}，现价较当时{comparison}'


def format_alert(captured_at, alert):
    local = shanghai_now(datetime.fromisoformat(captured_at))
    start_at = alert.get('window_start_at')
    window = f'{local:%H:%M}'
    if start_at:
        start = shanghai_now(datetime.fromisoformat(start_at))
        window = f'{start:%H:%M}–{window}'
    anchors, sectors = alert['anchors'], alert['sectors']
    headlines = []
    if len(anchors) == 1:
        row = anchors[0]
        headlines.append(row['name'] + ('上冲' if row['interval_pct'] > 0 else '回落'))
    elif anchors:
        headlines.append(f'{len(anchors)} 只锚点异动')
    if len(sectors) == 1:
        sector = sectors[0]
        change = '走强' if sector['median_pct'] > 0 else '走弱'
        headlines.append(f"{sector['name']}逆市{change}" if sector['reason'] == '逆市'
                         else f"{sector['name']}分化扩大")
    elif sectors:
        headlines.append(f'{len(sectors)} 个行业异动')
    title = f"{local:%m月%d日} {window}｜{'，'.join(headlines) or '盘中异动'}"
    lines = ['### ' + title]
    for row in anchors:
        detail = f"{row['price']:.2f} 元，{row['repeat_reason']}。" if row.get('repeat_reason') else ''
        move, daily = row['interval_pct'], row['pct_chg']
        position = f'当日 {daily:+.2f}%'
        if move > 0 > daily:
            position = '仍低于昨收' + _magnitude_suffix(daily)
        elif move < 0 < daily:
            position = '仍高于昨收' + _magnitude_suffix(daily)
        lines.append(f"**{row['name']}（{row['code']}）**　{detail}区间 {move:+.2f}%，{position}"
                     + _previous_price_text(row, local))
    for row in sectors[:3]:
        lines.append(f"**{row['name']}**　{row['reason']}，区间涨跌幅中位数 {row['median_pct']:+.2f}%，"
                     f"同期市场 {alert['market_median_pct']:+.2f}%（{row['count']} 只，"
                     f"上涨占比 {row['up_ratio_pct']:.0f}%）")
    if len(sectors) > 3:
        lines.append(f"另有 {len(sectors)-3} 个行业达标，详见本地报告。")
    return title, '\n\n'.join(lines)
