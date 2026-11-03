"""固定前日题材涨停样本的次日反馈；仅复用归档，不采集或写入业务数据。"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
import math
from statistics import mean, median
from zoneinfo import ZoneInfo

from analyzers import theme_launch as tl
from analyzers import strong_stock_feedback as sf
from analyzers.strong_stock_feedback_history import _envelope
from services.volume_concentration.capacity_history import _open_days

SCHEMA = 'theme-feedback-v1'
MARKER = 'data-theme-feedback'
LIMIT_SOURCES = {'tushare:limit_list_d', 'akshare:stock_zt_pool_em'}
NOTE = ('[事实] 固定严格上一开放日题材榜前10名中、成员与非ST涨停身份均已核验的股票；'
        '按来源日常规10/20/30cm分组，不代表整个题材成员的收益。题材与样本不随次日榜单或成员变化。'
        '同股可重复属于不同题材，跨题材不可相加。开收盘涨跌以反馈日行情昨收为基准，不是可实现交易收益。'
        '再次涨停依据反馈日已核验涨停名单；未涨停仅在Tushare完整名单覆盖、当日非ST身份及有效行情均核验后判断。'
        'AkShare有科创板/ST/连续一字新股排除，仅采用名单命中的正向事实；缺席或覆盖未知不推断断板。'
        '连板核验要求两日已核验板数恰好递增1；未知板数不默认首板。比例仅使用对应已核验样本，缺失不补0。'
        '来源：《10月31正课1》29:24—30:24、34:53—37:20；仅复盘事实，不识别龙头或生成买卖建议。'
        '9:15/9:20竞价封单及盘中一字板持续性未计算，不能用日线替代。')


def missing(day, reason, status='source_failed', previous=None):
    return dict(schema=SCHEMA, trade_date=day, source_date=previous, membership_date=None, status=status, rows=[], gaps=[reason])


def _quotes(block, day, previous, cohort, limit_source):
    checked = sf._view(block, day)
    if checked.get('source_date') != previous or checked['status'] not in {'complete', 'partial', 'empty', 'source_failed'}:
        raise ValueError('次日反馈来源日期或状态未核验')
    for key, expected_day in (('limit_up', previous), ('current_quotes', day)):
        source = checked.get('sources', {}).get(key, {})
        expected_source = limit_source if key == 'limit_up' else 'tushare:daily'
        if (source.get('date') != expected_day or source.get('source') != expected_source
                or (key == 'current_quotes' and source.get('status') != 'complete')):
            raise ValueError('反馈来源回执缺失或日期错位')
    rows = checked['details']
    expected = {r['code']: r for r in cohort}
    indexed = {sf._row_code(r): r for r in rows}
    if len(indexed) != len(rows) or set(indexed) != set(expected) or checked.get('cohort_count') != len(cohort):
        raise ValueError('次日反馈样本与来源日完整涨停身份不一致')
    result = {}
    for code, row in indexed.items():
        if row['name'] != expected[code]['name']:
            raise ValueError('次日反馈来源股票名称不一致')
        if row['quote_status'] != 'complete':
            continue
        # 从已保存价格复核比值，不接受自洽汇总掩盖错误的逐股涨跌幅。
        op, cl, pc = [sf._number(row[k], True) for k in ('current_open', 'current_close', 'pre_close')]
        for price in (op, cl):
            if not 0.01 <= price / pc <= 100:
                raise ValueError('反馈价格数量级异常')
        if (not math.isclose((op/pc-1)*100, row['open_pct'], abs_tol=1e-8)
                or abs((cl/pc-1)*100-row['close_pct']) > 0.03
                or row['bearish_candle'] != (cl < op)
                or row['candle'] != ('阴' if cl < op else '阳' if cl > op else '平')):
            raise ValueError('反馈价格与涨跌或阴阳线矛盾')
        result[code] = dict(open_pct=row['open_pct'], close_pct=row['close_pct'], bearish=row['bearish_candle'])
    return result


def _check_limit_dates(section, day):
    if not isinstance(section, dict) or section.get('_source') not in LIMIT_SOURCES:
        raise ValueError('涨停来源不在支持范围')
    rows = sf._cohort(section)
    if section['_source'] == 'tushare:limit_list_d' and section['count'] >= 2500:
        raise ValueError('涨停名单达到单页上限，完整性待核验')
    for record in [section, *section['stocks']]:
        if any(str(record[k]).replace('-', '') != day.replace('-', '')
               for k in ('date', 'trade_date') if k in record):
            raise ValueError('涨停名单返回日期错位')
    return rows


def _current_non_st(block, day):
    # 只采用当日广度已核验宇宙中正向存在的身份。stock_st摘要被截断，不能由未命中证明非ST。
    from analyzers.market_strength import view
    from utils import is_st_stock
    checked = view(block, day)
    if checked['status'] not in {'complete', 'partial'}:
        return set()
    expected = {'st': 'tushare:stock_st', 'identity': 'tushare:stock_basic:L+D+P:as_of', 'quotes': 'tushare:daily'}
    receipts = checked.get('sources')
    if not isinstance(receipts, dict):
        return set()
    for key, source in expected.items():
        receipt = receipts.get(key)
        if not isinstance(receipt, dict) or receipt.get('status') != 'success' or receipt.get('source') != source:
            return set()
    return {sf._row_code(r) for r in checked['rows'] if isinstance(r.get('name'), str)
            and r['name'].strip() and not is_st_stock(r['name']) and '退' not in r['name']}


def _group(rows, board):
    selected = [r for r in rows if board == 'all' or r['board'] == board]
    quotes = [r for r in selected if r['open_pct'] is not None]
    repeated = [r for r in selected if r['again_limit_up'] is not None]
    promoted = [r for r in selected if r['verified_continuation'] is not None]
    n, q, u, p = len(selected), len(quotes), len(repeated), len(promoted)
    total = lambda values: sum(values)
    again = total(r['again_limit_up'] for r in repeated) if u else None
    continuation = total(r['verified_continuation'] for r in promoted) if p else None
    return dict(board=board, sample_count=n, quote_count=q, limit_checked_count=u, continuation_checked_count=p,
                status='empty' if not n else 'complete' if q == u == p == n else 'partial' if q or u else 'source_failed',
                open_mean_pct=mean(r['open_pct'] for r in quotes) if q else None,
                open_median_pct=median(r['open_pct'] for r in quotes) if q else None,
                close_mean_pct=mean(r['close_pct'] for r in quotes) if q else None,
                close_median_pct=median(r['close_pct'] for r in quotes) if q else None,
                positive_count=total(r['close_pct'] > 0 for r in quotes) if q else None,
                positive_rate_pct=100*total(r['close_pct'] > 0 for r in quotes)/q if q else None,
                bearish_count=total(r['bearish'] for r in quotes) if q else None,
                again_limit_count=again, again_limit_rate_pct=100*again/u if u else None,
                continuation_count=continuation)


def calculate(day, previous, evidence):
    result = dict(schema=SCHEMA, trade_date=day, source_date=previous, membership_date=None,
                  status='partial', rows=[], gaps=[], evidence=evidence)
    try:
        if date.fromisoformat(previous) >= date.fromisoformat(day):
            raise ValueError('反馈日期顺序非法')
        launch = tl.validate(evidence.get('launch'), previous)
        if not launch['rows']:
            return missing(day, '来源日题材成员或涨停样本未取得',
                           'not_computed' if launch['status'] == 'not_computed' else 'source_failed', previous)
        cohort = _check_limit_dates(launch['evidence']['limit_up'], previous)
        result['membership_date'] = launch.get('membership_date')
        non_st = _current_non_st(evidence.get('market_strength'), day)
        try:
            quotes = _quotes(evidence.get('feedback'), day, previous, cohort, launch['evidence']['limit_up']['_source'])
            quote_error = None
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            quotes, quote_error = {}, '次日反馈行情缺失、日期错位或身份/价格未通过核验'
            result['gaps'].append(quote_error)
        try:
            if evidence.get('limit_up_date') != day:
                raise ValueError('反馈日涨停名单日期不符')
            _check_limit_dates(evidence.get('limit_up'), day)
            # 样本固定在前日，当前名单即使出现ST或更名也不能被过滤成“未涨停”。
            section = evidence['limit_up']
            current = {sf._row_code(r): r for r in section['stocks']}
            limit_error = None
        except (ValueError, TypeError, KeyError, AttributeError):
            current, limit_error = {}, '反馈日完整涨停名单未核验'
            result['gaps'].append(limit_error)
        for theme in launch['rows']:
            row = dict(code=theme['code'], name=theme['name'], rank=theme['rank'], sample_count=None,
                       status='source_failed', groups=None, stocks=[], gaps=[])
            result['rows'].append(row)
            if theme['counts'] is None:
                row['gaps'].append('来源日题材成员或涨停名单未核验，不能确认为零样本')
                continue
            for old in theme['limit_stocks']:
                code, q, cur = old['code'], quotes.get(old['code']), current.get(old['code'])
                stock = dict(code=code, name=old['name'], board=old['board'], source_height=old['limit_times'],
                             current_height=None, open_pct=None, close_pct=None, bearish=None,
                             again_limit_up=None, verified_continuation=None, gaps=[])
                if q is not None:
                    stock.update(q)
                else:
                    stock['gaps'].append(quote_error or '当日行情未覆盖，未确认停牌')
                if limit_error:
                    stock['gaps'].append(limit_error)
                elif cur is not None:
                    stock['again_limit_up'] = True
                    height, _ = sf.verified_streak(cur, evidence['limit_up']['_source'])
                    stock['current_height'] = height
                    if old['limit_times'] is not None and height == old['limit_times'] + 1:
                        stock['verified_continuation'] = True
                    else:
                        stock['gaps'].append('两日连板次数缺失或不连续，连板待核验')
                elif q is not None and code in non_st and evidence['limit_up']['_source'] == 'tushare:limit_list_d':
                    stock.update(again_limit_up=False, verified_continuation=False)
                else:
                    stock['gaps'].append('当日交易、非ST身份或名单覆盖未核验，不把缺席推断为断板')
                row['stocks'].append(stock)
            row['sample_count'] = len(row['stocks'])
            row['groups'] = {str(b): _group(row['stocks'], b) for b in ('all', 10, 20, 30)}
            row['status'] = row['groups']['all']['status']
        states = [r['status'] for r in result['rows']]
        result['status'] = ('complete' if all(s in {'complete', 'empty'} for s in states) else
                            'source_failed' if all(s == 'source_failed' for s in states) else 'partial')
        # 真空样本不依赖次日行情；其余缺源依然保留在逐股/题材覆盖内。
        if result['status'] == 'complete':
            result['gaps'] = []
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return missing(day, '来源日题材证据损坏，未计算反馈', previous=previous)


def build(daily_dir, day, *, current_raw=None, db_path=None, now=None):
    """只读三个严格开放日与两日归档；当批内存结果优先，不重取成员或行情。"""
    try:
        now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if now.tzinfo is None:
            raise ValueError('运行时间无时区')
        local = now.astimezone(ZoneInfo('Asia/Shanghai'))
        if date.fromisoformat(day) > local.date():
            return missing(day, '反馈日尚未到达', 'skipped')
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            days = _open_days(conn, day, 3)
        finally:
            conn.close()
        if day == local.date().isoformat() and local.hour < 16:
            return missing(day, '尚未收盘，不计算完整次日反馈', 'skipped', days[-2])
        prior = _envelope(daily_dir, days[-2], {})
        if isinstance(prior, str):
            return missing(day, '来源日盘后归档未取得', 'not_computed' if prior == 'missing-data' else 'source_failed', days[-2])
        launch = tl.validate(prior['raw_data'].get(tl.KEY), days[-2])
        if launch.get('evidence') and launch.get('previous_date') != days[-3]:
            return missing(day, '来源日题材比较日与完整日历不符', previous=days[-2])
        if current_raw is None:
            current = _envelope(daily_dir, day, {})
            if isinstance(current, str):
                return missing(day, '反馈日盘后归档未取得', 'not_computed' if current == 'missing-data' else 'source_failed', days[-2])
            current_raw = current['raw_data']
        if not isinstance(current_raw, dict) or ('date' in current_raw and current_raw['date'] != day):
            raise ValueError('当批行情日期错位')
        return calculate(day, days[-2], dict(launch=launch, feedback=current_raw.get(sf.KEY),
                                           limit_up=current_raw.get('limit_up'), limit_up_date=day,
                                           market_strength=current_raw.get('market_strength')))
    except Exception:
        return missing(day, '日历或归档未通过核验，不跨日猜测')


def load_for_review(daily_dir, day, db_path, *, now=None):
    from services.review_calendar import load_review_context
    try:
        fact, context = load_review_context(db_path, day)
        value = build(daily_dir, fact, db_path=db_path, now=now)
        return {**value, 'review_context': context} if context else value
    except Exception:
        return missing(day, '复盘日历未核验，不跨日猜测')


def validate(block, day):
    if block is None:
        return missing(day, '题材次日反馈尚未计算', 'not_computed')
    try:
        if block['schema'] != SCHEMA or block['trade_date'] != day:
            raise ValueError('反馈日期或版本不符')
        if 'evidence' not in block:
            if block['status'] not in {'source_failed', 'not_computed', 'skipped'} or block['rows'] != []:
                raise ValueError('反馈证据缺失')
            if not isinstance(block['gaps'], list) or any(not isinstance(g, str) for g in block['gaps']):
                raise ValueError('反馈缺口非法')
            previous = block.get('source_date')
            if previous is not None and date.fromisoformat(previous) >= date.fromisoformat(day):
                raise ValueError('来源日期非法')
            return missing(day, '', block['status'], previous) | {'gaps': block['gaps']}
        expected = calculate(day, block['source_date'], block['evidence'])
        if {k: v for k, v in block.items() if k != 'review_context'} != expected:
            raise ValueError('反馈与证据重算不一致')
        return expected
    except (ValueError, TypeError, KeyError, AttributeError):
        return missing(day, '反馈来源或计算值损坏')


def _view(block, day):
    from services.review_calendar import review_fact_date
    try:
        fact = review_fact_date({**(block or {}), 'date': (block or {}).get('trade_date')}, day)
        return validate(block, fact), fact
    except (ValueError, TypeError, AttributeError):
        return missing(day, '反馈日期证据未核验'), day


def _coverage(value, count):
    return '未计算' if value is None else f'{value}/{count}'


def _tables(value):
    groups, stocks = [], []
    for r in value['rows']:
        if r['groups'] is None:
            groups.append([r['name'], '未核验', '未计算'] + ['未计算']*5)
            continue
        for board in ('all', '10', '20', '30'):
            g = r['groups'][board]
            groups.append([r['name'], '合计' if board == 'all' else board+'cm', str(g['sample_count']),
                           f"{g['quote_count']}/{g['sample_count']}",
                           tl._fmt(g['open_mean_pct'], True)+' / '+tl._fmt(g['open_median_pct'], True),
                           tl._fmt(g['close_mean_pct'], True)+' / '+tl._fmt(g['close_median_pct'], True),
                           _coverage(g['again_limit_count'], g['limit_checked_count']),
                           _coverage(g['continuation_count'], g['continuation_checked_count'])])
        for s in r['stocks']:
            boolean = lambda v: '待核验' if v is None else '是' if v else '否'
            stocks.append([r['name'], f"{s['name']}（{s['code']}）", str(s['board'])+'cm',
                           tl._fmt(s['open_pct'], True), tl._fmt(s['close_pct'], True),
                           boolean(s['again_limit_up']), boolean(s['verified_continuation']), '；'.join(s['gaps']) or '完整'])
    return groups, stocks


GROUP_HEADERS = ['来源题材', '来源板型', '固定样本', '行情覆盖', '开盘均值/中位(%)', '收盘均值/中位(%)', '再次涨停/已核验', '连板通过/已核验']
STOCK_HEADERS = ['来源题材', '来源股票', '板型', '开盘(%)', '收盘(%)', '再次涨停', '连板核验', '缺口']


def render_markdown(block, day):
    value, fact = _view(block, day)
    lines = ['### 同题材次日反馈', f"[事实] 来源日：{value.get('source_date') or '未核验'}；反馈日：{fact}；报告日：{day}；状态：{value['status']}", NOTE]
    clean = lambda x: str(x).replace('|', '｜').replace('\n', ' ').replace('\r', ' ')
    rows, _ = _tables(value)
    if rows:
        lines += ['| '+' | '.join(GROUP_HEADERS)+' |', '| '+' | '.join(['---']*len(GROUP_HEADERS))+' |']
        lines += ['| '+' | '.join(clean(c) for c in r)+' |' for r in rows]
    lines.extend('缺口：'+clean(g) for g in value['gaps'])
    for row in value['rows']:
        lines.extend(clean(row['name'])+'缺口：'+clean(g) for g in row['gaps'])
    return '\n\n'.join(lines[:3])+'\n\n'+'\n'.join(lines[3:])


def render_html(block, day):
    value, fact = _view(block, day)
    usable = [r for r in value['rows'] if r['groups'] is not None]
    summary = '[事实] 来源日题材快照或反馈数据尚未核验，暂不能判断同题材次日表现。'
    if usable:
        summary = f"[事实] 固定{value['source_date']}题材内的涨停样本，观察{fact}次日表现；各题材不可相加。"
    elif value['status'] == 'skipped':
        summary = '[事实] 尚未取得完整交易日反馈，暂不计算。'
    parts = [f'<div {MARKER}="{escape(day)}" data-as-of="{escape(fact)}"><h3>同题材次日反馈</h3><p>{escape(summary)}</p>']
    if fact != day:
        parts.append('<p>[事实] 休市展示'+escape(fact)+'最近开放日历史，非当日新增。</p>')
    for row in usable:
        g = row['groups']['all']
        text = (f"{row['name']}：固定样本{g['sample_count']}只，行情覆盖{g['quote_count']}只；"
                f"开盘/收盘中位{tl._fmt(g['open_median_pct'], True)}% / {tl._fmt(g['close_median_pct'], True)}%；"
                f"再次涨停{_coverage(g['again_limit_count'], g['limit_checked_count'])}（已核验分母）。")
        parts.append('<p>'+escape(text)+'</p>')
    grouped, stocks = _tables(value)
    items = 2 + len(grouped) + len(stocks) + len(value['gaps']) + sum(len(r['gaps']) for r in value['rows'])
    parts.append(f'<details class="evidence" data-as-of="{escape(fact)}" data-items="{items}"><summary>分板型、逐股反馈与来源（{items}项）</summary>')
    parts.append('<p>'+escape(f"状态：{value['status']}；预期来源日：{value.get('source_date') or '未核验'}；成员快照日：{value.get('membership_date') or '未取得'}；反馈日：{fact}；报告日：{day}")+'</p>')
    parts.append('<p>'+escape(NOTE)+'</p>')
    for headers, rows in ((GROUP_HEADERS, grouped), (STOCK_HEADERS, stocks)):
        if rows:
            parts.append('<div style="overflow-x:auto"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>')
            parts.extend('<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in r)+'</tr>' for r in rows)
            parts.append('</tbody></table></div>')
    parts.extend('<p>缺口：'+escape(g)+'</p>' for g in value['gaps'])
    for row in value['rows']:
        parts.extend('<p>'+escape(row['name']+'缺口：'+g)+'</p>' for g in row['gaps'])
    parts.append('</details></div>')
    return ''.join(parts), '' if value['status'] == 'complete' else '同题材次日反馈存在缺口，缺失不补0；详见②。'
