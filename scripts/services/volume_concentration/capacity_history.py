"""容量趋势板块/历史只读派生：开放日脊柱、每日变动样本与同股同分类对照。"""
from __future__ import annotations

from datetime import date, timedelta
import math
import sqlite3

from . import capacity_trend as ct, repo
from .aggregator import UNCLASSIFIED

DEFAULT_DAYS = 10
NOTE = ("[事实] 比例分母为当日该组可计算股票数，覆盖另列；只代表成交榜样本，不代表整个行业。"
        "历史按完整SSE开放日历排列，不跨缺日比较。每日样本比例会受换榜影响；"
        "同股变化仅比较相邻开放日均complete且行业归类未变的共同股票。"
        "未分类单列；回退旧证据保留标记但不参与跨日变化。两市成交额沿用既有来源，来源改变不算环比。")


def _number(value):
    return (float(value) if type(value) in (int, float)
            and math.isfinite(value) and value > 0 else None)


def _industry(row):
    value = str(row.get('industry') or '').strip()
    return '未分类' if value in ('', UNCLASSIFIED, '未分类') else value


def _group(rows):
    valid = [r for r in rows if r['status'] == 'complete']
    n = len(valid)
    counts = {key: sum(r['ma20_position'] == label for r in valid)
              for key, label in (('above', '上方'), ('below', '下方'), ('flat', '持平'))}
    return dict(total=len(rows), valid=n,
                **{k: v if n else None for k, v in counts.items()},
                above_pct=100 * counts['above'] / n if n else None,
                below_pct=100 * counts['below'] / n if n else None,
                rising=sum(r['ma20_position'] == '上方' and r['ma20_direction'] == '向上' for r in valid) if n else None,
                falling=sum(r['ma20_position'] == '下方' and r['ma20_direction'] == '向下' for r in valid) if n else None)


def summarize(record, target):
    block = ct.from_record(record, target)
    if record is None:
        block = dict(status='missing-data', rows=[], trade_date=target)
    rows = block.get('rows', [])
    try:
        # 快照的旧校验未强制身份唯一；跨日比较必须先拒绝重复或非法身份。
        identities = [ct._stock_identity(r.get('code')) for r in rows]
        if len(set(identities)) != len(identities):
            raise ValueError('重复股票身份')
    except (ValueError, TypeError, AttributeError):
        block = dict(status='source_failed', rows=[], trade_date=target, reason='股票身份非法或重复')
        rows = []
    source = record.get('source') if isinstance(record, dict) else None
    source = source if isinstance(source, dict) else {}
    market = _number(record.get('market_total_billion')) if isinstance(record, dict) else None
    market_source = source.get('market_total_source')
    market_source = market_source if isinstance(market_source, str) and market_source.strip() else None
    sectors = {ind: _group([r for r in rows if _industry(r) == ind])
               for ind in sorted({_industry(r) for r in rows})}
    return dict(date=target, status=block['status'], reason=block.get('reason'), rows=rows,
                overall=_group(rows) if rows else None, sectors=sectors,
                market_total_billion=market, market_source=market_source,
                latest_attempt_status=(block.get('latest_attempt') or {}).get('status'))


def compare(previous, current, industry=None):
    """仅两端完整时发布百分点变化；板块不在榜的比例为空而不是0%。"""
    result = dict(status='not_comparable', common_count=None, new_count=None, dropped_count=None,
                  reclassified_count=None, daily_delta_pp=None, common_delta_pp=None)
    if previous is None or any(p['status'] != 'complete' for p in (previous, current)):
        return result
    left_all = {ct._stock_identity(r['code']): r for r in previous['rows']}
    right_all = {ct._stock_identity(r['code']): r for r in current['rows']}
    left = {k: r for k, r in left_all.items() if industry is None or _industry(r) == industry}
    right = {k: r for k, r in right_all.items() if industry is None or _industry(r) == industry}
    # 分类迁移独立计数，不能伪装成真实进出榜，也不能算同行业共同样本。
    changed = {k for k in left_all.keys() & right_all.keys()
               if _industry(left_all[k]) != _industry(right_all[k])
               and (industry is None or _industry(left_all[k]) == industry or _industry(right_all[k]) == industry)}
    common = (left.keys() & right.keys()) - changed
    a, b = _group(list(left.values())), _group(list(right.values()))
    result.update(status='complete', common_count=len(common),
                  new_count=len(right.keys() - left_all.keys()),
                  dropped_count=len(left.keys() - right_all.keys()), reclassified_count=len(changed))
    if a['above_pct'] is not None and b['above_pct'] is not None:
        result['daily_delta_pp'] = b['above_pct'] - a['above_pct']
    if common:
        before = sum(left[k]['ma20_position'] == '上方' for k in common)
        after = sum(right[k]['ma20_position'] == '上方' for k in common)
        result['common_delta_pp'] = 100 * (after - before) / len(common)
    return result


