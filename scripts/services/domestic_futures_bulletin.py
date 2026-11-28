"""导入博易转载的指定交易日收盘表，保留网页原文及现货出处以供复核。"""
from contextlib import closing, nullcontext
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ, calendar, digest, number
from services.board_break_intraday.store import lock
from services.domestic_index_futures import PRODUCTS, ROOT, _load_old, _spot
from services.volume_record.service import atomic_write

SCHEMA = 'pobo-domestic-futures-reference-v1'
SOURCE = re.compile(r'webnewslt1\.pobo\.net\.cn \((https://webnewslt1\.pobo\.net\.cn/DATA/STOCK/[0-9]+\.HTML)\)')
TITLE = re.compile(r'中国金融期货交易所(\d{1,2})月(\d{1,2})日(IF|IM)期货收盘行情')
PUBLISHED = re.compile(r'发布时间[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})')
HEADER = ('合约代码', '开盘价', '最高价', '最低价', '收盘价', '结算价', '成交量', '成交额', '持仓量')
FIELDS = ('open', 'high', 'low', 'close', 'settle', 'volume', 'turnover_as_published', 'open_interest')
MAX_BYTES = 200_000


def _number(value, field):
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', value):
        raise ValueError(f'{field}缺值或不是有效数字')
    n = Decimal(value.replace(',', ''))
    if field in ('volume', 'open_interest'):
        if n != n.to_integral_value():
            raise ValueError(f'{field}手数应为整数')
        return int(n)
    if field != 'turnover_as_published' and n <= 0:
        raise ValueError(f'{field}价格无效')
    return number(float(n))


def _cells(line):
    return tuple(value.strip() for value in line.strip('|').split('|'))


def _content_sha(url, lines):
    # web.open 的检索标识、抓取描述与行号属于转储包装。保留正文行中的原始空白和顺序。
    body = [re.fullmatch(r'L\d+: ?(.*)', line)[1] for line in lines[2:]]
    return digest(dict(url=url, body=body))


def parse(text, day, now):
    lines = text.splitlines()
    address = SOURCE.fullmatch(lines[0]) if lines else None
    total = re.search(r'Total lines:\s*(\d+)\s*$', lines[1]) if len(lines) >= 3 else None
    if not address or not total:
        raise ValueError('需要博易指定域名页面的完整带行号网页文本')
    content = []
    for i, line in enumerate(lines[2:]):
        m = re.fullmatch(r'L(\d+):\s?(.*)', line)
        if not m or int(m[1]) != i:
            raise ValueError('网页正文行号缺失或重复')
        content.append(m[2].strip())
    if len(content) != int(total[1]) or len(content) < 7:
        raise ValueError('原网页正文行数不完整')
    title = TITLE.fullmatch(content[0])
    if sum(TITLE.fullmatch(line) is not None for line in content) != 1:
        raise ValueError('收盘行情标题缺失或重复')
    dates = [PUBLISHED.fullmatch(s) for s in content if s.startswith('发布时间')]
    target = date.fromisoformat(day)
    if not title or (int(title[1]), int(title[2])) != (target.month, target.day):
        raise ValueError('收盘行情标题交易日或产品与请求不符')
    if len(dates) != 1 or not dates[0]:
        raise ValueError('原文发布日期缺失、重复或格式不符')
    published = datetime(*(int(v) for v in dates[0].groups()), tzinfo=TZ)
    if published.date() != target or published.time() < time(15) or published > now:
        raise ValueError('公布日期或时间不符')
    product = title[3]
    headers = [i for i, line in enumerate(content) if _cells(line) == HEADER]
    if len(headers) != 1 or any(('合约代码' in s and _cells(s) != HEADER) for s in content):
        raise ValueError('表头缺失、重复或字段错误')
    ix = headers[0]
    if ix + 2 >= len(content) or _cells(content[ix+1]) != ('---',) * len(HEADER):
        raise ValueError('表头与数据列未对齐')
    rows = {}
    tail = False
    for line in content[ix+2:]:
        if line in ('相关资讯:', '相关资讯：'):
            tail = True
            continue
        if not line:
            continue
        if tail and '|' not in line and not re.match(r'(IF|IM)\d', line):
            continue
        if tail or '|' not in line:
            raise ValueError('收盘表内有无法辨认的合约行')
        values = _cells(line)
        code = values[0]
        if len(values) != len(HEADER) or not re.fullmatch(product + r'\d{2}(?:0[1-9]|1[0-2])', code):
            raise ValueError('具体合约月份、产品或列数不符')
        if code in rows or code[2:] < target.strftime('%y%m'):
            raise ValueError('重复或过期合约')
        row = {key: _number(v, key) for key, v in zip(FIELDS, values[1:])}
        if not row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']:
            raise ValueError('开高低收范围矛盾')
        rows[code] = row
    if not rows:
        raise ValueError('原文未包含具体合约')
    return product, rows, dict(url=address[1], title=content[0], published_at=published.isoformat(),
                               raw_text=text, content_sha256=_content_sha(address[1], lines),
                               header=list(HEADER), source='博易转载的收盘行情')


