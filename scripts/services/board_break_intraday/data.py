"""只读证据与严格的分钟数据契约；不补日历、不写业务库。"""
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import re
from pathlib import Path
import statistics
import time
from zoneinfo import ZoneInfo

from analyzers.consecutive_pool import calculate
from services.handwritten_review import load_entry
from utils.limit_identity import verified_streak, positive_streak
from utils import is_st_stock

TZ = ZoneInfo('Asia/Shanghai')
LIVE_COLLECTION_GRACE_SECONDS = 180
MINUTE_RETRY_DELAY_SECONDS = 2


class EvidenceError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise EvidenceError('数值不能为布尔值')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise EvidenceError('数值缺失') from None
    if not math.isfinite(value) or (positive and value <= 0):
        raise EvidenceError('数值非法')
    return value


def calendar(conn, day):
    target = date.fromisoformat(day)
    # 覆盖前一年：元旦及长假后不能用自然日回退，也不能取最近行情代替。
    start = date(target.year - 1, 1, 1)
    end = date(target.year, 12, 31)
    rows = conn.execute('SELECT date,is_open,exchange FROM trade_calendar WHERE date BETWEEN ? AND ? ORDER BY date',
                        (start.isoformat(), end.isoformat())).fetchall()
    # 只强制目标年；跨年前20个开放日不足时再强制前一年。
    mapping = {r[0]: (r[1], r[2]) for r in rows}
    def year_complete(year):
        first, last = date(year, 1, 1), date(year, 12, 31)
        expected = {(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)}
        actual = {d for d in mapping if d.startswith(str(year)+'-')}
        if actual != expected or any(mapping[d][0] not in (0, 1) or mapping[d][1] != 'SSE' for d in expected):
            raise EvidenceError(f'{year}年完整SSE日历缺失或非法')
    year_complete(target.year)
    opens = [d for d in sorted(mapping) if mapping[d] == (1, 'SSE') and d < day]
    if len([d for d in opens if d.startswith(str(target.year))]) < 25:
        year_complete(target.year-1)
    if len(opens) < 25:
        raise EvidenceError('严格前序开放日不足25日')
    return dict(is_open=mapping[day][0] == 1, previous=opens[-1], history=opens[-25:],
                dates={d: v[0] for d, v in mapping.items()})


def archive(conn, day):
    row = conn.execute('SELECT raw_data FROM daily_market WHERE date=?', (day,)).fetchone()
    if row is None:
        raise EvidenceError(f'{day}盘后归档缺失')
    try:
        obj = json.loads(row[0])
        raw = obj['raw_data']
        if obj['date'] != day or raw['date'] != day:
            raise EvidenceError('归档日期不符')
        return raw, dict(date=day, source='daily_market.raw_data', sha256=hashlib.sha256(row[0].encode()).hexdigest())
    except (KeyError, TypeError, json.JSONDecodeError):
        raise EvidenceError('盘后归档结构损坏') from None


def valid_source(block):
    return (isinstance(block, dict) and not block.get('error') and not block.get('errors')
            and block.get('status', 'complete') in {'complete', 'ok', 'success'})


def limit_rows(raw, expected_day=None, key='limit_up'):
    block = raw.get(key, {})
    if not isinstance(block, dict):
        raise EvidenceError(key+'名单结构非法')
    rows = block.get('stocks')
    day = expected_day or raw.get('date')
    allowed = {'tushare:limit_list_d', 'akshare:stock_zt_pool_em'} if key=='limit_up' else {'tushare:limit_list_d', 'akshare:stock_zt_pool_dtgc_em', 'tushare:daily+stk_limit'}
    if (not day or not valid_source(block) or not isinstance(rows, list) or block.get('count') != len(rows)
            or block.get('_source') not in allowed
            or any(not isinstance(r,dict) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(r.get('code','')))
                   or any(str(r[k]).replace('-','') != day.replace('-','') for k in ('trade_date','date') if k in r) for r in rows)
            or any(str(block[k]).replace('-','') != day.replace('-','') for k in ('trade_date','date') if k in block)
            or len({r.get('code') for r in rows}) != len(rows)):
        raise EvidenceError(key+'名单失败、不完整、错日或重复')
    return rows


