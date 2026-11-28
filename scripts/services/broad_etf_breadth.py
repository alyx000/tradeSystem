"""宽基 ETF 同日的 A 股涨跌家数。每行保留本机实际十分钟采样的时刻。"""
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from services.board_break_intraday.data import TZ, digest
from services.intraday_events.archive import read_report
from services.intraday_events.sources import MIN_UNIVERSE
from services.intraday_events.turnover_baseline import _timestamp
from services.theme_intraday_breadth import SLOTS, _compact


def _label(minute):
    return f'{minute // 60:02}:{minute % 60:02}'


def _breadth_point(report, receipt, day):
    context = report['context']
    captured = _timestamp(report['captured_at'])
    frozen = _timestamp(context['frozen_at'])
    universe = context['universe']
    if not isinstance(universe, list) or not all(isinstance(code, str) for code in universe):
        raise ValueError('冻结股票名单格式不符')
    wanted = set(universe)
    if (context.get('trade_date') != day or frozen.date() != captured.date() or frozen > captured
            or len(wanted) != len(universe) or len(wanted) < MIN_UNIVERSE
            or report['snapshot'].get('expected') != len(wanted)):
        raise ValueError('冻结范围、样本数或冻结时间无法核验')
    slot, sample = _compact(report, receipt, wanted, day)
    if slot not in SLOTS:
        raise ValueError('未能定位十分钟采样时槽')
    rows = list(sample['stocks'].values())
    valid = len(rows)
    up = sum(r['price'] > r['pre_close'] for r in rows)
    down = sum(r['price'] < r['pre_close'] for r in rows)
    quotes = [r['quote_at'] for r in rows]
    return dict(slot=_label(slot), batch=receipt['batch'], captured_at=captured.isoformat(),
                source=sample['source'], source_sha256=receipt['sha256'], receipt_sha256=digest(receipt),
                frozen_at=frozen.isoformat(),
                universe_sha256=digest(sorted(wanted)), expected=len(wanted), valid=valid,
                up=up if valid else None, flat=valid-up-down if valid else None, down=down if valid else None,
                quote_at_min=min(quotes, default=None), quote_at_max=max(quotes, default=None),
                status='complete' if valid == len(wanted) else 'partial' if valid else 'source_failed')


def breadth_reference(archive_root, day, through, *, previous=None):
    if (not isinstance(through, datetime) or through.tzinfo is None
            or through.astimezone(TZ).date().isoformat() != day):
        raise ValueError('广度截止时刻须为同日带时区的时间')
    through = through.astimezone(TZ)
    cutoff = through.hour * 60 + through.minute
    expected = [_label(n) for n in SLOTS if n <= cutoff]
    groups = defaultdict(list)
    gaps = []
    folder = Path(archive_root) / day
    cached = {row['batch']: row for row in (previous or {}).get('timeseries', {}).values()}
    for batch in sorted(p for p in folder.glob('*') if p.is_dir()):
        try:
            receipt = json.loads((batch/'receipt.json').read_bytes())
            if receipt.get('captured_at') and _timestamp(receipt['captured_at']) > through:
                continue
            old = cached.get(batch.name)
            if (old and old.get('receipt_sha256') == digest(receipt) and receipt['trade_date'] == day
                    and old['source_sha256'] == receipt['sha256'] and _timestamp(old['captured_at']) <= through):
                if sha256((batch/'report.json').read_bytes()).hexdigest() != receipt['sha256']:
                    raise ValueError('采样报告哈希不符')
                groups[old['slot']].append(old)
                continue
            report = read_report(batch, receipt)
            if _timestamp(report['captured_at']) > through:
                continue
            if (report.get('snapshot') or {}).get('interval_minutes') == 5:
                continue
            point = _breadth_point(report, receipt, day)
            groups[point['slot']].append(point)
        except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
            gaps.append(dict(batch=batch.name, reason=str(exc)))
    points = {}
    conflicts = []
    first_universe = None
    for slot in sorted(groups):
        rows = groups[slot]
        if len(rows) != 1 or (first_universe and rows[0]['universe_sha256'] != first_universe):
            conflicts.append(slot)
            gaps.append(dict(slot=slot, reason='同槽多个来源批次或日内冻结名单不一致',
                             batches=[r['batch'] for r in rows]))
            continue
        point = rows[0]
        first_universe = point['universe_sha256']
        points[slot] = point
    missing = sorted(set(expected) - points.keys())
    complete = bool(expected) and not (missing or gaps) and all(p['status'] == 'complete' for p in points.values())
    return dict(status='complete' if complete else 'partial' if any(p['valid'] for p in points.values()) else 'source_failed',
                scope='当日盘中冻结的非ST、非退市、非B股样本，按最新价相对昨收统计',
                through=through.isoformat(), expected_slots=expected, timeseries=points, missing_slots=missing,
                conflicts=conflicts, gaps=gaps)
