"""不可覆盖的运行批次、哈希交接与推送去重；只写独立报告目录。"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .data import EvidenceError, TZ


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def lock(root, key):
    folder = Path(root) / '.locks'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (sha(key.encode())+'.lock')).open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def save(root, result, markdown):
    parent = Path(root) / result['mode'] / result['date'] / result['stage']
    parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(TZ).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:8]
    target = parent / run_id
    tmp = Path(tempfile.mkdtemp(prefix='.pending-', dir=parent))
    try:
        report = encode(result)
        md = markdown.encode()
        manifest = dict(schema='board-break-intraday-manifest-v1', run_id=run_id,
                        date=result['date'], mode=result['mode'], stage=result['stage'],
                        files={'report.json': sha(report), 'report.md': sha(md)})
        for name, data in [('report.json', report), ('report.md', md), ('manifest.json', encode(manifest))]:
            with (tmp/name).open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        os.rename(tmp, target)
        load(target/'manifest.json')
        return target/'manifest.json'
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def load(manifest_path):
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text())
        if (not isinstance(manifest,dict) or manifest['schema'] != 'board-break-intraday-manifest-v1'
                or not isinstance(manifest['files'],dict) or set(manifest['files']) != {'report.json', 'report.md'}):
            raise EvidenceError('候选批次清单格式非法')
        for name, checksum in manifest['files'].items():
            if sha((path.parent/name).read_bytes()) != checksum:
                raise EvidenceError('候选批次文件哈希不符')
        result = json.loads((path.parent/'report.json').read_text())
        if not isinstance(result,dict):
            raise EvidenceError('候选报告不是对象')
        if any(result[k] != manifest[k] for k in ('date', 'mode', 'stage')):
            raise EvidenceError('候选批次元数据不符')
        return result, sha(path.read_bytes())
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise EvidenceError('候选批次缺失或损坏') from None


def latest(root, day, mode):
    folder = Path(root) / mode / day / 'tail'
    paths = sorted(p/'manifest.json' for p in folder.glob('*') if not p.name.startswith('.') and p.is_dir())
    if not paths:
        raise EvidenceError('严格前一开放日无尾盘候选批次')
    # 只略过哈希、身份均核验过的无操作记录；损坏/失败批次仍阻断。
    for path in reversed(paths):
        result, _ = load(path)
        if (result['date'], result['mode'], result['stage']) != (day, mode, 'tail'):
            raise EvidenceError('候选批次目录身份不符')
        if result['status'] != 'skipped':
            return path
    raise EvidenceError('严格前一开放日只有跳过记录，无尾盘候选批次')


def deliver(root, manifest_path, input_by, pusher_factory=None, now=None):
    from pushers.dingtalk_pusher import DingTalkPusher
    import requests
    result, manifest_hash = load(manifest_path)
    clock = now or datetime.now(TZ)
    cutoff = datetime.fromisoformat(result['cutoff']).astimezone(TZ)
    if (result['mode'] != 'live' or result['status'] != 'complete'
            or result['date'] != clock.astimezone(TZ).date().isoformat()
            or not 0 <= (clock-cutoff).total_seconds() <= 600):
        raise EvidenceError('只允许同日时点后10分钟内发送完整正式报告')
    if not result['selected_codes']:
        return dict(status='not_sent', reason='没有经完整核验的观察对象')
    class ReceiptPusher(DingTalkPusher):
        def _post(self, url, payload):
            self.platform = {}
            try:
                with requests.Session() as session:
                    session.trust_env = self.trust_env
                    response = session.post(url, json=payload, timeout=10, allow_redirects=False)
                body = response.json()
                self.platform = dict(http_status=response.status_code, errcode=body.get('errcode'))
                return response.status_code == 200 and body.get('errcode') == 0
            except Exception as exc:
                self.platform = dict(error=type(exc).__name__)
                return False
    key = result['date']+'-'+result['stage']
    folder = Path(root)/'delivery'
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder/(key+'.json')
    with lock(root, 'delivery-'+key):
        if marker.exists():
            # 在网络调用前落发送意图：超时/崩溃后的不确定送达不自动重发。
            return dict(status='already_attempted', receipt=str(marker))
        pusher = pusher_factory() if pusher_factory else ReceiptPusher({})
        if not pusher.initialize():
            return dict(status='failed', reason='钉钉配置缺失')
        receipt = dict(status='attempting', input_by=input_by, at=clock.isoformat(), manifest_sha256=manifest_hash)
        with marker.open('xb') as stream:
            stream.write(encode(receipt)); stream.flush(); os.fsync(stream.fileno())
        labels = '、'.join(result['selected_codes'])
        content = (f'### 断板反包观察 · {result["date"]} · {result["stage"]}\n\n'
                   f'[判断] {result["evaluation"]["response"]["environment"]}；观察对象：{labels}\n\n'
                   f'数据截止：{result["cutoff"]}；批次：{Path(manifest_path).parent.name}\n\n'
                   '仅为证据复核观察，非买卖指令。完整来源与逐股依据已本地归档。')
        ok = bool(pusher.send_markdown('断板反包观察', content))
        receipt.update(status='sent' if ok else 'failed', platform=getattr(pusher, 'platform', {}))
        done = folder/(key+'-result.json')
        with done.open('xb') as stream:
            stream.write(encode(receipt)); stream.flush(); os.fsync(stream.fileno())
        return {**receipt, 'receipt': str(done)}
