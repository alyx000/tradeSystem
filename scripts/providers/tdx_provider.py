"""通达信行情 provider（pytdx）——专为「平均股价」(通达信 880003) 取数。

为什么单独一个 provider：平均股价不是标准市值指数（akshare/tushare/新浪/东财的
指数体系都不收 880xxx 通达信板块指数，实测确认），唯一可达源是 pytdx 直连通达信
行情服务器（TCP 7709，与东财 HTTP 推送服务器分属不同通道，后者在本网络被代理拦）。

只实现 get_index_weekly("avg_price")。其它 index_code 一律拒绝——本 provider 不参与
沪深创科四指数的取数（那些走 tushare/akshare）。调用方用 registry.call_specific("tdx",…)
直连本 provider，绕开「tushare 对未知 code 返回空列表也算 success」的遮蔽问题。
"""
from __future__ import annotations

import logging

from .base import DataProvider, DataResult, Confidence, Timeliness

logger = logging.getLogger(__name__)

# 通达信行情主站（多服务器 fallback；首选已实测可连，其余为公开备用节点）
_DEFAULT_SERVERS = [
    ("123.125.108.14", 7709),
    ("119.147.212.81", 7709),
    ("180.153.18.170", 7709),
    ("114.80.63.12", 7709),
]

_AVG_PRICE_CODE = "880003"  # 通达信「平均股价」
_TDX_MARKET_SH = 1          # 880xxx 通达信板块指数走沪市站（实测 market=0 返回 0 根）
_KLINE_WEEKLY = 5           # pytdx K 线周期：5=周线
_KLINE_DAILY = 9            # pytdx K 线周期：9=日线（实测 880003 日线可取）
_WEEKLY_COUNT = 20          # 取最近 20 根周线，够算 5 周均线 + 窗口冗余
_DAILY_COUNT = 120          # 取最近 120 根日线 ≈ 半年，够 swing/斐波那契/底分型窗口


class TdxProvider(DataProvider):
    """pytdx → 通达信 880003 平均股价。仅服务 avg_price 的 get_index_weekly。"""

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

    def _connect(self):
        """按服务器列表逐个尝试连接，返回 (api, "") 或 (None, err)。"""
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            return None, "pytdx 未安装（pip install pytdx）"
        api = TdxHq_API(heartbeat=True)
        for host, port in self._servers:
            try:
                if api.connect(host, port, time_out=self._timeout):
                    return api, ""
            except Exception as e:  # 单个服务器连不上不致命，退下一个
                logger.debug("tdx 连接 %s:%s 失败: %s", host, port, e)
                continue
        # 全部失败：pytdx 每次 connect 都创建新 socket，末次失败的 socket 会悬挂；
        # 显式 disconnect 释放（disconnect 内部 close self.client，未连成也安全）。
        try:
            api.disconnect()
        except Exception as e:
            logger.debug("tdx 全失败后 disconnect 失败: %s", e)
        return None, "所有通达信行情服务器连接失败"

    def get_index_weekly(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        if index_code != "avg_price":
            return DataResult(
                data=None, source=self.name,
                error="tdx provider 仅支持 avg_price（通达信 880003 平均股价）",
            )
        api, err = self._connect()
        if api is None:
            return DataResult(data=None, source=self.name, error=err)
        try:
            bars = api.get_index_bars(_KLINE_WEEKLY, _TDX_MARKET_SH, _AVG_PRICE_CODE, 0, _WEEKLY_COUNT)
            if not bars:
                return DataResult(data=None, source=self.name, error="tdx 880003 周线无数据")
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
        """平均股价(880003) 日线区间。仅支持 avg_price，走 pytdx 日线(category=9)。
        归一化 trade_date 为 YYYY-MM-DD（与 tushare get_index_daily_range 对齐，供 scanner 统一消费）。
        pct_chg 留空（由检测器按 close 自算）；vol/amount 透传。"""
        if index_code != "avg_price":
            return DataResult(
                data=None, source=self.name,
                error="tdx provider 仅支持 avg_price（通达信 880003 平均股价）",
            )
        api, err = self._connect()
        if api is None:
            return DataResult(data=None, source=self.name, error=err)
        try:
            bars = api.get_index_bars(_KLINE_DAILY, _TDX_MARKET_SH, _AVG_PRICE_CODE, 0, _DAILY_COUNT)
            if not bars:
                return DataResult(data=None, source=self.name, error="tdx 880003 日线无数据")
            sd = start_date.replace("-", "")
            ed = end_date.replace("-", "")
            rows = []
            for b in bars:
                td = str(b.get("datetime", "")).split()[0].replace("-", "")[:8]
                if not (len(td) == 8 and td.isdigit()):
                    continue
                if td < sd or td > ed:  # 钳到调用方请求窗口内
                    continue
                close = b.get("close")
                if close is None:
                    continue
                rows.append({
                    "trade_date": f"{td[:4]}-{td[4:6]}-{td[6:8]}",
                    "open": float(b.get("open", close)),
                    "high": float(b.get("high", close)),
                    "low": float(b.get("low", close)),
                    "close": float(close),
                    "vol": float(b.get("vol", 0.0) or 0.0),
                    "amount": float(b.get("amount", 0.0) or 0.0),
                    "pct_chg": None,
                    "ts_code": "avg_price",
                })
            if not rows:
                return DataResult(data=None, source=self.name, error="tdx 880003 日线窗口内无有效行")
            # 升序（最旧在前、最新在末尾），与 tushare get_index_daily_range 及检测器契约对齐。
            rows.sort(key=lambda r: r["trade_date"])
            return DataResult(
                data=rows, source="tdx:880003_daily",
                confidence=Confidence.HIGH, timeliness=Timeliness.HISTORICAL,
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=f"tdx 880003 日线取数异常: {e}")
        finally:
            try:
                api.disconnect()
            except Exception as e:
                logger.debug("tdx disconnect 失败: %s", e)
