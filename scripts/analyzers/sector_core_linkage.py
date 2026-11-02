"""板块核心联动：只读已保存的复盘身份与同日证据，不授予新核心身份。"""
from __future__ import annotations

from html import escape
import json
import re
from pathlib import Path
import sqlite3

from analyzers import strong_stock_feedback as sf
from analyzers.strong_stock_feedback_history import _envelope
from services.volume_concentration import capacity_trend as ct
from services.volume_concentration.capacity_history import _open_days, _read

SCHEMA = 'sector-core-linkage-v1'
MARKER = 'data-sector-core-linkage'
ROLES = ('趋势中军', '连板核心', '前排活跃', '弹性前排')
NOTE = ('[事实] 身份取观察日前最近5个开放日内最新一份已保存复盘第5步，不读取提案或自动补位；'
        '显示归档日期，不声称还原当时确认时间。5日为工程有效期，非课程阈值。'
        '板块沿用该份复盘分组，不将概念名称自动映射为申万行业。'
        '容量仅覆盖当日成交Top20，前排行情仅覆盖昨日涨停反馈；未覆盖不是走弱。'
        '[判断] 弱项规则：趋势中军MA20下方且末端连跌至少2根有效K线；连板核心经核验断板；'
        '前排活跃/弹性前排收盘涨跌为负。多角色同日弱项不等于板块退潮或因果带动。'
        '新增涨停仅是前后两日名单差，不授予接力或核心身份。出处：鞠磊笔记#1089，31:09—32:42。')


def _key(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _base(day):
    return dict(schema=SCHEMA, date=day, previous_date=None, identity_date=None, identity_age=None,
                status='source_failed', sectors=[], members=[], gaps=[], sources={})


def _roster(conn, days):
    # 只使用最新一份；损坏/空名单不回退旧名单，避免悄悄恢复已撤销的身份。
    record = conn.execute('SELECT date,step5_leaders FROM daily_reviews WHERE date>=? AND date<=? '
                          'AND step5_leaders IS NOT NULL ORDER BY date DESC LIMIT 1',
                          (days[0], days[-1])).fetchone()
    if record is None:
        return None, []
    day, payload = record
    if day not in days:
        raise ValueError('最票归档日不是开放日')
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get('top_leaders'), list):
        raise ValueError('复盘最票结构损坏')
    from services.daily_leaders.models import MAX_CONFIRMATION_CANDIDATES
    if len(data['top_leaders']) > MAX_CONFIRMATION_CANDIDATES:
        raise ValueError('历史最票超出15只身份上限')
    result, seen, seen_names, seen_roles = [], set(), set(), set()
    for row in data['top_leaders']:
        if not isinstance(row, dict):
            raise ValueError('最票记录非法')
        # 备选/剔除即使被旧入口写入，也不能成为本模块核心。
        from services.daily_leaders.selection import candidate_review_status
        if candidate_review_status(row):
            raise ValueError('复盘名单混入备选或剔除身份')
        sector, role = _key(row.get('sector')), _key(row.get('attribute_type'))
        from services.daily_leaders.selection import parse_stock_display_identity, canonical_stock_code
        display_text = row.get('stock')
        if not isinstance(display_text, str) or not display_text.strip():
            raise ValueError('最票名称缺失')
        suffix_match = re.match(r'^\s*(\d{6})\s*(\.[A-Za-z]{2})', display_text)
        if suffix_match:
            sf._code(''.join(suffix_match.groups()))
        display = parse_stock_display_identity(display_text)
        if not display.name_key or display.name_key in seen_names:
            raise ValueError('最票名称缺失或重复')
        seen_names.add(display.name_key)
        role_key = (sector or '未分类', role or '身份未标注')
        if role_key in seen_roles:
            raise ValueError('同板块角色重复')
        seen_roles.add(role_key)
        explicit = row.get('stock_code')
        if explicit:
            sf._code(explicit)  # 归一化前校验交易所，不能先丢弃非法后缀。
        code = canonical_stock_code(explicit) if explicit else display.code
        if display.malformed_code_prefix or (explicit and not code) or (display.code and display.code != code):
            raise ValueError('最票代码非法或冲突')
        if code:
            code = sf._code(code)
            if code in seen:
                raise ValueError('最票代码重复')
            seen.add(code)
        result.append(dict(code=code, name=display.name_key or '名称缺失', sector=sector or '未分类',
                           role=role if role in ROLES else '身份未标注'))
    return day, result


