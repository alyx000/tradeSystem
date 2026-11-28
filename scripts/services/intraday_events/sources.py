"""只读历史及实时行情，保留来源、固定集合和失败状态。"""
from datetime import datetime, timezone
import json

from analyzers.strong_stock_feedback import _cohort, _code
from .evidence import archive, calendar, digest, limit_rows, number
from services.intraday_monitor.guards import quote_datetime, shanghai_now
from services.intraday_summary.service import _initialize_capability
from utils import is_st_stock
from .alerts import in_sampling_session
from .anchors import load_anchors
from .levels import INDEX_ALIASES, LEVEL_PARSER_VERSION, LIVE_INDEX_CODES

MIN_UNIVERSE = 4000
ANCHOR_GAP = '手写固定锚点有缺源或未核验身份，详见锚点段'


def repair_formatted_anchors(context):
    anchors = context.get('anchors') or {}
    if not any('`' in row['original_name'] for row in anchors.get('unresolved', [])):
        return context
    rows = context['sources']['universe']['rows']
    parsed = load_anchors(rows, context['trade_date'], context['previous_date'])
    if not parsed['sources'] or parsed['sources'] != anchors.get('sources'):
        return context
    gaps = list(context['gaps'])
    if parsed['status'] == 'complete' and ANCHOR_GAP in gaps:
        gaps.remove(ANCHOR_GAP)
    return dict(context, anchors=parsed, gaps=gaps)


def anchors_for_level_watch(context):
    """支持同日更新关键位解析，以开盘前冻结版本为准。"""
    anchors = context.get('anchors') or {}
    archived = anchors.get('sources') or []
    universe = (context.get('sources') or {}).get('universe') or {}
    if (anchors.get('level_parser_version') == LEVEL_PARSER_VERSION
            or not archived or not archived[0].get('supplement_sha256') or not universe.get('rows')):
        return anchors
    parsed = load_anchors(universe['rows'], context['trade_date'], context['previous_date'])
    candidate = parsed.get('sources') or []
    keys = ('date', 'recorded_at', 'revision', 'source_sha256', 'supplement_sha256')
    if not candidate or any(candidate[0][key] != archived[0][key] for key in keys):
        return anchors
    # 保留冻结的个股名单及点位，仅更新与冻结复盘同源的指数解析。
    stock_levels = [row for row in anchors.get('key_levels', []) if row['kind'] == 'stock']
    index_levels = [row for row in parsed['key_levels'] if row['kind'] == 'index']
    return dict(anchors, key_levels=[*index_levels, *stock_levels], level_parser_version=LEVEL_PARSER_VERSION)


def call(registry, capability, *args, **kwargs):
    _initialize_capability(registry, capability)
    try:
        result = registry.call(capability, *args, **kwargs)
    except Exception as exc:
        raise ValueError(f'{capability} 调用失败（{type(exc).__name__}）') from None
    if not result.success:
        raise ValueError(f'{capability} 来源失败')
    return result.data, result.source


