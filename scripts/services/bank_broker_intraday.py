"""从同日冻结的申万二级名单与签收报价复算银行、证券的盘中对照。"""
from collections import defaultdict
from datetime import datetime
from html import escape
import json
from pathlib import Path
from statistics import median

from analyzers.strong_stock_feedback import _code
from services.board_break_intraday.data import TZ, calendar, digest, number
from services.board_break_intraday.store import lock
from services.intraday_events.archive import read_report
from services.intraday_events.sources import eligible
from services.intraday_events.turnover_baseline import _slot, _timestamp
from services.intraday_summary.state import atomic_write_text
from services.theme_intraday_breadth import ERRORS, SLOTS, _compact

SCHEMA = 'bank-broker-intraday-v1'
FOLDER = 'bank-broker-intraday'
BANK = ('国有大型银行Ⅱ', '股份制银行Ⅱ', '城商行Ⅱ', '农商行Ⅱ')
BROKER = ('证券Ⅱ',)
GROUPS = {'bank': BANK, 'broker': BROKER}


def _hhmm(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _membership(ctx, captured_at, day):
    frozen = _timestamp(ctx['frozen_at'])
    if ctx['trade_date'] != day or frozen.date().isoformat() != day or frozen > _timestamp(captured_at):
        raise ValueError('行业名单的冻结时间或事实日不符')
    source = ctx['sources']['industry']
    raw = source['mapping']
    identity = ctx['sources']['universe']
    if (source['source'] != 'tushare:index_member_all' or source['sha256'] != digest(raw)
            or not identity['source'] or identity['sha256'] != digest(identity['rows'])):
        raise ValueError('行业或股票名单来源未核验')
    identities = [(_code(r.get('ts_code') or r.get('code')), r['name']) for r in identity['rows']]
    if len({c for c, _ in identities}) != len(identities):
        raise ValueError('原始名单证券身份重复')
    universe = {code for code, name in identities if eligible(code, name)}
    mapping = ctx['industry_map']
    if (not universe or universe != set(ctx['universe']) or universe != set(mapping)
            or len(ctx['universe']) != len(universe)):
        raise ValueError('冻结股票范围与行业名单不一致')
    labels_by_code = {}
    for raw_code, raw_row in raw.items():
        try:
            code = _code(raw_code)
        except ValueError:
            continue  # 原始行业目录还包含退市等观察范围外的代码
        if code in universe:
            if code in labels_by_code:
                raise ValueError('原始行业证券身份重复')
            labels_by_code[code] = raw_row.get('sw_l2') if isinstance(raw_row, dict) else raw_row
    if labels_by_code != mapping or any(not label or label in ('未分类', 'UNCLASSIFIED') for label in mapping.values()):
        raise ValueError('冻结行业与原始映射不一致')
    labels = {label: sorted(c for c, ind in mapping.items() if ind == label) for label in BANK + BROKER}
    return dict(frozen_at=frozen.isoformat(), source=source['source'], source_sha256=source['sha256'],
                universe_source=identity['source'], universe_sha256=identity['sha256'], members_by_industry=labels)


def group_stats(names, labels, prices):
    codes = sorted(c for label in names for c in labels[label])
    rows = {c: prices[c] for c in codes if c in prices}
    vals = [r['pct_chg'] for r in rows.values()]
    missing_industries = [label for label in names if not labels[label]]
    complete = not missing_industries and len(rows) == len(codes)
    return dict(status='complete' if complete else 'partial', valid=len(rows),
                expected=None if missing_industries else len(codes), missing_industries=missing_industries,
                missing_codes=sorted(set(codes) - rows.keys()), quotes=rows,
                up=sum(v > 0 for v in vals) if vals else None,
                median_pct=round(median(vals), 5) if vals else None)


def _point(report, receipt, day):
    selection = _membership(report['context'], report['captured_at'], day)
    labels = selection['members_by_industry']
    wanted = {c for group in labels.values() for c in group}
    slot, sample = _compact(report, receipt, wanted, day)
    prices = {}
    stored = report['snapshot']['stocks']
    for code, q in sample['stocks'].items():
        # 以原价/昨收复算源百分比；行情可能只给两位小数。
        pct = number(stored[code]['pct_chg'])
        calculated = (q['price'] / q['pre_close'] - 1) * 100
        if abs(calculated - pct) <= 0.00501:
            prices[code] = dict(price=q['price'], pre_close=q['pre_close'], quote_at=q['quote_at'],
                                pct_chg=round(calculated, 4))
    stats = {key: group_stats(names, labels, prices) for key, names in GROUPS.items()}
    complete = all(v['status'] == 'complete' for v in stats.values())
    spread = (round(stats['broker']['median_pct'] - stats['bank']['median_pct'], 5) if complete else None)
    value = dict(slot=_hhmm(slot), status='complete' if complete else 'partial', captured_at=sample['captured_at'],
                 scheduled_at=sample.get('scheduled_at'), source=sample['source'], batch=receipt['batch'],
                 report_sha256=receipt['sha256'], context_sha256=sample['context_sha256'],
                 broker_minus_bank_pp=spread, **stats)
    return selection, value


def build(conn, root, day, actor, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    result = dict(schema=SCHEMA, trade_date=day, input_by=actor, status='source_failed',
                  selection=None, points=[], complete_slots=0, observed_slots=0, gaps=[])
    if day > now.date().isoformat() or (day == now.date().isoformat() and now.hour < 15) or not calendar(conn, day)['is_open']:
        return dict(result, status='skipped', gaps=['休市或目标日尚未收盘'])
    slots = defaultdict(list)
    selections = {}
    for path in sorted(p for p in (root / 'intraday-events' / day).glob('*') if p.is_dir()):
        slot = None
        try:
            receipt = json.loads((path / 'receipt.json').read_bytes())
            report = read_report(path, receipt)
            if (report.get('snapshot') or {}).get('interval_minutes') == 5:
                continue
            slot = _slot(dict(trade_date=day, captured_at=report['captured_at'], scheduled_at=report.get('scheduled_at')))
            selection, row = _point(report, receipt, day)
            key = digest(selection)
            selections[key] = selection
            slots[slot].append(row)
        except ERRORS as exc:
            result['gaps'].append(f'{path.name} 回执或行情未通过核验：{exc}')
            if slot is not None:
                slots[slot].append(None)
    if len(selections) == 1:
        result['selection'] = next(iter(selections.values()))
    elif len(selections) > 1:
        result['gaps'].append('同日多个批次的冻结行业或成分来源冲突')
    for slot in SLOTS:
        attempts = slots[slot]
        if result['selection'] and len(attempts) == 1 and attempts[0]:
            point = attempts[0]
            result['observed_slots'] += 1
            result['complete_slots'] += int(point['status'] == 'complete')
        else:
            reason = '槽位无记录' if not attempts else '重复批次' if len(attempts) > 1 else '来源或冻结名单未核验'
            point = dict(slot=_hhmm(slot), status='missing', gap=reason, broker_minus_bank_pp=None)
        result['points'].append(point)
    if result['observed_slots']:
        result['status'] = 'complete' if result['complete_slots'] == len(SLOTS) and not result['gaps'] else 'partial'
    return result


def _verify_points(obj, day):
    points = obj['points']
    if [p['slot'] for p in points] != [_hhmm(s) for s in SLOTS]:
        raise ValueError('日档时槽缺失或重复')
    ready = [p for p in points if p['status'] != 'missing']
    complete = 0
    for p in ready:
        captured = _timestamp(p['captured_at'])
        if _hhmm(_slot(dict(trade_date=day, captured_at=p['captured_at'], scheduled_at=p['scheduled_at']))) != p['slot']:
            raise ValueError('日档时槽与采样时间错位')
        labels = obj['selection']['members_by_industry']
        for group, industries in GROUPS.items():
            stats = p[group]
            quotes = stats['quotes']
            for q in quotes.values():
                stamp = _timestamp(q['quote_at'])
                change = 100 * (number(q['price'], positive=True) / number(q['pre_close'], positive=True) - 1)
                if (stamp.date() != captured.date() or not 0 <= (captured - stamp).total_seconds() <= 90
                        or round(change, 4) != q['pct_chg']):
                    raise ValueError('日档逐股价格或报价时刻不符')
            if group_stats(industries, labels, quotes) != stats:
                raise ValueError('日档统计与冻结成员及逐股行情不符')
        both = all(p[group]['status'] == 'complete' for group in GROUPS)
        spread = round(p['broker']['median_pct'] - p['bank']['median_pct'], 5) if both else None
        if p['status'] != ('complete' if both else 'partial') or p['broker_minus_bank_pp'] != spread:
            raise ValueError('日档覆盖与两组中位数之差不符')
        complete += int(both)
    expected_status = 'source_failed' if not ready else 'complete' if complete == len(SLOTS) and not obj['gaps'] else 'partial'
    if obj['observed_slots'] != len(ready) or obj['complete_slots'] != complete or obj['status'] != expected_status:
        raise ValueError('日档整体覆盖与逐槽明细不符')


def _read(path, day):
    obj = json.loads(path.read_bytes())
    checksum = obj.pop('sha256')
    if obj.get('schema') != SCHEMA or obj.get('trade_date') != day or checksum != digest(obj):
        raise ValueError('银行与证券日档日期、版本或哈希未通过核验')
    _verify_points(obj, day)
    return obj


def _evidence(report):
    return {(p['slot'], p['report_sha256']) for p in report['points'] if p.get('report_sha256')}


def save(folder, result):
    path = folder / (result['trade_date'] + '.json')
    with lock(folder, result['trade_date']):
        state = 'saved'
        if path.exists():
            previous = _read(path, result['trade_date'])
            if not _evidence(previous) <= _evidence(result):
                failure = dict(status='fallback_preserved', input_by=result['input_by'], trade_date=result['trade_date'],
                               archived_sha256=digest(previous), attempted_sha256=digest(result), gaps=result['gaps'])
                atomic_write_text(path.with_suffix('.last-attempt.json'), json.dumps(failure, ensure_ascii=False)+'\n')
                state = 'fallback_preserved'
        if state == 'saved':
            signed = dict(result, sha256=digest(result))
            atomic_write_text(path, json.dumps(signed, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
            path.with_suffix('.last-attempt.json').unlink(missing_ok=True)
        canonical = _read(path, result['trade_date'])
        if state == 'fallback_preserved':
            canonical['latest_attempt'] = state
        atomic_write_text(path.with_suffix('.html'), standalone(canonical))
        return state


def load_review(reports, day):
    path = reports / FOLDER / (day + '.json')
    try:
        obj = _read(path, day)
        last_path = path.with_suffix('.last-attempt.json')
        if last_path.is_file():
            last = json.loads(last_path.read_bytes())
            if last.get('status') != 'fallback_preserved' or last.get('archived_sha256') != digest(obj):
                raise ValueError('重试回执与保留的日档不一致')
            obj['latest_attempt'] = 'fallback_preserved'
        return obj
    except ERRORS as exc:
        reason = '尚无同日日档' if isinstance(exc, FileNotFoundError) else '日档读入或哈希核验失败'
        return dict(trade_date=day, status='source_failed', points=[], gaps=[reason])


def _shown(v, suffix=''):
    return '待核' if v is None else f'{v:+.2f}{suffix}'


def render(report, day, *, expanded=False):
    if report is None:
        return '', []
    title = '银行与证券盘中强弱'
    heading = f'<h1>{title}</h1>' if expanded else f'<h4>{title}</h4>'
    gaps = report.get('gaps') or []
    points = report.get('points') if report.get('trade_date') == day else None
    if not points:
        reason = escape('、'.join(gaps) or '缺少同日可用批次')
        return f'<div data-bank-broker-intraday="v1">{heading}<p>{escape(day)} {reason}。</p></div>', [title+'来源待核']
    html = [f'<div data-bank-broker-intraday="v1">{heading}']
    latest = next((p for p in reversed(points) if 'captured_at' in p), None)
    if latest:
        at = _timestamp(latest['captured_at']).strftime('%H:%M:%S')
        def brief(stats):
            n = stats['up'] if stats['up'] is not None else '待核'
            expected = stats['expected'] if stats['expected'] is not None else '待核'
            return f"{_shown(stats['median_pct'], '%')}、上涨 {n} 只（覆盖 {stats['valid']}/{expected}）"
        html.append(f"<p>[事实] {escape(day)} {at}，银行 {brief(latest['bank'])}，证券 {brief(latest['broker'])}。"
                    f"证券减银行的中位涨幅差 {_shown(latest['broker_minus_bank_pp'], ' 个百分点')}。</p>")
    open_attr = ' open' if expanded else ''
    html.append(f'<details class="evidence" data-as-of="{escape(day)}" data-items="{len(points)}"{open_attr}>'
                f"<summary>等权样本的 24 个采样时槽，完整 {report['complete_slots']}/24</summary>"
                "<p>按当日冻结的申万二级成分分组；逐股与昨收比较。上涨家数及中位数按有效报价计算，两组均完整才比较差值。"
                "时间为上海时间，实际报价时刻可查逐股日档。</p><table><thead><tr><th>时槽 / 实采</th>"
                "<th>银行中位数 / 上涨 / 覆盖</th><th>证券中位数 / 上涨 / 覆盖</th><th>证券减银行（百分点）</th></tr></thead><tbody>")
    for p in points:
        label = escape(p['slot'])
        if p.get('status') == 'missing':
            html.append(f'<tr><td>{label}</td><td colspan="3">{escape(p["gap"])}</td></tr>')
            continue
        actual = _timestamp(p['captured_at']).strftime('%H:%M:%S')
        cells = []
        for key in GROUPS:
            s = p[key]
            up = s['up'] if s['up'] is not None else '待核'
            expected = s['expected'] if s['expected'] is not None else '待核'
            cells.append(f"<td>{_shown(s['median_pct'], '%')} / {up} / {s['valid']}/{expected}</td>")
        html.append(f'<tr><td>{label} / {actual}</td>{"".join(cells)}<td>{_shown(p["broker_minus_bank_pp"])}</td></tr>')
    html.append('</tbody></table></details>')
    warnings = [] if report['status'] == 'complete' else [title+'存在缺槽或未核验样本']
    if report.get('latest_attempt') == 'fallback_preserved':
        warnings.append('银行与证券盘中对照最近复算缺少原始证据，当前展示已核验日档')
        html.append(f'<p>{warnings[-1]}。</p>')
    html.append('</div>')
    return ''.join(html), warnings


def standalone(report):
    day = report['trade_date']
    fragment, _ = render(report, day, expanded=True)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(day)} 银行与证券盘中强弱</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f5;color:#162b29;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1024px;margin:32px auto;padding:24px clamp(14px,4vw,44px);background:white;border-top:4px solid #266356}}
header{{display:flex;justify-content:space-between;gap:14px;align-items:center;color:#375850;font-size:.9rem}}
a{{color:#14594a}}h1{{line-height:1.3;margin:.6em 0;font-size:clamp(1.55rem,4vw,2.2rem)}}
summary{{cursor:pointer;font-weight:650}}details{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.91rem}}
thead{{background:#e8f1ed}}th,td{{padding:8px;text-align:right;border-bottom:1px solid #d8e3df;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}tbody tr:nth-child(even){{background:#f8faf9}}
</style></head><body><main><header><time datetime="{escape(day)}">{escape(day)}</time>
<a href="{escape(day)}.json">逐股原始依据与回执</a></header>{fragment}</main></body></html>
"""
