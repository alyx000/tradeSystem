"""Small chart projections from the selected post-market archive, without writes."""
import math
import re
from pathlib import Path
from statistics import mean, median

import yaml


def _load_archive(daily_root, day):
    path = Path(daily_root) / day / 'post-market.yaml'
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('date') != day:
        raise ValueError('盘后归档日期不符')
    return payload


def load_chart_market(daily_root, fact_date, *, previous_date=None):
    if fact_date is None:
        return None
    try:
        payload = _load_archive(daily_root, fact_date)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return dict(date=fact_date, status='source_failed', reason=str(exc))
    result = dict(date=fact_date, source=f'daily/{fact_date}/post-market.yaml', raw_data=payload.get('raw_data'))
    try:
        if previous_date is None:
            raise ValueError('溢价缺少严格上一开放日')
        previous = _load_archive(daily_root, previous_date)
        result['premium_source'] = dict(date=previous_date, source=f'daily/{previous_date}/post-market.yaml',
                                        backfill=previous.get('premium_backfill'))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        result['premium_source'] = dict(status='source_failed', reason=str(exc))
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('统计数值缺失或非法')
    return value


def build_chart_market(snapshot, manifest, fact_date):
    missing = dict(status='missing-data', reason='未提供事实日盘后图形证据')
    result = dict(board_feedback=dict(missing), style_feedback=dict(missing))
    if snapshot is None:
        return result
    try:
        if snapshot.get('date') != fact_date or snapshot.get('status') == 'source_failed':
            raise ValueError(snapshot.get('reason') or '盘后图形日期不符')
        raw = snapshot['raw_data']
        dates = manifest['trade_dates']
        if len(dates) < 3 or dates[-1] != fact_date or dates != sorted(set(dates)):
            raise ValueError('反馈缺少截至事实日的严格开放日窗口')
        connected, previous, outcome = dates[-3:]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return {key:dict(status='source_failed', reason=str(exc)) for key in result}
    try:
        feedback = raw['board_break_feedback']
        if (feedback['status'] not in ('ok', 'partial') or feedback['outcome_date'] != outcome or
                feedback['break_date'] != previous or feedback['source_connected_date'] != connected):
            raise ValueError('断板反馈日期或状态不符')
        rows = feedback['details']
        if not isinstance(rows, list) or type(feedback['sample_count']) is not int or len(rows) != feedback['sample_count']:
            raise ValueError('断板反馈明细与样本数不符')
        empty = (not rows and feedback['status'] == 'ok' and feedback.get('break_count') == 0 and
                 feedback.get('empty_reason') in ('no_connected_candidates', 'no_board_breaks'))
        if not rows and feedback['status'] == 'ok' and not empty:
            raise ValueError('零样本缺少完整空样本依据')
        seen, points = set(), []
        for row in rows:
            code, name = row['code'], row['name']
            if (not isinstance(code, str) or not re.fullmatch(r'\d{6}(?:\.(?:SH|SZ|BJ))?', code) or
                    code.split('.')[0] in seen or not isinstance(name, str) or not name.strip()):
                raise ValueError('断板反馈股票身份重复或非法')
            seen.add(code.split('.')[0])
            points.append(dict(code=code, label=name+' '+code,
                               open=_number(row['feedback_open_pct']), close=_number(row['feedback_close_pct'])))
        for key in (('open', 'close') if points else ()):
            values = [row[key] for row in points]
            if (abs(mean(values)-_number(feedback[key+'_mean_pct'])) > .02 or
                    abs(median(values)-_number(feedback[key+'_median_pct'])) > .02):
                raise ValueError('断板反馈明细与聚合值不一致')
        result['board_feedback'] = dict(status='complete' if feedback['status']=='ok' else 'partial',
            date=fact_date, source=snapshot['source'], sample=len(points), points=points, empty=empty,
            note=f'{connected}连板 → {previous}断板 → {outcome}反馈；来源状态 {feedback["status"]}')
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result['board_feedback'] = dict(status='source_failed', reason=str(exc))
    try:
        # Popularity is an independent sample. Only premium's own archive and
        # source/outcome dates authorize the style projection, even when it is empty.
        premium = snapshot['premium_source']
        if premium.get('status') == 'source_failed':
            raise ValueError(premium['reason'])
        backfill = premium['backfill']
        if (premium['date'] != previous or backfill['prev_date'] != previous or
                backfill['trade_date'] != outcome):
            raise ValueError('风格样本来源日与反馈日不符')
        rows = []
        for key in ('capacity_top10','first_board_10cm','first_board_20cm','first_board_30cm','second_board','third_board_plus'):
            row = backfill.get(key)
            if row is None:
                rows.append(dict(key=key, n=None, mean=None, median=None))
                continue
            n = row['count']
            if type(n) is not int or n < 0:
                raise ValueError('风格样本数非法')
            if n == 0:
                rows.append(dict(key=key, n=0, mean=None, median=None))
                continue
            rows.append(dict(key=key, n=n, mean=_number(row['premium_mean']), median=_number(row['premium_median'])))
        result['style_feedback'] = dict(status='partial' if any(r['mean'] is None for r in rows) else 'complete',
            date=fact_date, source=premium['source'], rows=rows, note=f'{previous}样本 → {outcome}开盘反馈')
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result['style_feedback'] = dict(status='source_failed', reason=str(exc))
    return result