def _cash(root, conn, day, product):
    primary = _load_old(root / (day + '.json'), day)
    if not primary:
        return None
    code = PRODUCTS[product][1]
    item = primary['products'][product]['spot'].get(code)
    if not item:
        return None
    spot = item['data']
    receipt_key = item['receipt']
    receipt = primary['receipts'][receipt_key]
    if spot['code'] != code or digest(receipt) != receipt_key:
        raise ValueError('主日档现货身份或回执哈希不符')
    if receipt.get('source') == 'daily_market.indices':
        current, witness = _spot(conn, day, product)
        if (receipt.get('sha256') != witness['sha256'] or receipt.get('upstream') != 'tushare:index_daily'
                or receipt.get('date') != day or receipt.get('record') != witness['record'] or current != spot):
            raise ValueError('现货价格、日期或原始归档校验不符')
        source_sha = witness['sha256']
    elif receipt.get('source') == 'index_daily':
        params = dict(ts_code=code, trade_date=day.replace('-', ''))
        if (receipt.get('status') != 'received' or receipt.get('params') != params or receipt.get('rows') != 1
                or not re.fullmatch(r'[a-f0-9]{64}', str(receipt.get('rows_sha256')))):
            raise ValueError('现货接口的日期、代码或回执不符')
        raw_row = dict(ts_code=code, trade_date=params['trade_date'], close=spot['close'])
        source_sha = receipt['rows_sha256']
        if digest([raw_row]) != source_sha:
            raise ValueError('无法以现货原始字段重建接口回执')
    else:
        raise ValueError('无法核验的现货来源')
    return dict(trade_date=day, code=code, close=number(spot['close'], positive=True),
                source=receipt['source'], primary_path=str(root / (day+'.json')), primary_sha256=digest(primary),
                original_source_sha256=source_sha, receipt_sha256=receipt_key, receipt=receipt)


def import_file(path, day, actor, *, root=ROOT, db_path=None, dry_run=False, clock=None):
    if not actor.strip():
        raise ValueError('请求者不能为空')
    root = Path(root)
    now = (clock() if clock else datetime.now(TZ)).astimezone(TZ)
    target = date.fromisoformat(day)
    if now < datetime.combine(target, time(16), TZ):
        raise ValueError('需等待事实日上海时间16:00')
    if not 0 < Path(path).stat().st_size <= MAX_BYTES:
        raise ValueError('来源文件为空或超出大小上限')
    raw = Path(path).read_bytes()
    product, rows, evidence = parse(raw.decode('utf-8'), day, now)
    evidence.update(raw_sha256=sha256(raw).hexdigest(), hash_scope='导入的完整网页文本UTF-8字节',
                    http_received_at=None, known_by_at=now.isoformat())
    out = root / 'references' / day / (product + '.json')
    with closing(get_readonly_connection(db_path)) as conn:
        if not calendar(conn, day)['is_open']:
            raise ValueError('非SSE开放日')
        with (nullcontext() if dry_run else lock(root, day+'/reference/'+product)):
            prior = {}
            if out.exists():
                prior = json.loads(out.read_bytes())
                sign = prior.pop('sha256')
                if (digest(prior) != sign or prior.get('schema') != SCHEMA or prior.get('trade_date') != day
                        or prior.get('product') != product):
                    raise ValueError('旧二级来源记录签名、日期或产品不符，已保留')
                original = prior['source']
                raw_text = original['raw_text']
                if sha256(raw_text.encode('utf-8')).hexdigest() != original['raw_sha256']:
                    raise ValueError('旧网页原始取证文本校验失败，已保留')
                _, original_rows, verified = parse(raw_text, day, now)
                content_sha = verified['content_sha256']
                if (original.get('content_sha256', content_sha) != content_sha or original['url'] != verified['url']
                        or original['url'] != evidence['url'] or content_sha != evidence['content_sha256']
                        or prior['contracts'] != original_rows or original_rows != rows):
                    raise ValueError('新原文与既有二级来源记录冲突，已保留旧记录')
                # 后补现货时，继续引用第一次获取的完整网页转储。
                evidence = dict(original, content_sha256=content_sha)
                if prior.get('spot'):
                    return dict(prior, sha256=sign, path=str(out), reused=True)
            spot = _cash(root, conn, day, product)
            result = dict(schema=SCHEMA, trade_date=day, product=product, input_by=prior.get('input_by',actor),
                          imported_at=prior.get('imported_at',now.isoformat()), source=evidence, contracts=rows,
                          spot=spot, basis_points={code: float(Decimal(str(r['close'])) - Decimal(str(spot['close'])))
                                                   if spot and r['volume'] > 0 else None for code,r in rows.items()},
                          coverage='转载表所列合约；会员排名未披露', acquisition='手工取得网页文本，盘后独立参考',
                          status='reference_with_spot' if spot else 'reference_without_spot')
            if spot:
                result.update(spot_linked_at=now.isoformat(), spot_linked_by=actor)
            result['sha256'] = digest(result)
            if not dry_run:
                atomic_write(out,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
            return dict(result,path=None if dry_run else str(out),reused=False)
