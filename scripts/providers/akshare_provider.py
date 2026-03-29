"""
AkShare 数据提供者（免费，作为 tushare 的补充和降级）
文档: https://akshare.akfamily.xyz/
"""
from __future__ import annotations

import logging
from datetime import datetime
from .base import DataProvider, DataResult, DataType, Confidence, Timeliness

logger = logging.getLogger(__name__)


class AkshareProvider(DataProvider):
    name = "akshare"
    priority = 2

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.ak = None

    def initialize(self) -> bool:
        try:
            import akshare as ak
            self.ak = ak
            # 简单测试
            ak.tool_trade_date_hist_sina()
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"AkShare 初始化失败: {e}")
            return False

    def get_capabilities(self) -> list[str]:
        return [
            "get_index_daily",
            "get_limit_up_list",
            "get_limit_down_list",
            "get_sector_rankings",
            "get_sector_fund_flow",
            "get_northbound",
            "get_global_index",
            "get_commodity",
            "get_forex",
            "is_trade_day",
        ]

    # ---- 外盘数据（tushare 不擅长，akshare 作为主力） ----

    def get_global_index(self, index_name: str) -> DataResult:
        """获取全球指数数据"""
        try:
            name_map = {
                "dow_jones": "道琼斯",
                "nasdaq": "纳斯达克",
                "sp500": "标普500",
                "hsi": "恒生指数",
                "hstech": "恒生科技指数",
                "nikkei": "日经225",
                "ftse": "富时100",
                "dax": "德国DAX30",
                "a50": "富时中国A50指数期货",
            }
            zh_name = name_map.get(index_name, index_name)

            if index_name == "a50":
                df = self.ak.futures_foreign_hist(symbol="A50")
                if df.empty:
                    return DataResult(data=None, source=self.name, error="无A50数据")
                row = df.iloc[-1]
                data = {
                    "name": zh_name,
                    "close": float(row["收盘"]),
                    "change_pct": float(row["涨跌幅"]),
                }
            else:
                df = self.ak.index_investing_global(area="美国" if "nasdaq" in index_name or "dow" in index_name or "sp" in index_name else "中国香港")
                # 简化处理：返回最新一行
                target = df[df["名称"].str.contains(zh_name)]
                if target.empty:
                    return DataResult(data=None, source=self.name, error=f"未找到: {zh_name}")
                row = target.iloc[0]
                data = {
                    "name": zh_name,
                    "close": float(row.get("最新价", 0)),
                    "change_pct": float(row.get("涨跌幅", "0").replace("%", "")),
                }
            return DataResult(data=data, source="akshare:global_index")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_commodity(self, name: str) -> DataResult:
        """获取大宗商品数据"""
        try:
            name_map = {
                "gold": "黄金",
                "crude_oil": "WTI原油",
                "copper": "铜",
            }
            zh_name = name_map.get(name, name)
            df = self.ak.futures_foreign_hist(symbol=zh_name)
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无商品数据: {name}")
            row = df.iloc[-1]
            data = {
                "name": zh_name,
                "close": float(row["收盘"]),
                "change_pct": float(row["涨跌幅"]),
            }
            return DataResult(data=data, source="akshare:commodity")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_forex(self, pair: str) -> DataResult:
        """获取汇率数据"""
        try:
            pair_map = {
                "usd_cny": "美元/人民币",
                "usd_index": "美元指数",
            }
            zh_name = pair_map.get(pair, pair)

            if pair == "usd_index":
                df = self.ak.index_investing_global(area="美国")
                target = df[df["名称"].str.contains("美元指数")]
                if target.empty:
                    return DataResult(data=None, source=self.name, error="未找到美元指数")
                row = target.iloc[0]
                data = {
                    "name": "美元指数",
                    "close": float(row.get("最新价", 0)),
                    "change_pct": float(str(row.get("涨跌幅", "0")).replace("%", "")),
                }
            else:
                df = self.ak.fx_spot_quote()
                target = df[df["货币对"].str.contains("USD/CNY")]
                if target.empty:
                    return DataResult(data=None, source=self.name, error="未找到USD/CNY")
                row = target.iloc[0]
                data = {
                    "name": zh_name,
                    "close": float(row.get("最新价", 0)),
                    "change_pct": float(str(row.get("涨跌幅", "0")).replace("%", "")),
                }
            return DataResult(data=data, source="akshare:forex")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 板块数据（akshare 更方便） ----

    def get_sector_rankings(self, date: str, sector_type: str = "industry") -> DataResult:
        """获取板块涨幅排名"""
        try:
            if sector_type == "industry":
                df = self.ak.stock_board_industry_name_em()
            else:
                df = self.ak.stock_board_concept_name_em()

            if df.empty:
                return DataResult(data=None, source=self.name, error="无板块数据")

            records = []
            for _, row in df.head(30).iterrows():
                records.append({
                    "name": row.get("板块名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "volume_billion": float(row.get("总成交额", 0)) / 1e8,
                    "top_stock": row.get("领涨股票", ""),
                })
            return DataResult(data=records, source=f"akshare:board_{sector_type}")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_fund_flow(self, date: str) -> DataResult:
        """获取板块资金流向"""
        try:
            df = self.ak.stock_sector_fund_flow_rank(indicator="今日")
            if df.empty:
                return DataResult(data=None, source=self.name, error="无资金流向数据")
            records = []
            for _, row in df.head(20).iterrows():
                records.append({
                    "name": row.get("名称", ""),
                    "net_inflow_billion": float(row.get("主力净流入-净额", 0)) / 1e8,
                    "change_pct": float(row.get("涨跌幅", 0)),
                })
            return DataResult(data=records, source="akshare:sector_fund_flow")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 涨跌停数据 ----

    def get_limit_up_list(self, date: str) -> DataResult:
        """获取涨停股列表"""
        try:
            df = self.ak.stock_zt_pool_em(date=date.replace("-", ""))
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无涨停数据: {date}")
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "amount_billion": float(row.get("成交额", 0)) / 1e8,
                    "turnover_rate": float(row.get("换手率", 0)),
                    "first_time": str(row.get("首次封板时间", "")),
                    "last_time": str(row.get("最后封板时间", "")),
                    "limit_times": int(row.get("连板数", 1)),
                    "seal_amount": float(row.get("封单额", 0)),
                })
            return DataResult(
                data={"count": len(stocks), "stocks": stocks},
                source="akshare:stock_zt_pool_em",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_limit_down_list(self, date: str) -> DataResult:
        """获取跌停股列表"""
        try:
            df = self.ak.stock_zt_pool_dtgc_em(date=date.replace("-", ""))
            if df.empty:
                return DataResult(data=[], source="akshare:stock_zt_pool_dtgc_em")
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0)),
                })
            return DataResult(
                data={"count": len(stocks), "stocks": stocks},
                source="akshare:stock_zt_pool_dtgc_em",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 北向资金 ----

    def get_northbound(self, date: str) -> DataResult:
        """获取北向资金"""
        try:
            df = self.ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df.empty:
                return DataResult(data=None, source=self.name, error="无北向资金数据")
            # 取最近一行
            row = df.iloc[-1]
            data = {
                "date": str(row.get("日期", "")),
                "net_buy_billion": float(row.get("当日资金流入", 0)) / 1e4,
            }
            return DataResult(data=data, source="akshare:northbound")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 交易日历 ----

    def is_trade_day(self, date: str) -> DataResult:
        """判断是否为交易日"""
        try:
            trade_dates = self.ak.tool_trade_date_hist_sina()
            target = date.replace("-", "")
            is_open = target in trade_dates["trade_date"].astype(str).values
            return DataResult(data=is_open, source="akshare:trade_cal")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))
