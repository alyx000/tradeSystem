"""固定样本、相邻快照的研究阈值；不是交易信号或引领因果判定。"""
from datetime import datetime
from statistics import median

from .alerts import select_alerts

RULE_VERSION = 'intraday-events-shadow-v1'
# 工程起点，尚未校准；只记录候选，不用于推送或交易。
RULES = dict(interval_seconds=[240, 420], sector_min_members=8,
             sector_move_pct=0.6, sector_direction_ratio=70,
             core_move_pct=1.5, follower_move_pct=0.5,
             feedback_ratio_change_pp=20, feedback_median_change_pp=1)


def metrics(values):
    return dict(count=len(values), median_pct=median(values) if values else None,
                up_ratio_pct=100*sum(v > 0 for v in values)/len(values) if values else None)


INTERVAL_SECONDS = {5: (240, 420), 10: (420, 780)}


def comparable(previous, current):
    if not previous or previous.get('trade_date') != current['trade_date']:
        return False
    a, b = (datetime.fromisoformat(s['captured_at']) for s in (previous, current))
    # 不跨午休、不跨采集断档，也不把每轮漂移的样本视作固定组合。
    cadence = current.get('interval_minutes', 5)
    low, high = INTERVAL_SECONDS[cadence]
    return (previous.get('interval_minutes', 5) == cadence
            and low <= (b-a).total_seconds() <= high
            and (a.hour < 12) == (b.hour < 12)
            and previous.get('context_sha256') == current.get('context_sha256'))