def baseline(raw, day):
    block = raw.get('consecutive_pool', {})
    recomputed = calculate(day, block.get('evidence', {}))
    if (block.get('trade_date') != day or recomputed['status'] != 'complete'
            or recomputed['count'] != block.get('count') or recomputed['rows'] != block.get('rows')):
        raise EvidenceError('连板基线重算不一致或不完整')
    lookup = {r['code']: r for r in limit_rows(raw, day)}
    selected = []
    for r in recomputed['rows']:
        # 沿用既有board-break的10cm沪深主板范围；不扩展到20/30cm。
        if not r['code'].startswith(('000', '001', '002', '003', '600', '601', '603', '605')):
            continue
        source = lookup.get(r['code'])
        height = verified_streak(source or {}, raw['limit_up'].get('_source'))[0]
        if source is None or source.get('name') != r['name'] or height != r['height']:
            raise EvidenceError('连板身份与涨停来源不一致')
        selected.append({**r, 'previous_close': number(source.get('close'), positive=True),
                         'industry': source.get('industry', '')})
    return selected


def breadth_evidence(block, day):
    """把前一开放日已核验的收盘家数换算成两种分别带分母、单位的广度指标。"""
    source = block.get('_source') if isinstance(block, dict) else None
    source = source.strip() if isinstance(source, str) else None
    evidence = dict(schema='market-breadth-denominators-v1', fact_date=day, period='收盘',
                    source=source, status='source_failed', advance_share_pct=None,
                    advance_decline_multiple=None)
    try:
        if not valid_source(block) or not source:
            raise EvidenceError('广度来源缺失或失败')
        if any(str(block[key]).replace('-', '') != day.replace('-', '')
               for key in ('date', 'trade_date') if key in block):
            raise EvidenceError('广度来源日期与盘后归档日期不符')
        counts = [number(block.get(key)) for key in ('advance', 'decline', 'flat', 'total')]
        if (any(n < 0 or not n.is_integer() for n in counts) or sum(counts[:3]) != counts[3]
                or counts[3] < 4000):
            raise EvidenceError('上涨、下跌、平盘家数与有效总数未通过覆盖核验')
    except (EvidenceError, TypeError, OverflowError) as exc:
        evidence['gap'] = str(exc) if isinstance(exc, EvidenceError) else '广度家数或来源状态无效'
        evidence['summary'] = f"{day} 收盘市场广度待核。{evidence['gap']}。"
        return evidence
    advance, decline, flat, total = (int(n) for n in counts)
    share = round(advance * 100 / total, 2)
    multiple = round(advance / decline, 4) if decline else None
    evidence.update(status='complete', advance=advance, decline=decline, flat=flat, total=total,
                    advance_share_pct=share, advance_decline_multiple=multiple,
                    gap=None if decline else '下跌家数为0，涨跌家数比无定义')
    ratio_text = f'{multiple:g}×' if decline else '无定义（下跌家数为0）'
    evidence['summary'] = (f'{day} 收盘，上涨占比 {advance}/{total} = {share:.2f}%（上涨/全部有效家数），'
                           f'涨跌家数比 {advance}/{decline} = {ratio_text}（上涨/下跌家数）。来源 {source}。')
    return evidence


def environment_gaps(raw, day, *, breadth=None):
    """来源成功与合法空节点分开；缺键不能被默认为正常空事实。"""
    gaps = []
    nodes = raw.get('node_signals')
    if (not isinstance(nodes, list) or any(not valid_source(r) or
            not all(isinstance(r.get(k), str) and r[k] for k in ('type', 'signal', 'description')) for r in nodes)):
        gaps.append('节点来源缺失或非法')
    for key in ('indices', 'breadth', 'sector_industry', 'limit_up', 'limit_down'):
        try:
            block = raw.get(key)
            if not valid_source(block):
                raise EvidenceError('来源失败')
            if key in {'limit_up', 'limit_down'}:
                limit_rows(raw, day, key)
            elif key == 'indices':
                for name in ('shanghai', 'shenzhen', 'chinext', 'star50'):
                    row = block[name]
                    if (not valid_source(row) or not row.get('_source')
                            or str(row.get('trade_date','')).replace('-','') != day.replace('-','')):
                        raise EvidenceError('指数错日或失败')
                    number(row['close'], positive=True)
                    number(row['change_pct'])
            elif key == 'breadth':
                checked = breadth if breadth is not None else breadth_evidence(block, day)
                if checked['status'] != 'complete':
                    raise EvidenceError(checked['gap'])
            else:
                rows = block['data']
                if not block.get('_source') or not isinstance(rows,list) or not rows:
                    raise EvidenceError('板块来源缺失')
                names = []
                for row in rows:
                    if not valid_source(row) or not isinstance(row.get('name'),str) or not row['name']:
                        raise EvidenceError('板块记录非法')
                    names.append(row['name'])
                    number(row['change_pct'])
                if len(set(names)) != len(names):
                    raise EvidenceError('板块重复')
        except (EvidenceError, KeyError, TypeError):
            gaps.append(key+'环境来源缺失、失败或未通过覆盖核验')
    return gaps


