"""
Tushare 数据提供者
文档: https://tushare.pro/document/2
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from .base import DataProvider, DataResult, DataType, Confidence, Timeliness

logger = logging.getLogger(__name__)


class TushareProvider(DataProvider):
    name = "tushare"
    priority = 1

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.pro = None

    def initialize(self) -> bool:
        try:
            import tushare as ts
            import tushare.pro.client as client
            client.DataApi._DataApi__http_url = "http://tushare.xyz"
            token = self.config.get("token") or os.getenv("TUSHARE_TOKEN", "")
            if not token:
                logger.error("TUSHARE_TOKEN 未配置")
                return False
            ts.set_token(token)
            self.pro = ts.pro_api()
            # 测试连接
            df = self.pro.trade_cal(exchange="SSE", start_date="20260101", end_date="20260101")
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Tushare 初始化失败: {e}")
            return False

    def get_capabilities(self) -> list[str]:
        return [
            "get_index_daily",
            "get_market_volume",
            "get_stock_daily",
            "get_stock_ma",
            "get_limit_up_list",
            "get_limit_down_list",
            "get_sector_rankings",
            "get_northbound",
            "get_margin_data",
            "get_dragon_tiger",
            "get_stock_announcements",
            "get_global_index",
            "is_trade_day",
        ]

    def _date_fmt(self, date: str) -> str:
        """统一日期格式为 YYYYMMDD"""
        return date.replace("-", "")

    # ---- 国际指数（index_global，需约 6000 积分）----

    def get_global_index(self, index_name: str) -> DataResult:
        """
        国际主要指数日线最新一行。ts_code 见 Tushare index_global 文档。
        """
        code_map = {
            "dow_jones": ("DJI", "道琼斯"),
            "nasdaq": ("IXIC", "纳斯达克"),
            "sp500": ("SPX", "标普500"),
            "a50": ("XIN9", "富时中国A50"),
        }
        pair = code_map.get(index_name)
        if not pair:
            return DataResult(
                data=None,
                source=self.name,
                error=f"tushare 未支持的国际指数键: {index_name}",
            )
        ts_code, display_name = pair
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
            df = self.pro.index_global(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty:
                return DataResult(
                    data=None,
                    source=self.name,
                    error=f"无 index_global 数据: {ts_code}",
                )
            df = df.sort_values("trade_date")
            row = df.iloc[-1]
            data = {
                "name": display_name,
                "close": float(row["close"]),
                "change_pct": float(row["pct_chg"]),
            }
            return DataResult(data=data, source="tushare:index_global")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 行情数据 ----

    def get_index_daily(self, index_code: str, date: str) -> DataResult:
        """获取指数日线数据"""
        try:
            d = self._date_fmt(date)
            # 指数代码映射
            code_map = {
                "shanghai": "000001.SH",
                "shenzhen": "399001.SZ",
                "chinext": "399006.SZ",
                "star50": "000688.SH",
            }
            ts_code = code_map.get(index_code, index_code)
            df = self.pro.index_daily(ts_code=ts_code, start_date=d, end_date=d)
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无数据: {ts_code} {date}")
            row = df.iloc[0]
            data = {
                "code": ts_code,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "change_pct": float(row["pct_chg"]),
                "volume": float(row.get("vol", 0)),
                "amount_billion": float(row.get("amount", 0)) / 1000,  # 千元 -> 亿
            }
            return DataResult(data=data, source="tushare:index_daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_market_volume(self, date: str) -> DataResult:
        """获取两市总成交额"""
        try:
            d = self._date_fmt(date)
            # 分别获取沪深成交额
            sh = self.pro.index_daily(ts_code="000001.SH", start_date=d, end_date=d)
            sz = self.pro.index_daily(ts_code="399001.SZ", start_date=d, end_date=d)
            if sh.empty or sz.empty:
                return DataResult(data=None, source=self.name, error=f"无成交额数据: {date}")
            sh_amount = float(sh.iloc[0].get("amount", 0)) / 1000
            sz_amount = float(sz.iloc[0].get("amount", 0)) / 1000
            data = {
                "shanghai_billion": round(sh_amount, 2),
                "shenzhen_billion": round(sz_amount, 2),
                "total_billion": round(sh_amount + sz_amount, 2),
            }
            return DataResult(data=data, source="tushare:index_daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_daily(self, stock_code: str, date: str) -> DataResult:
        """获取个股日线数据"""
        try:
            d = self._date_fmt(date)
            df = self.pro.daily(ts_code=stock_code, start_date=d, end_date=d)
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无数据: {stock_code} {date}")
            row = df.iloc[0]
            data = {
                "code": stock_code,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "change_pct": float(row["pct_chg"]),
                "volume": float(row["vol"]),  # 手
                "amount_billion": float(row["amount"]) / 100000,  # 千元 -> 亿
                "turnover_rate": float(row.get("turnover_rate", 0)),
                "amplitude_pct": round(
                    (float(row["high"]) - float(row["low"])) / float(row["pre_close"]) * 100, 2
                ),
            }
            return DataResult(data=data, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 涨跌停数据 ----

    def get_limit_up_list(self, date: str) -> DataResult:
        """获取涨停股列表"""
        try:
            d = self._date_fmt(date)
            df = self.pro.limit_list_d(trade_date=d, limit_type="U")
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无涨停数据: {date}")
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row["ts_code"],
                    "name": row["name"],
                    "close": float(row.get("close", 0)),
                    "pct_chg": float(row.get("pct_chg", 0)),
                    "amount_billion": float(row.get("amount", 0)) / 100000,
                    "turnover_rate": float(row.get("turnover_rate_f", 0)),
                    "fd_amount": float(row.get("fd_amount", 0)),  # 封单金额
                    "first_time": row.get("first_time", ""),
                    "last_time": row.get("last_time", ""),
                    "limit_times": int(row.get("limit_times", 1)),  # 连板天数
                })
            summary = {
                "count": len(stocks),
                "stocks": stocks,
            }
            return DataResult(data=summary, source="tushare:limit_list_d")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_limit_down_list(self, date: str) -> DataResult:
        """获取跌停股列表"""
        try:
            d = self._date_fmt(date)
            df = self.pro.limit_list_d(trade_date=d, limit_type="D")
            if df.empty:
                return DataResult(data=[], source="tushare:limit_list_d")
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row["ts_code"],
                    "name": row["name"],
                    "close": float(row.get("close", 0)),
                    "pct_chg": float(row.get("pct_chg", 0)),
                })
            return DataResult(
                data={"count": len(stocks), "stocks": stocks},
                source="tushare:limit_list_d",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 北向资金 ----

    def get_northbound(self, date: str) -> DataResult:
        """获取北向资金数据"""
        try:
            d = self._date_fmt(date)
            df = self.pro.moneyflow_hsgt(trade_date=d)
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无北向资金数据: {date}")
            row = df.iloc[0]
            data = {
                "north_money": float(row.get("north_money", 0)),  # 北向资金（百万）
                "south_money": float(row.get("south_money", 0)),
                "net_buy_billion": round(float(row.get("north_money", 0)) / 100, 2),
            }
            return DataResult(data=data, source="tushare:moneyflow_hsgt")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 融资融券汇总 ----

    def get_margin_data(self, date: str) -> DataResult:
        """
        沪深两市融资融券每日汇总（pro.margin）。
        余额单位为元，输出中附带亿元字段便于阅读。
        """
        try:
            d = self._date_fmt(date)
            df = self.pro.margin(trade_date=d)
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无融资融券汇总数据: {date}")
            exchanges: list[dict] = []
            total_rzye = 0.0
            total_rqye = 0.0
            total_rzrqye = 0.0
            for _, row in df.iterrows():
                ex = str(row.get("exchange_id", "") or "")
                rzye = float(row.get("rzye", 0) or 0)
                rqye = float(row.get("rqye", 0) or 0)
                rzrqye = float(row.get("rzrqye", 0) or 0)
                total_rzye += rzye
                total_rqye += rqye
                total_rzrqye += rzrqye
                exchanges.append({
                    "exchange_id": ex,
                    "rzye_yi": round(rzye / 1e8, 2),
                    "rqye_yi": round(rqye / 1e8, 2),
                    "rzrqye_yi": round(rzrqye / 1e8, 2),
                })
            data = {
                "trade_date": date,
                "exchanges": exchanges,
                "total_rzye_yi": round(total_rzye / 1e8, 2),
                "total_rqye_yi": round(total_rqye / 1e8, 2),
                "total_rzrqye_yi": round(total_rzrqye / 1e8, 2),
            }
            return DataResult(data=data, source="tushare:margin")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 龙虎榜 ----

    def get_dragon_tiger(self, date: str) -> DataResult:
        """获取龙虎榜数据"""
        try:
            d = self._date_fmt(date)
            df = self.pro.top_list(trade_date=d)
            if df.empty:
                return DataResult(data=[], source="tushare:top_list")
            records = []
            for _, row in df.iterrows():
                records.append({
                    "code": row["ts_code"],
                    "name": row["name"],
                    "reason": row.get("reason", ""),
                    "buy_amount": float(row.get("buy", 0)),
                    "sell_amount": float(row.get("sell", 0)),
                    "net_amount": float(row.get("net_amount", 0)),
                })
            return DataResult(data=records, source="tushare:top_list")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 公告 ----

    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """获取个股公告"""
        try:
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            df = self.pro.anns(ts_code=stock_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:anns")
            records = []
            for _, row in df.iterrows():
                records.append({
                    "title": row.get("title", ""),
                    "ann_date": row.get("ann_date", ""),
                    "url": row.get("url", ""),
                })
            return DataResult(data=records, source="tushare:anns")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 交易日历 ----

    def is_trade_day(self, date: str) -> DataResult:
        """判断是否为交易日"""
        try:
            d = self._date_fmt(date)
            df = self.pro.trade_cal(exchange="SSE", start_date=d, end_date=d)
            if df.empty:
                return DataResult(data=False, source="tushare:trade_cal")
            is_open = df.iloc[0]["is_open"] == 1
            return DataResult(data=is_open, source="tushare:trade_cal")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))
