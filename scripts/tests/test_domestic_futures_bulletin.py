from datetime import datetime
import json

import pytest

from services import domestic_futures_bulletin as bulletin
from services.board_break_intraday.data import TZ, digest


DAY = '2026-11-27'
NOW = datetime(2026, 11, 28, 12, tzinfo=TZ)
TEXT = """webnewslt1.pobo.net.cn (https://webnewslt1.pobo.net.cn/DATA/STOCK/123.HTML)
Tool result. Total lines: 8
L0: 中国金融期货交易所11月27日IM期货收盘行情
L1:
L2: 发布时间：2026年11月27日 15:30
L3: 合约代码 | 开盘价 | 最高价 | 最低价 | 收盘价 | 结算价 | 成交量 | 成交额 | 持仓量
L4: --- | --- | --- | --- | --- | --- | --- | --- | ---
L5: IM2612 | 5,525.6 | 5,959 | 5,482 | 5,959 | 5,958.8 | 323,013 | 369,039,977,160 | 317,815
L6:
L7: 相关资讯:
"""


@pytest.mark.parametrize('before,after', [
    ('https://webnewslt1.', 'http://webnewslt1.'), ('net.cn/DATA', 'net.cn.example.org/DATA'),
    ('L6:', 'L5:'), ('Total lines: 8', 'Total lines: 7'), ('L0: 中国', 'L0: 来源：中国'),
    ('11月27日IM', '11月28日IM'), ('2026年11月27日', '2025年11月27日'),
    ('15:30', '13:30'), ('L6:', 'L6: 发布时间：2026年11月27日 16:00'),
    ('持仓量', '成交量'), ('IM2612', 'IF2612'), ('IM2612', 'IM2613'), ('IM2612', 'IM2610'),
    ('323,013', '32,3013'), ('317,815', '317,815.3'), ('5,482', '6,482'),
    ('L6:', 'L6: IM2612 | 5525.6 | 5959 | 5482 | 5959 | 5958.8 | 1 | 5 | 2'),
])
def test_rejects_invalid_evidence(before, after):
    with pytest.raises(ValueError):
        bulletin.parse(TEXT.replace(before, after), DAY, NOW)


def test_accepts_products_and_preserves_fields():
    for product in ['IM', 'IF']:
        name, rows, source = bulletin.parse(TEXT.replace('IM', product), DAY, NOW)
        assert name == product and rows[product+'2612']['open_interest'] == 317815
        assert rows[product+'2612']['turnover_as_published'] == 369039977160
        assert source['raw_text'] == TEXT.replace('IM', product)


class FakeDB:
    def close(self):
        pass


def setup(tmp_path, monkeypatch):
    path = tmp_path/'source.txt'
    path.write_text(TEXT)
    monkeypatch.setattr(bulletin, 'get_readonly_connection', lambda _: FakeDB())
    monkeypatch.setattr(bulletin, 'calendar', lambda *args: {'is_open': True})
    record = {'trade_date':DAY, 'code':'000852.SH', '_source':'tushare:index_daily', 'close':5982.906}
    receipt = dict(date=DAY, source='daily_market.indices', upstream='tushare:index_daily', sha256='f'*64, record=record)
    spot = dict(code='000852.SH', close=record['close'])
    rid = digest(receipt)
    daily = dict(date=DAY, schema='domestic-index-futures-v1', receipts={rid:receipt},
                 products={'IM':{'spot':{'000852.SH':{'data':spot, 'receipt':rid}}}})
    (tmp_path/(DAY+'.json')).write_text(json.dumps(dict(daily, sha256=digest(daily))))
    monkeypatch.setattr(bulletin, '_spot', lambda *args: (spot, dict(sha256='f'*64, record=record)))
    return path


