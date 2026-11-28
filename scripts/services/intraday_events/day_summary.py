"""从已核验的采样与投递归档生成本地关键位及提醒回看。"""
from collections import Counter, defaultdict
from datetime import datetime
import fcntl
import json
from typing import NamedTuple, Optional

from .evidence import digest
from services.intraday_summary.state import atomic_write_text
from .analytics import comparable
from .alert_followup import collect_followup_batch, render_alert_followup
from .archive import read_report
from .levels import confirm_crossings, observe_levels
from .sources import anchors_for_level_watch
from .turnover_baseline import crossing_turnover_baselines

SUMMARY_NAME = 'key-levels-summary.md'


class LevelEvent(NamedTuple):
    time: str
    batch: str
    side: str
    amount: Optional[float]
    crossed_at: str


def _turnover_text(event, evidence):
    if event.amount is None:
        return '成交额未计算', '首次穿越区间缺少可核验的成交额。'
    amount = (f'{event.amount/1e8:.2f} 亿' if event.amount >= 1e8 else f'{event.amount/1e4:,.2f} 万')
    current = evidence.get('current') or {}
    ratio, valid = evidence.get('ratio'), evidence.get('valid_days')
    reason = evidence.get('reason') or '区间或历史报价无法核验'
    if current and current.get('amount_yuan') != event.amount:
        current, ratio, reason = {}, None, '确认记录与相邻报价的成交额差不一致'
    if ratio is not None and not current:
        ratio, reason = None, '首次穿越的逐股报价缺失'
    if ratio is None:
        coverage = f'，{valid}/5 日' if valid is not None else ''
        cell = f'{amount}（倍数未计算{coverage}）'
        detail = f'{reason}。'
    else:
        cell = f'{amount}（{ratio:.2f} 倍）'
        detail = f"此前 {valid} 日中位数 {evidence['median_yuan_per_minute']/1e4:,.2f} 万元/分。"
    if current:
        start = f"[{current['start_quote_at'][11:19]}]({current['start_batch']}/report.md)"
        end = f"[{current['end_quote_at'][11:19]}]({current['end_batch']}/report.md)"
        detail = f"报价区间 {start}–{end}，本次 {current['yuan_per_minute']/1e4:,.2f} 万元/分。{detail}"
    days = []
    for sample in evidence.get('samples', []):
        day = sample['date']
        rate = sample.get('yuan_per_minute')
        if rate is None:
            days.append(f"{day} {sample.get('reason', '未计算')}")
        else:
            days.append(f"[{day}](../{day}/{sample['end_batch']}/report.md) {rate/1e4:,.2f} 万元/分")
    return cell, detail + '、'.join(days)


def _read_level_batch(folder, report, receipt):
    snapshot = report.get('snapshot') or {}
    if not snapshot:
        raise ValueError('无有效行情快照')
    if snapshot['interval_minutes'] != 10:
        return None
    watch = json.loads((folder / 'key-levels.json').read_bytes())
    if (watch['report_sha256'] != receipt['sha256'] or watch['captured_at'] != snapshot['captured_at']
            or datetime.fromisoformat(watch['captured_at']).date().isoformat() != folder.parent.name
            or watch['indices_sha256'] != digest(watch['indices'])):
        raise ValueError('关键位归档绑定不符')
    modern = 'level_rows_sha256' in watch
    if modern:
        if watch['level_rows_sha256'] != digest(watch['rows']):
            raise ValueError('关键位明细哈希不符')
        targets = watch['rows']
    else:
        if 'confirmation_distance_pct' in watch or any('confirmation' in row for row in watch['rows']):
            raise ValueError('关键位确认明细缺少哈希')
        # 旧版未存确认结果。仅用事前复盘与已采行情重建，禁止引入新行情。
        targets = anchors_for_level_watch(report['context']).get('key_levels', [])
    quote = {key: snapshot[key] for key in ('trade_date', 'captured_at', 'context_sha256', 'interval_minutes')}
    quote.update(source=snapshot.get('source'), indices=watch['indices'], scheduled_at=report.get('scheduled_at'),
                 batch=folder.name,
                 stocks={r['code']: snapshot['stocks'][r['code']] for r in targets
                         if r['kind'] == 'stock' and r['code'] in snapshot['stocks']})
    return (watch['captured_at'], folder.name, watch, quote, targets, modern)


