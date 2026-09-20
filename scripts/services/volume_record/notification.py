"""仅推送已归档的天量报告；成功分片留回执，重复运行不重复发送。"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import service
from .history import day, fingerprint, metric_spec


def report_path(target, metric, root):
    target = day(target)
    folder = {'both': 'dual', 'volume': '', 'amount': 'amount'}[metric]
    return Path(root) / 'data/reports/volume-record' / folder / f'{target}.json'


def read_report(target, metric, root=service.ROOT):
    payload = json.loads(report_path(target, metric, root).read_text())
    digest = payload.pop('sha256')
    expected = 'lifetime-raw-dual-v1' if metric == 'both' else metric_spec(metric)['version']
    if (payload.get('date') != target or payload.get('version') != expected
            or fingerprint(payload) != digest or payload.get('status') not in ('complete', 'partial')):
        raise ValueError('日报校验失败或没有可发布的采集结果')
    return {**payload, 'sha256': digest}


def render_push(report):
    """钉钉只展示命中名单和关键数字；逐股缺口与报错保留在本地报告。"""
    metrics = report.get('metrics') or {report.get('metric', 'volume'): report}
    lines = [f"## 历史天量 · {report['date']}", '']
    for metric, part in metrics.items():
        spec = metric_spec(metric)
        n = part['matched_count']
        lines += [f"### {spec['label']}创历史新高（{'暂未完成核验' if n is None else str(n) + '只'}）", '']
        for row in part['stocks']:
            scale, unit = (100000, '亿元') if metric == 'amount' else (10000, '万手')
            lines += [f"- **{row['name']}**（{row['code'].split('.')[0]}）："
                      f"{row[spec['value']] / scale:,.2f}{unit}，原纪录的 **{row['multiple']:.2f}倍**"]
        lines.append('')
    if 'both_matched_count' in report:
        n = report['both_matched_count']
        names = '、'.join(x['name'] for x in report.get('both_records', []))
        label = '暂未完成核验' if n is None else (names or '无')
        lines += [f"**两项同时创新高：{label}**", '']
    if report['status'] != 'complete' or any(p['status'] != 'complete' for p in metrics.values()):
        lines += ['注：部分个股历史数据尚未核验，以上为已核验名单。']
    return '\n'.join(lines) + '\n'


def split_content(content, limit=15000):
    # 以UTF-8字节保守分片，连超长单行也不截断；完整顺序可还原。
    chunks, current, size = [], [], 0
    for char in content:
        width = len(char.encode('utf-8'))
        if size + width > limit:
            chunks.append(''.join(current)); current, size = [], 0
        current.append(char); size += width
    if current:
        chunks.append(''.join(current))
    return chunks


def push_saved(target, input_by, *, metric='both', root=service.ROOT, expected_sha=None, pusher=None):
    receipt = dict(status='failed', sent=False, date=target, metric=metric, input_by=input_by)
    try:
        if not input_by or not input_by.strip():
            raise ValueError('推送请求者缺失')
        target = day(target)
        report = read_report(target, metric, root)
        if expected_sha is not None and report['sha256'] != expected_sha:
            raise ValueError('归档已变化，拒绝推送与本次采集不一致的报告')
        content = render_push(report)
        digest = hashlib.sha256(content.encode()).hexdigest()
        chunks = split_content(content)
        directory = Path(root) / 'data/reports/volume-record/dingtalk'
        path = directory / f'{target}-{metric}-{digest}.json'
        with service.run_lock(directory, False):
            try:
                ledger = json.loads(path.read_text())
                if ledger['content_sha256'] != digest or ledger['total_parts'] != len(chunks):
                    raise ValueError('推送回执内容不匹配')
            except FileNotFoundError:
                ledger = dict(date=target, metric=metric, content_sha256=digest, report_sha256=report['sha256'],
                              input_by=input_by, total_parts=len(chunks), sent_parts=[], status='pending')
            if ledger['status'] == 'sent' and ledger['sent_parts'] == list(range(len(chunks))):
                return {**receipt, 'status': 'already_sent', 'sent': True, 'receipt_path': str(path)}
            # 在外部副作用前验证回执可以写入；不修改行情报告本身。
            service.atomic_write(path, json.dumps(ledger, ensure_ascii=False, indent=2))
            if pusher is None:
                from pushers.dingtalk_pusher import DingTalkPusher
                pusher = DingTalkPusher(config={})
            if not pusher.initialize():
                return {**receipt, 'error': '钉钉未配置或初始化失败'}
            for index, chunk in enumerate(chunks):
                if index in ledger['sent_parts']:
                    continue
                title = f"历史天量 · {target} · {metric} ({index + 1}/{len(chunks)})"
                if not pusher.send_markdown(title=title, content=chunk):
                    return {**receipt, 'error': '钉钉未确认发送成功', 'sent_parts': ledger['sent_parts']}
                ledger['sent_parts'].append(index)
                ledger['status'] = 'sent' if len(ledger['sent_parts']) == len(chunks) else 'partial'
                ledger['last_sent_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
                service.atomic_write(path, json.dumps(ledger, ensure_ascii=False, indent=2))
            return {**receipt, 'status': 'sent', 'sent': True, 'receipt_path': str(path), 'parts': len(chunks)}
    except Exception as exc:
        # 传输层自带凭据脱敏；此处不将未知异常中的URL写回回执。
        return {**receipt, 'error': f'推送未完成:{type(exc).__name__}'}


def push_result(result, input_by, *, metric='both', root=service.ROOT, enabled=True):
    if not enabled:
        return dict(status='not_requested', sent=False)
    if result.get('status') == 'skipped':
        return dict(status='skipped', sent=False)
    if (result.get('status') not in ('complete', 'partial') or result.get('previous_report_preserved')
            or not result.get('sha256')):
        return dict(status='blocked', sent=False, error='本次未成功发布归档，不发送旧报告')
    return push_saved(result['date'], input_by, metric=metric, root=root, expected_sha=result['sha256'])
