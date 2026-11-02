"""东方财富中证2000实时报价；只接受显式注册的指数，不替代股票行情源。"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import DataProvider, DataResult, Timeliness


QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
REQUEST_TIMEOUT = 8
MAX_ATTEMPTS = 2
INSTRUMENTS = {"932000.CSI": ("2.932000", "中证2000")}
FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86"
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        return ["get_realtime_quotes"]

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