def write_day_summary(day_root, input_by, *, history_days=None, now=None):
    if not day_root.is_dir():
        raise ValueError('没有该日的本地批次目录')
    # 手动重建只与同日摘要串行，不占实时采样锁，也不会用旧快照覆盖新摘要。
    with (day_root / '.key-levels-summary.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _write_day_summary(day_root, input_by, history_days, now)


def _write_day_summary(day_root, input_by, history_days, now):
    batches, gaps = [], []
    followup_batches, followup_gaps = [], []
    for folder in sorted(day_root.iterdir()):
        if not folder.is_dir():
            continue
        try:
            receipt = json.loads((folder / 'receipt.json').read_bytes())
            report = read_report(folder, receipt)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            gap = f'{folder.name} 主报告未核验（{type(exc).__name__}）'
            gaps.append(gap)
            followup_gaps.append(gap)
            continue
        try:
            followup_batches.append(collect_followup_batch(folder, report, receipt))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            followup_gaps.append(f'{folder.name} 提醒事实未核验（{type(exc).__name__}）')
        try:
            batch = _read_level_batch(folder, report, receipt)
            if batch:
                batches.append(batch)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            gaps.append(f'{folder.name} 未纳入（{type(exc).__name__}）')
    if not batches and not gaps:
        raise ValueError('没有可汇总的 10 分钟采样')
    groups = defaultdict(list)
    compared_batches = Counter()
    names = {}
    windows, confirmed_windows = {}, {}
    previous_quote, previous_rows = None, []
    for at, batch_id, watch, quote, targets, modern in sorted(batches, key=lambda batch: batch[:2]):
        linked = (comparable(previous_quote, quote)
                  and previous_quote['captured_at'] == watch['previous_captured_at'])
        if modern:
            rows = [dict(row) for row in watch['rows']]
            recorded = [row.pop('confirmation', None) for row in rows]
            if linked:
                confirm_crossings(rows, previous_rows, at)
            mismatch = False
            for row, original in zip(rows, recorded):
                if row.get('confirmation') != original:
                    row.pop('confirmation', None)
                    mismatch = True
            if mismatch:
                gaps.append(f"[{at[11:16]}]({batch_id}/key-levels.md) 确认链无法逐批复核，相关记录未计入")
        else:
            rows = observe_levels(previous_quote, quote, {'sources': True, 'key_levels': targets}, linked)['rows']
            confirm_crossings(rows, previous_rows if linked else [], at)
        if linked:
            compared_batches.update({row['kind'] for row in rows if row['status'] == 'compared'})
            windows[at] = previous_quote, quote
        for row in rows:
            state = row.get('confirmation') or {}
            if state.get('status') != 'confirmed':
                continue
            key = row['kind'], row['code'], row['level']
            names[key] = row['name']
            crossed_at = state['crossed_at']
            groups[key].append(LevelEvent(at[11:16], batch_id, row['side'], state['crossing_amount_yuan'], crossed_at))
            if row['kind'] == 'stock' and crossed_at in windows and state['crossing_amount_yuan'] is not None:
                confirmed_windows[row['code'], crossed_at] = windows[crossed_at]
        if watch['status'] != 'complete':
            gaps.append(f"[{at[11:16]}]({batch_id}/key-levels.md) "
                        + {'partial': '仅建基线或部分点位不可比', 'missing_source': '复盘缺源',
                           'not_configured': '没有明示点位'}.get(watch['status'], watch['status']))
        previous_quote, previous_rows = quote, rows

    turnover = crossing_turnover_baselines(day_root, confirmed_windows, history_days=history_days)
    turnover_details = {}
    lines = [f'# {day_root.name} 盘中关键位与提醒回看', '',
             f'已核验 {len(batches)} 批。穿越后至少连续两批在同侧，确认时偏离点位至少 0.2%。']
    for kind, heading in [('stock', '手写个股锚点'), ('index', '宽基指数')]:
        keys = [key for key in groups if key[0] == kind]
        lines.extend(['', f'## {heading}', ''])
        lines.append(f"至少有一个点位可比的批次 {compared_batches[kind]}/{len(batches)}。")
        lines.append('')
        if not keys:
            lines.append('可比批次内无确认记录。' if compared_batches[kind] else '没有可比点位，未计算。')
            continue
        amount_header = '|首次穿越成交额 · 同时段倍数（按确认顺序）' if kind == 'stock' else ''
        lines.extend([f'|对象|关键位|确认情况|确认时间与方向{amount_header}|',
                      '|---|---:|---|---|' + ('---:|' if kind == 'stock' else '')])
        for key in keys:
            events = groups[key]
            counts = Counter('向上' if event.side == 'above' else '向下' for event in events)
            counts_text = '、'.join(f'{label} {n} 次' for label, n in counts.items())
            times = '、'.join(f"[{event.time}{'↑' if event.side == 'above' else '↓'}]({event.batch}/key-levels.md)"
                             for event in events)
            cells = [f'{names[key]} {key[1]}', f'{key[2]:.4f}'.rstrip('0').rstrip('.'),
                     counts_text, times]
            if kind == 'stock':
                amounts = []
                for event in events:
                    evidence = turnover.get((key[1], event.crossed_at)) or {}
                    cell, detail = _turnover_text(event, evidence)
                    amounts.append(cell)
                    turnover_details[names[key], key[1], event.crossed_at] = detail
                cells.append('、'.join(amounts))
            lines.append('|' + '|'.join(cells) + '|')
    if turnover_details:
        lines.extend(['', '<details><summary>成交额倍数依据</summary>', '',
                      '以逐股报价间隔换算每分钟成交额，对照此前五个交易日同股、同源、同一采样时段的中位数。', ''])
        for (name, code, crossed_at), detail in turnover_details.items():
            lines.append(f'- {name} {code}，穿越采样 {crossed_at[11:16]}。{detail}')
        lines.extend(['', '</details>'])
    lines.extend(['', *render_alert_followup(followup_batches, now=now, archive_gaps=followup_gaps)])
    if gaps:
        lines.extend(['', '## 基线与覆盖缺口', '', *('- ' + gap for gap in gaps)])
    lines.extend(['', f'生成请求者 {input_by}。点击确认时间可查看当批行情。'])
    destination = day_root / SUMMARY_NAME
    atomic_write_text(destination, '\n'.join(lines) + '\n')
    return destination
