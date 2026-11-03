"""中字头日度赚钱效应；复用盘后行情与已核验身份，不写池或计划。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from html import escape
import statistics
from zoneinfo import ZoneInfo

from analyzers import market_strength as ms
from analyzers.ipo_effect import _date
from analyzers.low_price_effect import _finite_float
from analyzers.strong_stock_feedback import _row_code

KEY = 'zhong_prefix_effect'
SCHEMA = 'zhong-prefix-effect-v1'
MARKER = 'data-zhong-prefix-effect'
NOTE = ('简称以“中”开头的沪深北A股，剔ST、退市和B股；名称为事实日采集快照，'
        '不等同于央企名单。等权收盘相对昨收，开收盘表现不是可实现收益。'
        '强势前排取涨幅≥5%的前三名，上涨容量前排取收涨股票成交额前三名；'
        '同值并列保留。核心观察为规则判断，不授予正式最票身份。')


def missing(day, reason, status='source_failed'):
    return dict(schema=SCHEMA, trade_date=day, status=status, gaps=[str(reason)],
                rows=[], core=[], metrics=None, market=None)


def _metrics(rows):
    values = [r['pct_chg'] for r in rows]
    opening = [r['intraday_pct'] for r in rows if r['intraday_pct'] is not None]
    amounts = [r['amount_yi'] for r in rows if r['amount_yi'] is not None]
    n = len(rows)
    return dict(sample_count=n, pct_chg_mean=statistics.mean(values) if n else None,
                pct_chg_median=statistics.median(values) if n else None,
                advance_count=sum(v > 0 for v in values),
                advance_rate=sum(v > 0 for v in values)/n if n else None,
                strong_gain_count=sum(v >= 5 for v in values),
                strong_loss_count=sum(v <= -5 for v in values),
                intraday_count=len(opening),
                intraday_median=statistics.median(opening) if opening else None,
                amount_count=len(amounts),
                amount_yi=sum(amounts) if n and len(amounts) == n else None)


def calculate(day, evidence):
    """基于已保存的原始证据重算；覆盖不全时不做跨样本核心排名。"""
    result = missing(day, '')
    try:
        if evidence['name_snapshot_date'] != day:
            raise ValueError('名称快照不是事实日，禁止用当前名称回填历史')
        strength = ms.view(evidence['market_strength'], day)
        if strength['status'] not in {'complete', 'partial'}:
            raise ValueError('市场行情与身份未通过质量门')
        if strength['coverage']['minimum_count'] < 4000:
            raise ValueError('全市场最小样本门不足4000')
        if evidence['quote_source'] != 'tushare:daily':
            raise ValueError('成交额来源单位未核验')
        quotes = {}
        for q in evidence['quotes']:
            code = _row_code(q)
            if code in quotes or _date(q['trade_date']).isoformat() != day:
                raise ValueError('行情重复或日期错位')
            quotes[code] = q
        rows = []
        gaps = []
        for item in strength['rows']:
            code = _row_code(item)
            name = item['name']
            if not isinstance(name, str) or not name.strip():
                raise ValueError('证券名称缺失')
            q = quotes[code]
            close, previous, pct = (_finite_float(q.get(k)) for k in ('close', 'pre_close', 'pct_chg'))
            if (close is None or previous is None or min(close, previous) <= 0 or pct is None
                    or abs((close/previous-1)*100-pct) > .02 or pct != item['pct_chg']):
                raise ValueError('行情与市场身份快照数值不一致')
            amount, opening = (_finite_float(q.get(k)) for k in ('amount', 'open'))
            rows.append(dict(code=code, name=name.strip(), industry=item['industry'], pct_chg=pct,
                             amount_yi=amount/1e5 if amount is not None and amount >= 0 else None,
                             intraday_pct=(close/opening-1)*100 if opening is not None and opening > 0 else None))
        selected = [r for r in rows if r['name'].startswith('中')]
        market, metrics = _metrics(rows), _metrics(selected)
        coverage = strength['coverage']
        missing_codes = coverage['missing_codes']
        if (len(set(missing_codes)) != len(missing_codes)
                or len(missing_codes) != coverage['eligible_count']-coverage['sample_count']
                or set(missing_codes) & {r['code'] for r in rows}):
            raise ValueError('缺失身份名单与覆盖分母不一致')
        missing_names = coverage.get('missing_names', {})
        named_gaps = (isinstance(missing_names, dict) and set(missing_names) == set(missing_codes)
                      and all(isinstance(n, str) and n.strip() for n in missing_names.values()))
        missing_members = [c for c in missing_codes if missing_names[c].strip().startswith('中')] if named_gaps else None
        complete_members = missing_members == []
        metrics['eligible_count'] = len(selected)+len(missing_members) if missing_members is not None else None
        metrics['missing_quote_codes'] = missing_members
        if missing_codes:
            gaps.append('全市场基准存在行情缺口；统计仅按已核验样本，停牌未逐只确认')
        if not complete_members:
            gaps.append('中字头行情有缺口或缺失股票名称未知；只统计已核验样本，暂停核心排名')
        if metrics['intraday_count'] != metrics['sample_count']:
            gaps.append('部分开盘价缺失，开收盘指标仅按有效样本计算')
        if market['amount_count'] != market['sample_count']:
            gaps.append('全市场成交额不完整，成交额占比未计算')
        if metrics['amount_count'] != metrics['sample_count']:
            gaps.append('中字头成交额缺失，容量前排未计算')
        median = metrics['pct_chg_median']
        metrics['median_excess_pp'] = median-market['pct_chg_median'] if median is not None else None
        metrics['amount_share_pct'] = (100*metrics['amount_yi']/market['amount_yi']
                                       if metrics['amount_yi'] is not None and market['amount_yi'] else None)
        core = {}
        if complete_members:
            for role, key, eligible in (
                ('强势前排', 'pct_chg', [r for r in selected if r['pct_chg'] >= 5]),
                ('上涨容量前排', 'amount_yi', [r for r in selected if r['pct_chg'] > 0 and r['amount_yi'] is not None and r['amount_yi'] > 0]
                 if metrics['amount_count'] == metrics['sample_count'] else []),
            ):
                ranked = sorted(eligible, key=lambda r: (-r[key], r['code']))
                if not ranked:
                    continue
                cutoff = ranked[min(2, len(ranked)-1)][key]
                for row in ranked:
                    if row[key] < cutoff:
                        break
                    core.setdefault(row['code'], dict(**row, roles=[]))['roles'].append(role)
        return dict(schema=SCHEMA, trade_date=day, status='partial' if gaps else 'complete', gaps=gaps,
                    definition=NOTE, evidence=deepcopy(evidence), coverage=deepcopy(coverage),
                    rows=selected, core=list(core.values()), metrics=metrics, market=market)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result['gaps'] = [str(exc)]
        return result


def collect(day, *, market_strength, quote_result, now=None, db_path=None):
    """只消费同批结果；历史名称无可核验快照时不做历史补算。"""
    try:
        current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if current.tzinfo is None:
            raise ValueError('运行时刻无时区')
        current = current.astimezone(ZoneInfo('Asia/Shanghai'))
        target = date.fromisoformat(day)
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        start = target-timedelta(days=20)
        try:
            calendar = dict(conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ?",
                                         (start.isoformat(), day)))
        finally:
            conn.close()
        if any(calendar.get((start+timedelta(days=i)).isoformat()) not in (0, 1) for i in range(21)):
            raise ValueError('完整SSE交易日历缺失')
        if not calendar[day]:
            return missing(day, '非交易日', 'skipped')
        if target != current.date():
            raise ValueError('仅采集当日名称快照；历史请读取原归档，未来日未计算')
        if current.hour < 16:
            return missing(day, '尚未收盘', 'skipped')
        if not getattr(quote_result, 'success', False):
            raise ValueError('全市场日线来源失败')
        evidence = dict(market_strength=market_strength, name_snapshot_date=day,
                        quote_source=quote_result.source, quote_fetched_at=quote_result.fetched_at,
                        quotes=[{k: row.get(k) for k in ('ts_code', 'code', 'trade_date', 'open', 'close', 'pre_close', 'pct_chg', 'amount')}
                                for row in quote_result.data])
        return calculate(day, evidence)
    except Exception as exc:
        return missing(day, str(exc))


def validate(block, day):
    if block is None:
        return missing(day, '旧快照未计算中字头赚钱效应', 'not_computed')
    try:
        if block['schema'] != SCHEMA or block['trade_date'] != day:
            raise ValueError('日期或版本不匹配')
        if 'evidence' not in block:
            if block['status'] not in {'source_failed', 'not_computed', 'skipped'}:
                raise ValueError('来源证据缺失')
            return missing(day, '；'.join(block['gaps']), block['status'])
        expected = calculate(day, block['evidence'])
        if {k: v for k, v in block.items() if k != 'review_context'} != expected:
            raise ValueError('统计或核心名单与原始证据重算不一致')
        return expected
    except (ValueError, TypeError, KeyError, AttributeError):
        return missing(day, '中字头来源或统计核验失败')


def load_for_review(daily_dir, day, db_path):
    from analyzers.strong_stock_feedback_history import _envelope
    from services.review_calendar import load_review_context
    try:
        fact, context = load_review_context(db_path, day)
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            sse_window = [list(r) for r in conn.execute(
                "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ? ORDER BY date",
                (fact, day))]
        finally:
            conn.close()
        if sse_window != (context['calendar'] if context else [[day, 1]]):
            raise ValueError('复盘窗口不是完整SSE日历')
        envelope = _envelope(daily_dir, fact, {})
        if not isinstance(envelope, dict):
            return missing(day, '盘后归档缺失或损坏', 'not_computed' if envelope == 'missing-data' else 'source_failed')
        block = validate(envelope['raw_data'].get(KEY), fact)
        return {**block, 'review_context': context} if context else block
    except Exception:
        return missing(day, '复盘日期或来源未核验')


def _view(block, day):
    from services.review_calendar import review_fact_date
    try:
        fact = review_fact_date({**(block or {}), 'date': (block or {}).get('trade_date')}, day)
        return validate(block, fact), fact
    except (ValueError, TypeError, AttributeError):
        return missing(day, '事实日期未核验'), day


def _fmt(value, percent=False):
    return '未计算' if value is None else f'{value:.2f}'+('%' if percent else '')


def _summary(value):
    m = value['metrics']
    if m is None:
        return '[事实] 中字头赚钱效应尚未取得有效数据，核心票暂不能判断。'
    return (f"[事实] 已核验样本{m['sample_count']}只，上涨率{_fmt(m['advance_rate']*100 if m['advance_rate'] is not None else None, True)}；"
            f"涨幅均值{_fmt(m['pct_chg_mean'], True)}、中位{_fmt(m['pct_chg_median'], True)}；"
            f"≥5% {m['strong_gain_count']}只，≤-5% {m['strong_loss_count']}只；"
            f"相对全市场中位{_fmt(m['median_excess_pp'])}个百分点，成交额占比{_fmt(m['amount_share_pct'], True)}。")


def _core_lines(value):
    if not value['core']:
        return ['[判断] 核心观察：未选出符合条件且覆盖完整的候选。']
    return [f"[判断] {' / '.join(r['roles'])}：{r['name']}（{r['code']}），涨幅{_fmt(r['pct_chg'], True)}，成交额{_fmt(r['amount_yi'])}亿元。"
            for r in value['core']]


def render_markdown(block, day):
    value, fact = _view(block, day)
    clean = lambda s: str(s).replace('|', '｜').replace('\n', ' ').replace('\r', ' ')
    return '\n\n'.join(['### 中字头赚钱效应与核心观察', f'报告日：{day}；事实日：{fact}',
                         _summary(value), *map(clean, _core_lines(value)), NOTE,
                         '状态：'+value['status'], *('缺口：'+clean(g) for g in value['gaps'])])


def render_html(block, day):
    value, fact = _view(block, day)
    html = f'<div {MARKER}="{day}"><h3>中字头赚钱效应与核心观察</h3><p>{escape(_summary(value))}</p>'
    html += ''.join('<p>'+escape(s)+'</p>' for s in _core_lines(value)[:6])
    if len(value['core']) > 6:
        html += f'<p>另有{len(value["core"])-6}只并列观察票，见完整依据；代码顺序仅供查找。</p>'
    items = len(value['rows']) + len(value['gaps']) + 3
    html += f'<details class="evidence" data-as-of="{fact}" data-items="{items}"><summary>口径、覆盖与逐股依据（{items}项）</summary>'
    html += f'<p>报告日：{day}；事实日：{fact}；名称快照日：{fact if value["metrics"] is not None else "未取得"}；状态：{value["status"]}</p>'
    html += '<p>'+escape(NOTE)+'</p>'
    if value['metrics']:
        m = value['metrics']
        eligible = str(m['eligible_count']) if m['eligible_count'] is not None else '未知'
        html += f'<p>中字头有效行情/应有身份：{m["sample_count"]}/{eligible}；市场基准有效样本：{value["market"]["sample_count"]}；市场中位：{_fmt(value["market"]["pct_chg_median"], True)}。</p>'
        html += f'<p>开收盘中位：{_fmt(m["intraday_median"], True)}；开盘覆盖{m["intraday_count"]}/{m["sample_count"]}；成交额覆盖{m["amount_count"]}/{m["sample_count"]}。</p>'
        roles = {r['code']: ' / '.join(r['roles']) for r in value['core']}
        html += '<div style="overflow-x:auto"><table><thead><tr><th>股票</th><th>涨幅(%)</th><th>开收盘(%)</th><th>成交额(亿元)</th><th>核心观察[判断]</th></tr></thead><tbody>'
        for r in value['rows']:
            html += '<tr>'+''.join('<td>'+escape(s)+'</td>' for s in (f'{r["name"]}（{r["code"]}）', _fmt(r['pct_chg']), _fmt(r['intraday_pct']), _fmt(r['amount_yi']), roles.get(r['code'], '—')))+'</tr>'
        html += '</tbody></table></div>'
    html += ''.join('<p>缺口：'+escape(g)+'</p>' for g in value['gaps'])+'</details></div>'
    gap = '中字头赚钱效应存在缺口，详见④风格。' if value['status'] != 'complete' else None
    return html, gap
