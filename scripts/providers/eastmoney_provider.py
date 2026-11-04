"""东方财富中证2000实时报价；只接受显式注册的指数，不替代股票行情源。"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .base import DataProvider, DataResult, Timeliness


QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
REQUEST_TIMEOUT = 8
MAX_ATTEMPTS = 2
INSTRUMENTS = {"932000.CSI": ("2.932000", "中证2000")}
FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86"
SHANGHAI = ZoneInfo("Asia/Shanghai")
INDEX_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
INTRADAY_INSTRUMENTS = {
    "000001.SH": ("1.000001", "上证指数"),
    "399001.SZ": ("0.399001", "深证成指"),
    "399006.SZ": ("0.399006", "创业板指"),
    "000688.SH": ("1.000688", "科创50"),
}


def _parse_index_bars(payload: object, code: str, start_date: str, end_date: str) -> list[dict]:
    if not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0:
        raise ValueError("指数K线信封非法")
    data = payload.get("data")
    secid, name = INTRADAY_INSTRUMENTS[code]
    if (not isinstance(data, dict) or data.get("code") != code[:6]
            or data.get("name") != name or type(data.get("market")) is not int
            or data["market"] != int(secid[0])):
        raise ValueError("指数K线身份错位")
    lines = data.get("klines")
    if not isinstance(lines, list) or not lines or len(lines) > 128:
        raise ValueError("指数K线为空或超过有界深度")
    out, previous = [], None
    for line in lines:
        if not isinstance(line, str) or len(line.split(",")) != 7:
            raise ValueError("指数K线行格式非法")
        stamp, *values = line.split(",")
        dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        t = dt.time()
        if (dt.strftime("%Y-%m-%d %H:%M") != stamp or dt.minute % 15
                or not (time(9,45) <= t <= time(11,30) or time(13,15) <= t <= time(15))
                or previous is not None and dt <= previous):
            raise ValueError("指数K线时间重复、倒序或不在15分钟收盘网格")
        previous = dt
        open_, close, high, low, volume = [_number(v) for v in values[:5]]
        amount = values[5]
        # 货币保留源十进制值，不用二进制浮点数判断50%的严格边界。
        if not re.fullmatch(r"\d{1,18}(?:\.\d{1,8})?", amount):
            raise ValueError("指数成交额非法")
        if min(open_,close,high,low) <= 0 or volume < 0 or not low <= min(open_,close) <= max(open_,close) <= high:
            raise ValueError("指数K线OHLC或成交量非法")
        if start_date <= dt.date().isoformat() <= end_date:
            out.append({"ts_code":code, "trade_time":stamp, "period_minutes":15,
                        "amount":amount, "amount_unit":"CNY", "open":open_,
                        "close":close, "high":high, "low":low, "volume":volume})
    if not out:
        raise ValueError("请求日期内无指数K线")
    return out


def _number(raw: object) -> float:
    if isinstance(raw, bool):
        raise ValueError("布尔值不是行情数值")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("行情数值缺失或非法") from exc
    if not math.isfinite(value):
        raise ValueError("行情数值非有限")
    return value


def _parse_quote(payload: object, code: str) -> dict:
    if code not in INSTRUMENTS:
        raise ValueError("未注册的指数身份")
    if not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0:
        raise ValueError("东方财富行情信封失败或非法")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("东方财富行情为空")
    _, expected_name = INSTRUMENTS[code]
    if (data.get("f57") != code[:6]
            or str(data.get("f58") or "").strip() != expected_name):
        raise ValueError("东方财富指数代码或名称错位")
    decimals = _number(data.get("f59"))
    if not decimals.is_integer() or not 0 <= decimals <= 8:
        raise ValueError("行情小数位非法")
    # fltt=2 返回已按小数位还原的点位，不能再次除以10**f59。
    price, pre_close, open_, high, low = [
        _number(data.get(key)) for key in ("f43", "f60", "f46", "f44", "f45")
    ]
    volume, amount = [_number(data.get(key)) for key in ("f47", "f48")]
    if min(price, pre_close, open_, high, low) <= 0 or min(volume, amount) < 0:
        raise ValueError("行情价格或量额非法")
    if not low <= min(price, open_) <= max(price, open_) <= high:
        raise ValueError("行情高低价与最新价冲突")
    epoch = _number(data.get("f86"))
    if epoch <= 0 or not epoch.is_integer():
        raise ValueError("行情时间戳非法")
    quoted_at = datetime.fromtimestamp(epoch, SHANGHAI)
    # 不以抓取时间代替行情时间；当日/10分钟新鲜度仍由监控服务严格检查。
    return {
        "code": code, "name": expected_name, "price": price, "pre_close": pre_close,
        "pct_chg": round((price / pre_close - 1) * 100, 4),
        "open": open_, "high": high, "low": low,
        "volume": volume * 100,  # 东方财富手 -> 统一实时报价的股
        "amount": amount,  # 元
        "quote_date": quoted_at.date().isoformat(),
        "quote_time": quoted_at.time().isoformat(),
    }


class EastmoneyProvider(DataProvider):
    name = "eastmoney"
    priority = 5

    def initialize(self) -> bool:
        try:
            import requests  # noqa: F401
        except ImportError:
            self._initialized = False
            return False
        self._initialized = True
        return True

    def get_capabilities(self) -> list[str]:
        return ["get_realtime_quotes", "get_index_intraday_bars"]

    def get_index_intraday_bars(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        code = str(index_code or "").strip().upper()
        if code not in INTRADAY_INSTRUMENTS:
            return DataResult(None, self.name, error="未注册的盘中宽基指数")
        source = "eastmoney:index_15min"
        try:
            sd, ed = date.fromisoformat(start_date), date.fromisoformat(end_date)
            if sd > ed:
                raise ValueError("指数K线起止日期倒置")
            with self._make_session() as session:
                for attempt in range(MAX_ATTEMPTS):
                    try:
                        response = session.get(INDEX_KLINE_URL, params={
                            "secid": INTRADAY_INSTRUMENTS[code][0], "klt":"15", "fqt":"0",
                            "lmt":"128", "end":"20500101", "fields1":"f1,f2,f3,f4,f5,f6",
                            "fields2":"f51,f52,f53,f54,f55,f56,f57",
                        }, timeout=(2,3))
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except Exception:
                        if attempt == MAX_ATTEMPTS-1:
                            raise RuntimeError("指数K线两次有界请求失败")
            rows = _parse_index_bars(payload,code,str(sd),str(ed))
            return DataResult(rows,source,source_url=INDEX_KLINE_URL,timeliness=Timeliness.RECENT)
        except Exception as exc:
            return DataResult(None,source,source_url=INDEX_KLINE_URL,
                              error=f"{type(exc).__name__}: {exc}")

    def _make_session(self):
        import requests
        session = requests.Session()
        session.trust_env = False
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        return session

    def _fetch_one(self, session, secid: str) -> dict:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = session.get(QUOTE_URL, params={
                    "secid": secid, "fields": FIELDS, "fltt": "2", "invt": "2",
                }, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"东方财富连续{MAX_ATTEMPTS}次请求失败: {type(exc).__name__}") from exc
        raise AssertionError("unreachable")

    def get_realtime_quotes(self, codes: list[str]) -> DataResult:
        normalized = list(dict.fromkeys(str(raw or "").strip().upper() for raw in codes or []))
        valid = [code for code in normalized if code in INSTRUMENTS]
        notes = [f"{code}(不支持的指数代码)" for code in normalized if code not in INSTRUMENTS]
        if not valid:
            return DataResult(data=None, source=self.name, error="无有效东方财富指数代码", note="; ".join(notes))
        quotes = []
        try:
            with self._make_session() as session:
                for code in valid:
                    try:
                        quotes.append(_parse_quote(self._fetch_one(session, INSTRUMENTS[code][0]), code))
                    except (ValueError, TypeError, RuntimeError, OverflowError, OSError) as exc:
                        notes.append(f"{code}({exc})")
        except Exception as exc:
            return DataResult(data=None, source=self.name, error=f"行情会话失败: {type(exc).__name__}")
        if not quotes:
            return DataResult(data=None, source=self.name, source_url=QUOTE_URL,
                              error="全部东方财富指数无有效行情", note="; ".join(notes))
        return DataResult(data=quotes, source="eastmoney:quote", source_url=QUOTE_URL,
                          timeliness=Timeliness.REALTIME, note="; ".join(notes))
