"""题材启动证据卡：观察当日题材榜，不授予启动/主线或交易身份。"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
import math
import re
from zoneinfo import ZoneInfo

from analyzers import sector_increment as si
from analyzers import strong_stock_feedback as sf
from analyzers.strong_stock_feedback_history import _envelope
from services.volume_concentration.capacity_history import _open_days
from utils.price_limit import limit_pct_for

KEY = 'theme_launch'
SCHEMA = 'theme-launch-v1'
MARKER = 'data-theme-launch'
SOURCE = 'tushare:ths_member:snapshot'
NOTE = ('[事实] 仅观察当日题材榜前10名，榜单不代表首次启动；同股可属于多个题材，各题材不能相加。'
        '涨停按非ST、非退市A股及常规10/20/30cm分组，板型不等于特殊交易日实际限制。'
        '成交额为沪深成员（含ST）按采集当日成分快照比较两日，非历史成分、非资金净流入。'
        '[观点] 课程10cm≥10只与10/20cm合计≥10只存在表述差异，分别列数，不自动评定顶级启动。'
        '新颖性、海外共振、20cm未涨停扩散与竞价封单尚未计算；不生成选股评分或买卖建议。'
        '出处：用户提供《10月31正课1》15:40—27:11、47:23—48:15；未绑定已入库笔记。')


def missing(day, reason, status='source_failed'):
    return dict(schema=SCHEMA, trade_date=day, status=status, rows=[], gaps=[reason])


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('数值缺失或非法')
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('数值非有限')
    return n


def _integer(value):
    n = _number(value)
    if n < 0 or n != int(n):
        raise ValueError('计数非法')
    return int(n)


def rankings(section, day):
    if (not isinstance(section, dict) or section.get('_source') != 'tushare:limit_cpt_list'
            or section.get('error') or not isinstance(section.get('data'), list) or not section['data']):
        raise ValueError('当日题材榜缺失或失败，不解释为零题材')
    rows, codes, ranks = [], set(), set()
    for r in section['data']:
        code, name = r.get('ts_code'), r.get('name')
        rank = _integer(r.get('rank'))
        if (not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}\.TI', code)
                or not isinstance(name, str) or not name.strip() or code in codes
                or rank in ranks or rank < 1 or r.get('trade_date') != day.replace('-', '')):
            raise ValueError('题材榜身份、排名或日期错位')
        codes.add(code); ranks.add(rank)
        change = None
        try:
            change = _number(r.get('pct_chg'))
        except (ValueError, TypeError):
            pass
        rows.append(dict(code=code, name=name.strip(), rank=rank, change_pct=change))
    if ranks != set(range(1, len(rows)+1)):
        raise ValueError('题材榜排名不连续')
    return sorted(rows, key=lambda r: r['rank'])[:10]


def _members(snapshot, code, day):
    if not isinstance(snapshot, dict) or snapshot.get('date') != day:
        raise ValueError('题材成员快照日期错位')
    captured = datetime.fromisoformat(snapshot['fetched_at'])
    if captured.tzinfo is None or captured.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() != day:
        raise ValueError('成员采集时间未核验，禁止当前成员回填历史')
    block = snapshot['themes'].get(code)
    if not isinstance(block, dict) or block.get('status') != 'complete':
        raise ValueError('该题材成员来源失败')
    rows, pages = block.get('rows'), block.get('pages')
    if (not isinstance(rows, list) or not rows or not isinstance(pages, list) or not 2 <= len(pages) <= 10
            or any(type(n) is not int or not 0 <= n <= 2000 for n in pages)
            or pages[-1] != 0 or any(n == 0 for n in pages[:-1]) or sum(pages) != len(rows)):
        raise ValueError('成员分页未闭合或计数不符')
    members, seen = {}, set()
    for r in rows:
        c = sf._code(r['con_code'])
        if (r.get('ts_code') != code or c != r['con_code'] or c in seen
                or r.get('is_new') not in {'Y', 'N'} or not isinstance(r.get('con_name'), str)
                or not r['con_name'].strip()):
            raise ValueError('成员身份重复、冲突或有效状态未知')
        seen.add(c)
        if r['is_new'] == 'Y':
            members[c] = r['con_name'].strip()
    if not members:
        raise ValueError('当前有效成员为空，不能确认零样本')
    return members


def _amounts(block, day, previous):
    value = si.validate(block, day)
    if value['status'] not in {'complete', 'partial'} or value.get('previous_date') != previous:
        raise ValueError('同日成交额来源缺失或比较日错位')
    components = value.get('components')
    if not isinstance(components, list) or not components:
        raise ValueError('成交额逐股证据缺失')
    by_code, sums = {}, [0., 0.]
    for r in components:
        c = sf._code(r['ts_code'])
        if c != r['ts_code'] or c in by_code or not si._eligible(c):
            raise ValueError('成交额成分身份冲突')
        values = [None if r.get(k) is None else _number(r[k]) for k in ('previous_amount_yi', 'amount_yi')]
        if all(v is None for v in values) or any(v is not None and v < 0 for v in values):
            raise ValueError('成交额成分非法')
        by_code[c] = values
        for i, n in enumerate(values):
            if n is not None:
                sums[i] += n
    market = value['market']
    for total, key in zip(sums, ('previous_amount_yi', 'amount_yi')):
        if not math.isclose(total, market[key], rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError('成交额逐股合计与市场不一致')
    market = {**market, 'change_pct': (market['amount_yi']/market['previous_amount_yi']-1)*100}
    return by_code, market, value['status']


def calculate(day, previous, evidence):
    """所有派生值可由同一证据重算；错误不转成0，也不猜历史成员。"""
    result = dict(schema=SCHEMA, trade_date=day, previous_date=previous, status='partial',
                  membership_date=None, rows=[], market=None, indices=[], gaps=[], evidence=evidence)
    try:
        if date.fromisoformat(previous) >= date.fromisoformat(day) or day < '2022-01-01':
            raise ValueError('日期或常规板型适用范围非法')
        themes = rankings(evidence.get('ranking'), day)
        if evidence.get('limit_up_date') != day or evidence.get('previous_limit_up_date') != previous:
            raise ValueError('两日涨停名单来源日错位')
        if evidence.get('membership_source') != SOURCE:
            raise ValueError('题材成员来源未核验')
        try:
            up = {r['code']: r for r in sf._cohort(evidence.get('limit_up'))}
            up_error = None
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            up, up_error = {}, str(exc)
        try:
            prior = {r['code']: r for r in sf._cohort(evidence.get('previous_limit_up'))}
            prior_error = None
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            prior, prior_error = {}, str(exc)
        try:
            amounts, market, amount_status = _amounts(evidence.get('increment'), day, previous)
            result['market'] = market
            amount_error = None
            if amount_status != 'complete':
                result['gaps'].append('全市场成交额覆盖为partial，份额变化未计算')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            amounts, market, amount_status, amount_error = {}, None, 'source_failed', str(exc)
        for key, name, expected_code in (('shanghai', '上证指数', '000001.SH'), ('chinext', '创业板指', '399006.SZ')):
            try:
                row = evidence.get('indices', {}).get(key, {})
                dates = [row[k] for k in ('date', 'trade_date') if k in row]
                if (row.get('error') or row.get('_source') != 'tushare:index_daily'
                        or row.get('code') != expected_code or not dates
                        or any(str(d).replace('-', '') != day.replace('-', '') for d in dates)):
                    raise ValueError('指数身份、日期或来源缺失/冲突')
                result['indices'].append(dict(name=name, change_pct=_number(row.get('change_pct')), source=row['_source']))
            except (ValueError, TypeError, KeyError, AttributeError):
                result['gaps'].append(name+'同日涨跌未核验')
        for theme in themes:
            r = dict(**theme, status='partial', member_count=None, counts=None, limit_stocks=[],
                     lead_10cm=None, lead_unknown=None, amount=None, gaps=[])
            result['rows'].append(r)
            if r['change_pct'] is None:
                r['gaps'].append('题材榜当日涨跌缺失')
            try:
                members = _members(evidence.get('membership'), theme['code'], day)
                r['member_count'] = len(members)
                result['membership_date'] = day
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                r.update(status='source_failed', gaps=[str(exc)])
                continue
            if up_error:
                r['gaps'].append('涨停名单未核验：'+up_error)
            else:
                matched = [dict(v, board=int(limit_pct_for(c, v['name']))) for c, v in up.items() if c in members]
                if any(members[s['code']] != s['name'] for s in matched):
                    r['gaps'].append('成员与涨停名称冲突，计数未计算')
                else:
                    r['limit_stocks'] = sorted(matched, key=lambda s: (s['board'], s['code']))
                    r['counts'] = {str(b): sum(s['board'] == b for s in matched) for b in (10, 20, 30)}
                    r['counts']['10+20'] = r['counts']['10']+r['counts']['20']
                    # 身位只核验“前日已涨停且今日连续板数前后一致”，不是领先关系或龙头身份。
                    candidates = [s for s in matched if s['board'] == 10]
                    leaders, unknown = [], []
                    for s in candidates:
                        h, old = s['limit_times'], prior.get(s['code'])
                        if h == 1:
                            continue
                        if (h is not None and h >= 2 and not prior_error and old is not None
                                and old['limit_times'] is not None and old['limit_times']+1 == h):
                            leaders.append(dict(code=s['code'], name=s['name'], height=h))
                        else:
                            unknown.append(s['code'])
                    r.update(lead_10cm=leaders, lead_unknown=unknown)
                    if unknown:
                        r['gaps'].append(f'10cm身位待核验{len(unknown)}只')
            if amount_error:
                r['gaps'].append('成交额未计算：'+amount_error)
            else:
                eligible = [c for c in members if si._eligible(c)]
                valid = [c for c in eligible if c in amounts and all(v is not None for v in amounts[c])]
                r['amount_coverage'] = dict(eligible=len(eligible), complete=len(valid), missing=sorted(set(eligible)-set(valid)))
                if not eligible or len(valid) != len(eligible):
                    r['gaps'].append(f'沪深成员两日成交额覆盖{len(valid)}/{len(eligible)}，不补0')
                else:
                    pa, ta = (sum(amounts[c][i] for c in valid) for i in (0, 1))
                    r['amount'] = dict(previous_yi=pa, current_yi=ta, delta_yi=ta-pa,
                                       change_pct=(ta/pa-1)*100 if pa > 0 else None,
                                       share_delta_pp=(ta/market['amount_yi']-pa/market['previous_amount_yi'])*100 if amount_status == 'complete' else None)
                    if amount_status != 'complete':
                        r['gaps'].append('全市场成交额不完整，份额变化未计算')
            if not r['gaps']:
                r['status'] = 'complete'
        if all(r['status'] == 'source_failed' for r in result['rows']):
            result['status'] = 'source_failed'
        elif not result['gaps'] and all(r['status'] == 'complete' for r in result['rows']):
            result['status'] = 'complete'
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return missing(day, str(exc))


def collect(registry, day, raw, *, daily_dir, db_path=None, now=None):
    """随原盘后单一入口收集；完整日历+当日16:00门，历史只读取已存快照。"""
    try:
        now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if now.tzinfo is None:
            raise ValueError('运行时间缺少时区')
        now = now.astimezone(ZoneInfo('Asia/Shanghai'))
        if date.fromisoformat(day) > now.date():
            raise ValueError('未来日期')
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            target = conn.execute("SELECT is_open FROM trade_calendar WHERE exchange='SSE' AND date=?", (day,)).fetchone()
            if target is not None and target[0] == 0:
                return missing(day, '休市日不采集', 'skipped')
            days = _open_days(conn, day, 2)
        finally:
            conn.close()
        if day != now.date().isoformat():
            return load_snapshot(daily_dir, day)
        if now.hour < 16:
            return missing(day, '尚未收盘', 'skipped')
        if not isinstance(raw, dict) or raw.get('date') != day:
            raise ValueError('当批盘后日期不匹配')
        selected = rankings(raw.get('limit_cpt_list'), day)
        response = registry.call('get_theme_member_snapshot', day, [r['code'] for r in selected])
        if not response.success or response.source != SOURCE:
            raise ValueError('题材成员采集失败或来源未核验')
        previous = _envelope(daily_dir, days[-2], {})
        prior_raw = previous.get('raw_data', {}) if isinstance(previous, dict) else {}
        evidence = dict(ranking=raw.get('limit_cpt_list'), membership=response.data,
                        membership_source=response.source, limit_up=raw.get('limit_up'),
                        limit_up_date=day, previous_limit_up_date=days[-2],
                        previous_limit_up=prior_raw.get('limit_up'), increment=raw.get('sector_increment'),
                        indices={k: raw.get('indices', {}).get(k, {}) for k in ('shanghai', 'chinext')})
        return calculate(day, days[-2], evidence)
    except Exception as exc:
        # 收集错误隔离于盘后主链；外部错误文本不进入报告。
        return missing(day, '题材启动采集未完成：'+type(exc).__name__)


def validate(block, day):
    if block is None:
        return missing(day, '旧快照未计算题材启动证据', 'not_computed')
    try:
        if block.get('schema') != SCHEMA or block.get('trade_date') != day:
            raise ValueError('题材启动来源日期或版本不符')
        if 'evidence' not in block:
            if block.get('status') not in {'not_computed', 'source_failed', 'skipped'} or block.get('rows') != []:
                raise ValueError('题材启动证据缺失')
            if not isinstance(block.get('gaps'), list) or any(not isinstance(g, str) for g in block['gaps']):
                raise ValueError('缺口非法')
            # 失败信封只保留规范状态和缺口，绝不接受夹带的market/indices数值。
            return missing(day, '', block['status']) | {'gaps': block['gaps']}
        expected = calculate(day, block['previous_date'], block['evidence'])
        if {k: v for k, v in block.items() if k != 'review_context'} != expected:
            raise ValueError('题材启动计算值与原始证据不符')
        return expected
    except (ValueError, TypeError, KeyError, AttributeError):
        return missing(day, '题材启动快照损坏或与证据不符')


def load_snapshot(daily_dir, day):
    env = _envelope(daily_dir, day, {})
    if isinstance(env, str):
        return missing(day, '同日盘后归档'+env, 'not_computed' if env == 'missing-data' else 'source_failed')
    return validate(env['raw_data'].get(KEY), day)


def load_for_review(daily_dir, day, db_path):
    from services.review_calendar import load_review_context
    try:
        fact, context = load_review_context(db_path, day)
        block = load_snapshot(daily_dir, fact)
        return {**block, 'review_context': context} if context else block
    except Exception:
        return missing(day, '复盘日历未核验，不跨日猜测')


def _view(block, day):
    from services.review_calendar import review_fact_date
    try:
        fact = review_fact_date({**(block or {}), 'date': (block or {}).get('trade_date')}, day)
        return validate(block, fact), fact
    except (ValueError, TypeError, AttributeError):
        return missing(day, '复盘日期证据不符'), day


def _fmt(value, signed=False):
    if value is None:
        return '未计算'
    return f'{value:+.2f}' if signed else f'{value:.2f}'


def _cells(r):
    counts, amount = r['counts'] or {}, r['amount'] or {}
    lead = ('未计算' if r['lead_10cm'] is None else
            '；'.join(f"{s['name']}（{s['code']}）{s['height']}板" for s in r['lead_10cm']) or '已核验0只')
    if r.get('lead_unknown'):
        lead += f"；待核验{len(r['lead_unknown'])}只"
    return [r['name'], r['status'], str(r['member_count']) if r['member_count'] is not None else '未计算',
            str(counts.get('10', '未计算')), str(counts.get('20', '未计算')), str(counts.get('30', '未计算')),
            str(counts.get('10+20', '未计算')), lead, _fmt(r['change_pct'], True), _fmt(amount.get('delta_yi'), True), _fmt(amount.get('share_delta_pp'), True)]


HEADERS = ['题材', '覆盖状态', '成员数', '10cm涨停', '20cm涨停', '30cm涨停', '10+20合计', '10cm身位证据', '题材涨跌(%)', '增额(亿)', '份额变化(pp)']


def render_markdown(block, day):
    value, fact = _view(block, day)
    clean = lambda s: str(s).replace('|', '｜').replace('\n', ' ')
    member_date = value.get('membership_date') or '未取得'
    lines = ['### 题材启动证据卡', f"状态：{value['status']}；事实日：{fact}；成员快照日：{member_date}（逐题材覆盖见下表）；报告日：{day}", NOTE]
    if value.get('market'):
        lines.append('沪深市场成交额变化：'+_fmt(value['market']['change_pct'], True)+'%')
    lines.extend(f"{r['name']}：{_fmt(r['change_pct'], True)}%" for r in value.get('indices', []))
    if value['rows']:
        lines += ['| '+' | '.join(HEADERS)+' |', '| '+' | '.join(['---']*len(HEADERS))+' |']
        lines += ['| '+' | '.join(clean(c) for c in _cells(r))+' |' for r in value['rows']]
    lines.extend('缺口：'+clean(g) for g in value.get('gaps', []))
    for r in value['rows']:
        lines.extend(clean(r['name'])+'缺口：'+clean(g) for g in r['gaps'])
    return '\n\n'.join(lines[:3])+'\n\n'+'\n'.join(lines[3:])


def render_html(block, day):
    value, fact = _view(block, day)
    member_date = value.get('membership_date') or '未取得'
    rows, status = value['rows'], value['status']
    if status == 'not_computed':
        summary = '[事实] 本日未计算，暂无可判断的题材启动证据。'
    elif status == 'skipped':
        summary = '[事实] 本日未采集题材启动证据。'
    elif not rows or not any(r['counts'] is not None for r in rows):
        summary = '[事实] 题材成员或行情来源未核验，暂无可判断的题材启动证据。'
    elif status == 'complete':
        summary = f'[事实] 已完整核验{len(rows)}个题材，涨停分布如下。'
    else:
        complete = sum(r['status'] == 'complete' for r in rows)
        summary = f'[事实] {len(rows)}个题材中{complete}个完整核验；以下保留已取得结果，其余待核验。'
    parts = [f'<div {MARKER}="{escape(day)}" data-as-of="{escape(fact)}"><h3>题材启动证据卡</h3>',
             '<p>'+escape(summary)+'</p>']
    if fact != day:
        parts.append('<p>[事实] 休市展示'+escape(fact)+'最近开放日历史，非当日新增。</p>')
    market = value.get('market')
    if market:
        parts.append('<p>[事实] 沪深市场成交额变化：'+_fmt(market['change_pct'], True)+'%。</p>')
    for r in value.get('indices', []):
        parts.append('<p>[事实] '+escape(r['name'])+'：'+_fmt(r['change_pct'], True)+'%</p>')
    for r in rows:
        counts = r['counts']
        if counts is None:
            continue
        result = (f"10cm {counts['10']}只、20cm {counts['20']}只、30cm {counts['30']}只"
                  f"；10+20cm合计{counts['10+20']}只")
        if r['status'] != 'complete':
            result += '；身位或量价仍有缺口'
        parts.append('<p>[事实] '+escape(r['name']+'：'+result)+'。</p>')
    items = len(rows) + len(value.get('gaps', [])) + 2
    parts.append(f'<details class="evidence" data-as-of="{escape(fact)}" data-items="{items}"><summary>完整证据、来源与计算口径（{items}项）</summary>')
    parts.append(f"<p>状态：{escape(status)}；报告日{escape(day)}；事实日{escape(fact)}；成员快照日：{escape(member_date)}（逐题材覆盖见下表）。</p>")
    parts.append('<p>'+escape(NOTE)+'</p>')
    if rows:
        parts.append('<div style="overflow-x:auto"><table><thead><tr>')
        parts.extend('<th>'+escape(h)+'</th>' for h in HEADERS)
        parts.append('</tr></thead><tbody>')
        for r in rows:
            parts.append('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in _cells(r))+'</tr>')
        parts.append('</tbody></table></div>')
        for r in rows:
            stocks = '；'.join(f"{s['name']}（{s['code']}）{s['board']}cm" for s in r['limit_stocks'])
            parts.append('<p>'+escape(r['name'])+'涨停明细：'+escape(stocks or ('已核验0只' if r['counts'] is not None else '未计算'))+'</p>')
            parts.extend('<p>'+escape(r['name']+'缺口：'+g)+'</p>' for g in r['gaps'])
    parts.extend('<p>缺口：'+escape(g)+'</p>' for g in value.get('gaps', []))
    parts.append('</details></div>')
    gap = '' if value['status'] == 'complete' else '题材启动证据为'+value['status']+'，缺失不补0；详见②。'
    return ''.join(parts), gap
