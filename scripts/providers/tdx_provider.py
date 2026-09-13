"""通达信行情 provider：平均股价 880003 与微盘股 880823。

为什么单独一个 provider：平均股价不是标准市值指数（akshare/tushare/新浪/东财的
指数体系都不收 880xxx 通达信板块指数，实测确认），唯一可达源是 pytdx 直连通达信
行情服务器（TCP 7709，与东财 HTTP 推送服务器分属不同通道，后者在本网络被代理拦）。

周线仅支持 avg_price，日线支持 avg_price/880823.TDX。其它代码拒绝，本 provider 不参与
沪深创科四指数的取数（那些走 tushare/akshare）。调用方用 registry.call_specific("tdx",…)
直连本 provider，绕开「tushare 对未知 code 返回空列表也算 success」的遮蔽问题。
"""
from __future__ import annotations

import logging
import math
from datetime import date

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
MICROCAP_CODE = "880823.TDX"  # 独立身份，不复用中证2000/同花顺代码
_DAILY_CODES = {"avg_price": _AVG_PRICE_CODE, MICROCAP_CODE: "880823"}
_TDX_MARKET_SH = 1          # 880xxx 通达信板块指数走沪市站（实测 market=0 返回 0 根）
_KLINE_WEEKLY = 5           # pytdx K 线周期：5=周线
_KLINE_DAILY = 9            # pytdx K 线周期：9=日线（实测 880003 日线可取）
_WEEKLY_COUNT = 20          # 取最近 20 根周线，够算 5 周均线 + 窗口冗余
_DAILY_COUNT = 800          # 单次协议上限；覆盖当前740自然日及20月均线，非无限历史接口


def _daily_rows(bars, index_code: str, start_date: str, end_date: str) -> list[dict]:
    """完整取回800根且覆盖窗口起点；日期/OHLC有效，量额缺失不补零。"""
    # 两个支持的指数均已有超过800根历史。浅历史节点不能抢先成为成功源。
    if len(bars) != _DAILY_COUNT:
        raise ValueError(f"insufficient_depth: 需要{_DAILY_COUNT}根日线，实际{len(bars)}根")
    rows = []
    seen = set()
    for bar in bars:
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
        return ["get_index_weekly", "get_index_daily_range"]

    def _connect(self, category: int, count: int, code: str = _AVG_PRICE_CODE, transform=None):
        """逐节点验证目标代码；可转换并校验响应，失败继续下一节点。"""
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            return None, None, "pytdx 未安装（pip install pytdx）"
        connected = False
        validation_errors = set()
        for host, port in self._servers:
            api = TdxHq_API(heartbeat=True)
            keep_connection = False
            try:
                if api.connect(host, port, time_out=self._timeout):
                    connected = True
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
        return None, None, "所有通达信行情服务器连接失败"

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
                              error="tdx 日线仅支持 avg_price / 880823.TDX")
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
