"""尾盘与开盘的同批证据流程；历史演练不能晋升为实时批次。"""
from datetime import datetime
from pathlib import Path
import sqlite3

from . import data, llm, pk, pk_evidence, pk_report, store

RULES_V1 = dict(version='board-break-intraday-v1', previous_min_height=2,
                boards='沪深10cm主板，非ST/退市', tail_max_pct=6,
                tail_cutoff='14:50', open_cutoff='09:40', minute_stamp='closed_bar',
                live_collection_grace_seconds=data.LIVE_COLLECTION_GRACE_SECONDS, open_exclude_close_resealed=True,
                verified_full_day_suspension=True,
                history='严格已完成交易日；历史演练不得推送或作正式交接')
RULES = dict(RULES_V1, version='board-break-intraday-v2',
             tail_cutoff_price_exclusion='exact_sina_quote_or_closed_bar_with_verified_trading')
RULES_HASH = data.digest(RULES)
RULES_V1_HASH = data.digest(RULES_V1)
# 11 月 26 日开盘仍消费 25 日 v1 尾盘。v2 从 26 日尾盘交接链开始。
CUTOFF_PRICE_GATE_FIRST_TAIL = '2026-11-26'


def price_exclusion(price, limits, *, tail_max_pct=None):
    if price <= limits['down_limit'] + .005:
        return '截止时点跌停'
    if tail_max_pct is not None:
        if price >= limits['up_limit'] - .005:
            return '14:50仍涨停'
        if (price / limits['pre_close'] - 1) * 100 > tail_max_pct + 1e-8:
            return '14:50涨幅超过6%'
    return None


def rank_candidates(result, no_llm, ranker):
    if not result['candidate_coverage']:
        return dict(status='not_run', reason='候选覆盖不完整', rows=[], matches=[], total=None)
    if no_llm and result['candidates']:
        return dict(status='skipped', reason='本次关闭模型评估', rows=[], matches=[], total=None)
    try:
        evidence = pk_evidence.build_evidence(result)
        return ranker(evidence)
    except data.EvidenceError as exc:
        return dict(status='incomplete', reason=str(exc), rows=[], matches=[], total=None)


def render(result):
    title = '尾盘断板反包扫描' if result['stage']=='tail' else '次日开盘十分钟复核'
    lines = [f'# {title} · {result["date"]}', '',
             '**历史证据演练，非当时实际扫描结果，禁止推送。**' if result['mode']=='rehearsal' else '**实时证据观察，非买卖指令。**', '',
             f'状态：{result["status"]}；数据截止：{result["cutoff"]}；请求者：{result["input_by"]}',
             f'严格前一开放日：{result.get("previous_trade_day", "待核")}；候选覆盖：{result.get("candidate_coverage", False)}',
             f'数值候选数：{result["candidate_count"] if result["candidate_count"] is not None else "未能完整计算"}；模型：{result["evaluation"]["status"]}', '',
             '缺失或失败不等于0只符合；数值初筛不等于模型确认。']
    breadth = ((result.get('context') or {}).get('market') or {}).get('breadth') or {}
    if isinstance(breadth, dict) and breadth.get('schema') == 'market-breadth-denominators-v1':
        lines += ['', breadth['summary']]
    if result.get('parent'):
        lines += ['', f'原批次：{result["parent"]["manifest"]}；SHA256：{result["parent"]["sha256"]}']
    elif result.get('parent_validation'):
        validation = result['parent_validation']
        lines += ['', f'被拒绝的原批次：{validation["manifest"]}；SHA256：{validation["sha256"]}']
    cutoff_excluded = sum('cutoff_evidence' in row for row in result['excluded'])
    if cutoff_excluded:
        lines += ['', f'经截止价直接排除 {cutoff_excluded} 只，逐只证据见 report.json。']
    lines += pk_report.render_rows(result)
    if result['gaps']:
        lines += ['', '## 缺口']+['- '+g for g in result['gaps']]
    response = result['evaluation'].get('response')
    if response:
        lines += ['', '## 模型判断', response['environment']+'：'+response['summary']]
    lines += ['', '完整分钟路径、来源哈希、手写原文及版本、模型输入/结果保存在同批report.json。',
              '自动化启用状态、北京时间调度和绑定运行环境：待验证。本入口不修改任务配置。']
    return '\n'.join(lines)+'\n'