def test_signed_copy_isolated_dry_run_preserved_on_retry_and_conflict(tmp_path, monkeypatch):
    file = setup(tmp_path, monkeypatch)
    original = (tmp_path/(DAY+'.json')).read_bytes()
    def emit(**kwargs):
        return bulletin.import_file(file, DAY, 'USER', root=tmp_path, clock=lambda:NOW, **kwargs)
    assert emit(dry_run=True)['path'] is None and not (tmp_path/'references').exists()
    result = emit()
    ref = tmp_path/'references'/DAY/'IM.json'
    assert result['spot']['code']=='000852.SH' and result['spot']['original_source_sha256']=='f'*64
    assert result['basis_points']=={'IM2612':-23.906} and result['source']['raw_text']==TEXT
    saved=ref.read_bytes()
    assert emit()['reused'] and saved==ref.read_bytes()
    file.write_text(TEXT.replace('323,013', '323,014'))
    with pytest.raises(ValueError, match='冲突'):
        emit()
    assert saved==ref.read_bytes() and (tmp_path/(DAY+'.json')).read_bytes()==original


def test_invalid_cash_proof_refused_and_nested_evidence_can_stand_alone(tmp_path,monkeypatch):
    file=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(bulletin, '_spot', lambda *args: ({'code':'000852.SH','close':5900},dict(sha256='b'*64)))
    with pytest.raises(ValueError, match='现货'):
        bulletin.import_file(file, DAY, 'USER', root=tmp_path, clock=lambda:NOW)
    (tmp_path/(DAY+'.json')).unlink()
    out=bulletin.import_file(file, DAY, 'USER', root=tmp_path, clock=lambda:NOW)
    assert out['status']=='reference_without_spot' and out['basis_points']=={'IM2612':None}


def test_requires_open_day_and_market_closed(tmp_path,monkeypatch):
    file=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(bulletin, 'calendar', lambda *args: {'is_open':False})
    for now, gap in [(NOW,'开放日'), (datetime(2026,11,27,14,59,tzinfo=TZ),'16:00')]:
        with pytest.raises(ValueError, match=gap):
            bulletin.import_file(file, DAY, 'USER', root=tmp_path, clock=lambda:now)
    assert not (tmp_path/'references').exists()


def test_direct_index_receipt_revalidates_actual_hash(tmp_path):
    raw_row = dict(ts_code='000852.SH', trade_date='20261127', close=5982.906)
    receipt = dict(source='index_daily', params={'ts_code':'000852.SH','trade_date':'20261127'},
                   status='received',rows=1,rows_sha256=digest([raw_row]))
    def write(close):
        rid = digest(receipt)
        item = {'data':{'code':'000852.SH','close':close},'receipt':rid}
        daily = dict(date=DAY,schema='domestic-index-futures-v1',receipts={rid:receipt},
                     products={'IM':{'spot':{'000852.SH':item}}})
        (tmp_path/(DAY+'.json')).write_text(json.dumps(dict(daily, sha256=digest(daily))))
    write(5982.906)
    assert bulletin._cash(tmp_path, None, DAY, 'IM')['original_source_sha256']==receipt['rows_sha256']
    write(5999)
    with pytest.raises(ValueError,match='重建'):
        bulletin._cash(tmp_path, None, DAY, 'IM')


def test_new_tool_wrapper_and_legacy_daily_preserve_original_raw_document(tmp_path, monkeypatch):
    web_file = setup(tmp_path, monkeypatch)
    def import_source():
        return bulletin.import_file(web_file, DAY, 'USER', root=tmp_path, clock=lambda:NOW)
    first = import_source()
    web_file.write_text(TEXT.replace('Tool result.', '【turn770view1】 Crawled: yesterday; Source: open(...)'))
    ref = tmp_path/'references'/DAY/'IM.json'
    stored = ref.read_bytes()
    assert import_source()['reused'] and ref.read_bytes() == stored
    assert first['source']['raw_text'] == TEXT
    legacy = json.loads(stored)
    legacy.pop('sha256')
    legacy['source'].pop('content_sha256')
    ref.write_text(json.dumps(dict(legacy, sha256=digest(legacy))))
    legacy_bytes = ref.read_bytes()
    assert import_source()['reused'] and ref.read_bytes() == legacy_bytes
    web_file.write_text(TEXT.replace('相关资讯:', '相关资讯：'))
    with pytest.raises(ValueError, match='冲突'):
        import_source()
    web_file.write_text(TEXT.replace('/123.HTML', '/456.HTML'))
    with pytest.raises(ValueError, match='冲突'):
        import_source()
    assert ref.read_bytes() == legacy_bytes
