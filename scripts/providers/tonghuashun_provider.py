"""同花顺自编指数实时行情 provider。

只接同花顺官方 ``realhead`` 行情接口，当前用于 ``883421.THS``
（同花顺全A（沪深））。接口返回的 ``updateTime`` 是行情更新时间，消费方
继续复用盘中监控的当日/10分钟新鲜度门禁。
"""
from __future__ import annotations

import json
import math
import re

from .base import DataProvider, DataResult, Timeliness


REALHEAD_URL = "https://d.10jqka.com.cn/v6/realhead/bk_{code}/last.js"
REQUEST_TIMEOUT = 10
MAX_ATTEMPTS = 3
_CALLBACK_RE = re.compile(
    r"^quotebridge_v6_realhead_bk_(?P<code>\d{6})_last\((?P<payload>.*)\)\s*;?\s*$",
    re.DOTALL,
)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://q.10jqka.com.cn/",
}


def _normalize_code(raw: object) -> str | None:
    """只接受六位同花顺板块/指数码及显式 ``.THS`` 后缀。"""
    text = str(raw or "").strip().upper()
    if text.endswith(".THS"):
        text = text[:-4]
    if len(text) != 6 or not text.isdigit() or not text.startswith("88"):
        return None
    return f"{text}.THS"


def _parse_realhead(raw: str, code: str) -> dict:
    match = _CALLBACK_RE.fullmatch(str(raw or "").strip())
    if match is None or match.group("code") != code:
        raise ValueError("同花顺 realhead JSONP 布局异常")
    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError as exc:
        raise ValueError("同花顺 realhead JSON 非法") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        raise ValueError("同花顺 realhead 缺少 items")
    if str(items.get("5") or "") != code:
        raise ValueError("同花顺 realhead 证券代码错位")
    return items


class TonghuashunProvider(DataProvider):
    """同花顺官方自编指数实时报价；历史行情仍走其它 provider。"""

    name = "tonghuashun"
    priority = 4

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
        session.headers.update(_HEADERS)
        return session

    def _fetch_one(self, session, code: str) -> str:
        url = REALHEAD_URL.format(code=code)
        last_error: Exception | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                response = session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return response.text
            except Exception as exc:  # requests 的异常类型由运行时依赖提供
                last_error = exc
        raise RuntimeError(f"连续 {MAX_ATTEMPTS} 次请求失败: {last_error}")

    def get_realtime_quotes(self, codes: list[str]) -> DataResult:
        normalized: list[str] = []
        notes: list[str] = []
        for raw in codes or []:
            code = _normalize_code(raw)
            if code is None:
                notes.append(f"{raw!r}(同花顺代码非法或不支持)")
                continue
            if code not in normalized:
                normalized.append(code)
        if not normalized:
            return DataResult(
                data=None,
                source=self.name,
                error="无有效同花顺指数代码",
                note="; ".join(notes),
            )

        session = self._make_session()
        quotes: list[dict] = []
        for ts_code in normalized:
            raw_code = ts_code[:6]
            try:
                items = _parse_realhead(self._fetch_one(session, raw_code), raw_code)
                price = float(items.get("10"))
                pre_close = float(items.get("6"))
                open_ = float(items.get("7"))
                high = float(items.get("8"))
                low = float(items.get("9"))
                volume = float(items.get("13"))
                amount = float(items.get("19"))
                update_time = str(items.get("updateTime") or "").strip()
                quote_date, quote_time = update_time.split(" ", 1)
                if len(quote_time) == 5:
                    quote_time += ":00"
                if len(quote_date) != 10 or len(quote_time) != 8:
                    raise ValueError("行情更新时间非法")
            except (TypeError, ValueError, RuntimeError) as exc:
                notes.append(f"{ts_code}({exc})")
                continue
            numeric = (price, pre_close, open_, high, low, volume, amount)
            if not all(math.isfinite(value) for value in numeric):
                notes.append(f"{ts_code}(数值非有限)")
                continue
            if price <= 0 or pre_close <= 0:
                notes.append(f"{ts_code}(最新点位或昨收非法)")
                continue
            quotes.append(
                {
                    "code": ts_code,
                    "name": str(items.get("name") or "").strip(),
                    "price": price,
                    "pre_close": pre_close,
                    "pct_chg": round((price / pre_close - 1) * 100, 4),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "amount": amount,
                    "quote_date": quote_date,
                    "quote_time": quote_time,
                }
            )

        note = "; ".join(notes)
        if not quotes:
            return DataResult(
                data=None,
                source=self.name,
                source_url=REALHEAD_URL.format(code=normalized[0][:6]),
                error="全部同花顺指数无有效行情",
                note=note,
            )
        return DataResult(
            data=quotes,
            source="tonghuashun:realhead_v6",
            source_url=REALHEAD_URL.format(code=normalized[0][:6]),
            timeliness=Timeliness.REALTIME,
            note=note,
        )
