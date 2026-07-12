"""
新浪实时行情 provider

直连 https://hq.sinajs.cn/list=sh600519,... 批量获取个股+指数实时报价。
免费无 token；请求必须带 Referer 头（否则 403）；响应 GBK 编码。
（https 已于 2026-07-11 本机实测可达；早年 datasource-research 脚本用 http 是旧研究，不回退。）
选型与吞吐实测见 docs/superpowers/specs/2026-07-11-sina-realtime-quotes-design.md：
800 码/请求，全市场 5609 只（含北交所）串行 6.4s。

频控为已知未处理项：本期无消费方，不实现节流（YAGNI）；
首个盘中轮询消费方接入时，须在消费侧或此处实现最小间隔（建议 >=3s）。
"""
from __future__ import annotations

import logging

from .base import DataProvider, DataResult, Timeliness

logger = logging.getLogger(__name__)

SINA_HQ_URL = "https://hq.sinajs.cn/list="
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
    if not line.startswith("var hq_str_"):
        return None
    head, sep, rest = line[len("var hq_str_"):].partition("=")
    if not sep:
        return None
    body = rest.strip().rstrip(";").strip('"')
    return head.strip(), body


class SinaProvider(DataProvider):
    """新浪实时行情（仅 get_realtime_quotes；日线/历史仍走 tushare/akshare）"""

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
        return ["get_realtime_quotes"]

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

    def get_realtime_quotes(self, codes: list[str]) -> DataResult:
        """批量获取实时行情。

        codes: ts_code 风格（600519.SH / 000001.SH），兼容裸 6 位码。
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
            quotes.append({
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
            })

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
