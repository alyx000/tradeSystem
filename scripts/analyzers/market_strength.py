"""盘后≥5%/≤-5%广度事实与只读历史，不授予核心或交易身份。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

from analyzers import strong_stock_feedback as sf
from analyzers.ipo_effect import _date
from utils import is_st_stock
from analyzers.strong_stock_feedback_history import _envelope
from services.volume_concentration.capacity_history import _open_days

KEY = 'market_strength'
SCHEMA = 'market-strength-v1'
UNKNOWN = '未分类'
NOTE = ('[事实] 当日收盘涨跌幅≥5%及≤-5%，含边界，按来源未展示舍入前数值比较；'
        '沪深北A股剔ST/退市/B股，含上市首日，分母为当日有效行情样本。'
        '申万二级按采集当次映射保存，未分类单列，不用当前成分重写历史。'
        '5/10/20日按开放日锁窗，缺日不补0、不向前凑足；窗口比例按股票日合并。'
        '跨日样本和行业归属会变，变化不代表同股收益、二波确认或买卖信号。')


def failed(day, reason, status='source_failed'):
    return dict(schema=SCHEMA, trade_date=day, status=status, gaps=[str(reason)],
                coverage={}, sources={}, market=None, sectors={}, rows=[])


def _stats(rows):
    n=len(rows)
    up=sum(r['pct_chg'] >= 5 for r in rows)
    down=sum(r['pct_chg'] <= -5 for r in rows)
    return dict(sample_count=n, up_count=up, down_count=down,
                up_pct=100*up/n if n else None, down_pct=100*down/n if n else None)


def _aggregate(rows):
    groups=defaultdict(list)
    for row in rows:groups[row['industry']].append(row)
    return _stats(rows), {name:_stats(group) for name,group in sorted(groups.items())}


def calculate(quotes, identities, st_rows, industry_map, day, *, sources=None, min_count=4000):
    result=failed(day,'')
    result.update(gaps=[],sources=sources or {})
    try:
        target=date.fromisoformat(day)
        if not all(isinstance(x,list) and x for x in (quotes,identities,st_rows)):
            raise ValueError('行情、证券宇宙或ST名单缺失')
        basic={}
        for row in identities:
            code=sf._row_code(row)
            if code in basic:raise ValueError('证券宇宙身份重复')
            listed=_date(row['list_date']);delisted=_date(row['delist_date']) if row.get('delist_date') else None
            name=row.get('name')
            if not isinstance(name,str) or not name.strip():raise ValueError('证券名称缺失')
            basic[code]=dict(name=name,active=listed<=target and (delisted is None or delisted>target))
        st=set()
        for row in st_rows:
            code=sf._row_code(row)
            if code in st:raise ValueError('ST身份重复')
            if row.get('trade_date') and _date(row['trade_date'])!=target:raise ValueError('ST日期错位')
            st.add(code)
        eligible={c for c,b in basic.items() if b['active'] and c not in st and not is_st_stock(b['name']) and '退' not in b['name']
                  and c.startswith(('0','3','6','4','8','92'))
                  and not c.startswith(('200','201','900'))}
        seen=set();valid={};invalid=[]
        for row in quotes:
            code=sf._row_code(row)
            if code in seen:raise ValueError('行情身份重复')
            seen.add(code)
            if _date(row['trade_date'])!=target:raise ValueError('行情日期错位')
            if code not in basic:raise ValueError('行情无法匹配证券身份')
            try:
                close=sf._number(row['close'],True);previous=sf._number(row['pre_close'],True)
                pct=sf._number(row['pct_chg'])
                if abs((close/previous-1)*100-pct)>.02:raise ValueError('昨收与涨跌幅不一致')
            except (ValueError,TypeError,KeyError,OverflowError):
                invalid.append(code);continue
            valid[code]=pct
        covered=eligible & valid.keys();missing=sorted(eligible-covered)
        result['coverage']=dict(raw_count=len(quotes),valid_count=len(valid),eligible_count=len(eligible),
                                sample_count=len(covered),invalid_codes=invalid,missing_codes=missing,
                                quote_coverage=len(covered)/len(eligible) if eligible else None,
                                valid_ratio=len(valid)/len(quotes),minimum_count=min_count)
        if (len(valid)<min_count or len(eligible)<min_count or len(valid)/len(quotes)<.98
                or not eligible or len(covered)/len(eligible)<.98):
            raise ValueError('有效行情/宇宙数量不足或有效率/宇宙覆盖低于98%')
        mapped={}
        if not isinstance(industry_map,dict) or not industry_map:
            result['gaps'].append('申万行业来源缺失')
        else:
            for key,value in industry_map.items():
                code=sf._code(key)
                if code in mapped:raise ValueError('行业映射身份重复')
                industry=value.get('sw_l2') if isinstance(value,dict) else None
                mapped[code]=industry.strip() if isinstance(industry,str) and industry.strip() not in {'','UNCLASSIFIED',UNKNOWN} else UNKNOWN
        rows=[dict(code=c,name=basic[c]['name'],pct_chg=valid[c],industry=mapped.get(c,UNKNOWN)) for c in sorted(covered)]
        unknown=sum(r['industry']==UNKNOWN for r in rows)
        result['coverage']['unclassified_count']=unknown
        if missing:result['gaps'].append(f'宇宙缺行情{len(missing)}只，停牌未逐只核验')
        if invalid:result['gaps'].append(f'无效行情{len(invalid)}只')
        if unknown:result['gaps'].append(f'行业未分类{unknown}只')
        market,sectors=_aggregate(rows)
        result.update(status='partial' if result['gaps'] else 'complete',rows=rows,market=market,sectors=sectors)
        return result
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        result.update(status='source_failed',gaps=[*result['gaps'],str(exc)],market=None,sectors={},rows=[])
        return result


def _source(result):
    return dict(status='success' if getattr(result,'success',False) else 'source_failed',
                source=getattr(result,'source',None),fetched_at=getattr(result,'fetched_at',None),
                error=getattr(result,'error',None))


def collect(registry, day, *, quote_result, stock_st_result, now=None, db_path=None):
    """复用同批日线/ST结果；新增两种批量身份来源，随原盘后信封落盘。"""
    from db.connection import get_readonly_connection
    sources={}
    try:
        current=now or datetime.now(ZoneInfo('Asia/Shanghai'))
        if current.tzinfo is None:raise ValueError('运行时刻缺时区')
        current=current.astimezone(ZoneInfo('Asia/Shanghai'));target=date.fromisoformat(day)
        if target>current.date():raise ValueError('目标日尚未到达')
        conn=get_readonly_connection(db_path)
        try:
            record=conn.execute("SELECT is_open FROM trade_calendar WHERE exchange='SSE' AND date=?",(day,)).fetchone()
            if record and record[0]==0:return failed(day,'非交易日','skipped')
            _open_days(conn,day,1)
        finally:conn.close()
        if target==current.date() and current.hour<16:
            return failed(day,'尚未收盘','skipped')
        results={'quotes':quote_result,'st':stock_st_result}
        for key,r in results.items():
            sources[key]=_source(r)
            if not getattr(r,'success',False) or not isinstance(r.data,list) or not r.data:
                raise ValueError(f'{key}来源失败:{getattr(r,"error","")}')
        basic=registry.call('get_stock_universe_as_of',day)
        sources['identity']=_source(basic)
        if not basic.success:raise ValueError('证券宇宙来源失败:'+str(getattr(basic,'error','')))
        try:
            mapping=registry.call('get_stock_sw_industry_map')
            industry=mapping.data if mapping.success else None
            sources['industry']=_source(mapping)
        except Exception as exc:
            industry=None
            sources['industry']=dict(status='source_failed',error=str(exc))
        return calculate(quote_result.data,basic.data,stock_st_result.data,industry,day,sources=sources)
    except Exception as exc:
        result=failed(day,str(exc));result['sources']=sources
        return result


def view(block, day):
    if block is None:return failed(day,'旧快照未计算','not_computed')
    try:
        if not isinstance(block,dict) or block.get('schema')!=SCHEMA or block.get('trade_date')!=day:
            raise ValueError('统计日期或版本不匹配')
        if block.get('status') not in {'complete','partial','source_failed','skipped','not_computed','missing-data'}:
            raise ValueError('统计状态非法')
        if block['status'] not in {'complete','partial'}:
            out=failed(day,'；'.join(map(str,block.get('gaps',[]))),block['status'])
            # 失败不提供统计，但保留有限的审计元数据，不能把故障原因洗掉。
            sources=block.get('sources')
            if isinstance(sources,dict):
                out['sources']={k:{field:v for field,v in value.items() if field in {'status','source','fetched_at','error'} and (v is None or isinstance(v,str))}
                                for k,value in sources.items() if isinstance(k,str) and isinstance(value,dict)}
            coverage=block.get('coverage')
            if isinstance(coverage,dict):
                out['coverage']={k:v for k,v in coverage.items() if k in {'raw_count','valid_count','eligible_count','sample_count','minimum_count','unclassified_count'} and type(v) is int and v>=0}
            return out
        rows=block['rows'];seen=set()
        if not isinstance(rows,list) or not rows:raise ValueError('有效统计缺逐股来源')
        for r in rows:
            code=sf._row_code(r)
            if code in seen:raise ValueError('统计身份重复')
            seen.add(code);sf._number(r['pct_chg'])
            if type(r['pct_chg']) not in (float,int) or not isinstance(r['industry'],str) or not r['industry'].strip():
                raise ValueError('逐股数值或行业非法')
        market,sectors=_aggregate(rows)
        if market!=block['market'] or sectors!=block['sectors']:raise ValueError('汇总与逐股来源不一致')
        coverage=block['coverage']
        for key in ('raw_count','valid_count','eligible_count','sample_count','minimum_count','unclassified_count'):
            if type(coverage.get(key)) is not int or coverage[key]<0:raise ValueError('覆盖计数非法')
        raw,valid,eligible,n=(coverage[k] for k in ('raw_count','valid_count','eligible_count','sample_count'))
        minimum=coverage['minimum_count']
        if (minimum<=0 or valid<minimum or eligible<minimum or not 0<n<=valid<=raw or n>eligible
                or n!=len(rows) or valid/raw<.98 or n/eligible<.98
                or coverage['valid_ratio']!=valid/raw or coverage['quote_coverage']!=n/eligible
                or coverage['unclassified_count']!=sum(r['industry']==UNKNOWN for r in rows)):
            raise ValueError('覆盖统计不一致或未过质量门')
        if not isinstance(block['gaps'],list) or any(not isinstance(g,str) for g in block['gaps']):raise ValueError('缺口结构非法')
        if block['status']=='complete' and (block['gaps'] or n!=eligible or valid!=raw or coverage['unclassified_count']):
            raise ValueError('完整状态与覆盖缺口冲突')
        return block
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        return failed(day,str(exc))


def _db_block(conn, day):
    try:
        row=conn.execute('SELECT raw_data FROM daily_market WHERE date=?',(day,)).fetchone()
        if not row:return failed(day,'归档缺失','missing-data')
        obj=json.loads(row[0]);raw=obj.get('raw_data',obj)
        if obj.get('date',day)!=day:raise ValueError('信封日期错位')
        return view(raw.get(KEY),day)
    except (sqlite3.Error,ValueError,TypeError,KeyError,AttributeError) as exc:return failed(day,str(exc))


def _difference(before, current):
    if not before or not current:return None
    return dict(up_count=current['up_count']-before['up_count'],down_count=current['down_count']-before['down_count'],
                up_pp=current['up_pct']-before['up_pct'],down_pp=current['down_pct']-before['down_pct'])


def history(conn, day, *, current=None, daily_dir=None):
    """一个来源模式贯穿全部历史；当批current优先，缺日绝不换成最近一日。"""
    result=dict(schema=SCHEMA,date=day,status='source_failed',points=[],windows={},gaps=[])
    def read(d):
        if d==day and current is not None:return view(current,d)
        if daily_dir is None:return _db_block(conn,d)
        envelope=_envelope(daily_dir,d,{})
        return view(envelope['raw_data'].get(KEY),d) if isinstance(envelope,dict) else failed(d,'归档缺失或损坏',envelope)
    try:days=_open_days(conn,day,21)
    except (sqlite3.Error,ValueError,TypeError,AttributeError) as exc:
        result['gaps']=[str(exc)];result['current']=read(day)
        return result
    previous=None
    for d in days:
        block=read(d)
        known=block['status'] in {'complete','partial'}
        point=dict(date=d,status=block['status'],market=block['market'],sectors=block['sectors'],coverage=block['coverage'],gaps=block['gaps'],delta=None,sector_deltas={})
        if previous and known and previous['market']:
            point['delta']=_difference(previous['market'],point['market'])
            point['sector_deltas']={k:_difference(previous['sectors'][k],v) for k,v in point['sectors'].items() if k in previous['sectors']}
            point['comparison_status']='complete' if previous['status']==point['status']=='complete' else 'partial'
        result['points'].append(point);previous=point
    result['points']=result['points'][-20:];result['current']=block
    for n in (5,10,20):
        ps=result['points'][-n:];valid=[p for p in ps if p['market'] is not None]
        names=set().union(*(p['sectors'] for p in valid))
        def window_stats(stats):
            sample=sum(s['sample_count'] for s in stats);up=sum(s['up_count'] for s in stats);down=sum(s['down_count'] for s in stats)
            return dict(stock_days=sample if stats else None,up_count=up if stats else None,down_count=down if stats else None,up_pct=100*up/sample if sample else None,down_pct=100*down/sample if sample else None)
        result['windows'][str(n)]=dict(days=n,valid_days=len(valid),complete_days=sum(p['status']=='complete' for p in valid),
            market=window_stats([p['market'] for p in valid]),
            sectors={k:dict(valid_days=sum(k in p['sectors'] for p in valid),complete_days=sum(k in p['sectors'] and p['status']=='complete' for p in valid),**window_stats([p['sectors'][k] for p in valid if k in p['sectors']])) for k in names})
    result['status']='complete' if all(p['status']=='complete' for p in result['points']) else 'partial'
    return result


def load(day, *, daily_dir=None, current=None, db_path=None):
    from db.connection import get_readonly_connection
    try:
        conn=get_readonly_connection(db_path)
        try:return history(conn,day,current=current,daily_dir=daily_dir)
        finally:conn.close()
    except (sqlite3.Error,ValueError,OSError) as exc:
        return dict(schema=SCHEMA,date=day,status='source_failed',current=view(current,day),points=[],windows={},gaps=[str(exc)])


def card_context(report, industry):
    point=report['points'][-1] if report.get('points') else None
    current=report.get('current') or {}
    stats=(point['sectors'].get(industry) if point else current.get('sectors',{}).get(industry)) if industry and industry!=UNKNOWN else None
    status=point['status'] if point else current.get('status','not_computed')
    windows={n:dict(days=w['days'],**w['sectors'].get(industry,dict(valid_days=0,complete_days=0,stock_days=None,up_pct=None,down_pct=None))) for n,w in report.get('windows',{}).items()}
    if not stats and status in {'complete','partial'}:status='not_computed'
    if stats and status=='complete' and (report.get('status')!='complete' or set(windows)!={'5','10','20'}
            or any(w['complete_days']!=int(n) for n,w in windows.items())):
        status='partial'
    return dict(date=report['date'],industry=industry or UNKNOWN,status=status,
                stats=stats,delta=point['sector_deltas'].get(industry) if point else None,
                comparison_status=point.get('comparison_status','not_computed') if point else 'not_computed',
                windows=windows)


def valid_context(context, day):
    """旧档缺字段可降级，坏结构不能在渲染中制造数值或异常。"""
    import math
    if (not isinstance(context,dict) or context.get('date')!=day
            or context.get('status') not in {'complete','partial','source_failed','not_computed','missing-data','skipped'}
            or not isinstance(context.get('industry'),str) or not isinstance(context.get('windows'),dict)):
        return False
    def numeric(value):return type(value) in (int,float) and math.isfinite(value)
    if context.get('comparison_status','not_computed') not in {'complete','partial','not_computed'}:return False
    stats=context.get('stats')
    if stats is not None:
        if not isinstance(stats,dict) or context['status'] not in {'complete','partial'}:return False
        n=stats.get('sample_count')
        if type(n) is not int or n<=0:return False
        if not all(type(stats.get(k)) is int for k in ('up_count','down_count')) or stats['up_count']+stats['down_count']>n:return False
        for kind in ('up','down'):
            c,p=stats.get(kind+'_count'),stats.get(kind+'_pct')
            if type(c) is not int or not 0<=c<=n or not numeric(p) or abs(p-100*c/n)>1e-8:return False
    delta=context.get('delta')
    if delta is not None:
        if not isinstance(delta,dict) or any(not numeric(delta.get(k)) for k in ('up_count','down_count','up_pp','down_pp')):return False
    for key,w in context['windows'].items():
        if key not in {'5','10','20'} or not isinstance(w,dict):return False
        if type(w.get('valid_days')) is not int or not 0<=w['valid_days']<=int(key):return False
        if w.get('days')!=int(key) or type(w.get('complete_days')) is not int or not 0<=w['complete_days']<=w['valid_days']:return False
        for kind in ('up_pct','down_pct'):
            p=w.get(kind)
            if p is not None and (not numeric(p) or not 0<=p<=100):return False
    return True