def valid_empty_feedback(conn, fb):
    """零分母是已核验的空样本，不是0%或来源失败。"""
    if (fb.get('empty_reason') not in {'no_connected_candidates', 'no_board_breaks'}
            or any(fb.get(k) != 0 for k in ('sample_count', 'break_count', 'break_candidate_count', 'dirty_source_count'))
            or fb.get('details') != [] or fb.get('errors') != []
            or fb.get('break_coverage_pct') is not None or fb.get('feedback_coverage_pct') is not None
            or fb.get('close_mean_pct') is not None or fb.get('close_median_pct') is not None):
        return False
    # 缺来源的空名单仍拒绝；T-2与T-1原始名单独立核验。
    original = {}
    for key in ('source_connected_date', 'break_date'):
        raw, _ = archive(conn, fb[key])
        original[key] = limit_rows(raw, fb[key])
    connected = set()
    for row in original['source_connected_date']:
        height = positive_streak(row.get('limit_times', row.get('nums')))
        if height is None or not row.get('name'):
            return False
        if height >= 2 and not is_st_stock(row['name']):
            connected.add(row['code'][:6])
    missing = connected - {r['code'][:6] for r in original['break_date']}
    suspended = fb.get('excluded_suspended', [])
    if (not isinstance(suspended,list) or any(not isinstance(r,dict) or r.get('source')!='tushare:suspend_d'
            or r.get('date')!=fb['break_date'] or r.get('full_day') is not True or r.get('suspend_type')!='S'
            for r in suspended)):
        return False
    excluded = {r.get('code') for r in suspended}
    return (fb.get('connected_count')==len(connected) and missing==excluded
            and ((fb['empty_reason']=='no_connected_candidates' and not connected)
                 or (fb['empty_reason']=='no_board_breaks' and bool(connected))))


def checked_feedback(conn, fb, days):
    connected, broken, outcome = days
    details = fb['details']
    vals = [number(r['feedback_close_pct']) for r in details]
    if (fb['status'] != 'ok' or fb['outcome_date'] != outcome
            or fb['break_date'] != broken or fb['source_connected_date'] != connected
            or fb.get('errors')
            or (not vals and not valid_empty_feedback(conn, fb))
            or (bool(vals) and (fb['break_coverage_pct'] != 100 or fb['feedback_coverage_pct'] != 100))
            or len(details) != fb['sample_count'] or len({r['code'] for r in details}) != len(details)):
        raise EvidenceError('反馈日期、样本或覆盖不完整')
    if vals and (abs(statistics.mean(vals)-fb['close_mean_pct']) > .011
                 or abs(statistics.median(vals)-fb['close_median_pct']) > .011):
        raise EvidenceError('反馈统计与明细不符')
    return fb


def _feedback_for_day(conn, days, recovery_dir):
    raw, source = archive(conn, days[-1])
    try:
        return checked_feedback(conn, raw['board_break_feedback'], days), source
    except (EvidenceError, KeyError, TypeError):
        if recovery_dir is None:
            raise
    from .feedback_recovery import load_recovery
    fb, source = load_recovery(conn, recovery_dir, days, raw)
    return checked_feedback(conn, fb, days), source