def _limits(raw):
    if not isinstance(raw, dict):
        return {}, '来源归档缺失'
    try:
        rows = sf._cohort(raw.get('limit_up'))
        source = {sf._row_code(r): r for r in raw['limit_up']['stocks']}
        return {r['code']: {**r, 'industry': _key(source[r['code']].get('industry'))} for r in rows}, None
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {}, str(exc)


def _capacity(block, day):
    # 失败刷新保留的旧证据不参与本次联动结论。
    if block.get('status') not in {'complete', 'partial'}:
        return {}, block.get('status', '未计算')
    try:
        ct._validate_block(block, day)
        rows = {}
        for row in block['rows']:
            code = sf._code(row['code'])
            if code in rows:
                raise ValueError('容量身份重复')
            rows[code] = row
        return rows, None
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}, '容量证据损坏'


def _cap_state(row, sector):
    if not row or row.get('status') != 'complete':
        return None, '容量未覆盖或不完整'
    if _key(row.get('industry')) != sector or sector in {'未分类', 'UNCLASSIFIED'}:
        return None, '容量行业与归档分组不一致或未分类'
    weak = row['ma20_position'] == '下方' and row['end_run_direction'] == '连跌' and row['end_run_bars'] >= 2
    label = f"MA20{row['ma20_position']}／{row['ma20_direction']}；{row['end_run_direction']}{row['end_run_bars']}根"
    return weak, label


