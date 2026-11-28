"""中金所 IF/IM 各挂牌月、公开会员榜和收盘基差。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from datetime import date, datetime, time
import json
from pathlib import Path
import re
import zipfile

import requests

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ, archive, calendar, digest, number
from services.board_break_intraday.store import lock
from services.domestic_index_futures_sources import CONTRACT, provider
from services.volume_record.service import atomic_write

ROOT = Path(__file__).resolve().parents[2] / 'data/reports/domestic-index-futures'
PRODUCTS = {'IF': ('csi300', '000300.SH'), 'IM': ('csi1000', '000852.SH')}
SCHEMA = 'domestic-index-futures-v1'


def _missing(v):
    return v is None or isinstance(v,str) and v.strip() in ('', '-', '--')


def _integer(value, *, signed=False):
    n = number(value)
    if not n.is_integer() or (not signed and n < 0):
        raise ValueError('手数非法')
    return int(n)


def listings(rows, compact, product):
    active = {}
    for r in rows:
        code = str(r.get('ts_code',''))
        if not code.startswith(product) or not CONTRACT.fullmatch(code):
            continue
        if (r.get('symbol') != code[:-4] or r.get('exchange') != 'CFFEX' or r.get('fut_code') != product):
            raise ValueError('挂牌证券身份不符')
        start, end = r['list_date'], r['delist_date']
        for d in (start,end):
            datetime.strptime(d, '%Y%m%d')
        if start > end or end[2:6] != code[2:6]:
            raise ValueError('挂牌有效期或最后交易年月不符')
        if start <= compact <= end:
            if code in active:
                raise ValueError('重复挂牌合约')
            active[code] = dict(list_date=start, delist_date=end)
    return active


def standard_months(codes, day):
    """以来源已列出的近月核通常四个月份，只做覆盖检查。"""
    if len(codes) != 4:
        return False
    months = sorted((int(k[2:4])*12 + int(k[4:6])-1) for k in codes)
    target = date.fromisoformat(day)
    current = (target.year % 100)*12 + target.month-1
    first = months[0]
    if first not in (current, current+1):
        return False
    quarter = next(m for m in range(first+2, first+5) if m % 3 == 2)
    return months == [first, first+1, quarter, quarter+3]


def daily_bar(r, day, code):
    if r.get('ts_code') != code or r.get('trade_date') != day:
        raise ValueError('行情合约或日期不符')
    data = {k:number(r.get(k), positive=True) for k in ('open','high','low','close','settle','pre_settle')}
    if not data['low'] <= min(data['open'],data['close']) <= max(data['open'],data['close']) <= data['high']:
        raise ValueError('期货日线价格范围错误')
    for col in ('oi','vol','oi_chg'):
        data[col] = _integer(r.get(col), signed=col=='oi_chg')
    return data


def member_book(rows, compact, code):
    sides = {'long':{}, 'short':{}}
    for r in rows:
        if r.get('trade_date') != compact or r.get('symbol') != code[:-4] or r.get('exchange') != 'CFFEX':
            raise ValueError('会员榜日期、交易所或具体合约不符')
        who = r.get('broker')
        if not isinstance(who,str) or not who.strip() or who.strip() in ('合计','总计','小计','-'):
            raise ValueError('会员身份不符')
        who = who.strip()
        for side, members in sides.items():
            size, change = r.get(side+'_hld'), r.get(side+'_chg')
            if _missing(size) and _missing(change):
                continue
            if _missing(size):
                raise ValueError('持仓数量缺失，有增减无数量')
            row = dict(position=_integer(size), change=None if _missing(change) else _integer(change,signed=True))
            if side+'_rank' in r:
                row['rank'] = _integer(r[side+'_rank'])
                if not 1 <= row['rank'] <= 20 or any(row['rank']==m.get('rank') for m in members.values()):
                    raise ValueError('披露名次错误或重复')
            if who in members:
                raise ValueError('同合约同侧会员重复')
            members[who] = row
    if not any(sides.values()):
        raise ValueError('没有可核验的合约多空会员记录')
    for side in sides.values():
        if len(side) > 20:
            raise ValueError('单侧会员披露数量超限')
    return sides


def _attempt(api, params, transport, clock):
    receipt = dict(source=api, params=params, requested_at=clock().isoformat())
    compact = params.get('trade_date','')
    if api == 'cffex_members':
        receipt['source_url'] = f"https://www.cffex.com.cn/sj/ccpm/{compact[:6]}/{compact[6:]}/{params['product']}_1.csv"
    if api == 'cffex_daily':
        receipt['source_url'] = f"https://www.cffex.com.cn/fzjy/mrhq/{compact[:6]}/{compact[6:]}/{compact}_1.csv"
    if api == 'cffex_month':
        receipt['source_url'] = f"https://www.cffex.com.cn/sj/historysj/{compact[:6]}/zip/{compact[:6]}.zip"
    try:
        rows, meta = transport(api, params)
        if not isinstance(rows,list):
            raise ValueError('源结果不是列表')
        receipt.update(meta=meta, rows=len(rows), rows_sha256=digest(rows), status='received' if rows else 'empty')
    except (OSError,requests.RequestException,ValueError,KeyError,TypeError,RuntimeError,zipfile.BadZipFile) as exc:
        rows = []
        # 运行回执不携带供应商异常正文中的连接或凭据。
        message = str(exc) if isinstance(exc,ValueError) and not isinstance(exc,requests.RequestException) else type(exc).__name__
        if isinstance(exc,requests.exceptions.ProxyError):
            message = '代理连接失败'
        elif isinstance(exc,requests.ConnectionError):
            message = '来源域名解析失败' if 'NameResolutionError' in repr(exc) else '来源连接失败'
        elif isinstance(exc,requests.Timeout):
            message = '来源超时'
        elif isinstance(exc,RuntimeError):
            message = '上游返回错误（权限/配额/业务错误待核）'
        receipt.update(status='source_failed', error=message)
    receipt['received_at'] = clock().isoformat()
    return receipt, rows


def _query_many(specs, transport, clock):
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {key:pool.submit(_attempt,api,params,transport,clock) for key,api,params in specs}
        return {key:f.result() for key,f in pending.items()}


def _load_old(path, day):
    if not path.is_file():
        return {}
    obj = json.loads(path.read_text())
    sha = obj.pop('sha256')
    if sha != digest(obj) or obj.get('date') != day or obj.get('schema') != SCHEMA:
        raise ValueError('旧日档日期、版本或签名错误')
    return obj


def _keep(records, key, data, receipt, gaps):
    if data:
        old = records.get(key)
        if old and old['data'] != data:
            gaps.append(key+'：新旧同日来源冲突，保留原值')
        elif not old:
            records[key] = dict(data=data, receipt=receipt)


def _merge_members(records, key, data, receipt, gaps):
    bucket = records.setdefault(key, {'long':{}, 'short':{}})
    witnesses = bucket.setdefault('full_side_receipts', {})
    for side, members in data.items():
        existing = bucket[side]
        if not members:
            continue
        if len(set(existing) | set(members)) > 20:
            gaps.append(key+'：同侧公开会员集合冲突，保留旧榜')
            continue
        conflict = False
        for name, item in members.items():
            old = existing.get(name)
            if old and any(old['data'].get(k) is not None and v is not None and old['data'][k] != v for k,v in item.items()):
                gaps.append(key+'：新旧同一会员同侧持仓冲突，保留原值')
                conflict = True
                continue
            if old and any(k not in item or item[k] is None for k in old['data'] if old['data'][k] is not None):
                continue
            if not old or item != old['data']:
                existing[name] = dict(data=item,receipt=receipt)
        if (not conflict and len(members) == 20 and
                all(item['change'] is not None for item in members.values())):
            witnesses[side] = receipt


def _bars(rows, product, compact, receipt, result, gaps):
    candidates = {}
    for row in rows:
        code = str(row.get('ts_code',''))
        if CONTRACT.fullmatch(code) and code.startswith(product):
            candidates.setdefault(code,[]).append(row)
    for code, block in candidates.items():
        try:
            if len(block) != 1:
                raise ValueError('重复行情')
            _keep(result, code, daily_bar(block[0],compact,code), receipt, gaps)
        except (ValueError,KeyError,TypeError) as exc:
            gaps.append(f'{code}：{exc}')


def _spot(conn, day, product):
    key, code = PRODUCTS[product]
    raw, evidence = archive(conn,day)
    item = raw['indices'][key]
    if item.get('_source') != 'tushare:index_daily' or item.get('code') != code or item.get('trade_date') != day:
        raise ValueError('现货来源、指数或交易日错误')
    return (dict(code=code,close=number(item['close'],positive=True)),
            dict(evidence,upstream=item['_source'],post_generated_at=raw.get('generated_at'),record=item))


def collect(day, actor, *, root=ROOT, db_path=None, dry_run=False, clock=None, transport=provider, connect=get_readonly_connection):
    if not isinstance(actor,str) or not actor.strip():
        raise ValueError('请求者不能为空')
    target = date.fromisoformat(day)
    clock = clock or (lambda: datetime.now(TZ))
    if clock().astimezone(TZ) < datetime.combine(target,time(16),tzinfo=TZ):
        return dict(date=day,status='skipped',reason='尚未到目标日上海16:00')
    compact, root = target.strftime('%Y%m%d'), Path(root)
    path = root/(day+'.json')
    with closing(connect(db_path)) as conn:
        if not calendar(conn,day)['is_open']:
            return dict(date=day,status='skipped',reason='SSE休市')
        with (nullcontext() if dry_run else lock(root,day)):
            old = _load_old(path,day)
            if old.get('status') == 'complete':
                return dict(old,sha256=digest(old),path=str(path))
            result = dict(schema=SCHEMA, date=day,input_by=actor.strip(), products={},
                          attempts=old.get('attempts',[]), receipts=old.get('receipts',{}),
                          scope='IF/IM逐挂牌合约；会员为交易所公开披露范围，单位手。收盘基差=期货收盘−对应现货收盘，单位指数点。')
            gaps, used = [], []
            def keep_receipt(rec):
                key = digest(rec)
                result['receipts'][key] = rec
                used.append(key)
                return key
            specs=[('listing','fut_basic',dict(exchange='CFFEX',fut_type='1')),
                   ('quotes','fut_daily',dict(exchange='CFFEX',trade_date=compact))]
            sources = _query_many(specs,transport,clock)
            listing_receipt, listing_rows = sources['listing']
            quote_receipt, quote_rows = sources['quotes']
            listing_key, quote_key = keep_receipt(listing_receipt),keep_receipt(quote_receipt)
            jobs=[]
            for product,(index_key,index_code) in PRODUCTS.items():
                previous=old.get('products',{}).get(product,{})
                obj = result['products'][product] = {k:previous.get(k,{}) for k in ('listing','spot','bars','members')}
                try:
                    spot,ev=_spot(conn,day,product)
                    ev.update(source='daily_market.indices',read_at=clock().isoformat())
                    _keep(obj['spot'],index_code,spot,keep_receipt(ev),gaps)
                except (KeyError,TypeError,ValueError):
                    jobs.append((product+'spot','index_daily',dict(ts_code=index_code,trade_date=compact)))
                try:
                    listed = listings(listing_rows,compact,product)
                    _keep(obj['listing'],product,listed,listing_key,gaps)
                except (ValueError,KeyError,TypeError) as exc:
                    gaps.append(product+'挂牌名单：'+str(exc))
                _bars(quote_rows,product,compact,quote_key,obj['bars'],gaps)
                active=set(obj['listing'].get(product,{}).get('data',{}))
                if not obj['bars'] or active-set(obj['bars']):
                    if not any(k=='cffex_daily' for k,_,_ in jobs):
                        jobs.append(('cffex_daily','cffex_daily',dict(trade_date=compact)))
                for code in sorted(active|set(obj['bars'])):
                    jobs.append((code,'fut_holding',dict(exchange='CFFEX',trade_date=compact,symbol=code[:-4])))
            follow=_query_many(jobs,transport,clock)
            if ('cffex_daily' in follow and not follow['cffex_daily'][1]
                    and compact[:6] < clock().astimezone(TZ).strftime('%Y%m')):
                follow['cffex_month'] = _attempt('cffex_month', dict(trade_date=compact), transport, clock)
            for key,(rec,rows) in follow.items():
                evidence=keep_receipt(rec)
                if key in ('cffex_daily','cffex_month'):
                    for p in PRODUCTS:
                        _bars(rows,p,compact,evidence,result['products'][p]['bars'],gaps)
                elif key.endswith('spot'):
                    p=key[:2]
                    code=PRODUCTS[p][1]
                    if len(rows)==1 and rows[0].get('ts_code')==code and rows[0].get('trade_date')==compact:
                        try:
                            _keep(result['products'][p]['spot'],code,dict(code=code,close=number(rows[0]['close'],positive=True)),evidence,gaps)
                        except (KeyError,ValueError) as exc:
                            gaps.append(p+'现货：'+str(exc))
                elif rows:
                    try:
                        data=member_book(rows,compact,key)
                        _merge_members(result['products'][key[:2]]['members'],key,data,evidence,gaps)
                    except (KeyError,TypeError,ValueError) as exc:
                        gaps.append(key+'会员：'+str(exc))
            for p in PRODUCTS:
                obj=result['products'][p]
                active=set(obj['listing'].get(p,{}).get('data',{}))|set(obj['bars'])
                if not active or any(not _member_complete(obj['members'].get(k,{})) for k in active):
                    rec,rows=_attempt('cffex_members',dict(product=p,trade_date=compact),transport,clock)
                    ref=keep_receipt(rec)
                    codes={str(r.get('symbol',''))+'.CFX' for r in rows}
                    for code in sorted(k for k in codes if k.startswith(p) and CONTRACT.fullmatch(k)):
                        try:
                            data=member_book([r for r in rows if r['symbol']==code[:-4]],compact,code)
                            _merge_members(obj['members'],code,data,ref,gaps)
                        except (KeyError,TypeError,ValueError) as exc:
                            gaps.append(code+'会员：'+str(exc))
                update_product(obj,p,day)
            statuses=[p['status'] for p in result['products'].values()]
            status='complete' if all(s=='complete' for s in statuses) and not gaps else 'source_failed' if all(s=='source_failed' for s in statuses) else 'partial'
            result.update(status=status,gaps=gaps)
            result['attempts'].append(dict(input_by=actor,finished_at=clock().isoformat(),receipts=sorted(set(used)),status=status,gaps=gaps))
            result['sha256']=digest(result)
            if not dry_run:
                atomic_write(path,json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))
            return dict(result,path=None if dry_run else str(path))


def _member_complete(book):
    return all(book.get('full_side_receipts', {}).get(side) and len(book.get(side, {})) == 20
               for side in ('long','short'))


def update_product(obj,p,day):
    listed=obj['listing'].get(p,{}).get('data',{})
    codes=sorted(set(listed)|set(obj['bars'])|set(obj['members']))
    spot=obj['spot'].get(PRODUCTS[p][1],{}).get('data',{})
    obj['basis_points']={code:round(obj['bars'][code]['data']['close']-spot['close'],6)
                         if spot and code in obj['bars'] and obj['bars'][code]['data']['vol'] > 0
                         else None for code in codes}
    standard = standard_months(listed, day)
    obj['coverage']=dict(listed_contracts=len(listed) if listed else None, usual_four_months_verified=standard,
                         observed_contracts=len(codes),
                         daily_bars=len(obj['bars']), cash_close=spot.get('close'),
                         contract_member_books=sum(_member_complete(obj['members'].get(k,{})) for k in codes),
                         disclosed_rows={k:{s:len(v.get(s,{})) for s in ('long','short')} for k,v in obj['members'].items()},
                         basis_comparisons=sum(v is not None for v in obj['basis_points'].values()))
    complete=(standard and set(listed)==set(codes) and spot and
              all(obj['basis_points'].get(k) is not None and _member_complete(obj['members'].get(k,{})) for k in codes))
    obj['status']='complete' if complete else 'partial' if spot or codes else 'source_failed'
