"""仅比较复盘明确列出的价位与已归档的相邻行情。"""
from datetime import date, datetime
from decimal import Decimal
import re
import unicodedata

INDEX_ALIASES = {
    '上证指数': '000001.SH', '上证综指': '000001.SH', '上证': '000001.SH',
    '深证成指': '399001.SZ', '深成指': '399001.SZ', '深成': '399001.SZ',
    '创业板指': '399006.SZ', '创业板': '399006.SZ',
    '科创50指数': '000688.SH', '科创50': '000688.SH',
    '中证1000指数': '000852.SH', '中证1000': '000852.SH',
    '沪深300指数': '000300.SH', '沪深300': '000300.SH',
    '上证50指数': '000016.SH', '上证50': '000016.SH',
    '中证500指数': '000905.SH', '中证500': '000905.SH',
    '平均股价': '880003.TDX',
}
INDEX_PATTERN = re.compile(
    '|'.join(re.escape(name) for name in sorted(INDEX_ALIASES, key=len, reverse=True))
    + r'(?!\d)')
LIVE_INDEX_CODES = frozenset(code for code in INDEX_ALIASES.values() if not code.endswith('.TDX'))
PRICE = r'[1-9]\d{0,5}(?:\.\d{1,4})?|0\.\d{1,4}'
LEVEL_KIND = r'缺口|颈线|前低|前高|关口|风险区'
DATE_PATTERN = re.compile(r'(?<![\d.:])(?:(\d{4})[-/年])?(\d{1,2})(?:月|[-/])(\d{1,2})(?:日)?(?![\d.:])')
INDEX_SECTIONS = ('明日锚点', '次日锚点', '次日宽基关键位', '宽基指数关键位', '市场与延续条件')
LEVEL_PARSER_VERSION = 3
REFERENCE_DATE = re.compile(DATE_PATTERN.pattern + r'(?=\s*前[低高])')
CONFIRMATION_DISTANCE_PCT = Decimal('0.2')


def _plain(text):
    return unicodedata.normalize('NFKC', text).replace('**', '').replace('`', '')


def _index_values(tail):
    group = re.match(r'\s*\(([^()]+)\)', tail)
    if group:
        for token in re.split(r'[/、,]', group[1]):
            label_first = re.fullmatch(rf'\s*(MA\d{{1,2}})\s*=\s*({PRICE})\s*', token)
            if label_first:
                yield label_first[1], float(label_first[2])
            label_last = re.fullmatch(rf'\s*({PRICE})\s*({LEVEL_KIND})\s*', token)
            if label_last:
                yield label_last[2], float(label_last[1])
    line = re.match(
        rf'\s*(?:能否\s*|是否\s*)?(?:止跌\s*|有效\s*)?'
        rf'(?:守住?|站稳|站上|跌破|收复|突破|失守|重返)\s*'
        rf'(?:(?P<ma>MA\d{{1,2}})\s*\(\s*(?P<ma_price>{PRICE})\s*\)|'
        rf'(?:(?P<kind>前低|前高)\s*)?(?P<price>{PRICE})'
        rf'(?=\s*(?:点|{LEVEL_KIND}|之上|之下|后|[、,;。()|]|$)))', tail)
    if line:
        if line['ma']:
            yield line['ma'], float(line['ma_price'])
        else:
            yield line['kind'] or '明示观察位', float(line['price'])
        follow = re.match(rf'\s*(?:并|再)(?:试探|挑战)\s*({PRICE})'
                          rf'(?=\s*(?:点|{LEVEL_KIND}|[、,;。)|]|$))', tail[line.end():])
        if follow:
            yield '明示观察位', float(follow[1])


def _target_date(text, day):
    year, month, date = map(int, day.split('-'))
    return all((not y or int(y) == year) and (int(m), int(d)) == (month, date)
               for y, m, d in DATE_PATTERN.findall(text))


def _remove_reference_dates(text, day):
    """仅在过去日期直接修饰前低／前高时忽略它，目标日期仍走原门禁。"""
    target = date.fromisoformat(day)

    def replace(match):
        year, month, day_of_month = match.groups()
        try:
            reference = date(int(year) if year else target.year, int(month), int(day_of_month))
        except ValueError:
            return match[0]
        return '' if reference < target else match[0]

    return REFERENCE_DATE.sub(replace, text)