def _open_days(conn, target, days):
    """日历逐自然日验证；不凭星期推断，也不以最近记录代替上一开放日。

    生产日历已有写入路径：volume_watch._run_daily → is_non_trading_day →
    ensure_trade_calendar → db.queries.upsert_trade_calendar。这里刻意只读，
    不为HTML/trend/dry-run另开隐式采集或写库；跨年窗口缺旧年日历仍拒算。
    """
    end = date.fromisoformat(target)
    start = end - timedelta(days=max(90, days * 4))
    rows = conn.execute('SELECT date,is_open FROM trade_calendar WHERE exchange=? AND date BETWEEN ? AND ? ORDER BY date DESC',
                        ('SSE', start.isoformat(), target)).fetchall()
    expected, opened = end, []
    for day, is_open in rows:
        if day != expected.isoformat() or is_open not in (0, 1):
            raise ValueError('交易日历存在缺口或非法状态')
        if day == target and not is_open:
            raise ValueError('目标日不是开放日')
        if is_open:
            opened.append(day)
        if len(opened) == days:
            return list(reversed(opened))
        expected -= timedelta(days=1)
    raise ValueError('交易日历不足，历史变化未计算')


def _read(conn, target):
    try:
        return repo.get_concentration(conn, target)
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return dict(source={ct.KEY: None})  # 明确损坏，不折叠为旧快照未计算。


def build(conn, target, *, current_record=None, days=DEFAULT_DAYS):
    days = max(1, min(60, int(days)))
    current_record = current_record if current_record is not None else _read(conn, target)
    current = summarize(current_record, target)
    result = dict(schema='capacity-history-v1', current=current, days=days,
                  calendar_status='complete', history=[], note=NOTE)
    try:
        opened = _open_days(conn, target, days)
    except (sqlite3.Error, ValueError, TypeError):
        result.update(calendar_status='source_failed', reason='交易日历缺失、不完整或目标日非开放日；历史变化未计算')
        return result
    previous = None
    for day in opened:
        point = current if day == target else summarize(_read(conn, day), day)
        point['comparison'] = compare(previous, point)
        point['sector_comparisons'] = {ind: compare(previous, point, ind)
                                       for ind in point['sectors'].keys() | (previous['sectors'].keys() if previous else set())}
        point['market_change_pct'] = None
        if (previous and previous['market_total_billion'] and point['market_total_billion']
                and point['market_source'] and point['market_source'] == previous['market_source']):
            point['market_change_pct'] = 100 * (point['market_total_billion'] / previous['market_total_billion'] - 1)
        result['history'].append(point)
        previous = point
    return result


def tables(view):
    """MD与HTML共享文本表格，所有来源文本交由各自renderer统一转义。"""
    def number(value, suffix=''):
        return '—' if value is None else f'{value:.1f}{suffix}'

    def ratio(group):
        return ('—' if not group or group['above_pct'] is None else
                f"{group['above_pct']:.1f}% / {group['below_pct']:.1f}%")

    def coverage(group):
        return '—' if group is None else f"{group['valid']}/{group['total']}"

    def changes(comp):
        if comp['status'] != 'complete':
            return '未计算'
        return f"新{comp['new_count']} / 出{comp['dropped_count']} / 改类{comp['reclassified_count']}"

    def same(comp):
        if comp['common_count'] is None:
            return '未计算'
        return f"{comp['common_count']}只 / {number(comp['common_delta_pp'], 'pp')}"

    current = view['current']
    sector_rows = []
    for industry, group in [('全部在榜样本', current['overall']), *current['sectors'].items()]:
        if group is None:
            continue
        sector_rows.append([industry, coverage(group), ratio(group), str(group['flat']) if group['flat'] is not None else '—',
                            str(group['rising']) if group['rising'] is not None else '—',
                            str(group['falling']) if group['falling'] is not None else '—'])
    output = [('容量板块汇总', ['申万二级／范围', '可计算/在榜', '线上/线下比例', '贴线数', '线上且线向上', '线下且线向下'], sector_rows)]
    history_rows, sector_history = [], []
    for point in view['history']:
        comp = point['comparison']
        state = point['status'] + (f"（本次{point['latest_attempt_status']}）" if point['latest_attempt_status'] else '')
        history_rows.append([point['date'], state, coverage(point['overall']), ratio(point['overall']),
                             number(comp['daily_delta_pp'], 'pp'), same(comp), changes(comp),
                             number(point['market_total_billion'], '亿'), number(point['market_change_pct'], '%'),
                             point['market_source'] or '来源未记录'])
        # 上一日独有行业也列出，真实退出与缺数据分别显示。
        for industry in sorted(point['sector_comparisons']):
            group = point['sectors'].get(industry)
            sc = point['sector_comparisons'][industry]
            sector_history.append([point['date'], industry, state,
                                   '未在榜' if group is None and point['status'] == 'complete' else coverage(group),
                                   ratio(group), number(sc['daily_delta_pp'], 'pp'), same(sc), changes(sc)])
    output += [('容量历史变化', ['日期', '状态', '可计算/在榜', '线上/线下比例', '线上日变', '同股数/线上变化', '样本变动', '两市成交额', '成交额环比', '成交额来源'], history_rows),
               ('板块历史变化', ['日期', '申万二级', '状态', '可计算/在榜', '线上/线下比例', '线上日变', '同股数/线上变化', '样本变动'], sector_history)]
    return output


def gaps(view):
    missing = [f"{p['date']}:{p['status']}" for p in view['history'] if p['status'] != 'complete']
    if view['calendar_status'] != 'complete':
        return view['reason']
    messages = ['容量历史缺口：' + '、'.join(missing)] if missing else []
    market_missing = [p['date'] for p in view['history'] if p['market_total_billion'] is None or not p['market_source']]
    if market_missing:
        messages.append('两市成交额或来源缺口：' + '、'.join(market_missing))
    return '；'.join(messages) or None
