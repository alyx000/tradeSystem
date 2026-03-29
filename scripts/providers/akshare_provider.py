"""
AkShare 数据提供者（免费，作为 tushare 的补充和降级）
文档: https://akshare.akfamily.xyz/

盘前外盘/大宗/汇率：优先使用东方财富全球指数现货 index_global_spot_em、
全球期货现货 futures_global_spot_em；已移除失效的 index_investing_global。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from .base import DataProvider, DataResult

logger = logging.getLogger(__name__)


def _to_float_pct(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_float_price(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


class AkshareProvider(DataProvider):
    name = "akshare"
    priority = 2

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.ak = None
        self._df_global_spot_em: Optional[pd.DataFrame] = None
        self._df_futures_global_spot_em: Optional[pd.DataFrame] = None

    def initialize(self) -> bool:
        try:
            import akshare as ak
            self.ak = ak
            ak.tool_trade_date_hist_sina()
            self._df_global_spot_em = None
            self._df_futures_global_spot_em = None
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

    # ------------------------------------------------------------------
    # 缓存：单次盘前采集会多次调用，避免重复拉全表
    # ------------------------------------------------------------------

    def _global_spot_em(self) -> pd.DataFrame:
        if self._df_global_spot_em is None:
            self._df_global_spot_em = self.ak.index_global_spot_em()
        return self._df_global_spot_em

    def _futures_global_spot_em(self) -> pd.DataFrame:
        if self._df_futures_global_spot_em is None:
            self._df_futures_global_spot_em = self.ak.futures_global_spot_em()
        return self._df_futures_global_spot_em

    def _row_global_spot_by_name(self, zh_name: str) -> Optional[pd.Series]:
        df = self._global_spot_em()
        exact = df[df["名称"] == zh_name]
        if not exact.empty:
            return exact.iloc[0]
        sub = df[df["名称"].str.contains(zh_name, na=False)]
        if not sub.empty:
            return sub.iloc[0]
        return None

    def _global_index_from_hist_em(self, symbol: str) -> Optional[dict]:
        """index_global_hist_em：无涨跌幅列时用最近两日收盘推算涨跌幅。"""
        try:
            df = self.ak.index_global_hist_em(symbol=symbol)
        except Exception:
            return None
        if df is None or df.empty or "最新价" not in df.columns:
            return None
        df = df.sort_values("日期" if "日期" in df.columns else df.columns[0])
        if len(df) < 1:
            return None
        last = df.iloc[-1]
        close = _to_float_price(last.get("最新价", 0))
        pct = 0.0
        if len(df) >= 2:
            prev_close = _to_float_price(df.iloc[-2].get("最新价", 0))
            if prev_close:
                pct = round((close - prev_close) / prev_close * 100, 4)
        nm = str(last.get("名称", symbol))
        return {"name": nm, "close": close, "change_pct": pct}

    # ---- 外盘数据 ----

    def get_global_index(self, index_name: str) -> DataResult:
        """
        全球指数：index_global_spot_em 按中文名称匹配；
        A50：futures_global_spot_em 中「A50期指当月连续」等；
        部分指数可回退 index_global_hist_em（推算涨跌幅）。
        """
        name_map = {
            "dow_jones": "道琼斯",
            "nasdaq": "纳斯达克",
            "sp500": "标普500",
            "hsi": "恒生指数",
            "hstech": "恒生科技",
            "nikkei": "日经225",
            "ftse": "英国富时100",
            "dax": "德国DAX30",
            "a50": "__A50_FUTURES__",
        }
        zh_name = name_map.get(index_name, index_name)

        try:
            if index_name == "a50":
                fut = self._futures_global_spot_em()
                # 优先连续合约，否则取有成交价的 A50 期指合约
                for prefer in ("A50期指当月连续",):
                    m = fut[fut["名称"] == prefer]
                    if not m.empty and pd.notna(m.iloc[0].get("最新价")):
                        row = m.iloc[0]
                        break
                else:
                    m = fut[
                        (fut["名称"].str.contains("A50期指", na=False))
                        & (fut["最新价"].notna())
                    ]
                    if m.empty:
                        return DataResult(
                            data=None,
                            source=self.name,
                            error="AkShare: 未找到 A50 期货现货数据",
                        )
                    row = m.sort_values("最新价", ascending=False).iloc[0]
                data = {
                    "name": str(row.get("名称", "A50")),
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:futures_global_spot_em")

            if zh_name not in ("__A50_FUTURES__",):
                row = self._row_global_spot_by_name(zh_name)
                if row is not None and pd.notna(row.get("最新价")):
                    data = {
                        "name": str(row.get("名称", zh_name)),
                        "close": _to_float_price(row.get("最新价")),
                        "change_pct": _to_float_pct(row.get("涨跌幅")),
                    }
                    return DataResult(data=data, source="akshare:index_global_spot_em")

            return DataResult(
                data=None,
                source=self.name,
                error=f"AkShare: 未匹配全球指数: {index_name}",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_commodity(self, name: str) -> DataResult:
        """大宗商品：futures_global_spot_em 按名称关键词取第一条有效报价。"""
        plans: dict[str, list[str]] = {
            "gold": ["迷你黄金当月连续", "迷你黄金", "COMEX黄金", "微型黄金"],
            "crude_oil": ["迷你原油当月连续", "迷你原油", "布伦特原油"],
            "copper": ["COMEX铜"],
        }
        keywords = plans.get(name, [name])
        try:
            fut = self._futures_global_spot_em()
            for kw in keywords:
                if kw.endswith("当月连续"):
                    sub = fut[fut["名称"] == kw]
                else:
                    sub = fut[fut["名称"] == kw]
                    if sub.empty:
                        sub = fut[fut["名称"].str.contains(kw, na=False)]
                sub = sub[sub["最新价"].notna()]
                if sub.empty:
                    continue
                row = sub.iloc[0]
                data = {
                    "name": str(row.get("名称", kw)),
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:futures_global_spot_em")
            return DataResult(
                data=None,
                source=self.name,
                error=f"AkShare: 无商品数据: {name}",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_forex(self, pair: str) -> DataResult:
        """汇率：美元指数来自 index_global_spot_em；USD/CNY 优先 美元人民币中间价（forex_spot_em）。"""
        try:
            if pair == "usd_index":
                row = self._row_global_spot_by_name("美元指数")
                if row is None or pd.isna(row.get("最新价")):
                    got = self._global_index_from_hist_em("美元指数")
                    if got:
                        return DataResult(data=got, source="akshare:index_global_hist_em")
                    return DataResult(data=None, source=self.name, error="未找到美元指数")
                data = {
                    "name": "美元指数",
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:index_global_spot_em")

            if pair == "usd_cny":
                df = self.ak.forex_spot_em()
                for label in ("美元人民币中间价", "美元兑离岸人民币"):
                    m = df[df["名称"] == label]
                    if not m.empty:
                        row = m.iloc[0]
                        data = {
                            "name": label,
                            "close": _to_float_price(row.get("最新价")),
                            "change_pct": _to_float_pct(row.get("涨跌幅")),
                        }
                        return DataResult(data=data, source="akshare:forex_spot_em")
                return DataResult(data=None, source=self.name, error="未找到 USD/CNY 汇率")

            return DataResult(data=None, source=self.name, error=f"不支持: {pair}")
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
