"""IF/IM 日行情与会员排名的只读来源。"""
import csv
import hashlib
from io import BytesIO, StringIO
import re
import zipfile
from xml.etree import ElementTree

import requests

from utils.network_env import use_http_proxy_for_data_collection

CONTRACT = re.compile(r'(?:IF|IM)\d{2}(?:0[1-9]|1[0-2])\.CFX')
FIELDS = {
    'fut_basic': 'ts_code,symbol,exchange,fut_code,list_date,delist_date',
    'fut_daily': 'trade_date,ts_code,open,high,low,close,settle,pre_settle,vol,oi,oi_chg',
    'fut_holding': 'trade_date,symbol,exchange,broker,long_hld,long_chg,short_hld,short_chg',
    'index_daily': 'trade_date,ts_code,close',
}


def _records(frame):
    return frame.astype(object).where(frame.notna(), None).to_dict('records')


def file_body(url, limit):
    with requests.Session() as session:
        session.trust_env = use_http_proxy_for_data_collection()
        with session.get(url, timeout=(3, 7), stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                raise ValueError(f'中金所HTTP {r.status_code}')
            parts, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ValueError('中金所文件超出预期大小')
                parts.append(chunk)
            return b''.join(parts)


def cffex_month(body, compact):
    if not zipfile.is_zipfile(BytesIO(body)):
        raise ValueError('中金所月包尚未发布或非ZIP')
    member = compact + '_1.csv'
    with zipfile.ZipFile(BytesIO(body)) as z:
        if z.namelist().count(member) != 1 or z.getinfo(member).file_size > 4_000_000:
            raise ValueError('中金所月包中目标日交易文件缺失或重复')
        csv_text = z.read(member).decode('gb18030')
    return cffex_csv(csv_text, compact)


def cffex_csv(csv_text, compact):
    names = {'开盘价':'open', '最高价':'high', '最低价':'low', '今收盘':'close',
             '今结算':'settle', '前结算':'pre_settle', '成交量':'vol', '持仓量':'oi', '持仓变化':'oi_chg'}
    aliases = {'今开盘':'开盘价', '收盘价':'今收盘', '今收盘价':'今收盘', '结算价':'今结算', '今结算价':'今结算',
               '前结算价':'前结算', '持仓量变化':'持仓变化', '持仓量增减':'持仓变化'}
    out = []
    for raw in csv.DictReader(StringIO(csv_text)):
        row = {aliases.get(k.strip(), k.strip()): v.strip() for k,v in raw.items() if k and v is not None}
        code = row.get('合约代码','') + '.CFX'
        if not CONTRACT.fullmatch(code):
            continue
        if '交易日' in row and row['交易日'].replace('-','') != compact:
            raise ValueError('中金所日行情交易日错位')
        if any(key not in row for key in names):
            raise ValueError('中金所日行情字段缺失')
        out.append(dict(ts_code=code, trade_date=compact, **{dest:row[src] for src,dest in names.items()}))
    return out


def checked_daily(csv_body, xml_body, compact):
    """日CSV的合约量价须与带日期XML逐合约一致。"""
    rows = cffex_csv(csv_body.decode('gb18030'), compact)
    declared = {}
    if b'<!DOCTYPE' in xml_body.upper() or b'<!ENTITY' in xml_body.upper():
        raise ValueError('中金所日期回执含不支持的结构')
    try:
        nodes = ElementTree.fromstring(xml_body).findall('.//dailydata')
    except ElementTree.ParseError as exc:
        raise ValueError('中金所日期回执无法解析') from exc
    for node in nodes:
        code = (node.findtext('instrumentid') or '').strip().upper()+'.CFX'
        if not CONTRACT.fullmatch(code):
            continue
        if node.findtext('tradingday','').strip() != compact or code in declared:
            raise ValueError('中金所日期回执错日或重复')
        declared[code] = tuple(float(node.findtext(k,'')) for k in ('volume','closeprice','openinterest'))
    actual = {r['ts_code']:tuple(float(r[k]) for k in ('vol','close','oi')) for r in rows}
    if not rows or len(actual)!=len(rows) or actual!=declared:
        raise ValueError('中金所日CSV与同日XML合约或行情不一致')
    return rows


def cffex_members(body, compact, product):
    """中金所CSV把三份独立榜单并列在同一行。按列名核验后拆开两份持仓榜。"""
    rows = []
    header = None
    source = iter(csv.reader(StringIO(body.decode('gb18030'))))
    for values in source:
        v = [str(x).strip().lstrip('\ufeff') for x in values]
        if not v:
            continue
        if v[:3] == ['交易日','合约','排名'] and len(v)>=12 and v[3:12:3] == ['成交量排名','持买单量排名','持卖单量排名']:
            sub = [k.strip() for k in next(source,[])]
            if len(sub)<12:
                raise ValueError('中金所会员明细二级表头缺失')
            header = v[:3] + sub[3:12]
            continue
        if v[0] == '交易日':
            header = v
            continue
        if not re.fullmatch(r'\d{8}', v[0]):
            continue
        if header and header[:2]==['交易日','合约'] and header[2:3] not in (['排名'],['名次']):
            continue  # 同文件另载合约级统计，不是逐会员记录
        if len(v) < 12 or not header or len(header) < 12:
            raise ValueError('中金所会员表缺少完整的列头或数据')
        if (header[1] != '合约' or header[2] not in ('名次','排名') or '会员' not in header[6] or
                '持买' not in header[7] or '增减' not in header[8] or '会员' not in header[9] or
                '持卖' not in header[10] or '增减' not in header[11]):
            raise ValueError('中金所会员表多空列未核验')
        if not v[2].isdigit():  # 表尾合计、产品合计各自不作为会员
            continue
        code = v[1].upper()
        if v[0] != compact or not re.fullmatch(product + r'\d{2}(?:0[1-9]|1[0-2])',code):
            raise ValueError('中金所会员表交易日或合约不符')
        for offset, side in [(6,'long'),(9,'short')]:
            broker, position, change = v[offset:offset+3]
            if broker and broker not in ('-','合计','总计'):
                rows.append(dict(trade_date=compact, symbol=code, exchange='CFFEX', broker=broker,
                                 **{side+'_hld':position, side+'_chg':change, side+'_rank':int(v[2])}))
    return rows


def provider(api, params):
    compact = params.get('trade_date','')
    if api == 'cffex_daily':
        base = f'https://www.cffex.com.cn/fzjy/mrhq/{compact[:6]}/{compact[6:]}/'
        csv_url, xml_url = base+compact+'_1.csv', base+'index.xml'
        csv_body = file_body(csv_url, 4_000_000)
        xml_body = file_body(xml_url, 6_000_000)
        return checked_daily(csv_body, xml_body, compact), dict(url=csv_url, sha256=hashlib.sha256(csv_body).hexdigest(),
            date_evidence_url=xml_url, date_evidence_sha256=hashlib.sha256(xml_body).hexdigest())
    if api == 'cffex_month':
        url = f"https://www.cffex.com.cn/sj/historysj/{compact[:6]}/zip/{compact[:6]}.zip"
        body = file_body(url, 32_000_000)
        return cffex_month(body, compact), dict(url=url, member=compact+'_1.csv', sha256=hashlib.sha256(body).hexdigest())
    if api == 'cffex_members':
        product = params['product']
        url = f'https://www.cffex.com.cn/sj/ccpm/{compact[:6]}/{compact[6:]}/{product}_1.csv'
        body = file_body(url, 3_000_000)
        return cffex_members(body, compact, product), dict(url=url, sha256=hashlib.sha256(body).hexdigest())
    import os
    from main import load_config
    from providers.tushare_transport import TushareMirrorClient
    token = load_config().get('providers',{}).get('tushare',{}).get('token') or os.getenv('TUSHARE_TOKEN')
    if not token:
        raise ValueError('Tushare 凭据未配置')
    rows = _records(TushareMirrorClient(token, timeout=7).query(api, fields=FIELDS[api], **params))
    limit = {'fut_basic':10000, 'fut_daily':2000, 'fut_holding':2000, 'index_daily':8000}[api]
    if len(rows) >= limit:
        raise ValueError('Tushare结果达分页上限，完整性未核验')
    return rows, dict(fields=FIELDS[api])
