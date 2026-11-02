"""把既有容量快照转成有股票身份的变化摘要，不参与选股或评分。"""
from __future__ import annotations

from .capacity_trend import _stock_identity
from .capacity_history import _industry

DISPLAY_LIMIT = 3


def _name(row):
    return f"{row.get('name') or '名称缺失'}（{row.get('code') or '代码缺失'}）"


def _position(row):
    distance = row['ma20_distance_pct']
    return f"距MA20{distance:+.2f}%，均线{row['ma20_direction']}"


def _listed(items, limit):
    # 沿用当日成交榜顺序，截取仅为阅读长度，不另造综合强弱排名。
    selected = items if limit is None else items[:limit]
    visible = '；'.join(selected)
    return visible + (f'；另有{len(items) - len(selected)}只见完整变化' if len(items) > len(selected) else '')


def _pair(block):
    view = block.get('history_view') or {}
    history = view.get('history') or []
    if block['status'] != 'complete' or view.get('calendar_status') != 'complete' or len(history) < 2:
        return None
    previous, current = history[-2:]
    if (current['date'] != block['trade_date'] or previous['status'] != 'complete'
            or current['status'] != 'complete' or current['comparison']['status'] != 'complete'):
        return None
    left = {_stock_identity(r['code']): r for r in previous['rows']}
    right = {_stock_identity(r['code']): r for r in block['rows']}
    if len(left) != len(previous['rows']) or len(right) != len(block['rows']):
        return None
    common = {code for code in left.keys() & right.keys() if _industry(left[code]) == _industry(right[code])}
    return previous['date'], left, right, common


def observations(block, *, limit=DISPLAY_LIMIT):
    """返回纯文本；调用方负责Markdown/HTML转义。变化只比较相邻开放日完整同股。"""
    valid = [r for r in block.get('rows', []) if r['status'] == 'complete']
    if not valid:
        return []
    pair = _pair(block)
    if pair is None:
        strong = [f'{_name(r)}：{_position(r)}' for r in valid
                  if r['ma20_position'] == '上方' and r['ma20_direction'] == '向上']
        lines = ['当前线上且均线向上：' + (_listed(strong, limit) if strong else '可计算样本中暂无')]
        lines.append('跨日变化未计算：相邻开放日证据不完整；不能据此判断新增改善或走坏。')
        return lines

    day, left, right, common = pair
    if not common:
        lines = [f'与{day}无同股同分类可比样本，不能把换榜当作个股改善。']
    else:
        lines = [f'对照{day}：同股同分类{len(common)}只。']
        kept, improving, worsening, extending = [], [], [], []
        for code, row in right.items():
            if code not in common:
                continue
            old = left[code]
            if (row['ma20_position'] == old['ma20_position'] == '上方'
                    and row['ma20_direction'] == old['ma20_direction'] == '向上'):
                kept.append(f'{_name(row)}：{_position(row)}')
            better, worse = [], []
            if row['ma20_position'] == '上方' and old['ma20_position'] != '上方':
                better.append('新站上MA20')
            if row['ma20_direction'] == '向上' and old['ma20_direction'] != '向上':
                better.append('MA20转向上')
            if row['ma20_position'] == '下方' and old['ma20_position'] != '下方':
                worse.append('新跌破MA20')
            if row['ma20_direction'] == '向下' and old['ma20_direction'] != '向下':
                worse.append('MA20转向下')
            if better:
                improving.append(f"{_name(row)}：{'、'.join(better)}，{_position(row)}")
            if worse:
                worsening.append(f"{_name(row)}：{'、'.join(worse)}，{_position(row)}")
            elif (row['ma20_position'] == old['ma20_position'] == '下方'
                  and round(row['ma20_distance_pct'], 2) < round(old['ma20_distance_pct'], 2)
                  and row['end_run_direction'] == '连跌'):
                extending.append(f"{_name(row)}：距线{old['ma20_distance_pct']:+.2f}%→"
                                 f"{row['ma20_distance_pct']:+.2f}%，近5根末端连跌{row['end_run_bars']}根")
        worsening += extending
        lines += ['保持线上且线向上：' + (_listed(kept, limit) if kept else '可比同股中暂无'),
                  '改善迹象：' + (_listed(improving, limit) if improving else '可比同股中无新站上或均线转上'),
                  '走坏迹象：' + (_listed(worsening, limit) if worsening else '可比同股中无新跌破、均线转下或线下连跌且距线扩大')]
    entrants = [f"{_name(row)}：MA20{row['ma20_position']}、均线{row['ma20_direction']}"
                for code, row in right.items() if code not in left]
    if entrants:
        lines.append('新进榜单列（不算同股改善）：' + _listed(entrants, limit))
    return lines