def calculate(day, previous, identity_date, identity_age, roster, raw, prior_raw, capacity, prior_capacity):
    result = _base(day)
    result.update(previous_date=previous, identity_date=identity_date, identity_age=identity_age)
    current_up, current_error = _limits(raw)
    previous_up, previous_error = _limits(prior_raw)
    cap, cap_error = _capacity(capacity, day)
    old_cap, old_cap_error = _capacity(prior_capacity, previous)
    feedback = sf._view(raw.get(sf.KEY) if isinstance(raw, dict) else None, day)
    valid_feedback = feedback.get('source_date') == previous and feedback['status'] in {'complete', 'partial', 'empty'}
    quotes = {sf._row_code(r): r for r in feedback['details'] if r.get('quote_status') == 'complete'} if valid_feedback else {}
    result['sources'] = dict(limit_up=current_error or 'complete', previous_limit_up=previous_error or 'complete',
                             capacity=cap_error or 'usable', previous_capacity=old_cap_error or 'usable',
                             feedback=feedback['status'] if valid_feedback else '未计算或来源日不匹配')
    for member in roster:
        r = dict(member, weak=None, evidence='未计算', change='未计算', gaps=[])
        code, role, sector = r['code'], r['role'], r['sector']
        if not code or role not in ROLES or sector in {'未分类', 'UNCLASSIFIED'}:
            r['gaps'].append('代码、角色或板块未确认')
        elif role == '趋势中军':
            r['weak'], r['evidence'] = _cap_state(cap.get(code), sector)
            before, _ = _cap_state(old_cap.get(code), sector)
            if before is not None and r['weak'] is not None:
                r['change'] = ('弱项持续' if r['weak'] and before else '新增弱项' if r['weak'] else
                               '弱项解除' if before else '未触发弱项')
            else:
                r['gaps'].append('前后日同股容量变化未覆盖')
            if r['weak'] is None:
                r['gaps'].append(r['evidence'])
        elif role == '连板核心':
            prior = previous_up.get(code)
            if previous_error or current_error or not prior or (prior.get('limit_times') or 0) < 2:
                r['gaps'].append('前日连板身份或当日完整涨停名单未核验')
            elif code in current_up:
                r.update(weak=False, evidence='前日连板→当日仍涨停', change='涨停延续')
            elif code in quotes:
                r.update(weak=True, evidence='前日连板→当日有交易且未涨停', change='已核验断板')
            else:
                r['gaps'].append('当日有交易未核验，不能把停牌或缺行情判为断板')
        else:
            q = quotes.get(code)
            if q:
                r.update(weak=q['close_pct'] < 0, evidence=f"收盘{q['close_pct']:+.2f}%；"+('触及前高' if q.get('touched_previous_high') is True else '未触及前高' if q.get('touched_previous_high') is False else '前高未计算'))
            else:
                r['gaps'].append('昨日涨停反馈未覆盖该核心行情')
        result['members'].append(r)
    for sector in sorted({r['sector'] for r in result['members']}):
        members = [r for r in result['members'] if r['sector'] == sector]
        known = [r for r in members if r['weak'] is not None]
        weak_roles = sorted({r['role'] for r in known if r['weak']})
        roles = {r['role'] for r in members if r['role'] in ROLES}
        missing_roles = [role for role in ROLES if role not in roles]
        all_covered = len(known) == len(members) and not missing_roles
        label = ('多类核心同日弱项' if len(weak_roles) >= 2 else '局部弱项' if len(weak_roles) == 1 and all_covered else
                 '已见弱项，其余待核验' if weak_roles else '未触发本模块弱项' if all_covered else '覆盖不足，暂不判断')
        fresh = None if current_error or previous_error or sector in {'未分类','UNCLASSIFIED'} else [
            r for code,r in current_up.items() if code not in previous_up and r['industry'] == sector]
        new_unknown = None if current_error or previous_error else sum(
            r['industry'] in {'', '未分类', 'UNCLASSIFIED'} for code,r in current_up.items() if code not in previous_up)
        result['sectors'].append(dict(sector=sector, members=len(members), covered=len(known), weak_roles=weak_roles,
                                      missing_roles=missing_roles, assessment=label, new_limit_up=fresh, new_unknown=new_unknown))
    for r in result['members']:
        if r['gaps']:
            result['gaps'].append(f"{r['name']}："+'；'.join(r['gaps']))
    for s in result['sectors']:
        if s['missing_roles']:
            result['gaps'].append(s['sector']+'角色缺位：'+'、'.join(s['missing_roles']))
        if s['new_unknown']:
            result['gaps'].append(s['sector']+f"新增涨停行业覆盖不全：另{s['new_unknown']}只未分类，不能确认归属")
        if s['new_limit_up'] is None:
            result['gaps'].append(s['sector']+'新增涨停样本未计算')
    result['status'] = 'partial' if result['gaps'] else 'complete' if roster else 'empty'
    return result


def build(daily_dir, day, *, db_path=None, current_raw=None):
    """全链路只读，无网络、补采、推送或业务表写入。"""
    from db.connection import get_readonly_connection
    result, conn = _base(day), None
    try:
        conn = get_readonly_connection(db_path)
        days = _open_days(conn, day, 6)
        previous = days[-2]
        result['previous_date'] = previous
        identity_date, roster = _roster(conn, days[:-1])
        if identity_date is None:
            result.update(status='missing-data', gaps=['前5个开放日内没有已保存的复盘最票身份'])
            return result
        if not roster:
            result.update(status='empty', identity_date=identity_date, gaps=['最新复盘最票为空，未沿用更早名单'])
            return result
        cache = {}
        def raw(d):
            e = _envelope(Path(daily_dir), d, cache)
            return e['raw_data'] if isinstance(e, dict) else None
        today = current_raw if current_raw is not None else raw(day)
        # 报告接口以day独立传日期；无根date合法，但显式冲突不能被覆盖。
        if current_raw is not None and current_raw.get('date', day) != day:
            raise ValueError('当批盘后数据日期错位')
        prior = raw(previous)
        for data, expected in ((today, day), (prior, previous)):
            if isinstance(data, dict) and data.get('date', expected) != expected:
                raise ValueError('盘后归档内部日期错位')
        return calculate(day, previous, identity_date, len(days)-1-days.index(identity_date), roster,
                         today, prior, ct.from_record(_read(conn, day), day), ct.from_record(_read(conn, previous), previous))
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        result['gaps'] = [str(exc)]
        return result
    finally:
        if conn is not None:
            conn.close()