def context(conn, cal, handwritten_root, recovery_dir=None):
    feedback, sources, gaps = [], [], []
    prior = cal['previous']
    raw, source = archive(conn, prior)
    opens = sorted(d for d, o in cal['dates'].items() if o and d <= prior)
    for i in range(max(2, len(opens)-20), len(opens)):
        d = opens[i]
        try:
            fb, src = _feedback_for_day(conn, opens[i-2:i+1], recovery_dir)
            feedback.append(fb)
            sources.append(src)
        except (EvidenceError, KeyError, TypeError):
            gaps.append(f'{d}断板反馈未通过核验')
    notes = None
    folders = sorted(p for p in Path(handwritten_root).glob('20*') if p.is_dir() and p.name <= prior)
    if folders:
        try:
            entry = load_entry(folders[-1])
            notes = dict(date=folders[-1].name, raw=entry['raw'], meta=entry['meta'])
            if notes['date'] != prior:
                gaps.append('手写复盘未更新至严格前一开放日')
        except (ValueError, KeyError, OSError):
            gaps.append('手写复盘来源链校验失败')
    else:
        gaps.append('手写复盘缺失')
    breadth = breadth_evidence(raw.get('breadth'), prior)
    gaps.extend(environment_gaps(raw, prior, breadth=breadth))
    market = {k: raw.get(k) for k in ('indices', 'sector_industry', 'limit_up', 'limit_down')}
    market['breadth'] = breadth
    return dict(as_of=prior, feedback=feedback[-10:], stock_history_feedback=feedback,
                history_definition='严格截至前一开放日的近20开放日断板次日反馈；非实体反包、非策略胜率，缺口不等于无历史反包',
                open_days=opens, handwritten=notes,
                nodes=raw.get('node_signals'), market=market,
                sources=sources+[source], gaps=gaps)


def expected_minutes(day, cutoff):
    spans = [('09:31', '11:30'), ('13:01', '15:00')]
    result = []
    for first, last in spans:
        cur = datetime.fromisoformat(day+'T'+first)
        stop = datetime.fromisoformat(day+'T'+min(last, cutoff))
        while cur <= stop:
            result.append(cur.strftime('%Y-%m-%d %H:%M:%S'))
            cur += timedelta(minutes=1)
    return result


def normalize_minutes(rows, code, day, cutoff, *, cutoff_exclusion=None):
    if not isinstance(rows, list) or not rows or len(rows) >= 8000:
        raise EvidenceError('分钟源空、失败或疑似截断')
    expected = expected_minutes(day, cutoff)
    allowed = set(expected)
    data, seen, excluded = {}, set(), 0
    off_session = []
    for row in rows:
        if not isinstance(row,dict):
            raise EvidenceError('分钟记录格式非法')
        if row.get('ts_code', row.get('code')) != code:
            raise EvidenceError('分钟股票身份不符')
        stamp = str(row.get('trade_time', row.get('time', '')))
        try:
            t = datetime.fromisoformat(stamp)
        except ValueError:
            raise EvidenceError('分钟时间非法') from None
        if t.tzinfo is not None or t.date().isoformat() != day or t.second or t.microsecond:
            raise EvidenceError('分钟日期或时间口径不符')
        stamp = t.strftime('%Y-%m-%d %H:%M:%S')
        if stamp in seen:
            raise EvidenceError('分钟时间重复')
        seen.add(stamp)
        if stamp not in allowed:
            # 09:30竞价记录和截止后的分钟保留排除数量，绝不进入计算或LLM。
            if t.strftime('%H:%M') != '09:30' and t.strftime('%H:%M') <= cutoff:
                off_session.append(stamp)
            excluded += 1
            continue
        item = dict(time=stamp, **{k: number(row.get(k), positive=k in ('open', 'high', 'low', 'close'))
                                   for k in ('open', 'high', 'low', 'close', 'vol', 'amount')})
        if (not item['low'] <= min(item['open'], item['close']) <= max(item['open'], item['close']) <= item['high']
                or item['vol'] < 0 or item['amount'] < 0):
            raise EvidenceError('分钟OHLC或量额非法')
        data[stamp] = item
    missing = sorted(allowed-set(data))
    if off_session:
        gap = f'，首缺{missing[0]}' if missing else ''
        raise EvidenceError(f'非交易时段分钟{off_session[0]}等{len(off_session)}根，'
                            f'窗口覆盖{len(data)}/{len(expected)}{gap}')
    if missing:
        last = data.get(expected[-1]) if expected else None
        if cutoff_exclusion is not None and last is not None and cutoff_exclusion(last['close']):
            trade_witness = next((data[stamp] for stamp in reversed(expected) if stamp in data
                                  and data[stamp]['vol'] > 0 and data[stamp]['amount'] > 0), None)
            if trade_witness is not None:
                return dict(status='cutoff_exclusion', cutoff_bar=last, trade_witness=trade_witness,
                            verified_minutes=len(data), expected=len(expected), excluded_rows=excluded)
        error = EvidenceError(f'分钟覆盖{len(data)}/{len(expected)}，缺{missing[0]}等{len(missing)}根')
        if data:
            error.partial_rows = dict(rows=[data[t] for t in expected if t in data], missing=missing,
                excluded_rows=excluded, expected=len(expected), status='partial', volume_unit='share', amount_unit='CNY')
        raise error
    return dict(rows=[data[t] for t in expected], excluded_rows=excluded,
                expected=len(expected), status='complete', volume_unit='share', amount_unit='CNY')


