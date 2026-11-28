"""用实发回执和紧邻三批报价，在本地回验个股提醒。"""
from collections import Counter
from datetime import datetime, timedelta
import json
from math import isclose
from sys import float_info

from services.intraday_monitor.guards import shanghai_now
from .alerts import alert_window
from .analytics import comparable, INTERVAL_SECONDS
from .archive import verify_report_identity


OUTCOMES = ('延续', '部分回吐', '完全回吐', '持平', '待下一批', '不可比')


def _finite_number(value):
    return type(value) in (int, float) and -float_info.max <= value <= float_info.max


def collect_followup_batch(folder, report, receipt, *, delivery=None):
    """调用方已核验 report.json 原始字节 SHA。坏投递回执只影响入选，不影响该批报价。"""
    snapshot = report.get('snapshot') or {}
    analysis = report.get('analysis') or {}
    context = report.get('context') or {}
    if not all(isinstance(obj, dict) for obj in (snapshot, analysis, context)):
        raise ValueError('提醒回验的采样结构不符')
    alert = analysis.get('alert') or {}
    anchors = context.get('anchors') or {}
    stamps = snapshot.get('stocks') or {}
    if not all(isinstance(obj, dict) for obj in (alert, anchors, stamps)):
        raise ValueError('提醒回验的标的或行情结构不符')
    members = anchors.get('stocks', [])
    codes = {item['code'] for item in members if isinstance(item, dict) and isinstance(item.get('code'), str)}
    captured = report['captured_at']
    day = folder.parent.name
    stamp = datetime.fromisoformat(captured)
    if (stamp.tzinfo is None or shanghai_now(stamp).date().isoformat() != day
            or receipt['batch'] != folder.name or report['trade_date'] != day
            or receipt['trade_date'] != day or receipt['input_by'] != report['input_by']):
        raise ValueError('提醒回验的采样身份或日期不符')
    if snapshot and (snapshot['captured_at'] != captured or snapshot['trade_date'] != day):
        raise ValueError('提醒回验的行情时刻或日期不符')

    batch = dict(trade_date=day, batch=folder.name, captured_at=captured,
                 interval_minutes=snapshot.get('interval_minutes'), context_sha256=snapshot.get('context_sha256'),
                 slot=alert_window(stamp),
                 source=snapshot.get('source'), previous_at=alert.get('window_start_at'),
                 quotes={code: (stamps[code].get('price'), stamps[code].get('quote_at'))
                         for code in codes if isinstance(stamps.get(code), dict)},
                 alerts=[], delivery_gap=None)
    if not report.get('push_requested'):
        return batch
    try:
        if delivery is None:
            delivery = json.loads((folder / 'delivery.json').read_bytes())
        if (delivery['report_sha256'] != receipt['sha256'] or delivery['batch'] != folder.name
                or delivery['trade_date'] != day or delivery['input_by'] != report['input_by']):
            raise ValueError('投递回执绑定不符')
        status = delivery['status']
        if status in ('quiet', 'no_comparison', 'expired', 'unconfigured'):
            return batch
        if status != 'accepted':
            batch['delivery_gap'] = '投递未确认'
            return batch
        content = delivery['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('已接收回执缺少正文')
        if not isinstance(alert.get('anchors'), list):
            raise ValueError('个股提醒名单不完整')
        for item in alert['anchors']:
            if not isinstance(item, dict):
                batch['delivery_gap'] = '个股提醒字段不完整'
                continue
            pct = item.get('interval_pct')
            price = item.get('price')
            name, code = item.get('name'), item.get('code')
            if (not _finite_number(pct) or not _finite_number(price) or price <= 0
                    or item.get('direction') not in ('up', 'down')
                    or not isinstance(name, str) or not name.strip()
                    or not isinstance(code, str) or not code.strip()
                    or (pct <= 0 if item['direction'] == 'up' else pct >= 0)):
                batch['delivery_gap'] = '个股提醒字段不完整'
                continue
            if f"{name}（{code}）" not in content:
                batch['delivery_gap'] = '部分个股与实发正文无法对应'
                continue
            batch['alerts'].append({k: item[k] for k in ('name', 'code', 'direction', 'interval_pct', 'price')})
    except (OSError, ValueError, KeyError, TypeError):
        batch['delivery_gap'] = '投递回执缺失或未通过核验'
    return batch


def _linked(before, after):
    return (before is not None and after is not None
            and before['interval_minutes'] == after['interval_minutes'] == 10
            and before['source'] and before['source'] == after['source']
            and before['context_sha256'] and after['previous_at'] == before['captured_at']
            and before['slot'] and after['slot'] == (before['slot'][0], before['slot'][1] + 10)
            and comparable(before, after))


def _quote(batch, code):
    price, quoted_at = batch['quotes'][code]
    if not _finite_number(price) or price <= 0:
        raise ValueError('无有效价格')
    quoted, captured = datetime.fromisoformat(quoted_at), datetime.fromisoformat(batch['captured_at'])
    if (quoted.tzinfo is None or shanghai_now(quoted).date().isoformat() != batch['trade_date']
            or not 0 <= (captured - quoted).total_seconds() <= 90):
        raise ValueError('未取得当批有效报价')
    return price, quoted


def accepted_anchor_prices(folder, report, receipt, delivery, *, as_of):
    """提取已接收且能与当批原始报价核对的提醒价。None 表示回执在 as_of 之后。"""
    prices = {}
    try:
        verify_report_identity(report, receipt, folder.parent.name)
        received = datetime.fromisoformat(delivery['recorded_at'])
        if received > as_of:
            return None
        if received < datetime.fromisoformat(report['captured_at']) or delivery['status'] != 'accepted':
            return prices
        batch = collect_followup_batch(folder, report, receipt, delivery=delivery)
    except (KeyError, ValueError, TypeError, OverflowError):
        return prices
    for alert in batch['alerts']:
        code = alert['code']
        # 短报逐股标题具有完整边界，避免“甲（代码）”意外匹配“新甲（代码）”。
        if f"**{alert['name']}（{code}）**" not in delivery['content']:
            continue
        try:
            price, _ = _quote(batch, code)
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if price == alert['price']:
            prices[f"anchor:{code}:{alert['direction']}"] = price
    return prices


def _classify(prior, trigger, after, alert, now):
    if not _linked(prior, trigger):
        return '不可比', None, '提醒起点无法核验'
    try:
        code = alert['code']
        start_price, start_at = _quote(prior, code)
        price, at = _quote(trigger, code)
        sign = {'up': 1, 'down': -1}[alert['direction']]
        if (price != alert['price'] or start_at >= at or sign * (price - start_price) <= 0
                or not isclose((price / start_price - 1) * 100, alert['interval_pct'], abs_tol=1e-8)):
            return '不可比', None, '提醒区间无法核验'
        if after is None:
            captured = datetime.fromisoformat(trigger['captured_at'])
            slot = alert_window(captured)
            if slot and slot[1] in (11 * 60 + 22, 14 * 60 + 52):
                return '不可比', None, '当半场末批'
            if now > captured + timedelta(seconds=INTERVAL_SECONDS[10][1]):
                return '不可比', None, '下一批归档缺失'
            return '待下一批', None, ''
        if not _linked(trigger, after):
            return '不可比', None, '下一批断档、换源或跨时段'
        next_price, next_at = _quote(after, code)
        if next_at <= at:
            return '不可比', None, '下一批报价时间未前进'
    except (KeyError, TypeError, ValueError, OverflowError):
        return '不可比', None, '个股行情或提醒字段缺失'
    pct = (next_price / price - 1) * 100
    if next_price == price:
        return '持平', pct, ''
    if sign * (next_price - price) > 0:
        return '延续', pct, ''
    if sign * (next_price - start_price) <= 0:
        return '完全回吐', pct, ''
    return '部分回吐', pct, ''


def render_alert_followup(batches, *, now=None, archive_gaps=()):
    ordered = sorted(batches, key=lambda batch: (datetime.fromisoformat(batch['captured_at']), batch['batch']))
    now = shanghai_now(now)
    gaps = [f"{b['captured_at'][11:16]} {b['delivery_gap']}" for b in ordered if b['delivery_gap']]
    lines = ['', '## 已发个股提醒的下一批表现', '']
    details = []
    counts = Counter()
    for i, batch in enumerate(ordered):
        previous = ordered[i - 1] if i else None
        following = ordered[i + 1] if i + 1 < len(ordered) else None
        for alert in batch['alerts']:
            outcome, change, reason = _classify(previous, batch, following, alert, now)
            counts[outcome] += 1
            trigger_link = f"[{batch['captured_at'][11:16]}]({batch['batch']}/delivery.json)"
            next_link = (f"[{following['captured_at'][11:16]}]({following['batch']}/report.md)"
                         if following else '—')
            result = f'{outcome}（{reason}）' if reason else outcome
            pct = f'{change:+.2f}%' if change is not None else '—'
            details.append(f"|{trigger_link}|{alert['name']} {alert['code']}|{alert['interval_pct']:+.2f}%|"
                           f"{next_link}|{pct}|{result}|")
    if details:
        labels = '、'.join(f'{label} {counts[label]}' for label in OUTCOMES if counts[label])
        lines.append(f'按每次收到的个股提醒计，共核实 {len(details)} 条。{labels}。')
        lines.extend(['', '<details>', '<summary>逐条查看提醒与下一批报价</summary>', '',
                      '|提醒时间|个股|提醒区间涨跌|下一批|下一批涨跌|表现|',
                      '|---|---|---:|---|---:|---|', *details, '',
                      '完全回吐表示下一批报价已回到或越过提醒区间的起点。', '', '</details>'])
    elif not ordered or gaps or archive_gaps:
        lines.append('可核验资料中未找到已接收的个股提醒，覆盖不完整。')
    else:
        lines.append('归档回执中没有已接收的个股提醒。')
    if archive_gaps:
        lines.append(f'有 {len(archive_gaps)} 批采样归档无法核验。')
    if gaps:
        lines.extend(['', *('- ' + gap for gap in gaps)])
    return lines
