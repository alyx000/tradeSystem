"""用采样回执校验本地盘中报告。"""
from hashlib import sha256
import json


def read_report(folder, receipt):
    day = folder.parent.name
    if receipt['batch'] != folder.name or receipt['trade_date'] != day:
        raise ValueError('采样回执日期或批次不符')
    raw = (folder / 'report.json').read_bytes()
    if sha256(raw).hexdigest() != receipt['sha256']:
        raise ValueError('采样报告哈希不符')
    report = json.loads(raw)
    verify_report_identity(report, receipt, day)
    return report


def verify_report_identity(report, receipt, day):
    if report['trade_date'] != day or report['input_by'] != receipt['input_by']:
        raise ValueError('报告日期或请求者不符')
    snapshot = report.get('snapshot') or {}
    if snapshot and (snapshot['trade_date'] != day or snapshot['captured_at'] != report['captured_at']):
        raise ValueError('行情快照日期或时刻不符')
    if report.get('scheduled_at') or receipt.get('scheduled_at'):
        if (receipt.get('scheduled_at') != report.get('scheduled_at')
                or receipt.get('started_at') != report.get('started_at')
                or receipt.get('captured_at') != snapshot.get('captured_at')):
            raise ValueError('定时回执与报告时刻不符')
