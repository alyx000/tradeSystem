"""通达信行情 provider：平均股价、微盘股与研究行业银行881385。

为什么单独一个 provider：平均股价不是标准市值指数（akshare/tushare/新浪/东财的
指数体系都不收 880xxx 通达信板块指数，实测确认），唯一可达源是 pytdx 直连通达信
行情服务器（TCP 7709，与东财 HTTP 推送服务器分属不同通道，后者在本网络被代理拦）。

周线仅支持 avg_price，日线支持 avg_price/880823.TDX/881385.TDX；
盘中分钟快照支持880003.TDX与881385.TDX。
其它代码拒绝，本 provider 不参与
沪深创科四指数的取数（那些走 tushare/akshare）。调用方用 registry.call_specific("tdx",…)
直连本 provider，绕开「tushare 对未知 code 返回空列表也算 success」的遮蔽问题。
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time
from time import monotonic

from utils.realtime_quote import quote_is_fresh, shanghai_now

from .base import DataProvider, DataResult, Confidence, Timeliness

logger = logging.getLogger(__name__)

# 通达信行情主站（多服务器 fallback）。部分节点虽然 TCP 可连，却不再提供 880xxx
# 板块指数，因此节点选择必须以目标代码的有效 K 线为准，不能只验证 connect()。
# 2026-09-13：原节点只返回两字节响应；以下节点已实测 880823 日线。
_DEFAULT_SERVERS = [
    ("139.9.52.158", 7709),
    ("103.251.85.94", 7709),
    ("59.36.5.11", 7709),
    ("103.221.142.82", 7709),
    ("117.34.114.16", 7709),
    ("159.75.55.232", 7709),
]

_AVG_PRICE_CODE = "880003"  # 通达信「平均股价」
AVG_PRICE_CODE = "880003.TDX"  # 盘中独立规范身份，不复用同花顺全A或ETF
MICROCAP_CODE = "880823.TDX"  # 独立身份，不复用中证2000/同花顺代码
BANK_CODE = "881385.TDX"  # 通达信研究行业银行，不是同花顺881155或申万801780
_INTRADAY_CODES = {AVG_PRICE_CODE: (_AVG_PRICE_CODE, "平均股价"),
                   BANK_CODE: ("881385", "银行（通达信研究行业）")}
_DAILY_CODES = {"avg_price": _AVG_PRICE_CODE, MICROCAP_CODE: "880823", BANK_CODE: "881385"}
_TDX_MARKET_SH = 1          # 880xxx 通达信板块指数走沪市站（实测 market=0 返回 0 根）
_KLINE_WEEKLY = 5           # pytdx K 线周期：5=周线
_KLINE_DAILY = 9            # pytdx K 线周期：9=日线（实测 880003 日线可取）
_WEEKLY_COUNT = 20          # 取最近 20 根周线，够算 5 周均线 + 窗口冗余
_DAILY_COUNT = 800          # 单次协议上限；覆盖当前740自然日及20月均线，非无限历史接口
_KLINE_MINUTE = 8
_MINUTE_COUNT = 2
_INTRADAY_BUDGET_SECONDS = 5.0
_INTRADAY_SOCKET_TIMEOUT = 1.0


def _minute_quote(bars: object, code: str = AVG_PRICE_CODE) -> dict:
    """使用最新1分钟K线close及其原始时间，不伪造逐笔时间或当日累计量额。"""
    if not isinstance(bars, list) or len(bars) != _MINUTE_COUNT:
        raise ValueError("分钟响应须含最近2根K线")
    raw_code, name = _INTRADAY_CODES[code]
    previous_at = None
    for bar in bars:
        if not isinstance(bar, dict):
            raise ValueError("分钟行非法")
        if ("code" in bar and bar["code"] != raw_code
                or "market" in bar and (type(bar["market"]) is not int or bar["market"] != _TDX_MARKET_SH)):
            raise ValueError("分钟行身份与请求不一致")
        raw_time = bar.get("datetime")
        try:
            quoted_at = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
            if quoted_at.strftime("%Y-%m-%d %H:%M") != raw_time:
                raise ValueError("非规范时间")
        except (ValueError, TypeError) as exc:
            raise ValueError("分钟行情缺少合法原始日期时间") from exc
        if (previous_at is not None and quoted_at <= previous_at
                or not (time(9, 30) <= quoted_at.time() <= time(11, 30)
                        or time(13) <= quoted_at.time() <= time(15))):
            raise ValueError("分钟行情时间重复、倒序或非交易时段")
        prices = {}
        for key in ("open", "high", "low", "close"):
            raw = bar.get(key)
            try:
                value = float(raw)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError("分钟行情价格缺失或非法") from exc
            if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("分钟行情价格非有限或非正数")
            prices[key] = value
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            raise ValueError("分钟行情OHLC冲突")
        previous_at = quoted_at
    # pytdx已将指数价格除以1000，不再缩放；分钟时间的当日/10分钟新鲜度由服务校验。
    return {"code": code, "name": name, "price": prices["close"],
            "quote_date": quoted_at.date().isoformat(), "quote_time": quoted_at.time().isoformat()}


def _daily_rows(bars, index_code: str, start_date: str, end_date: str) -> list[dict]:
    """完整取回800根且覆盖窗口起点；日期/OHLC有效，量额缺失不补零。"""
    # 两个支持的指数均已有超过800根历史。浅历史节点不能抢先成为成功源。
    if len(bars) != _DAILY_COUNT:
        raise ValueError(f"insufficient_depth: 需要{_DAILY_COUNT}根日线，实际{len(bars)}根")
    rows = []
    seen = set()
    for bar in bars:
        if ("code" in bar and bar["code"] != _DAILY_CODES[index_code]
                or "market" in bar and (type(bar["market"]) is not int or bar["market"] != _TDX_MARKET_SH)):
            raise ValueError("日线行身份与请求不一致")
        td = date.fromisoformat(str(bar.get("datetime", "")).split()[0]).isoformat()
        prices = {key: float(bar[key]) for key in ("open", "high", "low", "close")}
        if (td in seen or not all(math.isfinite(v) and v > 0 for v in prices.values())
                or not prices["low"] <= min(prices["open"], prices["close"])
                <= max(prices["open"], prices["close"]) <= prices["high"]):
            raise ValueError("日线日期重复或 OHLC 非法")
        seen.add(td)
        if not start_date <= td <= end_date:
            continue
        row = {"trade_date": td, "ts_code": index_code, "pct_chg": None, **prices}
        for key in ("vol", "amount"):
            try:
                value = float(bar[key])
            except (KeyError, TypeError, ValueError):
                value = None
            row[key] = value if value is not None and math.isfinite(value) and value >= 0 else None
        rows.append(row)
    rows.sort(key=lambda row: row["trade_date"])
    if min(seen) > start_date:
        raise ValueError("insufficient_depth: 最近800根日线未覆盖请求窗口起点")
    if not rows:
        raise ValueError("日线窗口内无有效行")
    return rows


class TdxProvider(DataProvider):
    """pytdx → 通达信专用指数，不参与标准宽基的身份替换。"""

    name = "tdx"
    priority = 4  # 排在 tushare(1)/akshare(2) 之后；实际通过 call_specific 直连，priority 仅占位

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._servers = self.config.get("servers") or _DEFAULT_SERVERS
        self._timeout = self.config.get("timeout", 6)

    def initialize(self) -> bool:
        # 懒连接：每次取数现连现断，不维持长连接（通达信服务器会主动断闲连）
        self._initialized = True
        return True

    def get_capabilities(self) -> list[str]:
        return ["get_index_weekly", "get_index_daily_range", "get_realtime_quotes"]

    def get_realtime_quotes(self, codes: list[str], *, now: datetime | None = None) -> DataResult:
        """白名单指数最新分钟价；不以抓取时间填行情日期，不伪造昨收。"""
        normalized = list(dict.fromkeys(str(raw or "").strip().upper() for raw in codes or []))
        if not normalized or any(code not in _INTRADAY_CODES for code in normalized):
            return DataResult(None, self.name, error="tdx盘中快照仅支持880003.TDX平均股价 / 881385.TDX银行")
        checked_at = shanghai_now(now)
        quotes, errors = [], []
        for code in normalized:
            def validate(bars):
                quote = _minute_quote(bars, code)
                fresh, reason, _ = quote_is_fresh(quote, checked_at)
                if not fresh:
                    raise ValueError(reason)
                return quote

            api, quote, error = self._connect(
                _KLINE_MINUTE, _MINUTE_COUNT, _INTRADAY_CODES[code][0], transform=validate,
                budget_seconds=_INTRADAY_BUDGET_SECONDS, socket_timeout=_INTRADAY_SOCKET_TIMEOUT,
            )
            if api is None:
                errors.append(f"{code}: {error}")
                continue
            try:
                quotes.append(quote)
            finally:
                try:
                    api.disconnect()
                except Exception as exc:
                    logger.debug("tdx分钟连接关闭失败: %s", exc)
        source = f"tdx:{normalized[0][:6]}_1min" if len(normalized) == 1 else "tdx:index_1min"
        return DataResult(quotes or None, source, timeliness=Timeliness.REALTIME,
                          error="; ".join(errors) if not quotes else "",
                          note="最新1分钟K线close，并非逐笔价；不提供昨收及当日累计量额" +
                               ("; " + "; ".join(errors) if errors else ""))

    def _connect(self, category: int, count: int, code: str = _AVG_PRICE_CODE, transform=None,
                 *, budget_seconds: float | None = None, socket_timeout: float | None = None):
        """逐节点验证目标代码；可转换并校验响应，失败继续下一节点。"""
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            return None, None, "pytdx 未安装（pip install pytdx）"
        connected = False
        validation_errors = set()
        deadline = monotonic() + budget_seconds if budget_seconds is not None else None
        for host, port in self._servers:
            remaining = deadline - monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                validation_errors.add("盘中节点尝试预算耗尽")
                break
            timeout = self._timeout if socket_timeout is None else min(self._timeout, socket_timeout)
            if remaining is not None:
                timeout = min(timeout, remaining)
            api = TdxHq_API(heartbeat=True)
            keep_connection = False
            try:
                if api.connect(host, port, time_out=timeout):
                    connected = True
                    # 5秒为节点尝试软预算；每个socket操作另有<=1秒超时。
                    # 已在途的握手可略跨预算，但不再启动下一次取数/节点尝试。
                    if deadline is not None and monotonic() >= deadline:
                        validation_errors.add("盘中节点尝试预算耗尽")
                        break
                    bars = api.get_index_bars(category, _TDX_MARKET_SH, code, 0, count)
                    # 空响应无法证明节点提供此指数，可能是不支持该代码或源不可用；
                    # 保留“无有效数据”，不解释成已确认历史深度为0。非空浅历史才
                    # 标 insufficient_depth；两者都会换节点且全部失败时绝不返回成功。
                    if bars and transform is not None:
                        try:
                            bars = transform(bars)
                        except ValueError as exc:
                            validation_errors.add(str(exc))
                            raise
                    if bars:
                        keep_connection = True
                        return api, bars, ""
                    logger.debug("tdx 节点 %s:%s 无 %s 有效数据，尝试下一个", host, port, code)
            except Exception as e:  # 单节点连接或取数失败不致命，退到下一个
                logger.debug("tdx 节点 %s:%s 失败: %s", host, port, e)
            finally:
                if not keep_connection:
                    try:
                        api.disconnect()
                    except Exception as e:
                        logger.debug("tdx 节点 %s:%s disconnect 失败: %s", host, port, e)
        if connected:
            detail = "; ".join(sorted(validation_errors))
            return None, None, f"所有可连接的通达信行情服务器均无 {code} 有效数据" + (f": {detail}" if detail else "")
        detail = "; ".join(sorted(validation_errors))
        return None, None, "所有通达信行情服务器连接失败" + (f": {detail}" if detail else "")

    def get_index_weekly(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        if index_code != "avg_price":
            return DataResult(
                data=None, source=self.name,
                error="tdx provider 仅支持 avg_price（通达信 880003 平均股价）",
            )
        api, bars, err = self._connect(_KLINE_WEEKLY, _WEEKLY_COUNT)
        if api is None:
            return DataResult(data=None, source=self.name, error=err)
        try:
            sd = start_date.replace("-", "")
            ed = end_date.replace("-", "")
            rows = []
            for b in bars:
                # pytdx datetime 形如 '2026-05-29 15:00'，取日期段归一化为 YYYYMMDD
                td = str(b.get("datetime", "")).split()[0].replace("-", "")[:8]
                if not (len(td) == 8 and td.isdigit()):
                    continue
                if td < sd or td > ed:  # 钳到调用方请求窗口内
                    continue
                close = b.get("close")
                if close is None:
                    continue
                rows.append({
                    "trade_date": td,
                    "close": float(close),
                    "open": float(b.get("open", close)),
                    "high": float(b.get("high", close)),
                    "low": float(b.get("low", close)),
                })
            if not rows:
                return DataResult(data=None, source=self.name, error="tdx 880003 周线窗口内无有效行")
            return DataResult(
                data=rows, source="tdx:880003_weekly",
                confidence=Confidence.HIGH, timeliness=Timeliness.HISTORICAL,
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=f"tdx 880003 取数异常: {e}")
        finally:
            try:
                api.disconnect()
            except Exception as e:
                logger.debug("tdx disconnect 失败: %s", e)

    def get_index_daily_range(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        """最近800根日线按请求窗口过滤；来源身份固定，不回退到其他指数。"""
        code = _DAILY_CODES.get(index_code)
        if code is None:
            return DataResult(data=None, source=self.name,
                              error="tdx 日线仅支持 avg_price / 880823.TDX / 881385.TDX")
        source = f"tdx:{code}_daily"
        try:
            sd = date.fromisoformat(start_date).isoformat()
            ed = date.fromisoformat(end_date).isoformat()
            if sd > ed:
                raise ValueError("start_date 晚于 end_date")
        except ValueError as exc:
            return DataResult(data=None, source=source, error=str(exc))
        api, rows, err = self._connect(
            _KLINE_DAILY, _DAILY_COUNT, code,
            transform=lambda bars: _daily_rows(bars, index_code, sd, ed),
            **({"budget_seconds": _INTRADAY_BUDGET_SECONDS,
                "socket_timeout": _INTRADAY_SOCKET_TIMEOUT} if index_code == BANK_CODE else {}),
        )
        if api is None:
            return DataResult(data=None, source=source, error=err)
        try:
            return DataResult(data=rows, source=source,
                              confidence=Confidence.HIGH, timeliness=Timeliness.HISTORICAL)
        finally:
            try:
                api.disconnect()
            except Exception as exc:
                logger.debug("tdx disconnect 失败: %s", exc)