def tables(view):
    summary = []
    for s in view['sectors']:
        fresh = s['new_limit_up']
        added_text = ('未计算' if fresh is None else '、'.join(f"{r['name']}（{r['code']}）" for r in fresh) or '已匹配0只')
        if s.get('new_unknown'):
            added_text += f"；另{s['new_unknown']}只行业未知"
        summary.append([s['sector'], f"{s['covered']}/{s['members']}", '、'.join(s['weak_roles']) or '未见已核验弱项',
                        '、'.join(s['missing_roles']) or '无', '[判断] '+s['assessment'],
                        added_text])
    detail = [[r['sector'], f"{r['name']}（{r['code'] or '代码未核验'}）", r['role'], r['evidence'],
               r['change'], '；'.join(r['gaps']) or '完整'] for r in view['members']]
    return [('板块核心联动', ['归档板块','已核验/已列核心','弱项角色','缺位角色','观察结论','新增涨停样本（不等于接力）'], summary),
            ('逐核心证据', ['归档板块','股票','身份','当日事实','较前日变化','缺口'], detail)]


def render_html(view, day):
    view = view if view is not None else {**_base(day), 'gaps':['尚未计算']}
    if view.get('schema') != SCHEMA or view.get('date') != day:
        raise ValueError('板块核心联动日期或口径错误')
    members = view['members']
    known = sum(r['weak'] is not None for r in members)
    weak = [r for r in members if r['weak'] is True]
    if not members:
        summary = ('[事实] 最近保存的最票名单为空，本日没有可跟踪的核心。' if view['status'] == 'empty' else
                   '[事实] 核心名单或行情尚未核验，本日暂无法判断板块核心联动。')
    out = [f'<div {MARKER}="{escape(day,quote=True)}"><h3>板块核心联动</h3>']
    if members:
        identity_date = view['identity_date'] or '未核验'
        short_identity = identity_date[5:] if identity_date[:4] == day[:4] else identity_date
        out.append('<p style="margin-bottom:8px">[事实] 观察'+escape(day[5:])+' · 核心名单归档'+escape(short_identity)+
                   f' · 已核验{known}/{len(members)}只归档核心</p>')
        # 色块只表示已归档成员的证据覆盖，不扩张为板块强弱评分。
        states = {
            True: ('weak', '已核验走弱', '#9f3038', '#fff1f2', '▼'),
            False: ('clear', '已核验未触发', '#245986', '#edf5fc', '○'),
            None: ('unknown', '待核验', '#596474', '#f3f4f6', '?'),
        }
        counts = (len(weak), known-len(weak), len(members)-known)
        coverage_label = f'已核验{known}/{len(members)}只；走弱{counts[0]}只，未触发{counts[1]}只，待核验{counts[2]}只'
        out.append('<div role="img" aria-label="'+escape(coverage_label,quote=True)+
                   '" style="display:flex;gap:3px;height:12px;margin:8px 0;border-radius:6px;overflow:hidden">')
        for r in members:
            state, label, color, background, symbol = states[r['weak']]
            out.append(f'<span data-core-coverage="{state}" style="flex:1;min-width:0;background:{color}"></span>')
        out.append('</div><div style="display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;margin-bottom:12px">')
        for value, count in zip((True, False, None), counts):
            _, label, color, _, symbol = states[value]
            out.append(f'<span style="color:{color}">{symbol} {label} {count}只</span>')
        out.append('</div>')
        for sector in dict.fromkeys(r['sector'] for r in members):
            group = [r for r in members if r['sector'] == sector]
            covered = sum(r['weak'] is not None for r in group)
            out.append('<div role="group" data-core-sector="'+escape(sector,quote=True)+'" style="margin:12px 0">'+
                       '<h4 style="margin:0 0 8px">'+escape(sector)+
                       f' <small style="font-weight:400">已核验{covered}/{len(group)}</small></h4>'+
                       '<div role="list" style="display:flex;flex-wrap:wrap;gap:8px">')
            for r in group:
                state, label, color, background, symbol = states[r['weak']]
                out.append(f'<div role="listitem" data-core-state="{state}" '+
                           f'style="flex:1 1 180px;min-width:0;max-width:100%;box-sizing:border-box;overflow-wrap:anywhere;'+
                           f'border:1px solid {color};border-left:4px solid {color};border-radius:8px;padding:10px 12px;background:{background};color:#253246">')
                out.append('<div style="font-size:12px">'+escape(r['role'])+'</div><strong>'+escape(r['name'])+
                           '</strong><div style="font-size:12px">'+escape(r['code'] or '代码未核验')+'</div>')
                out.append(f'<div style="color:{color};font-weight:600;margin-top:6px">'+
                           ('[判断] ' if r['weak'] is not None else '')+symbol+' '+label+'</div>')
                if r['weak'] is not None:
                    out.append('<div style="font-size:12px;margin-top:4px">[事实] '+escape(r['evidence'])+'</div>')
                out.append('</div>')
            out.append('</div></div>')
    else:
        out.append('<p>'+escape(summary)+'</p>')
    for label, headers, rows in tables(view):
        if not rows:
            continue
        out += [f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{len(rows)}" data-evidence-kind="sector-core-linkage"><summary>{escape(label)}（{len(rows)}项）</summary><div style="overflow-x:auto;max-width:100%"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>']
        out += ['<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in r)+'</tr>' for r in rows]
        out += ['</tbody></table></div></details>']
    gap = '；'.join(view['gaps']) or None
    items = len(view['gaps']) + 2
    out.append(f'<details class="evidence" data-as-of="{escape(day,quote=True)}" data-items="{items}" data-evidence-kind="sector-core-linkage"><summary>来源、缺口与判断口径（{items}项）</summary>')
    out.append('<p>'+escape(NOTE)+'</p>')
    out.append('<p>'+escape(f"状态：{view['status']}；观察日{day}；身份归档日{view['identity_date'] or '未核验'}；距观察日{view['identity_age'] if view['identity_age'] is not None else '未核验'}个开放日。")+'</p>')
    out.extend('<p>[事实] 缺口：'+escape(g)+'</p>' for g in view['gaps'])
    out.append('</details>')
    return '\n'.join(out+['</div>']), gap


def render_markdown(view):
    # 日报只放摘要，完整明细进正式复盘；整行限长，不截半行。
    clean = lambda x: str(x).replace('|','／').replace('\n',' ').replace('<','＜').replace('>','＞')
    lines = [f"[事实] 板块核心联动：{view['status']}；身份归档日{view['identity_date'] or '未核验'}。", NOTE]
    _, headers, rows = tables(view)[0]
    if rows:
        lines += ['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']
        for r in rows:
            line = '| '+' | '.join(clean(x) for x in r)+' |'
            if len(('\n'.join(lines+[line])).encode()) > 5000:
                lines.append('其余明细见正式复盘。')
                break
            lines.append(line)
    if view['gaps']:
        lines.append('存在身份或行情缺口，详见正式复盘；未覆盖不等于走弱。')
    return '\n'.join(lines)