def normalize_suspensions(response, day):
    """仅同日成功的S记录且显式空timing才证明全天停牌；缺字段不能当空值。"""
    if (not response.success or response.source != 'tushare:suspend_d'
            or not isinstance(response.data, list) or len(response.data) >= 5000):
        raise EvidenceError('同日停牌来源失败、结构非法或疑似截断')
    full_day, seen = {}, set()
    for row in response.data:
        if not isinstance(row, dict):
            raise EvidenceError('同日停牌记录结构非法')
        code = row.get('ts_code')
        if (not isinstance(code, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code)
                or row.get('code', code) != code or code in seen
                or row.get('trade_date') != day.replace('-', '')
                or row.get('suspend_type') != 'S' or 'suspend_timing' not in row
                or (row['suspend_timing'] is not None and not isinstance(row['suspend_timing'], str))):
            raise EvidenceError('同日停牌记录日期、身份、唯一性或类型非法')
        seen.add(code)
        if row['suspend_timing'] in (None, ''):
            full_day[code] = dict(row)
    return dict(date=day, source=response.source, status='complete', rows=response.data,
                raw_sha256=digest(response.data), full_day=full_day)


class MarketSource:
    """复用配置中的Tushare镜像；实时与历史API明确分离。"""
    def __init__(self):
        from providers.tushare_provider import TushareProvider
        from providers.sina_provider import SinaProvider
        from providers.tdx_provider import TdxProvider
        from . import eastmoney
        self.provider = TushareProvider({})
        if not self.provider.initialize():
            raise EvidenceError('Tushare初始化失败（未输出上游凭据或响应）')
        self.sina = SinaProvider({})
        if not self.sina.initialize():
            self.sina = None
        self.tdx = TdxProvider({})
        self.tdx.initialize()
        self.eastmoney = eastmoney

    def minutes(self, code, day, cutoff, mode, *, realtime_diagnostic=False, cutoff_exclusion=None, keep_partial=False):
        enforce_window = mode == 'live' and not realtime_diagnostic
        if enforce_window and not self._retry_allowed(day, cutoff, mode):
            raise EvidenceError('不在正式采集窗口，未发起分钟请求')
        try:
            return self._tushare_minutes(code, day, cutoff, mode)
        except EvidenceError as primary:
            attempts = [dict(source='tushare:rt_min_daily' if mode=='live' else 'tushare:stk_mins',
                             status='source_failed',reason=str(primary))]
            fragment = getattr(primary, 'partial_minutes', None) if keep_partial else None

        def remember_partial(exc, source, raw, requested_at, note=None):
            nonlocal fragment
            partial = getattr(exc, 'partial_rows', None)
            if not keep_partial or not partial or len(partial['rows']) <= len((fragment or {}).get('rows', [])):
                return
            fragment = dict(partial, source=source, raw_sha256=digest(raw), requested_at=requested_at,
                            source_note=note, identity='single_symbol_request', adjust='none', cutoff=cutoff)

        # 两轮各自整段校验，绝不合并不完整响应。仅在原实时采集窗口内重试备源。
        for round_no in (1, 2):
            if round_no == 2:
                if not self._retry_allowed(day, cutoff, mode, MINUTE_RETRY_DELAY_SECONDS):
                    break
                time.sleep(MINUTE_RETRY_DELAY_SECONDS)
            for name in ('sina','tdx','eastmoney'):
                if (enforce_window or round_no == 2) and not self._retry_allowed(day, cutoff, mode):
                    attempts.append(dict(source=name, status='source_failed', reason='实时采集窗口已结束，未发起请求'))
                    break
                provider = getattr(self,name,None)
                if provider is None:
                    continue
                requested_at = self._minute_now().isoformat()
                if name == 'tdx':
                    def validate_node(rows):
                        try:
                            return normalize_minutes(rows, code, day, cutoff)
                        except EvidenceError as exc:
                            remember_partial(exc, 'tdx:get_security_bars:1min', rows, requested_at)
                            raise
                    fallback = provider.get_stock_minutes(
                        code, day, validate=validate_node,
                        can_request=lambda: not (enforce_window or round_no == 2) or self._retry_allowed(day, cutoff, mode))
                else:
                    fallback = provider.get_stock_minutes(code, day)
                try:
                    raw_hash = digest(fallback.data) if fallback.data is not None else None
                except (ValueError, TypeError):
                    raw_hash = None
                try:
                    if not fallback.success:
                        raise EvidenceError(fallback.error)
                    if raw_hash is None:
                        raise EvidenceError('分钟源原始响应哈希校验失败')
                    result = normalize_minutes(
                        fallback.data, code, day, cutoff,
                        cutoff_exclusion=cutoff_exclusion if name == 'sina' and cutoff == '14:50' else None)
                except EvidenceError as exc:
                    if raw_hash and fallback.success:
                        remember_partial(exc, fallback.source, fallback.data, requested_at, fallback.note)
                    attempts.append(dict(source=fallback.source,status='source_failed',reason=str(exc),
                                         attempt=round_no, requested_at=requested_at, raw_sha256=raw_hash))
                    continue
                result.update(source=fallback.source,requested_at=requested_at,attempt=round_no,
                              raw_sha256=raw_hash,attempts=attempts,
                              identity='single_symbol_request',adjust='none',source_note=fallback.note)
                if fallback.source=='tdx:get_security_bars:1min':
                    kept = {r['time'] for r in result['rows']}
                    result['source_corrections'] = [dict(time=r['trade_time'],field=key,raw_value=r['raw_'+key],
                        normalized_value=r[key],reason='pytdx_get_volume_protocol_zero')
                        for r in fallback.data if r['trade_time'] in kept
                        for key in ('vol','amount') if 'raw_'+key in r]
                return result
        error = EvidenceError('；'.join(a['source']+'：'+a['reason'] for a in attempts))
        error.attempts = attempts
        if fragment:
            error.partial_minutes = fragment
        raise error

    def _minute_now(self):
        return datetime.now(TZ)

    def _retry_allowed(self, day, cutoff, mode, delay=0):
        end = datetime.fromisoformat(day+'T'+cutoff).replace(tzinfo=TZ)
        elapsed = (self._minute_now()-end).total_seconds()
        return mode == 'live' and 0 <= elapsed <= LIVE_COLLECTION_GRACE_SECONDS-delay

    def suspensions(self, day):
        requested_at = datetime.now(TZ).isoformat()
        result = normalize_suspensions(self.provider.get_suspend_list(day), day)
        result['requested_at'] = requested_at
        return result

    def _tushare_minutes(self, code, day, cutoff, mode):
        api = 'rt_min_daily' if mode == 'live' else 'stk_mins'
        if getattr(self, '_minute_permission_denied', False):
            raise EvidenceError(f'{api}权限不足，本次运行不重复请求')
        params = dict(ts_code=code, freq='1MIN' if mode == 'live' else '1min')
        if mode != 'live':
            params.update(start_date=day+' 09:30:00', end_date=day+' '+cutoff+':00')
        try:
            df = self.provider.pro.query(api, **params)
        except Exception as exc:
            # 白名单分类，不能让镜像错误携带账户链接/密钥进日志。
            msg = str(exc).lower()
            kind = '权限不足' if any(x in msg for x in ('权限', 'permission', 'access denied', '积分')) else type(exc).__name__
            if kind == '权限不足':
                self._minute_permission_denied = True
            raise EvidenceError(f'{api}来源失败：{kind}') from None
        records = df.to_dict('records')
        try:
            result = normalize_minutes(records, code, day, cutoff)
        except EvidenceError as exc:
            if getattr(exc, 'partial_rows', None):
                try:
                    raw_sha = digest(records)
                except (TypeError, ValueError):
                    raw_sha = None
                if raw_sha is not None:
                    exc.partial_minutes = dict(exc.partial_rows, source='tushare:'+api, raw_sha256=raw_sha,
                        requested_at=datetime.now(TZ).isoformat(), identity='response_ts_code', adjust='none', cutoff=cutoff)
            raise
        result.update(source='tushare:'+api, requested_at=datetime.now(TZ).isoformat(),
                      raw_sha256=digest(records))
        return result

    def limits(self, day, codes):
        requested = set(codes)
        r = self.provider.get_stock_limit_prices(day)
        if not r.success or not r.data:
            raise EvidenceError('同日涨跌停价来源失败')
        result = {}
        for row in r.data:
            code = row.get('ts_code')
            if code not in requested:
                continue
            if code in result or str(row.get('trade_date')).replace('-', '') != day.replace('-', ''):
                raise EvidenceError(f'{day} {code} 涨跌停价重复或日期不符')
            prices = {}
            for field in ('down_limit', 'up_limit'):
                try:
                    prices[field] = number(row.get(field), positive=True)
                except EvidenceError as exc:
                    raise EvidenceError(f'{day} {code} 涨跌停价 {field}：{exc}') from None
            if not prices['down_limit'] < prices['up_limit']:
                raise EvidenceError(f'{day} {code} 涨跌停价区间非法')
            result[code] = prices
        return result

    def reference_prices(self, codes, day, mode, *, cutoff=None):
        # stk_limit实际镜像只有上下限，无pre_close。昨收只能取同日行情的参考字段，
        # 不用涨停价倒推，避免除权日误算；历史日线只提取事前已知pre_close。
        quote_requested_at = None
        if mode == 'live':
            from providers.sina_provider import SinaProvider
            source = SinaProvider({})
            source.initialize()
            quote_requested_at = self._minute_now()
            result = source.get_realtime_quotes(codes)
        else:
            result = self.provider.get_market_daily_quotes(day)
        if not result.success or not result.data:
            raise EvidenceError('同日昨收参考价来源失败')
        cutoff_stamp = None
        if cutoff is not None and quote_requested_at is not None and result.source == 'sina':
            stamp = datetime.fromisoformat(f'{day}T{cutoff}').replace(tzinfo=TZ)
            quote_observed_at = self._minute_now()
            if (stamp <= quote_requested_at <= quote_observed_at
                    <= stamp + timedelta(seconds=LIVE_COLLECTION_GRACE_SECONDS)):
                cutoff_stamp = stamp
        selected = {}
        for r in result.data:
            code = r.get('ts_code', r.get('code'))
            if code not in codes:
                continue
            actual = str(r.get('quote_date', r.get('trade_date'))).replace('-', '')
            if actual != day.replace('-', '') or code in selected:
                raise EvidenceError('参考昨收行情日期或唯一性失败')
            selected[code] = dict(pre_close=number(r.get('pre_close'),positive=True),
                                  source=result.source,date=day)
            if cutoff_stamp is None or r.get('quote_time') != cutoff_stamp.strftime('%H:%M:%S'):
                continue
            try:
                price = number(r.get('price'), positive=True)
                volume = number(r.get('volume'), positive=True)
                amount = number(r.get('amount'), positive=True)
            except EvidenceError:
                continue
            proof = dict(code=code, time=cutoff_stamp.isoformat(), close=price,
                         cumulative_volume=volume, cumulative_amount=amount, source=result.source)
            selected[code]['cutoff_quote'] = dict(
                **proof, kind='realtime_quote', requested_at=quote_requested_at.isoformat(),
                observed_at=quote_observed_at.isoformat(), quote_sha256=digest(proof))
        if set(selected) != set(codes):
            raise EvidenceError('参考昨收覆盖不足')
        return selected
