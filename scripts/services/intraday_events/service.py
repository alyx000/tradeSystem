"""盘中采样与归档；显式启用时按批发送钉钉提醒。"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import uuid

from db.connection import get_readonly_connection
from .evidence import calendar, digest
from services.intraday_monitor.guards import shanghai_now
from services.intraday_summary.state import atomic_write_text
from .alert_followup import accepted_anchor_prices
from .alerts import ANCHOR_COOLDOWN_SECONDS, POSSIBLY_DELIVERED, alert_window, format_alert, in_sampling_session
from .analytics import INTERVAL_SECONDS, analyze, comparable
from .day_summary import SUMMARY_NAME, write_day_summary
from .levels import CONFIRMATION_DISTANCE_PCT, confirm_crossings, observe_levels, render_level_watch
from .sources import anchors_for_level_watch, capture, capture_level_indices, load_context, repair_formatted_anchors

ROOT = Path(__file__).resolve().parents[3] / 'data/reports/intraday-events'


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)+'\n'


def render(payload, *, level_link=True):
    lines = [f"# 盘中异动影子采样 · {payload['trade_date']}",
             f"状态：{payload['status']}；采样：{payload['captured_at']}；请求者：{payload['input_by']}",
             ('本批启用条件提醒，投递结果见同目录 delivery.json。' if payload.get('push_requested')
              else '仅本地采样，未推送。'),
             '板块采用当日首次采样冻结的申万二级行业；不代表题材，也不证明核心带动。']
    if payload.get('error'):
        lines.append('缺口：'+payload['error'])
    for gap in (payload.get('context') or {}).get('gaps', []):
        lines.append('- '+gap)
    analysis = payload.get('analysis') or {}
    history_error = (analysis.get('alert') or {}).get('anchor_history_error')
    if history_error:
        lines.append('- '+history_error)
    if analysis.get('baseline_only'):
        low, high = analysis['rules']['interval_seconds']
        lines.append(f'本轮仅建基线：缺连续{low//60}—{high//60}分钟同日同半场快照，不计算区间事件。')
    if analysis:
        lines.extend(['', '## 本轮候选事件', f"候选 {len(analysis['events'])} 条；不等于全市场无异动。"])
        for event in analysis['events']:
            if event['kind'] == 'sector_cluster':
                m = event['metrics']
                lines.append(f"- {event['sector']}集体{'走强' if event['direction']=='up' else '走弱'}："
                             f"{m['count']}只，区间中位数{m['median_pct']:.2f}%，上涨占比{m['up_ratio_pct']:.1f}%。")
            elif event['kind'] == 'strong_feedback_change':
                lines.append(f"- {event['group']}反馈变化：涨幅中位数变化{event['median_change_pp']:+.2f}个百分点，"
                             f"上涨率变化{event['up_ratio_change_pp']:+.1f}个百分点。")
            elif event['kind'] == 'anchor_move':
                lines.append(f"- 手写锚点 {event['name']}（{event['code']}）：区间变化{event['interval_pct']:+.2f}%，"
                             f"当日{event['pct_chg']:+.2f}%；原文来源{event['source_date']}。")
            else:
                lines.append(f"- {event['name']}（{event['code']}）之后出现同业同向扩散："
                             f"{len(event['followers'])}/{event['expected']}只；仅窗口先后，不认定带动。")
        lines.extend(['', '## 昨日强势股固定样本', '|分组|状态|有效/原样本|涨幅中位数|上涨率|', '|---|---|---|---|---|'])
        for row in analysis['feedback']:
            m = row['metrics']
            fmt = lambda x: '未计算' if x is None else f'{x:.2f}%'
            lines.append(f"|{row['group']}|{row['status']}|{row.get('valid','—')}/{row.get('expected','—')}|{fmt(m['median_pct'])}|{fmt(m['up_ratio_pct'])}|")
        lines.extend(['', '## 核心后续窗口', f"等待下一窗口 {len(analysis['pending_following'])} 只。"])
        for row in analysis['following']:
            lines.append(f"- {row['code']}：{row['conclusion']}（{row['status']}）")
        anchors = (payload.get('context') or {}).get('anchors') or {}
        lines.extend(['', '## 手写固定锚点', anchors.get('scope', '手写锚点来源未提供'),
                      '|对象|复盘来源日|当日涨跌|区间涨跌|状态|', '|---|---|---|---|---|'])
        fmt = lambda x: '未计算' if x is None else f'{x:+.2f}%'
        for row in analysis.get('anchor_watch', []):
            lines.append(f"|{row['name']} {row['code']}|{row['source_date']}|{fmt(row['pct_chg'])}|{fmt(row['interval_pct'])}|{row['status']}|")
        for row in anchors.get('unresolved', []):
            lines.append(f"- 原称「{row['original_name']}」（{row['source_date']}）：{row['reason']}，未自动补代码。")
        for gap in anchors.get('errors', []):
            lines.append('- '+gap)
        lines.append('定性承接、带动及完整原文条件满足状态未自动判定。')
        if level_link and (payload.get('snapshot') or {}).get('interval_minutes') == 10:
            lines.extend(['', '宽基与手写锚点关键位见同批 [key-levels.md](key-levels.md)。'])
            lines.append(f'[当日关键位合并摘要](../{SUMMARY_NAME})')
        lines.extend(['', '缺少实时涨跌停价格/逐笔路径：再涨停、跌停计数及精确开板次数未计算。',
                      '完整成员、快照、行业映射、来源哈希、参数及逐组覆盖见同批 report.json。'])
    return '\n'.join(lines)+'\n'


@contextmanager
def lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root/'sample.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('已有采样运行中') from None
        yield


def load_latest(root, day):
    pointer = root/day/'latest.json'
    if not pointer.exists():
        return None, None
    receipt = json.loads(pointer.read_text())
    batch = receipt['batch']
    if not isinstance(batch, str) or not batch.isalnum():
        raise ValueError('批次指针非法')
    path = root/day/batch/'report.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt['sha256']:
        raise ValueError('最近批次哈希不符，拒绝退回旧结果')
    payload = json.loads(raw)
    if payload['trade_date'] != day:
        raise ValueError('最近批次日期不符')
    return payload, receipt


def persist(root, payload):
    batch = uuid.uuid4().hex
    folder = root/payload['trade_date']/batch
    folder.mkdir(parents=True, mode=0o700)
    content = encode(payload)
    # 先完成不可变批次，再发布指针；失败批次也推进指针，打断后续窗口。
    atomic_write_text(folder/'report.md', render(payload))
    atomic_write_text(folder/'report.json', content)
    receipt = dict(batch=batch, sha256=hashlib.sha256(content.encode()).hexdigest(),
                   input_by=payload['input_by'], trade_date=payload['trade_date'])
    if 'scheduled_at' in payload:
        receipt.update(scheduled_at=payload['scheduled_at'], started_at=payload['started_at'],
                       captured_at=(payload.get('snapshot') or {}).get('captured_at'))
    atomic_write_text(folder/'receipt.json', encode(receipt))
    atomic_write_text(root/payload['trade_date']/'latest.json', encode(receipt))
    return str(folder/'report.md')


def load_anchor_history(day_root, captured, anchor_codes):
    """从同日已归档行情和投递回执重建冷却、此前高低及最后一次同向提醒。"""
    recent_codes, ranges, latest = set(), {}, {}
    accepted = {}
    for path in day_root.glob('*/receipt.json'):
        receipt = json.loads(path.read_bytes())
        raw = path.with_name('report.json').read_bytes()
        if (receipt['batch'] != path.parent.name or receipt['trade_date'] != day_root.name
                or hashlib.sha256(raw).hexdigest() != receipt['sha256']):
            raise ValueError('本日采样回执与归档不一致')
        report = json.loads(raw)
        at = datetime.fromisoformat(report['captured_at'])
        if report['trade_date'] != day_root.name or at.date().isoformat() != day_root.name:
            raise ValueError('本日采样归档日期不一致')
        if at >= captured:
            continue
        stocks = (report.get('snapshot') or {}).get('stocks', {})
        for code in anchor_codes.intersection(stocks):
            price = stocks[code]['price']
            low, high = ranges.get(code, (price, price))
            ranges[code] = (min(low, price), max(high, price))

        delivery_path = path.with_name('delivery.json')
        if not delivery_path.exists():
            continue
        delivery = json.loads(delivery_path.read_bytes())
        if (delivery['batch'] != receipt['batch'] or delivery['trade_date'] != day_root.name
                or delivery['report_sha256'] != receipt['sha256']):
            raise ValueError('本日投递回执与归档不一致')
        status = delivery['status']
        if status in ('quiet', 'no_comparison', 'expired', 'unconfigured'):
            continue
        if status not in POSSIBLY_DELIVERED:
            raise ValueError('本日投递状态无法核验')
        prices = (accepted_anchor_prices(path.parent, report, receipt, delivery, as_of=captured)
                  if status == 'accepted' else None)
        for row in report['analysis']['alert']['anchors']:
            code = row['code']
            if code not in anchor_codes:
                continue
            if (captured - at).total_seconds() < ANCHOR_COOLDOWN_SECONDS:
                recent_codes.add(code)
            key = row['key']
            if key not in latest or at > latest[key][0]:
                latest[key] = (at, row['pct_chg'])
            if prices is not None and (key not in accepted or at > accepted[key][0]):
                # 最近一次已接收但报价不可核验时，保留缺口，不沿用更早的提醒价。
                accepted[key] = (at, prices.get(key))
    last_accepted = {key: dict(captured_at=at.isoformat(), price=price)
                     for key, (at, price) in accepted.items() if price is not None}
    return dict(recent_codes=recent_codes, ranges=ranges, last_pct={key: pct for key, (_, pct) in latest.items()},
                last_accepted=last_accepted)


def _write_level_watch(folder, previous_receipt, previous, payload, registry, clock):
    snapshot = payload['snapshot']
    previous_snapshot = (previous or {}).get('snapshot')
    linked = comparable(previous_snapshot, snapshot)
    anchors = anchors_for_level_watch(payload['context'])
    targets = anchors.get('key_levels', [])
    at = datetime.fromisoformat(snapshot['captured_at'])
    index_data = capture_level_indices(registry, targets, at, clock)
    history_error = None
    confirmation_history_error = None
    previous_rows = []
    if linked and previous_receipt and targets:
        prior_path = folder.parent / previous_receipt['batch'] / 'key-levels.json'
        earlier = None
        try:
            loaded = json.loads(prior_path.read_bytes())
            if (loaded['report_sha256'] != previous_receipt['sha256']
                    or loaded['captured_at'] != previous_snapshot['captured_at']):
                raise ValueError('上一批关键位归档绑定不一致')
            earlier = loaded
        except (ValueError, OSError, KeyError, TypeError):
            confirmation_history_error = '上一批关键位归档无法核验，待确认记录重新开始'
        if index_data['index_source']['expected']:
            try:
                if earlier is None or earlier['indices_sha256'] != digest(earlier['indices']):
                    raise ValueError('上一批宽基证据哈希不一致')
                previous_snapshot = dict(previous_snapshot, indices=earlier['indices'])
            except (ValueError, KeyError, TypeError):
                history_error = '上一批宽基报价没有可核验的关键位归档'
        if earlier is not None and 'level_rows_sha256' in earlier:
            try:
                if earlier['level_rows_sha256'] != digest(earlier['rows']):
                    raise ValueError('上一批关键位明细哈希不一致')
                previous_rows = earlier['rows']
            except (ValueError, KeyError, TypeError):
                confirmation_history_error = '上一批关键位明细无法核验，待确认记录重新开始'
    report_hash = hashlib.sha256((folder/'report.json').read_bytes()).hexdigest()
    current = dict(snapshot, indices=index_data['indices'])
    watch = observe_levels(previous_snapshot, current, anchors, linked)
    confirm_crossings(watch['rows'], previous_rows, snapshot['captured_at'])
    record = dict(**index_data, **watch, report_sha256=report_hash, captured_at=snapshot['captured_at'],
                  previous_captured_at=previous_snapshot['captured_at'] if linked else None,
                  indices_sha256=digest(index_data['indices']), index_history_error=history_error,
                  level_rows_sha256=digest(watch['rows']), confirmation_history_error=confirmation_history_error,
                  confirmation_distance_pct=float(CONFIRMATION_DISTANCE_PCT),
                  level_parser_version=anchors.get('level_parser_version'))
    atomic_write_text(folder/'key-levels.md', render_level_watch(record))
    atomic_write_text(folder/'key-levels.json', encode(record))
    return record['status']


def run(registry, *, input_by, root=ROOT, db_path=None, dry_run=False, clock=shanghai_now,
        interval_minutes=5, push=False, scheduled_at=None):
    if interval_minutes not in INTERVAL_SECONDS:
        raise ValueError('采样间隔须为5或10分钟')
    if push and (dry_run or interval_minutes != 10):
        raise ValueError('推送须使用 10 分钟实采模式')
    actor = str(input_by or '').strip()
    if not actor:
        raise ValueError('必须显式提供非空 --input-by')
    now = clock()
    day = now.date().isoformat()
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None or not push:
            raise ValueError('定时采样须指定带时区的计划时刻并启用正式提醒模式')
        scheduled_at = shanghai_now(scheduled_at)
        slot = alert_window(scheduled_at)
        if (not slot or scheduled_at.weekday() > 4 or scheduled_at.second or scheduled_at.microsecond
                or slot[1] != scheduled_at.hour * 60 + scheduled_at.minute):
            raise ValueError('不是有效的工作日采样计划时点')
        if not 0 <= (now - scheduled_at).total_seconds() < 180:
            return dict(status='skipped', reason_code='outside_scheduled_slot', pushed=False)
    if not in_sampling_session(now) or (push and alert_window(now) is None):
        return dict(status='skipped', reason_code='outside_session', reason='非上海连续交易时段', pushed=False)
    try:
        conn = get_readonly_connection(db_path)
    except Exception:
        return dict(status='source_failed', reason='只读数据库不可用', pushed=False)
    try:
        cal = calendar(conn, day)
        if not cal['is_open']:
            return dict(status='skipped', reason_code='non_trade_day', reason='非开放日', pushed=False)
        if dry_run:
            return _run(registry, conn, cal, actor, Path(root), now, clock, True, interval_minutes)
        with lock(Path(root)):
            return _run(registry, conn, cal, actor, Path(root), now, clock, False, interval_minutes, push, scheduled_at)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return dict(status='source_failed', reason=str(exc), pushed=False)
    finally:
        conn.close()


def _run(registry, conn, cal, actor, root, now, clock, dry_run, interval_minutes, push=False, scheduled_at=None):
    day = now.date().isoformat()
    previous, previous_receipt = load_latest(root, day)
    if previous:
        prior_slot = previous.get('scheduled_at') or previous['captured_at']
        if push and previous.get('push_requested') and alert_window(now) == alert_window(datetime.fromisoformat(prior_slot)):
            return dict(status='skipped', reason_code='duplicate_slot', pushed=False)
        elapsed = (now-datetime.fromisoformat(previous['captured_at'])).total_seconds()
        if elapsed < 240:
            return dict(status='skipped', reason_code='too_soon', retry_after_seconds=240-elapsed,
                        reason='距上次采样不足4分钟', pushed=False)
    payload = dict(schema='intraday-events-v1', trade_date=day, captured_at=now.isoformat(),
                   input_by=actor, push_requested=push, pushed=None if push else False, status='source_failed', context=None,
                   snapshot=None, analysis=None)
    if scheduled_at is not None:
        payload.update(scheduled_at=scheduled_at.isoformat(), started_at=now.isoformat())
    history_error = None
    try:
        context = previous.get('context') if previous else None
        if not context:
            context = load_context(registry, conn, day, cal, now)
        else:
            context = repair_formatted_anchors(context)
        payload['context'] = context
        snapshot = capture(registry, context, now, clock=clock)
        snapshot['interval_minutes'] = interval_minutes
        captured = datetime.fromisoformat(snapshot['captured_at'])
        if captured.date().isoformat() != day or not in_sampling_session(captured):
            raise ValueError('采集完成已越过交易时段，不能作为盘中快照')
        if scheduled_at is not None and alert_window(captured) != alert_window(scheduled_at):
            raise ValueError('采样完成已越过计划槽位')
        payload['captured_at'] = snapshot['captured_at']
        payload['snapshot'] = snapshot
        prev_analysis = (previous or {}).get('analysis') or {}
        anchor_history = None
        anchors = context.get('anchors') or {}
        anchor_codes = {row['code'] for row in anchors.get('stocks', [])}
        if push and anchor_codes:
            try:
                anchor_history = load_anchor_history(root/day, captured, anchor_codes)
            except (ValueError, OSError, KeyError, TypeError):
                history_error = '本日采样或投递回执未能核验，本批暂停手写锚点提醒'
        analysis = analyze((previous or {}).get('snapshot'), snapshot, context,
                           prev_analysis.get('pending_following'), prev_analysis.get('active'),
                           (prev_analysis.get('alert') or {}).get('sector_state'),
                           previous_anchor_watch=prev_analysis.get('anchor_watch', ()),
                           anchor_history=anchor_history)
        if history_error:
            analysis['alert'].update(anchors=[], anchor_history_error=history_error)
        payload['analysis'] = analysis
        partial = (history_error or context['gaps'] or snapshot['rejected'] or analysis['baseline_only'] or
                   any(s['status']=='partial' for s in analysis['sectors']) or
                   any(s['status'] in ('partial','source_failed') for s in analysis['feedback']) or
                   any(s['status']=='partial' for s in analysis['anchor_watch']) or
                   any(s['status']=='partial' for s in analysis['following']))
        payload['status'] = 'partial' if partial else 'complete'
    except Exception as exc:
        # 不保留失败采集为连续基线；不输出异常中的凭据/上游地址。
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        payload.update(status='source_failed', error=f'采样失败：{reason}', snapshot=None, analysis=None)
    path = None if dry_run else persist(root, payload)
    delivery = _deliver(Path(path).parent, payload, clock) if push else None
    level_status = None
    summary_path, summary_error = None, None
    if path and interval_minutes == 10 and payload['snapshot']:
        try:
            level_status = _write_level_watch(Path(path).parent, previous_receipt, previous, payload, registry, clock)
        except (ValueError, OSError, KeyError, TypeError):
            level_status = 'source_failed'
    if path and interval_minutes == 10:
        try:
            summary_path = str(write_day_summary(root/day, actor, history_days=cal.get('history', ()), now=clock()))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            summary_error = f'本地关键位合并摘要生成失败（{type(exc).__name__}）'
    return dict(status=payload['status'], pushed=delivery == 'accepted', delivery=delivery, dry_run=dry_run,
                report_path=path, level_watch_status=level_status, level_summary_path=summary_path,
                event_count=len((payload['analysis'] or {}).get('events', [])) if payload['analysis'] else None,
                baseline_only=(payload['analysis'] or {}).get('baseline_only'),
                gaps=[*(payload['context'] or {}).get('gaps', []),
                      *([history_error] if history_error else []), *([summary_error] if summary_error else [])],
                error=payload.get('error'),
                markdown=render(payload, level_link=False) if dry_run else None)


def _deliver(folder, payload, clock):
    from pushers.dingtalk_pusher import DingTalkPusher

    path = folder/'delivery.json'
    if path.exists():
        return json.loads(path.read_bytes())['status']
    batch_receipt = json.loads((folder/'receipt.json').read_bytes())
    archived = (folder/'report.json').read_bytes()
    if (batch_receipt['batch'] != folder.name or batch_receipt['trade_date'] != payload['trade_date']
            or batch_receipt['input_by'] != payload['input_by']
            or hashlib.sha256(archived).hexdigest() != batch_receipt['sha256']):
        raise ValueError('本批归档与采样回执不一致，停止投递')
    alert = (payload['analysis'] or {}).get('alert') or {}
    receipt = dict(batch=folder.name, trade_date=payload['trade_date'], input_by=payload['input_by'],
                   report_sha256=batch_receipt['sha256'])

    def record(status):
        receipt.update(status=status, recorded_at=clock().isoformat())
        atomic_write_text(path, encode(receipt))
        return status

    if not (alert.get('anchors') or alert.get('sectors')):
        return record('no_comparison' if not payload['analysis'] or payload['analysis']['baseline_only'] else 'quiet')
    captured = datetime.fromisoformat(payload['captured_at'])
    now = clock()
    if alert_window(now) is None or alert_window(now) != alert_window(captured):
        return record('expired')
    title, content = format_alert(payload['captured_at'], alert)
    sender = DingTalkPusher()
    if not sender.initialize():
        return record('unconfigured')
    receipt.update(title=title, content=content)
    record('sending')
    try:
        accepted = sender.send_markdown(title, content)
    except Exception:
        accepted = False
    return record('accepted' if accepted else 'unconfirmed')
