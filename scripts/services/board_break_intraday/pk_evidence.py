"""从本批截止窗口生成盘中 PK 事实。只读已采集数据，不读取盘后榜或模型判断。"""
from datetime import date, datetime
import re

from . import data


WINDOWS = {
    'tail': ('14:50', ('09:40', '10:00', '11:30', '14:00', '14:45', '14:50')),
    'open-review': ('09:40', ('09:31', '09:35', '09:40')),
}


def _change(price, basis):
    return round((price / basis - 1) * 100, 4)


def _candidate(row, day, prior, cutoff, checkpoints, expected, stage):
    code, minute, limits = row['code'], row['minute'], row['limits']
    if not isinstance(code, str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ)', code):
        raise data.EvidenceError('PK 候选股票身份异常')
    if (minute.get('status') != 'complete' or minute.get('expected') != len(expected)
            or minute.get('amount_unit') != 'CNY' or minute.get('volume_unit') != 'share'
            or not minute.get('source') or not re.fullmatch('[0-9a-f]{64}', minute.get('raw_sha256') or '')):
        raise data.EvidenceError(f'{code} PK 分钟覆盖、单位或来源凭据不足')
    bars = minute['rows']
    if [bar['time'] for bar in bars] != expected:
        raise data.EvidenceError(f'{code} PK 分钟窗口与本阶段截止时点不符')
    if any(bar.get(key, code) != code for bar in bars for key in ('code', 'ts_code')):
        raise data.EvidenceError(f'{code} PK 分钟股票身份不符')
    # 只让既定日期、交易时段、OHLC 与量额进入证据摘要及其哈希。
    raw = [dict(bar, ts_code=code, trade_time=bar['time']) for bar in bars]
    clean = data.normalize_minutes(raw, code, day, cutoff)['rows']
    if sum(bar['vol'] for bar in clean) <= 0:
        raise data.EvidenceError(f'{code} PK 窗口无已核验成交')
    if row.get('reference_day') != prior or limits.get('date') != day or not limits.get('source'):
        raise data.EvidenceError(f'{code} PK 昨收日期或来源不符')
    ref = data.number(limits['pre_close'], positive=True)
    if not data.number(limits['down_limit'], positive=True) < ref < data.number(limits['up_limit'], positive=True):
        raise data.EvidenceError(f'{code} PK 参考昨收与同日涨跌停价不符')
    height = row['height']
    if type(height) is not int or height < 2 or not isinstance(row['name'], str) or not row['name'].strip():
        raise data.EvidenceError(f'{code} PK 原连板身份异常')

    first, last = clean[0]['open'], clean[-1]['close']
    high = max(clean, key=lambda bar: bar['high'])
    low = min(clean, key=lambda bar: bar['low'])
    item = dict(
        code=code, name=row['name'], height=height, industry=row.get('industry') or None,
        pre_close=ref, open=first, close=last, high=high['high'], low=low['low'],
        pct_chg=_change(last, ref), open_pct=_change(first, ref), from_open_pct=_change(last, first),
        high_pct=_change(high['high'], ref), low_pct=_change(low['low'], ref),
        from_high_pct=_change(last, high['high']), from_low_pct=_change(last, low['low']),
        first_high_time=high['time'][11:16], first_low_time=low['time'][11:16],
        last_5m_pct=_change(last, clean[-6]['close']), amount_yuan=round(sum(bar['amount'] for bar in clean), 2),
        path={bar['time'][11:16]: _change(bar['close'], ref) for bar in clean if bar['time'][11:16] in checkpoints},
        bar_count=len(clean), source=minute['source'], pre_close_source=limits['source'],
        rows_sha256=data.digest({'code': code, 'rows': clean}),
    )
    if stage == 'open-review':
        close_check = row['close_check']
        prior_close = data.number(close_check['close'], positive=True)
        upper = data.number(close_check['limits']['up_limit'], positive=True)
        if close_check['date'] != prior or close_check['resealed'] is not False or prior_close >= upper - .005:
            raise data.EvidenceError(f'{code} PK 前日收盘回封证据不符')
        item['prior_close_check'] = dict(date=prior, close=prior_close, up_limit=upper, resealed=False)
    return item


def build_evidence(result):
    """只接受覆盖完整的固定样本；任何单票的必需证据失败均不产出局部输入。"""
    try:
        stage, day, prior = result['stage'], result['date'], result['previous_trade_day']
        cutoff, checkpoints = WINDOWS[stage]
        stamp = datetime.fromisoformat(f'{day}T{cutoff}').replace(tzinfo=data.TZ)
        if (result['mode'] not in ('live', 'rehearsal') or date.fromisoformat(prior) >= date.fromisoformat(day)
                or datetime.fromisoformat(result['cutoff']) != stamp):
            raise data.EvidenceError('PK 阶段、日期或截止时刻不符')
        rows = result['candidates']
        codes = [row['code'] for row in rows]
        if (result['candidate_coverage'] is not True or result.get('unverified')
                or type(result['candidate_count']) is not int
                or result['candidate_count'] != len(codes) or len(set(codes)) != len(codes)):
            raise data.EvidenceError('PK 候选覆盖不完整或代码重复')
        baseline_date = prior
        if stage == 'open-review':
            parent = result['parent']
            if not set(codes).issubset(parent['candidate_codes']):
                raise data.EvidenceError('开盘 PK 候选超出原尾盘批次')
            baseline_date = parent.get('baseline_date')
            if baseline_date is None:
                # 老批次不带此字段；只从本次已经核验的严格开放日序列定位。
                context = result.get('context') or {}
                opens = context.get('open_days', [])
                if context.get('as_of') != prior or len(opens) < 2 or opens[-1] != prior:
                    raise data.EvidenceError('开盘 PK 原连板基线日期缺失')
                baseline_date = opens[-2]
            if date.fromisoformat(baseline_date) >= date.fromisoformat(prior):
                raise data.EvidenceError('开盘 PK 原连板基线日期异常')
            if rows:
                origin = result.get('close_source') or {}
                if (origin.get('date') != prior or not origin.get('source')
                        or not re.fullmatch('[0-9a-f]{64}', origin.get('sha256') or '')):
                    raise data.EvidenceError('开盘 PK 前日收盘来源或日期未核验')
        expected = data.expected_minutes(day, cutoff)
        return dict(
            stage=stage, mode=result['mode'], date=day, cutoff=stamp.isoformat(), previous_trade_day=prior,
            baseline_date=baseline_date, baseline_fields=['height', 'industry'],
            candidates=[_candidate(row, day, prior, cutoff, checkpoints, expected, stage) for row in rows],
        )
    except data.EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError) as exc:
        raise data.EvidenceError('PK 截止时点证据结构异常') from exc