def eligible(code, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('证券名称缺失')
    return (not is_st_stock(name) and '退' not in name
            and not code.startswith(('200', '201', '900')))


def load_context(registry, conn, day, cal, now):
    context = dict(trade_date=day, previous_date=cal['previous'], frozen_at=now.isoformat(),
                   universe=[], industry_map={}, cohorts={}, cores=[], gaps=[], sources={})
    rows, source = call(registry, 'get_stock_basic_list', day)
    if not isinstance(rows, list) or len(rows) < MIN_UNIVERSE:
        raise ValueError('证券宇宙覆盖不足')
    seen = set()
    for row in rows:
        code = _code(row.get('ts_code') or row.get('code'))
        if code in seen:
            raise ValueError('证券宇宙重复')
        seen.add(code)
        if eligible(code, row.get('name')):
            context['universe'].append(code)
    if len(context['universe']) < MIN_UNIVERSE:
        raise ValueError('剔除后证券宇宙不足')
    context['universe'].sort()
    context['sources']['universe'] = dict(source=source, sha256=digest(rows), rows=rows)
    context['anchors'] = load_anchors(rows, day, cal['previous'])
    if context['anchors']['status'] != 'complete':
        context['gaps'].append(ANCHOR_GAP)
    try:
        mapping, source = call(registry, 'get_stock_sw_industry_map')
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError('行业来源为空')
        normalized = {}
        outside_universe = []
        universe = set(context['universe'])
        for code, item in mapping.items():
            try:
                canonical = _code(code)
            except ValueError:
                # 成分全集可能含T00018.SH等非普通股票记录。只服务已校验证券宇宙，
                # 原值仍留在来源，未知身份不转成某个现有成员；缺成员仍由100%门拦截。
                outside_universe.append(str(code))
                continue
            if canonical not in universe:
                outside_universe.append(str(code))
                continue
            name = item.get('sw_l2') if isinstance(item, dict) else item
            if name and name not in ('未分类', 'UNCLASSIFIED'):
                if canonical in normalized:
                    raise ValueError('行业映射证券身份重复')
                normalized[canonical] = str(name)
        covered = set(context['universe']) & set(normalized)
        context['sources']['industry'] = dict(source=source, sha256=digest(mapping), mapping=mapping)
        context['sources']['industry']['outside_universe'] = outside_universe
        context['industry_coverage_pct'] = 100*len(covered)/len(context['universe'])
        context['unmapped_codes'] = sorted(set(context['universe'])-covered)
        if context['unmapped_codes']:
            # 不知道缺失证券属于哪个组，不能证明任一组的全量分母。
            raise ValueError(f"申万二级映射不完整（{len(covered)}/{len(context['universe'])}），暂停板块及跟随候选")
        context['industry_map'] = normalized
    except Exception as exc:
        context['industry_map'] = {}
        context['gaps'].append(f'行业: {type(exc).__name__}: {exc}')

    prior = cal['previous']
    cohort = None
    try:
        raw, source = archive(conn, prior)
        limit_rows(raw, prior)
        cohort = _cohort(raw['limit_up'])
        context['sources']['previous_limit_up'] = dict(**source, block=raw['limit_up'])
        context['cohorts']['昨日涨停'] = dict(codes=[r['code'] for r in cohort])
        for label, predicate in [('首板', lambda r:r['limit_times']==1),
                                 ('连板', lambda r:r['limit_times'] is not None and r['limit_times']>=2),
                                 ('高度未知', lambda r:r['limit_times'] is None)]:
            context['cohorts']['昨日'+label] = dict(codes=[r['code'] for r in cohort if predicate(r)])
        for label, prefixes in [('20cm代码组', ('30','68')), ('30cm代码组', ('4','8','92')),
                                ('10cm代码组', ('00','60'))]:
            context['cohorts']['昨日涨停·'+label] = dict(codes=[r['code'] for r in cohort if r['code'].startswith(prefixes)])
    except Exception as exc:
        context['cohorts']['昨日涨停'] = dict(codes=None, error=str(exc))
        context['gaps'].append(f'昨日涨停: {exc}')
    try:
        if cohort is None:
            raise ValueError('昨日涨停名单未核验')
        older = cal['history'][-2]
        old_raw, source = archive(conn, older)
        limit_rows(old_raw, older)
        old_cohort = _cohort(old_raw['limit_up'])
        if any(r['limit_times'] is None for r in old_cohort):
            raise ValueError('前日连板身份存在未知项')
        # 比较完整名单，而非已剔ST的展示样本，避免改名ST但仍涨停者被误判断板。
        yesterday = {r['code'] for r in raw['limit_up']['stocks']}
        candidates = [r['code'] for r in old_cohort if r['limit_times'] >= 2 and r['code'] not in yesterday]
        context['sources']['older_limit_up'] = dict(**source, block=old_raw['limit_up'])
        if candidates:
            quotes, source = call(registry, 'get_market_daily_quotes', prior)
            if not isinstance(quotes, list):
                raise ValueError('昨日行情结构非法')
            indexed = {}
            for q in quotes:
                code = _code(q.get('ts_code') or q.get('code'))
                if code in indexed or str(q.get('trade_date', '')).replace('-','') != prior.replace('-',''):
                    raise ValueError('昨日行情重复或错日')
                indexed[code] = q
            # 停牌/缺行不能默认为断板；宁可整组缺源。
            for code in candidates:
                q = indexed[code]
                number(q['close'], positive=True)
                number(q.get('vol', q.get('volume')), positive=True)
            kept = [indexed[c] for c in candidates]
            context['sources']['broken_daily'] = dict(source=source, date=prior, sha256=digest(kept), rows=kept)
        context['cohorts']['昨日断板（前日连板）'] = dict(codes=candidates)
    except Exception as exc:
        context['cohorts']['昨日断板（前日连板）'] = dict(codes=None, error=str(exc))
        context['gaps'].append(f'昨日断板: {exc}')

    try:
        row = conn.execute('SELECT step5_leaders,updated_at FROM daily_reviews WHERE date=?', (prior,)).fetchone()
        if row is None:
            raise ValueError('前一开放日人工最票缺失')
        updated = datetime.fromisoformat(row[1])
        # SQLite datetime('now')为UTC；拒绝开盘后补写/更改，避免事后选核心。
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if shanghai_now(updated) >= datetime.fromisoformat(day+'T09:30:00+08:00'):
            raise ValueError('前日复盘在今日开盘后修改，无法证明核心事前确定')
        block = json.loads(row[0])
        leaders = block['top_leaders']
        if not isinstance(leaders, list):
            raise ValueError('人工最票结构非法')
        seen = set()
        cores = []
        for item in leaders:
            code = _code(item.get('stock_code') or item.get('code'))
            if code in seen or code not in context['universe']:
                raise ValueError('核心代码重复或不属于有效宇宙')
            seen.add(code)
            cores.append(dict(code=code, name=item['stock'], role=item.get('attribute_type'),
                              source='daily_reviews.step5_leaders', source_date=prior))
        context['cores'] = cores
        context['sources']['cores'] = dict(date=prior, updated_at=row[1], block=block, sha256=digest(block))
        if not cores:
            context['gaps'].append('前日人工最票为空，核心跟随不计算')
    except Exception as exc:
        context['gaps'].append(f'核心名单: {exc}')
    return context


def capture_level_indices(registry, targets, now, clock):
    expected = {row['code'] for row in targets if row['kind'] == 'index'}
    supported = sorted(expected & LIVE_INDEX_CODES)
    result = dict(indices={}, index_source=dict(expected=sorted(expected), source=None, raw_rows=[],
                  raw_sha256=None, errors={code: '未接入实时行情' for code in sorted(expected - LIVE_INDEX_CODES)}))
    if not supported:
        return result
    evidence = result['index_source']
    try:
        started = clock() if clock else now
        if started.date() != now.date() or not in_sampling_session(started):
            raise ValueError('当前已越过交易时段')
        rows, source = call(registry, 'get_realtime_quotes', supported)
        received = clock() if clock else now
        if received.date() != now.date() or not in_sampling_session(received):
            raise ValueError('宽基行情返回已越过交易时段')
        if not isinstance(rows, list):
            raise ValueError('宽基行情结构非法')
        evidence.update(source=source, raw_rows=rows, raw_sha256=digest(rows))
    except (ValueError, TypeError) as exc:
        evidence['errors'].update({code: str(exc) for code in supported})
        return result
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = raw.get('code')
        if code not in supported:
            continue
        if code in seen:
            result['indices'].pop(code, None)
            evidence['errors'][code] = '宽基行情代码重复'
            continue
        seen.add(code)
        try:
            if INDEX_ALIASES.get(raw.get('name', '').strip()) != code:
                raise ValueError('宽基名称与代码未核验一致')
            stamp = quote_datetime(raw)
            if (stamp is None or stamp.date() != now.date()
                    or not 0 <= (received - stamp).total_seconds() <= 90):
                raise ValueError('宽基报价错日或陈旧')
            result['indices'][code] = dict(name=raw['name'], price=number(raw['price'], positive=True),
                                          quote_at=stamp.isoformat())
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            evidence['errors'][code] = str(exc)
    evidence['errors'].update({code: '宽基报价缺失' for code in supported if code not in seen})
    return result


def capture(registry, context, now, *, clock=None, books=None):
    options = {'provider_kwargs': {'sina': {'include_book': True}}} if books is not None else {}
    rows, source = call(registry, 'get_realtime_quotes', context['universe'], **options)
    now = clock() if clock else now
    if not isinstance(rows, list):
        raise ValueError('实时行情结构非法')
    stocks, seen, rejected = {}, set(), []
    universe = set(context['universe'])
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError('实时行情记录结构非法')
        if books is not None and 'book' in raw:
            # 同一笔报价的临时盘口仅供本槽小样本归档。全市场回执仍保留原有字段。
            book = raw.pop('book')
            if source == 'sina' and isinstance(book, dict):
                books[raw.get('code')] = book
        try:
            code = _code(raw.get('code'))
            if code not in universe:
                continue
            if code in seen:
                raise RuntimeError('实时行情代码重复')
            seen.add(code)
            stamp = quote_datetime(raw)
            if stamp is None or not 0 <= (now-stamp).total_seconds() <= 90:
                raise ValueError('行情并非90秒内已到达的当日数据')
            if stamp.date() != now.date():
                raise ValueError('行情错日')
            if not eligible(code, raw.get('name')):
                raise ValueError('实时身份变更，暂不参与固定样本')
            amount = number(raw.get('amount'))
            if amount < 0:
                raise ValueError('累计成交额负值')
            stocks[code] = dict(name=raw['name'], price=number(raw['price'], positive=True),
                                pct_chg=number(raw['pct_chg']), amount=amount,
                                quote_at=stamp.isoformat())
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append(dict(code=raw.get('code'), reason=str(exc)))
    if len(stocks)/len(universe) < .95:
        raise ValueError(f'新鲜行情覆盖不足95%: {len(stocks)}/{len(universe)}')
    return dict(trade_date=now.date().isoformat(), captured_at=now.isoformat(), stocks=stocks,
                context_sha256=digest(context), source=source, raw_sha256=digest(rows),
                raw_rows=rows, rejected=rejected, expected=len(universe), valid=len(stocks))