def extract_levels(section, stocks, targets, metadata, day):
    levels = {}

    def add(kind, name, code, label, price, quote):
        if price > 0:
            key = (kind, code, price)
            entry = levels.setdefault(key, dict(kind=kind, name=name, code=code, level=price,
                source_date=metadata['date'], supplement_sha256=metadata['supplement_sha256'],
                labels=[], quotes=[]))
            if label not in entry['labels']:
                entry['labels'].append(label)
            if quote not in entry['quotes']:
                entry['quotes'].append(quote)

    in_index_section = False
    for line in section.splitlines():
        normalized = _plain(line)
        heading = re.match(r'\s*#{3,6}\s+(.*)', normalized)
        if heading:
            in_index_section = heading[1].startswith(INDEX_SECTIONS) and _target_date(heading[1], day)
            continue
        normalized = _remove_reference_dates(normalized, day)
        if not in_index_section or not _target_date(normalized, day):
            continue
        for mention in INDEX_PATTERN.finditer(normalized):
            if re.search(r'(?:港股|港市|香港|美股|境外|海外)\s*的?\s*$', normalized[:mention.start()]):
                continue
            name = mention[0]
            for label, value in _index_values(normalized[mention.end():]):
                add('index', name, INDEX_ALIASES[name], label, value, line)

    for stock in stocks:
        line = targets.get((stock['name'], stock['code']))
        if not line:
            continue
        cells = _plain(line).strip().strip('|').split('|')
        if len(cells) < 3:
            continue
        comparison = re.split(r';\s*(?:结合|同看|对照)', cells[1], maxsplit=1)[0]
        reference_price = re.match(rf'\s*对比\s*({PRICE})\s*元', comparison)
        if reference_price and _target_date(comparison, day):
            add('stock', stock['name'], stock['code'], '复盘对比价', float(reference_price[1]), line)
        name = re.escape(stock['name'])
        level = re.match(
            rf'\s*(?:(?:\d{{1,2}}:\d{{2}}(?:/\d{{1,2}}:\d{{2}})?)\s*)?'
            rf'(?:核|观察|看)?\s*(?:若\s*)?(?:{name}\s*)?(?:是否\s*)?(?:放量\s*)?'
            rf'(?:站稳|站上|守住|稳在|跌破|收复|突破|失守)\s*({PRICE})'
            rf'(?:\s*元|(?=\s*(?:之上|之下|上方|下方)))', cells[2])
        if level and _target_date(cells[2], day):
            add('stock', stock['name'], stock['code'], '盘中明示价位', float(level[1]), line)
    return list(levels.values())


def _side(price, level):
    return 'above' if price > level else 'below' if price < level else 'at'


def observe_levels(previous, current, anchors, linked):
    rows = []
    expected = anchors.get('key_levels', [])
    for target in expected:
        quote_key = 'stocks' if target['kind'] == 'stock' else 'indices'
        now = current.get(quote_key, {}).get(target['code'])
        before = (previous or {}).get(quote_key, {}).get(target['code']) if linked else None
        row = dict(target, status='missing_quote', price=None, side=None,
                   previous_price=None, previous_side=None, crossed=None, amount_yuan=None,
                   quote_at=None, previous_quote_at=None)
        if now:
            row.update(status='baseline', price=now['price'], side=_side(now['price'], target['level']),
                       quote_at=now['quote_at'])
        if now and before and datetime.fromisoformat(before['quote_at']) < datetime.fromisoformat(now['quote_at']):
            old_side = _side(before['price'], target['level'])
            row.update(status='compared', previous_price=before['price'], previous_side=old_side,
                       previous_quote_at=before['quote_at'])
            if {row['side'], old_side} == {'above', 'below'}:
                row['crossed'] = 'up' if row['side'] == 'above' else 'down'
            if (target['kind'] == 'stock' and current.get('source') and current['source'] == previous.get('source')
                    and before['amount'] <= now['amount']):
                row['amount_yuan'] = now['amount'] - before['amount']
        rows.append(row)
    return dict(status=('missing_source' if not anchors.get('sources') else 'not_configured' if not rows
                        else 'partial' if any(r['status'] != 'compared' for r in rows) else 'complete'),
                rows=rows)


def _level_identity(row):
    return (row['kind'], row['code'], row['level'], row.get('source_date'), row.get('supplement_sha256'))


