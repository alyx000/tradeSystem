"""
新浪实时行情 provider

直连 https://hq.sinajs.cn/list=sh600519,... 批量获取个股+指数实时报价。
免费无 token；请求必须带 Referer 头（否则 403）；响应 GBK 编码。
（https 已于 2026-07-11 本机实测可达；早年 datasource-research 脚本用 http 是旧研究，不回退。）
选型与吞吐实测见 docs/superpowers/specs/2026-07-11-sina-realtime-quotes-design.md：
800 码/请求，全市场 5609 只（含北交所）串行 6.4s。

盘中轮询消费方 `intraday-monitor` 已在消费侧以本地状态实现跨进程最小
抓取间隔（>=3s），生产 launchd 实际每 5 分钟调用一次。provider 仍每次
创建短生命周期 session：5 分钟长间隔下复用 keep-alive 容易命中 stale
连接，节省的握手成本不值得扩大整体请求失败面。
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .base import DataProvider, DataResult, Timeliness

logger = logging.getLogger(__name__)

SINA_HQ_URL = "https://hq.sinajs.cn/list="
SINA_MINUTE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
MINUTE_DEPTH = 1970
BATCH_SIZE = 800
REQUEST_TIMEOUT = 10
# 标准行情行至少含字段 0-31（30=行情日期 31=行情时间）
MIN_FIELDS = 32
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}
_EXCHANGE_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}
_LINE_PREFIX = "var hq_str_"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
POST_ASSET_MAX_AGE = timedelta(minutes=30)
POST_ASSET_SOURCE = "sina:hq:post_asset_quotes"

# 代码、源名称、最新价、参考价、日期、时间和名称字段。
_POST_ASSET_FEEDS = {
    "brent": ("hf_OIL", "布伦特原油", 0, 7, 12, 6, 13),
    "usd_index": ("DINIW", "美元指数", 8, 3, 10, 0, 9),
    "xau_usd": ("fx_sxauusd", "黄金盎司兑美元", 8, 3, 17, 0, 9),
}
POST_ASSET_SYMBOLS = {key: feed[0] for key, feed in _POST_ASSET_FEEDS.items()}


# 人民币普通股代码段。指数、基金、B股、存托凭证和北交所均无本口径盘口。
_L1_STOCK_PREFIXES = (
    "sh600", "sh601", "sh603", "sh605", "sh688",
    "sz000", "sz001", "sz002", "sz003", "sz300", "sz301",
)
# Tushare 1.4.25 stock/rtq.py::get_realtime_quotes_sina 将五档原始数量去掉
# 末两位转换为手（realtime_quote 文档 315）。此处直接保留股，零股也不截尾。
_L1_UNIT_EVIDENCE = "tushare:1.4.25:rtq.get_realtime_quotes_sina+doc315"


def _normalize_code(raw: str) -> str:
    """兼容不带交易所后缀的 6 位代码（规则同 TushareProvider._normalize_stock_code）。"""
    code = str(raw or "").strip().upper()
    if not code:
        return code
    if "." in code:
        return code
    if code.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{code}.BJ"
    if code.startswith(("60", "68", "90", "51", "52", "53", "56", "58")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _to_sina_symbol(ts_code: str) -> str | None:
    """ts_code -> 新浪前缀码；不支持的后缀或非 6 位纯数字 symbol 返回 None。

    symbol 严格校验为 6 位数字：入参会被拼进请求 URL，
    畸形串（如 "600000,sz000001.SH"）绝不能流到外部接口。
    """
    symbol, _, exchange = ts_code.partition(".")
    prefix = _EXCHANGE_PREFIX.get(exchange)
    if prefix is None or len(symbol) != 6 or not symbol.isdigit():
        return None
    return prefix + symbol


def _parse_line(line: str) -> tuple[str, str] | None:
    """解析一行 `var hq_str_sh600519="...";` -> (sina_symbol, body)。非行情行返回 None。"""
    line = line.strip()
    if not line.startswith(_LINE_PREFIX):
        return None
    head, sep, rest = line.removeprefix(_LINE_PREFIX).partition("=")
    if not sep:
        return None
    body = rest.strip().rstrip(";").strip('"')
    return head.strip(), body


def _book_level(fields: list[str], size_index: int) -> tuple[float | None, int | None]:
    size_text = fields[size_index].strip()
    if not size_text.isascii() or not size_text.isdigit():
        return None, None
    try:
        size = int(size_text)
        price = float(fields[size_index + 1])
    except ValueError:
        return None, None
    if not math.isfinite(price) or price < 0 or (price == 0) != (size == 0):
        return None, None
    return price, size


class SinaProvider(DataProvider):
    """新浪实时报价及近期未复权1分钟数据；非无限历史行情接口。"""

    name = "sina"
    priority = 3

    def initialize(self) -> bool:
        # dependency-only 初始化：无 token 可验、不打真网（避免启动阻塞）。
        # initialize_all 报“成功”仅代表依赖可用，真实可达性由业务调用暴露。
        try:
            import requests  # noqa: F401
        except ImportError:
            logger.error("requests 未安装，SinaProvider 不可用")
            self._initialized = False
            return False
        self._initialized = True
        return True

    def get_capabilities(self) -> list[str]:
        return ["get_realtime_quotes", "get_stock_minutes", "get_post_asset_quotes"]

    def _make_session(self):
        import requests

        session = requests.Session()
        session.headers.update(_HEADERS)
        return session

    def _fetch_raw(self, symbols: list[str]) -> list[str]:
        """分片请求新浪，返回原始行情行。任一分片失败抛 RuntimeError（含分片定位）。"""
        session = self._make_session()
        chunks = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        lines: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            try:
                resp = session.get(SINA_HQ_URL + ",".join(chunk), timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"分片 {idx}/{len(chunks)} 请求失败: {e}") from e
            lines.extend(resp.content.decode("gbk", errors="replace").splitlines())
        return lines

    def get_post_asset_quotes(self, trade_date: str) -> DataResult:
        """同批取布伦特、DXY、XAU/USD 的新浪参考报价。"""
        source = POST_ASSET_SOURCE
        symbols = list(POST_ASSET_SYMBOLS.values())
        source_url = SINA_HQ_URL + ",".join(symbols)
        fetched_at = datetime.now(_SHANGHAI)
        if trade_date != fetched_at.date().isoformat():
            status, reason = "historical_not_supported", "实时源无法回取目标日快照"
        elif fetched_at.hour < 20:
            status, reason = "not_ready", "北京时间未到20:00"
        else:
            status, reason = "ok", ""
        if reason:
            return DataResult(
                {key: {"status": status, "error": reason} for key in POST_ASSET_SYMBOLS},
                source, source_url=source_url, fetched_at=fetched_at.isoformat(),
            )
        try:
            lines = self._fetch_raw(symbols)
        except Exception as exc:
            return DataResult(
                None, source, source_url=source_url,
                error=f"新浪跨资产报价请求失败：{type(exc).__name__}",
            )

        fetched_at = datetime.now(_SHANGHAI)
        fields_by_symbol = {}
        for line in lines:
            parsed = _parse_line(line)
            if parsed is None or parsed[0] not in symbols:
                continue
            symbol, body = parsed
            # 重复代码视为响应异常。
            fields_by_symbol[symbol] = [] if symbol in fields_by_symbol else body.split(",")

        quotes = {}
        for key, (symbol, name, price_i, ref_i, day_i, time_i, name_i) in _POST_ASSET_FEEDS.items():
            try:
                fields = fields_by_symbol[symbol]
                if fields[name_i] != name:
                    raise ValueError("identity")
                price, reference = float(fields[price_i]), float(fields[ref_i])
                if not all(math.isfinite(value) and value > 0 for value in (price, reference)):
                    raise ValueError("price")
                stamp = datetime.fromisoformat(f"{fields[day_i]}T{fields[time_i]}")
                if stamp.tzinfo is not None or stamp.date() != fetched_at.date() or fields[day_i] != trade_date:
                    raise ValueError("date")
            except (KeyError, IndexError, ValueError):
                quotes[key] = {"status": "missing_data", "error": "报价缺失、品种不符或源日期不匹配"}
                continue

            stamp = stamp.replace(tzinfo=_SHANGHAI)
            if not timedelta(0) <= fetched_at - stamp <= POST_ASSET_MAX_AGE:
                quotes[key] = {"status": "stale_snapshot", "error": "源报价时间晚于抓取时间或滞后超过30分钟"}
                continue

            change_pct = (price / reference - 1) * 100
            if not math.isfinite(change_pct):
                quotes[key] = {"status": "missing_data", "error": "参考价无法计算涨跌幅"}
                continue
            quotes[key] = {
                "status": "ok",
                "symbol": symbol,
                "as_of": trade_date,
                "quote_time": stamp.isoformat(),
                "price": price,
                "reference_price": reference,
                "change_pct": round(change_pct, 4),
            }
        return DataResult(quotes, source, source_url=source_url, fetched_at=fetched_at.isoformat())

    def get_stock_minutes(self, stock_code: str, trade_date: str) -> DataResult:
        """固定1分钟/未复权/1970根；返回目标日原值，完整截止窗口由消费方核验。

        复用AkShare stock_zh_a_minute的新浪接口，不调用其额外日线复权请求。
        不填补停牌或零成交缺失分钟；身份绑定单码请求参数，不伪称响应自带代码。
        """
        source = 'sina:CN_MarketDataService.getKLineData:1min'
        try:
            if date.fromisoformat(trade_date).isoformat() != trade_date:
                raise ValueError('invalid_date')
            symbol = _to_sina_symbol(stock_code)
            if not symbol or not stock_code.endswith(('.SH', '.SZ')):
                raise ValueError('invalid_stock_code')
        except (ValueError, TypeError):
            return DataResult(None, source, error='分钟请求代码或日期非法')
        try:
            with self._make_session() as session:
                resp = session.get(SINA_MINUTE_URL, params=dict(symbol=symbol,scale='1',ma='no',datalen=str(MINUTE_DEPTH)),
                                   timeout=REQUEST_TIMEOUT, allow_redirects=False)
                if resp.status_code != 200:
                    raise ValueError('http_failed')
                text = resp.text.strip()
                if text.startswith('/*') and '*/' in text:
                    text = text.split('*/',1)[1].strip()
                if not text.startswith('=(') or not text.endswith(');'):
                    raise ValueError('invalid_wrapper')
                raw = json.loads(text[2:-2])
            if not isinstance(raw,list) or not raw or len(raw)>MINUTE_DEPTH:
                raise ValueError('empty_or_invalid_response')
            rows, seen = [], set()
            for bar in raw:
                stamp = bar['day']
                t = datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S')
                if t.strftime('%Y-%m-%d %H:%M:%S')!=stamp or t.second or stamp in seen:
                    raise ValueError('invalid_timestamp')
                seen.add(stamp)
                if t.date().isoformat()!=trade_date:
                    continue
                row = dict(ts_code=stock_code,trade_time=stamp)
                for key,src in [('open','open'),('high','high'),('low','low'),('close','close'),('vol','volume'),('amount','amount')]:
                    if isinstance(bar[src],bool):
                        raise ValueError('invalid_number')
                    value=float(bar[src])
                    if not math.isfinite(value) or value<0 or (key in ('open','high','low','close') and value==0):
                        raise ValueError('invalid_number')
                    row[key]=value
                if not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
                    raise ValueError('invalid_ohlc')
                rows.append(row)
            if not rows:
                raise ValueError('target_day_unavailable')
            return DataResult(sorted(rows,key=lambda r:r['trade_time']),source,source_url=SINA_MINUTE_URL,
                              timeliness=Timeliness.RECENT,
                              note='未复权；volume股/amount元；身份绑定请求参数'+symbol+
                                   '；最近1970根，缺分钟不补齐；首根可能含开盘竞价，按新浪分桶，不与其他源逐分钟量额混用')
        except Exception as exc:
            # 只输出类型，避免代理异常泄露地址中的账户信息。
            return DataResult(None,source,error='新浪分钟来源失败或目标日不可用：'+type(exc).__name__)

    def get_realtime_quotes(self, codes: list[str], include_book: bool = False) -> DataResult:
        """批量获取实时行情。

        codes: ts_code 风格（600519.SH / 000001.SH），兼容裸 6 位码。
        include_book: 沪深人民币普通股可附买卖一档，报价元、原始委托量股。
        同侧价格和数量都为零表示空档，同侧字段不完整则两者均为 None。
        成功: data=list[dict]，跳过/缺失的代码逐一记入 note。
        失败（HTTP 异常 / 无有效代码 / 全部无数据）: error 非空。
        """
        skipped: list[str] = []
        symbol_map: dict[str, str] = {}
        for raw in codes or []:
            ts_code = _normalize_code(raw)
            if not ts_code:
                skipped.append(f"{raw!r}(空代码)")
                continue
            symbol = _to_sina_symbol(ts_code)
            if symbol is None:
                skipped.append(f"{ts_code}(新浪不支持或代码非法)")
                continue
            symbol_map[symbol] = ts_code
        if not symbol_map:
            return DataResult(
                data=None, source=self.name, error="无有效代码", note="; ".join(skipped),
            )

        try:
            raw_lines = self._fetch_raw(list(symbol_map))
        except Exception as e:
            return DataResult(data=None, source=self.name, error=f"新浪行情请求失败: {e}")

        quotes: list[dict] = []
        seen: set[str] = set()
        for line in raw_lines:
            parsed = _parse_line(line)
            if parsed is None:
                continue
            symbol, body = parsed
            ts_code = symbol_map.get(symbol)
            if ts_code is None:
                continue
            seen.add(symbol)
            if not body:
                skipped.append(f"{ts_code}(无数据)")
                continue
            fields = body.split(",")
            if len(fields) < MIN_FIELDS:
                skipped.append(f"{ts_code}(布局异常未解析)")
                continue
            try:
                open_ = float(fields[1])
                pre_close = float(fields[2])
                price = float(fields[3])
                high = float(fields[4])
                low = float(fields[5])
                volume = float(fields[8])
                amount = float(fields[9])
            except ValueError:
                skipped.append(f"{ts_code}(数值解析失败)")
                continue
            if not all(math.isfinite(value) for value in (
                open_, pre_close, price, high, low, volume, amount,
            )):
                skipped.append(f"{ts_code}(数值非有限)")
                continue
            # 停牌股新浪返回完整布局但 price=0（真机 430047.BJ 实测），
            # 直接返回会带出 pct_chg=-100% 的误导行，跳过进 note
            if price <= 0:
                skipped.append(f"{ts_code}(停牌或无最新价)")
                continue
            pct_chg = (
                round((price - pre_close) / pre_close * 100, 4)
                if pre_close > 0
                else None
            )
            quote = {
                "code": ts_code,
                "name": fields[0],
                "price": price,
                "pct_chg": pct_chg,
                "open": open_,
                "high": high,
                "low": low,
                "pre_close": pre_close,
                "volume": volume,
                "amount": amount,
                "quote_date": fields[30],
                "quote_time": fields[31],
            }
            if include_book and symbol.startswith(_L1_STOCK_PREFIXES):
                bid_price, bid_size = _book_level(fields, 10)
                ask_price, ask_size = _book_level(fields, 20)
                quote["book"] = {
                    "bid1_price": bid_price, "bid1_size": bid_size,
                    "ask1_price": ask_price, "ask1_size": ask_size,
                    "size_unit": "shares", "unit_evidence": _L1_UNIT_EVIDENCE,
                }
            quotes.append(quote)

        # 限流/异常可能静默少返某些行：请求了但响应没出现的码不允许被吞掉
        for symbol, ts_code in symbol_map.items():
            if symbol not in seen:
                skipped.append(f"{ts_code}(响应缺失)")

        note = "; ".join(skipped)
        if not quotes:
            return DataResult(
                data=None, source=self.name, error="全部代码无有效行情", note=note,
            )
        return DataResult(
            data=quotes,
            source=self.name,
            source_url="https://hq.sinajs.cn",
            timeliness=Timeliness.REALTIME,
            note=note,
        )
