"""盘后连续跌停数量：只读日历与逐日原始名单，不跨越数据缺口。"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import yaml

from analyzers.strong_stock_feedback import _row_code

SOURCES = {'tushare:limit_list_d', 'akshare:stock_zt_pool_dtgc_em'}
SCOPE = '跌停名单来源覆盖范围（非全市场）；ST及北交所完整性未核验'


def missing(day, reason, status='source_failed'):
    return dict(schema='consecutive-limit-down-v1', trade_date=day, status=status,
                count=None, verified_count=None, st_verified_count=None, market_count=None,
                rows=[], unknown_rows=[], single_day_rows=[], gaps=[reason], scope=SCOPE)


def _index(section, day):
    if (not isinstance(section, dict) or section.get('error')
            or not isinstance(section.get('_source'), str)
            or section.get('_source') not in SOURCES
            or not isinstance(section.get('status', 'complete'), str)
            or section.get('status', 'complete') not in {'complete', 'ok', 'success'}
            or not isinstance(section.get('stocks'), list)
            or type(section.get('count')) is not int
            or section['count'] != len(section['stocks'])):
        raise ValueError(f'{day}跌停名单缺失、失败或计数不符')
    if str(section.get('trade_date', day)).replace('-', '') != day.replace('-', ''):
        raise ValueError(f'{day}跌停名单日期错位')
    rows = {}
    for row in section['stocks']:
        if not isinstance(row, dict):
            raise ValueError(f'{day}跌停名单行结构非法')
        code = _row_code(row)
        name = row.get('name')
        if (code in rows or not isinstance(name, str) or not name.strip()
                or str(row.get('trade_date', day)).replace('-', '') != day.replace('-', '')):
            raise ValueError(f'{day}跌停名单身份重复、缺失或日期错位')
        rows[code] = row
    return rows


def calculate(day, days, sections):
    """days 为日历核验的连续开放日（升序）；缺日必须保留空槽。"""
    result = missing(day, SCOPE, 'partial')
    try:
        today = _index(sections.get(day), day)
        if not days or days[-1] != day or days != sorted(set(days)):
            raise ValueError('连续开放日窗口非法')
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        return missing(day, str(exc))
    indexed, errors = {day: today}, {}
    for previous in reversed(days[:-1]):
        try:
            indexed[previous] = _index(sections.get(previous), previous)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            errors[previous] = str(exc)
    rows, unknown, single_day = [], [], []
    for code, stock in today.items():
        name = stock['name'].strip()
        if '退' in name or code.startswith(('200', '201', '900')):
            continue
        evidence = [dict(trade_date=day, source=sections[day]['_source'], stock=deepcopy(stock))]
        exact, boundary, gap = False, None, '回溯窗口左侧未核验'
        for previous in reversed(days[:-1]):
            if previous not in indexed:
                gap = errors[previous]
                break
            source = sections[previous]['_source']
            historical = indexed[previous].get(code)
            if historical is None:
                # 降级源覆盖不同，缺席不证明未跌停。
                if source != evidence[-1]['source']:
                    gap = f'{previous}来源切换，名单缺席不能证明连续性中断'
                    break
                exact, gap = True, None
                boundary = dict(trade_date=previous, source=source, present=False)
                break
            evidence.append(dict(trade_date=previous, source=source, stock=deepcopy(historical)))
        streak = len(evidence)
        row = dict(code=code, name=name, is_st='ST' in name.upper(), days=streak,
                   days_exact=exact, start_date=evidence[-1]['trade_date'] if exact else None,
                   verified_from=evidence[-1]['trade_date'], verified_to=day,
                   pct_chg=stock.get('pct_chg', stock.get('change_pct')),
                   evidence=evidence, boundary=boundary, gap=gap)
        if streak >= 2:
            rows.append(row)
        elif not exact:
            unknown.append(row)
        else:
            single_day.append(row)
    rows.sort(key=lambda r: (-r['days'], r['code']))
    non_st = [r for r in rows if not r['is_st']]
    unknown_non_st = [r for r in unknown if not r['is_st']]
    result.update(count=None if unknown_non_st else len(non_st), verified_count=len(non_st),
                  st_verified_count=sum(r['is_st'] for r in rows), rows=rows,
                  unknown_rows=unknown, single_day_rows=single_day, calendar_days=days,
                  evidence=deepcopy(sections), count_scope='当日来源名单内非ST、剔退市/B股',
                  definition='截至事实日连续至少2个市场开放日收盘跌停；停牌不跨日拼接')
    result['gaps'] += sorted({r['gap'] for r in rows + unknown if r['gap']})
    return result


def collect(day, *, limit_down, daily_dir, db_path=None, now=None):
    """复用当日名单，历史只读；随既有 post 信封双写，无独立调度/推送。"""
    try:
        from db.connection import get_readonly_connection
        end = date.fromisoformat(day)
        start = end - timedelta(days=60)
        with closing(get_readonly_connection(db_path)) as conn:
            calendar = dict(conn.execute(
                "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ?",
                (start.isoformat(), day)).fetchall())
        if calendar.get(day) == 0:
            return missing(day, '休市日不采集', 'skipped')
        current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if current.tzinfo is None:
            raise ValueError('运行时刻缺少时区')
        current = current.astimezone(ZoneInfo('Asia/Shanghai'))
        if day > current.date().isoformat() or (day == current.date().isoformat() and current.hour < 16):
            return missing(day, '尚未完成盘后窗口', 'skipped')
        # 自然日历必须完整，不能以归档目录猜测相邻交易日。
        if any(calendar.get((start + timedelta(days=i)).isoformat()) not in (0, 1) for i in range(61)):
            raise ValueError('近61自然日SSE交易日历不完整')
        days = sorted(d for d, flag in calendar.items() if flag == 1)
        sections = {day: deepcopy(limit_down)}
        active = set(_index(limit_down, day))
        for previous in reversed(days[:-1]):
            if not active:
                break
            try:
                payload = yaml.load((Path(daily_dir)/previous/'post-market.yaml').read_text(encoding='utf-8'), Loader=yaml.CSafeLoader)
                if not isinstance(payload, dict) or str(payload.get('date')) != previous:
                    raise ValueError('盘后归档日期错位')
                section = payload.get('raw_data', {}).get('limit_down')
                sections[previous] = section
                active &= set(_index(section, previous))
            except (OSError, ValueError, AttributeError, yaml.YAMLError):
                sections[previous] = None
                break
        return calculate(day, days, sections)
    except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
        return missing(day, str(exc))


def render(block):
    count = block.get('count')
    verified = block.get('verified_count')
    amount = f'{count}只' if count is not None else ('未核验' if verified is None else f'待核（已核验{verified}只）')
    st_count = block.get('st_verified_count')
    st_text = '未核验' if st_count is None else f'{st_count}只'
    lines = [f"- [事实] **连续跌停个股数（非ST，≥2日）：{amount}**；来源范围内，状态：{block['status']}",
             f"- ST连续跌停已核验：{st_text}（完整总数待核）"]
    if block.get('gaps'):
        lines.append('- 连续跌停覆盖缺口：' + '；'.join(block['gaps']))
    if block.get('rows') or block.get('unknown_rows'):
        lines += ['', '<details><summary>连续跌停名单与待核对象</summary>', '',
                  '| 股票 | 类别 | 连续跌停 | 起始日 |', '|---|---|---|---|']
        for row in block.get('rows', []) + block.get('unknown_rows', []):
            duration = ('' if row['days_exact'] else '至少') + str(row['days']) + '日'
            lines.append(f"| {row['name']} {row['code']} | {'ST' if row['is_st'] else '非ST'} | {duration} | {row['start_date'] or '待核'} |")
        lines += ['', '</details>']
    return lines