def confirm_crossings(rows, previous_rows, captured_at):
    """首次穿越留档。同侧观察可延续，直到达到距离、折返或中断。"""
    pending = {_level_identity(row): row for row in previous_rows
               if (row.get('confirmation') or {}).get('status') == 'pending'}
    for row in rows:
        if row['status'] != 'compared':
            continue
        if row['crossed']:
            row['confirmation'] = dict(status='pending', crossed_at=captured_at,
                                       crossing_quote_at=row['quote_at'], crossing_price=row['price'],
                                       crossing_amount_yuan=row['amount_yuan'])
            continue
        earlier = pending.get(_level_identity(row))
        if (not earlier or row['side'] not in ('above', 'below')
                or not row['side'] == row['previous_side'] == earlier['side']
                or row['previous_quote_at'] != earlier['quote_at']):
            continue
        state = dict(earlier['confirmation'])
        level = Decimal(str(row['level']))
        distance_pct = abs(Decimal(str(row['price'])) - level) * 100 / level
        if distance_pct >= CONFIRMATION_DISTANCE_PCT:
            state.update(status='confirmed', confirmed_at=captured_at, distance_pct=float(distance_pct))
        row['confirmation'] = state


def _price_text(value, missing='未计算'):
    return f'{value:.4f}'.rstrip('0').rstrip('.') if value is not None else missing


def render_level_watch(record):
    statuses = {'missing_source': '上一交易日复盘缺源', 'not_configured': '没有解析到明确点位',
                'partial': '有报价缺口或本批仅建基线', 'complete': '已比较'}
    rows = record['rows']
    index_count = sum(row['kind'] == 'index' for row in rows)
    lines = ['# 宽基与手写锚点关键位', '',
             f"采样 {record['captured_at']}，{statuses[record['status']]}",
             f'已识别宽基点位 {index_count} 个，个股点位 {len(rows) - index_count} 个。']
    if record['previous_captured_at']:
        lines.append(f"对比上批 {record['previous_captured_at']}。")
    lines.extend(['', '## 本批确认'])
    confirmed = [row for row in rows if (row.get('confirmation') or {}).get('status') == 'confirmed']
    if not confirmed:
        lines.append('本批无新增确认记录。')
    for row in confirmed:
        state = row['confirmation']
        side = '上方' if row['side'] == 'above' else '下方'
        start = datetime.fromisoformat(state['crossed_at']).strftime('%H:%M:%S')
        amount = state['crossing_amount_yuan']
        turnover = f'，首次穿越区间成交 {amount / 1e4:,.2f} 万元' if amount is not None else ''
        lines.append(f"- **{row['name']}（{row['code']}）** 确认在 {_price_text(row['level'])} {side}，"
                     f"最新 {_price_text(row['price'])}，距关键位 {state['distance_pct']:.2f}%。"
                     f"首次穿越于 {start} 的采样中记录{turnover}。")
    if rows:
        lines.extend(['', '## 逐项观察', '', '|对象|复盘价位|上批 → 本批|位置变化|区间成交额|',
                      '|---|---:|---|---|---:|'])
    relation = {'above': '上方', 'at': '触及', 'below': '下方', None: '缺行情'}
    for row in rows:
        before = _price_text(row['previous_price'])
        after = _price_text(row['price'], '缺行情')
        change = {'up': '向上穿越', 'down': '向下穿越'}.get(row['crossed'], relation[row['side']])
        if row['side'] in ('above', 'below') and row['side'] == row['previous_side']:
            change = f'连续两批在{change}'
        elif row['side'] in ('above', 'below') and row['previous_side'] == 'at':
            change = f'从点位回到{change}'
        amount = '—' if row['kind'] == 'index' else '未计算'
        if row['amount_yuan'] is not None:
            amount = f"{row['amount_yuan'] / 1e4:,.2f} 万元"
        label = '／'.join(row['labels'])
        lines.append(f"|{row['name']} {row['code']}|{_price_text(row['level'])}（{label}）|{before} → {after}|{change}|{amount}|")
    gaps = [f"{code} {reason}" for code, reason in record['index_source']['errors'].items()]
    if record['index_history_error']:
        gaps.append(record['index_history_error'])
    if record.get('confirmation_history_error'):
        gaps.append(record['confirmation_history_error'])
    if gaps:
        lines.extend(['', *('- ' + gap for gap in gaps)])
    return '\n'.join(lines) + '\n'