def run(conn, *, stage, day, mode, input_by, root, handwritten_root,
        batch=None, no_llm=False, source_factory=data.MarketSource, evaluator=llm.evaluate,
        ranker=pk.run, now=None):
    if stage not in {'tail','open-review'} or mode not in {'live','rehearsal'} or not input_by.strip():
        raise ValueError('阶段、模式或请求者非法')
    clock = now or datetime.now(data.TZ)
    if clock.tzinfo is None:
        raise ValueError('运行时间必须带时区')
    clock = clock.astimezone(data.TZ)
    cutoff_time = RULES['tail_cutoff' if stage=='tail' else 'open_cutoff']
    cutoff = datetime.fromisoformat(day+'T'+cutoff_time).replace(tzinfo=data.TZ)
    result = dict(schema='board-break-intraday-v1', stage=stage,date=day,mode=mode,
                  input_by=input_by,started_at=clock.isoformat(),cutoff=cutoff.isoformat(),
                  rules=RULES,rules_sha256=RULES_HASH,status='source_failed',
                  candidates=[],excluded=[],unverified=[],candidate_count=None,candidate_coverage=False,
                  selected_codes=[],gaps=[],evaluation={'status':'not_run'},automation_runtime='unverified',
                  pk=dict(status='not_run', reason='未取得本时点完整候选，见本批缺口', rows=[], matches=[], total=None))
    try:
        cal = data.calendar(conn, day)
        result['previous_trade_day']=cal['previous']
        tail_day = day if stage == 'tail' else cal['previous']
        if mode == 'live' and tail_day < CUTOFF_PRICE_GATE_FIRST_TAIL:
            result.update(rules=RULES_V1, rules_sha256=RULES_V1_HASH)
        if not cal['is_open']:
            result.update(status='skipped',gaps=['目标日为已核验休市日'])
            return result
        if mode=='live' and (day != clock.date().isoformat() or not 0 <= (clock-cutoff).total_seconds() <= RULES['live_collection_grace_seconds']):
            result.update(status='skipped',gaps=['不在北京时间正式采集窗口内，禁止用当前或收盘行情冒充'])
            return result
        if mode=='rehearsal' and clock <= cutoff:
            raise data.EvidenceError('历史演练时点尚未发生')
        result['context']=data.context(conn,cal,handwritten_root,Path(root)/'feedback-recovery')
        result['gaps'].extend(result['context']['gaps'])
        raw, origin = data.archive(conn,cal['previous'])
        result['baseline_source']=origin
        parent = None
        if stage=='tail':
            universe = data.baseline(raw,cal['previous'])
        else:
            manifest = Path(batch) if batch else store.latest(root,cal['previous'],mode)
            parent, checksum = store.load(manifest)
            validation = dict(manifest=str(manifest.resolve()), sha256=checksum, status='rejected',
                              tail_rules_sha256=parent.get('rules_sha256'), review_rules_sha256=result['rules_sha256'])
            result['parent_validation'] = validation
            if (parent.get('schema') != result['schema'] or parent['stage'] != 'tail'
                    or parent['date'] != cal['previous'] or parent['mode'] != mode):
                raise data.EvidenceError('原尾盘批次日期、阶段、模式或格式不符')
            if (mode == 'rehearsal' and parent.get('rules_sha256') == RULES_V1_HASH
                    and parent.get('rules') == RULES_V1):
                result.update(rules=RULES_V1, rules_sha256=RULES_V1_HASH)
                validation['review_rules_sha256'] = RULES_V1_HASH
            parent_gaps = []
            if (parent.get('rules_sha256') != result['rules_sha256']
                    or parent.get('rules') != result['rules']):
                parent_gaps.append('原尾盘批次与本次复核的规则版本不一致')
            if not parent.get('candidate_coverage') or parent.get('status') not in {'complete','partial'}:
                parent_gaps.append('原尾盘批次失败或候选覆盖不完整')
            if parent_gaps:
                result['gaps'].extend(parent_gaps)
                return result
            universe = parent['candidates']
            if len(universe) != parent['candidate_count'] or len({r['code'] for r in universe}) != len(universe):
                raise data.EvidenceError('原批次候选成员不一致')
            result['parent']=dict(manifest=str(manifest.resolve()),sha256=checksum,
                                  candidate_codes=[r['code'] for r in universe],status=parent['status'],
                                  baseline_date=parent['previous_trade_day'])
            validation['status'] = 'accepted'
            if parent['status'] != 'complete':
                result['gaps'].append('原尾盘批次未通过完整评估，今早不升级为正式符合')
        result['universe_codes']=[r['code'] for r in universe]
        source = source_factory() if universe else None
        suspensions = source.suspensions(day) if universe else dict(full_day={})
        result['suspensions'] = suspensions
        trading_codes = [code for code in result['universe_codes'] if code not in suspensions['full_day']]
        limits = source.limits(day,trading_codes) if trading_codes else {}
        tail_price_gate = stage == 'tail' and 'tail_cutoff_price_exclusion' in result['rules']
        reference_options = {'cutoff': cutoff_time} if tail_price_gate else {}
        references = source.reference_prices(trading_codes, day, mode, **reference_options) if trading_codes else {}
        close_limits = source.limits(cal['previous'],trading_codes) if trading_codes and stage=='open-review' else {}
        closed_ups = {r['code']:r for r in data.limit_rows(raw, cal['previous'])} if stage=='open-review' else {}
        close_quotes = {}
        if stage=='open-review' and trading_codes:
            # 严格同日的原始收盘价，来自已有市场广度归档，不能用涨停名单缺席推断断板。
            quotes = source.provider.get_market_daily_quotes(cal['previous'])
            if not quotes.success or not quotes.data:
                raise data.EvidenceError('原尾盘日收盘行情来源失败')
            for q in quotes.data:
                code = q.get('ts_code',q.get('code'))
                if str(q.get('trade_date')).replace('-','') != cal['previous'].replace('-','') or code in close_quotes:
                    raise data.EvidenceError('收盘行情日期错位或重复')
                close_quotes[code]=q
            result['close_source']=dict(source=quotes.source,date=cal['previous'],sha256=data.digest(quotes.data))
        for item in universe:
            row = {k:item[k] for k in ('code','name','height','industry') if k in item}
            code = row['code']
            try:
                if code in suspensions['full_day']:
                    result['excluded'].append({**row, 'status':'excluded', 'reason':'同日已核验全天停牌',
                        'suspension_evidence':dict(date=day, source=suspensions['source'],
                            raw_sha256=suspensions['raw_sha256'], row=suspensions['full_day'][code])})
                    continue
                if code not in limits:
                    raise data.EvidenceError('同日涨跌停价缺失')
                reference = references[code]
                price_limits = {**limits[code], **{key: value for key, value in reference.items() if key != 'cutoff_quote'}}
                if not price_limits['down_limit'] < price_limits['pre_close'] < price_limits['up_limit']:
                    raise data.EvidenceError('同日参考昨收与涨跌停区间不符')
                if stage=='open-review':
                    if code not in close_quotes or code not in close_limits:
                        raise data.EvidenceError('原尾盘日收盘或涨停价缺失')
                    close = data.number(close_quotes[code].get('close'),positive=True)
                    resealed = close >= close_limits[code]['up_limit']-.005
                    if resealed != (code in closed_ups):
                        raise data.EvidenceError('收盘回封判据与完整涨停名单冲突')
                    row['close_check']=dict(date=cal['previous'],close=close,limits=close_limits[code],resealed=resealed)
                    if resealed:
                        result['excluded'].append({**row,'status':'excluded','reason':'原尾盘日收盘回封，排除'})
                        continue
                if price_limits['up_limit']/price_limits['pre_close'] < 1.08:
                    result['excluded'].append({**row,'status':'excluded','reason':'同日涨停幅度不属于普通10cm主板'})
                    continue
                tail_max_pct = result['rules']['tail_max_pct'] if stage == 'tail' else None
                if tail_price_gate:
                    def cutoff_exclusion(price):
                        if price_limits['down_limit'] - .005 <= price <= price_limits['up_limit'] + .005:
                            return price_exclusion(price, price_limits, tail_max_pct=tail_max_pct)
                        return None

                    proof = reference.get('cutoff_quote') if mode == 'live' else None
                    reason = cutoff_exclusion(proof['close']) if proof else None
                    if not reason:
                        minute = source.minutes(code, day, cutoff_time, mode, cutoff_exclusion=cutoff_exclusion)
                        if minute['status'] == 'cutoff_exclusion':
                            price = minute['cutoff_bar']['close']
                            proof = dict(minute, kind='closed_minute_bar', close=price)
                            reason = cutoff_exclusion(price)
                            if reason is None:
                                raise data.EvidenceError('截止价未通过排除判据')
                    if reason:
                        price = proof['close']
                        result['excluded'].append(dict(
                            row, status='excluded', reason=reason, price=price,
                            pct_chg=round((price / price_limits['pre_close'] - 1) * 100, 4),
                            limits=price_limits, reference_day=cal['previous'], cutoff_evidence=proof))
                        continue
                else:
                    minute = source.minutes(code, day, cutoff_time, mode)
                bars = minute['rows']
                last = bars[-1]
                pct = (last['close']/price_limits['pre_close']-1)*100
                row.update(minute=minute,limits=price_limits,pct_chg=round(pct,4),
                           price=last['close'],open=bars[0]['open'],high=max(b['high'] for b in bars),
                           low=min(b['low'] for b in bars),amount=sum(b['amount'] for b in bars),
                           reference_day=cal['previous'])
                if sum(b['vol'] for b in bars)<=0:
                    raise data.EvidenceError('窗口无成交，不能判断承接或反包')
                reason = price_exclusion(last['close'], price_limits, tail_max_pct=tail_max_pct)
                if reason:
                    result['excluded'].append({**row,'status':'excluded','reason':reason})
                else:
                    result['candidates'].append(row)
            except (data.EvidenceError,KeyError,TypeError,ValueError) as exc:
                reason = str(exc) if isinstance(exc,data.EvidenceError) else '股票证据结构非法'
                result['unverified'].append({**row,'status':'unverified','reason':reason,
                                             'source_attempts':getattr(exc,'attempts',[])})
        result['candidate_coverage']=not result['unverified']
        if result['candidate_coverage']:
            result['candidate_count']=len(result['candidates'])
        else:
            result['gaps'].append(f'{len(result["unverified"])}只原始对象证据未完成，候选总数未计算')
        payload = {k: result[k] for k in ('stage','date','mode','cutoff','rules','context','candidates')}
        result['model_input']=payload
        if result['unverified']:
            result['evaluation']={'status':'not_run','reason':'候选覆盖不完整'}
        elif not result['candidates']:
            result['evaluation']={'status':'not_needed','reason':'完整证据下无数值候选'}
        elif no_llm:
            result['evaluation']={'status':'skipped','reason':'用户显式关闭LLM，未完成评估'}
        else:
            result['evaluation']=evaluator(payload)
        if result['candidates'] and result['evaluation']['status'] != 'complete':
            result['gaps'].append('LLM未完成有效结构化评估')
        result['pk'] = rank_candidates(result, no_llm, ranker)
        if result['pk']['status'] == 'incomplete':
            result['gaps'].append('本时点PK未完成：' + result['pk']['reason'])
        result['status']='partial' if result['gaps'] or result['unverified'] else 'complete'
        if result['status']=='complete' and result['evaluation']['status']=='complete':
            result['selected_codes']=[r['code'] for r in result['evaluation']['response']['candidates'] if r['assessment']=='观察']
            if parent:
                result['selected_codes']=[c for c in result['selected_codes'] if c in parent['selected_codes']]
        return result
    except (data.EvidenceError,KeyError,TypeError,ValueError,sqlite3.Error) as exc:
        result['status']='source_failed'
        result['selected_codes']=[]
        result['gaps'].append(str(exc) if isinstance(exc,data.EvidenceError) else '证据结构非法')
        return result
    finally:
        result['finished_at']=datetime.now(data.TZ).isoformat()