def analyze(previous, current, context, pending=None, active=None, sector_state=None, *,
            previous_anchor_watch=(), anchor_history=None):
    """缺任一成员就保留指标但阻断该组事件；原始股票列表随结果存档。"""
    pending, active = pending or [], active or []
    linked = comparable(previous, current)
    old = previous['stocks'] if linked else {}
    quotes = current['stocks']
    intervals = {c: (q['price']/old[c]['price']-1)*100 for c,q in quotes.items() if c in old}
    events, sectors, feedback, following, next_pending, anchor_watch = [], [], [], [], [], []
    current_active = []
    mapping = context.get('industry_map', {})
    groups = {}
    for code in context['universe']:
        if mapping.get(code):
            groups.setdefault(mapping[code], []).append(code)
    for sector, members in sorted(groups.items()):
        valid = [c for c in members if c in intervals]
        values = [intervals[c] for c in valid]
        stat = metrics(values)
        complete = linked and len(valid) == len(members)
        row = dict(sector=sector, expected=len(members), valid=len(valid),
                   status='complete' if complete else 'partial', metrics=stat,
                   members=members, amount_delta_yuan=None)
        if complete and all(quotes[c]['amount'] >= old[c]['amount'] for c in members):
            row['amount_delta_yuan'] = sum(quotes[c]['amount']-old[c]['amount'] for c in members)
        if complete and len(valid) >= RULES['sector_min_members']:
            direction = ('up' if stat['median_pct'] >= .6 and stat['up_ratio_pct'] >= 70
                         else 'down' if stat['median_pct'] <= -.6 and
                         100*sum(v < 0 for v in values)/len(values) >= 70 else None)
            if direction:
                key = f'sector:{sector}:{direction}'
                current_active.append(key)
                if key not in active:
                    events.append(dict(kind='sector_cluster', key=key, direction=direction,
                                       sector=sector, metrics=stat, members=valid))
        sectors.append(row)

    for label, cohort in context.get('cohorts', {}).items():
        members = cohort.get('codes')
        if members is None:
            feedback.append(dict(group=label, status='source_failed', expected=None,
                                 error=cohort.get('error'), metrics=metrics([])))
            continue
        valid = [c for c in members if c in quotes]
        stat = metrics([quotes[c]['pct_chg'] for c in valid])
        before = metrics([old[c]['pct_chg'] for c in members if c in old])
        full = len(valid) == len(members)
        row = dict(group=label, status=('empty' if not members else 'complete' if full else 'partial'),
                   expected=len(members), valid=len(valid), metrics=stat, members=members,
                   previous_metrics=before if linked else None,
                   limit_up_count=None, limit_down_count=None)
        if linked and members and full and all(c in old for c in members):
            dm = stat['median_pct'] - before['median_pct']
            dr = stat['up_ratio_pct'] - before['up_ratio_pct']
            direction = 'up' if dm >= 1 and dr >= 20 else 'down' if dm <= -1 and dr <= -20 else None
            if direction:
                key = f'feedback:{label}:{direction}'
                current_active.append(key)
                if key not in active:
                    events.append(dict(kind='strong_feedback_change', key=key, direction=direction,
                                       group=label, median_change_pp=dm, up_ratio_change_pp=dr,
                                       metrics=stat, members=members))
        feedback.append(row)

    # 锚点自身异动独立于板块映射，不因行业缺源或不在昨日最票中而消失。
    anchors = (context.get('anchors') or {}).get('stocks', [])
    for anchor in anchors:
        code = anchor['code']
        q = quotes.get(code)
        change = intervals.get(code)
        row = dict(code=code, name=anchor['name'], source_date=anchor['source_date'],
                   roles=anchor.get('roles', []), status='complete' if q else 'partial',
                   price=q['price'] if q else None, pct_chg=q['pct_chg'] if q else None,
                   interval_pct=change, condition_status='原文条件未自动判定')
        anchor_watch.append(row)
        if change is not None and abs(change) >= RULES['core_move_pct']:
            direction = 'up' if change > 0 else 'down'
            key = f'anchor:{code}:{direction}'
            current_active.append(key)
            if key not in active:
                events.append(dict(kind='anchor_move', key=key, direction=direction, **row))

    # 严格下一有效窗口看跟随。先前已经同向异动的成员排除，防止把同涨叫先后跟随。
    if linked:
        for item in pending:
            eligible = item['eligible_followers']
            def ordered(code):
                try:
                    trigger = datetime.fromisoformat(item['trigger_quote_at'])
                    start = datetime.fromisoformat(old[code]['quote_at'])
                    end = datetime.fromisoformat(quotes[code]['quote_at'])
                    return trigger <= start < end
                except (KeyError, TypeError, ValueError):
                    return False
            valid = [c for c in eligible if c in intervals and ordered(c)]
            hits = [c for c in valid if intervals[c]*item['sign'] >= .5]
            status = 'complete' if len(valid) == len(eligible) else 'partial'
            result = dict(**item, status=status, observed_at=current['captured_at'],
                          valid=len(valid), expected=len(eligible), followers=hits,
                          unverified_followers=[c for c in eligible if c not in valid],
                          follower_changes={c:intervals[c] for c in valid},
                          conclusion=('后续同向扩散' if status == 'complete' and len(hits) >= 3
                                      else '未见足够后续扩散' if status == 'complete' else '跟随窗口缺源'),
                          causality='仅采样窗口先后，不证明核心带动板块')
            following.append(result)
            if status == 'complete' and len(hits) >= 3:
                events.append(dict(kind='core_following', key=f"follow:{item['code']}:{item['trigger_at']}", **result))
        watchers = {r['code']: r for r in [*anchors, *context.get('cores', [])]}
        for core in watchers.values():
            code = core['code']
            if code not in intervals or abs(intervals[code]) < RULES['core_move_pct']:
                continue
            sign = 1 if intervals[code] > 0 else -1
            key = f'core:{code}:{sign}'
            current_active.append(key)
            if key in active:
                continue
            members = groups.get(mapping.get(code), [])
            # 完整同业分母和前窗观测是先后观察的前提。
            if len(members) < 8 or not all(c in intervals for c in members):
                following.append(dict(code=code, status='partial', conclusion='同业完整样本不足，未计算跟随'))
                continue
            try:
                trigger_at = datetime.fromisoformat(quotes[code]['quote_at'])
                start_at = datetime.fromisoformat(old[code]['quote_at'])
                if not start_at < trigger_at:
                    raise ValueError('核心报价时间未前进')
            except (KeyError, TypeError, ValueError):
                following.append(dict(code=code, status='partial', conclusion='核心报价时间缺失或未前进，未计算跟随'))
                continue
            eligible = [c for c in members if c != code and intervals[c]*sign < .5]
            next_pending.append(dict(code=code, name=core['name'], sector=mapping[code],
                                     sign=sign, trigger_at=current['captured_at'],
                                     trigger_quote_at=quotes[code]['quote_at'],
                                     trigger_interval_pct=intervals[code], eligible_followers=eligible,
                                     already_moving=[c for c in members if c != code and c not in eligible]))
    elif pending:
        following = [dict(code=p['code'], status='partial', conclusion='采样断档，原跟随窗口失效') for p in pending]
    bounds = INTERVAL_SECONDS[current.get('interval_minutes', 5)]
    result = dict(rule_version=RULE_VERSION, rules=dict(RULES, interval_seconds=list(bounds)), baseline_only=not linked,
                sectors=sectors, feedback=feedback, following=following, anchor_watch=anchor_watch,
                pending_following=next_pending, active=current_active, events=events)
    if current.get('interval_minutes') == 10:
        result['alert'] = select_alerts(
            result, intervals, context, sector_state or {},
            previous_anchor_watch=previous_anchor_watch, anchor_history=anchor_history)
        if linked:
            result['alert']['window_start_at'] = previous['captured_at']
    return result
