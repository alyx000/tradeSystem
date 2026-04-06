"""
Tushare 数据提供者
文档: https://tushare.pro/document/2
"""
from __future__ import annotations

import os
import logging
import math
from datetime import datetime, timedelta
from .base import DataProvider, DataResult, DataType, Confidence, Timeliness

logger = logging.getLogger(__name__)


class TushareProvider(DataProvider):
    name = "tushare"
    priority = 1

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.pro = None
        self._sw_l2_codes: set | None = None
        self._ths_concept_map: dict | None = None

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
            "get_daily_basic",
            "get_adj_factor",
            "get_limit_up_list",
            "get_limit_down_list",
            "get_stock_limit_prices",
            "get_limit_step",
            "get_limit_cpt_list",
            "get_sector_rankings",
            "get_sector_moneyflow_ths",
            "get_sector_moneyflow_dc",
            "get_ths_index",
            "get_ths_member",
            "get_index_classify",
            "get_market_breadth",
            "get_daily_info",
            "get_market_moneyflow_dc",
            "get_northbound",
            "get_northbound_top_stocks",
            "get_margin_data",
            "get_margin_detail",
            "get_dragon_tiger",
            "get_block_trade",
            "get_stock_announcements",
            "get_market_announcements",
            "get_disclosure_dates",
            "get_share_float",
            "get_stock_st",
            "get_global_index",
            "is_trade_day",
            "get_trade_calendar",
            "get_stock_basic_list",
            "get_top_volume_stocks",
        ]

    def _date_fmt(self, date: str) -> str:
        """统一日期格式为 YYYYMMDD"""
        return date.replace("-", "")

    def _quarter_end_for_date(self, date: str) -> str:
        dt = datetime.strptime(date, "%Y-%m-%d")
        if dt.month <= 3:
            return f"{dt.year - 1}1231"
        if dt.month <= 6:
            return f"{dt.year}0331"
        if dt.month <= 9:
            return f"{dt.year}0630"
        return f"{dt.year}0930"

    def _clean_scalar(self, value):
        if hasattr(value, "item"):
            try:
                value = value.item()
            except Exception:
                pass
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    def _df_to_records(self, df) -> list[dict]:
        if df is None or df.empty:
            return []
        return [
            {key: self._clean_scalar(val) for key, val in row.items()}
            for row in df.to_dict(orient="records")
        ]

    def _query_records(self, api_name: str, **params) -> list[dict]:
        return self._df_to_records(self.pro.query(api_name, **params))

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
                "amount_billion": round(float(row.get("amount", 0)) / 1e5, 2),  # 千元 → 亿
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
            sh_amount = float(sh.iloc[0].get("amount", 0)) / 1e5  # 千元 → 亿
            sz_amount = float(sz.iloc[0].get("amount", 0)) / 1e5
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

    def get_stock_ma(self, stock_code: str, date: str, periods: list[int] | None = None) -> DataResult:
        """计算个股均线（基于 tushare daily 历史数据）"""
        periods = periods or [5, 10, 20]
        max_p = max(periods)
        try:
            ed = self._date_fmt(date)
            sd = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=max_p * 2 + 10)).strftime("%Y%m%d")
            df = self.pro.daily(ts_code=stock_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无历史数据: {stock_code}")
            df = df.sort_values("trade_date")
            data: dict = {}
            for p in periods:
                if len(df) >= p:
                    data[f"ma{p}"] = round(float(df["close"].tail(p).mean()), 2)
            if len(df) >= 5:
                data["volume_ma5"] = round(float(df["vol"].tail(5).mean()), 2)
            return DataResult(data=data, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_daily_basic(self, date: str) -> DataResult:
        """获取全市场每日指标快照。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("daily_basic", trade_date=d),
                source="tushare:daily_basic",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_adj_factor(self, date: str) -> DataResult:
        """获取全市场复权因子快照。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("adj_factor", trade_date=d),
                source="tushare:adj_factor",
            )
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

    def get_stock_limit_prices(self, date: str) -> DataResult:
        """获取全市场涨跌停价格。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("stk_limit", trade_date=d),
                source="tushare:stk_limit",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_limit_step(self, date: str) -> DataResult:
        """获取连板天梯。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("limit_step", trade_date=d),
                source="tushare:limit_step",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_limit_cpt_list(self, date: str) -> DataResult:
        """获取最强板块统计。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("limit_cpt_list", trade_date=d),
                source="tushare:limit_cpt_list",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 市场宽度 ----

    def get_market_breadth(self, date: str) -> DataResult:
        """全市场涨跌家数统计"""
        try:
            d = self._date_fmt(date)
            df = self.pro.daily(trade_date=d, fields="ts_code,pct_chg")
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无全市场行情数据: {date}")
            pcts = df["pct_chg"].dropna()
            advance = int((pcts > 0).sum())
            decline = int((pcts < 0).sum())
            flat = int((pcts == 0).sum())
            total = advance + decline + flat
            ratio = round(advance / decline, 2) if decline > 0 else float("inf")
            data = {
                "advance": advance,
                "decline": decline,
                "flat": flat,
                "total": total,
                "advance_ratio": ratio,
            }
            return DataResult(data=data, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_daily_info(self, date: str) -> DataResult:
        """获取交易所市场交易统计。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("daily_info", trade_date=d),
                source="tushare:daily_info",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_market_moneyflow_dc(self, date: str) -> DataResult:
        """获取东财大盘资金流向。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("moneyflow_mkt_dc", trade_date=d),
                source="tushare:moneyflow_mkt_dc",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_top_volume_stocks(self, date: str, top_n: int = 20) -> DataResult:
        """全市场按成交额降序取 Top N 个股（用于成交量集中度分析与次日人气股追踪）"""
        try:
            d = self._date_fmt(date)
            df = self.pro.daily(
                trade_date=d,
                fields="ts_code,name,close,amount,pct_chg",
            )
            if df is None or df.empty:
                return DataResult(data=None, source=self.name,
                                  error=f"无全市场行情数据: {date}")
            df = df.dropna(subset=["amount"])
            df = df.sort_values("amount", ascending=False).head(top_n).reset_index(drop=True)
            stocks = []
            for rank, (_, row) in enumerate(df.iterrows(), start=1):
                stocks.append({
                    "rank": rank,
                    "code": str(row["ts_code"]),
                    "name": str(row.get("name", "")),
                    "close": round(float(row.get("close", 0) or 0), 2),
                    "amount_billion": round(float(row["amount"]) / 1e4, 2),
                    "change_pct": round(float(row.get("pct_chg", 0) or 0), 2),
                })
            return DataResult(data=stocks, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_moneyflow_ths(self, date: str) -> DataResult:
        """获取同花顺行业资金流向原始数据。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("moneyflow_ind_ths", trade_date=d),
                source="tushare:moneyflow_ind_ths",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_moneyflow_dc(self, date: str) -> DataResult:
        """获取东财行业/概念板块资金流向原始数据。"""
        try:
            d = self._date_fmt(date)
            return DataResult(
                data=self._query_records("moneyflow_ind_dc", trade_date=d),
                source="tushare:moneyflow_ind_dc",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_ths_index(self, _date: str) -> DataResult:
        """获取同花顺板块指数主数据。"""
        try:
            df = self.pro.ths_index(exchange="A")
            return DataResult(data=self._df_to_records(df), source="tushare:ths_index")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_ths_member(self, _date: str) -> DataResult:
        """获取同花顺概念板块成分。按概念板块(type=N)循环拉取，适合低频刷新。"""
        try:
            index_df = self.pro.ths_index(type="N")
            if index_df is None or index_df.empty:
                return DataResult(data=[], source="tushare:ths_member")
            records: list[dict] = []
            for _, index_row in index_df.iterrows():
                ts_code = str(index_row.get("ts_code", "") or "")
                if not ts_code:
                    continue
                try:
                    member_df = self.pro.ths_member(ts_code=ts_code)
                except Exception as exc:
                    logger.debug("ths_member %s 获取失败: %s", ts_code, exc)
                    continue
                for item in self._df_to_records(member_df):
                    item.setdefault("ts_code", ts_code)
                    item["index_name"] = str(index_row.get("name", "") or "")
                    item["index_type"] = str(index_row.get("type", "") or "")
                    records.append(item)
            return DataResult(data=records, source="tushare:ths_member")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_index_classify(self, _date: str) -> DataResult:
        """获取申万行业分类主数据。"""
        try:
            df = self.pro.index_classify(src="SW2021")
            return DataResult(data=self._df_to_records(df), source="tushare:index_classify")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 板块排名 ----

    def _ensure_sw_l2_codes(self) -> set:
        """惰性加载申万二级行业代码表（~134个细分行业，粒度适合短线板块跟踪）"""
        if self._sw_l2_codes is None:
            try:
                ic = self.pro.index_classify(level="L2", src="SW2021")
                if ic is not None and not ic.empty:
                    self._sw_l2_codes = set(ic["index_code"].tolist())
                else:
                    self._sw_l2_codes = set()
            except Exception as e:
                logger.warning(f"获取申万行业分类失败: {e}")
                self._sw_l2_codes = set()
        return self._sw_l2_codes

    def _ensure_ths_concept_map(self) -> dict:
        """惰性加载同花顺概念指数 ts_code → name 映射"""
        if self._ths_concept_map is None:
            try:
                idx = self.pro.ths_index(type="N")
                if idx is not None and not idx.empty:
                    self._ths_concept_map = dict(zip(idx["ts_code"], idx["name"]))
                else:
                    self._ths_concept_map = {}
            except Exception as e:
                logger.warning(f"获取同花顺概念列表失败: {e}")
                self._ths_concept_map = {}
        return self._ths_concept_map

    def get_sector_rankings(self, date: str, sector_type: str = "industry") -> DataResult:
        """板块涨跌幅排名（涨幅前30 + 跌幅前5）"""
        try:
            d = self._date_fmt(date)
            if sector_type == "industry":
                return self._sector_rankings_sw(d)
            else:
                return self._sector_rankings_ths(d)
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def _sector_rankings_sw(self, d: str) -> DataResult:
        """申万二级行业排名（~134个细分行业）"""
        l2_codes = self._ensure_sw_l2_codes()
        df = self.pro.sw_daily(trade_date=d)
        if df is None or df.empty:
            return DataResult(data=None, source=self.name, error="无申万行业日线数据")

        if l2_codes:
            df = df[df["ts_code"].isin(l2_codes)]
        if df.empty:
            return DataResult(data=None, source=self.name, error="无匹配的L2行业数据")

        df = df.sort_values("pct_change", ascending=False)

        def _row(r):
            amt = float(r.get("amount", 0) or 0)
            return {
                "name": r.get("name", ""),
                "change_pct": round(float(r.get("pct_change", 0) or 0), 2),
                "volume_billion": round(amt / 10000, 2),  # 万元 → 亿
                "top_stock": "",
            }

        top = [_row(row) for _, row in df.head(30).iterrows()]
        bottom_df = df.tail(5).sort_values("pct_change")
        bottom = [_row(row) for _, row in bottom_df.iterrows()]

        return DataResult(data={"top": top, "bottom": bottom}, source="tushare:sw_daily")

    def _sector_rankings_ths(self, d: str) -> DataResult:
        """同花顺概念指数排名"""
        concept_map = self._ensure_ths_concept_map()
        df = self.pro.ths_daily(trade_date=d)
        if df is None or df.empty:
            return DataResult(data=None, source=self.name, error="无同花顺指数日线数据")

        if concept_map:
            df = df[df["ts_code"].isin(concept_map)]
        if df.empty:
            return DataResult(data=None, source=self.name, error="无匹配的概念指数数据")

        df = df.sort_values("pct_change", ascending=False)

        def _row(r):
            code = r.get("ts_code", "")
            return {
                "name": concept_map.get(code, code),
                "change_pct": round(float(r.get("pct_change", 0) or 0), 2),
                "volume_billion": 0,
                "top_stock": "",
            }

        top = [_row(row) for _, row in df.head(30).iterrows()]
        bottom_df = df.tail(5).sort_values("pct_change")
        bottom = [_row(row) for _, row in bottom_df.iterrows()]

        return DataResult(data={"top": top, "bottom": bottom}, source="tushare:ths_daily")

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
                "north_money": float(row.get("north_money", 0)),  # 万元
                "south_money": float(row.get("south_money", 0)),
                "net_buy_billion": round(float(row.get("north_money", 0)) / 10000, 2),  # 万元 → 亿
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

    def get_margin_detail(self, date: str) -> DataResult:
        """获取逐股融资融券明细。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("margin_detail", trade_date=d)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:margin_detail")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_northbound_top_stocks(self, date: str) -> DataResult:
        """北向资金十大活跃股（按成交额排序）"""
        try:
            d = self._date_fmt(date)
            df = self.pro.hsgt_top10(trade_date=d, market_type="1")  # 沪股通
            df2 = self.pro.hsgt_top10(trade_date=d, market_type="3")  # 深股通
            combined = []
            for src_df in [df, df2]:
                if src_df is not None and not src_df.empty:
                    for _, row in src_df.iterrows():
                        amount_raw = float(row.get("amount", 0) or 0)
                        net_raw = float(row.get("net_amount", 0) or 0)
                        combined.append({
                            "code": row.get("ts_code", ""),
                            "name": row.get("name", ""),
                            "amount_yi": round(amount_raw / 1e8, 2),  # 元 → 亿
                            "net_amount_yi": round(net_raw / 1e8, 2),
                        })
            combined.sort(key=lambda x: x["amount_yi"], reverse=True)
            data = {"top_active": combined[:10]}
            return DataResult(data=data, source="tushare:hsgt_top10")
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
                    "buy_amount": float(row.get("l_buy", 0) or 0),
                    "sell_amount": float(row.get("l_sell", 0) or 0),
                    "net_amount": float(row.get("net_amount", 0) or 0),
                })
            return DataResult(data=records, source="tushare:top_list")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_block_trade(self, date: str) -> DataResult:
        """获取当日大宗交易。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("block_trade", trade_date=d)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:block_trade")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 公告 ----

    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """获取个股公告（anns_d 接口，tushare.xyz 镜像可能不支持）"""
        try:
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            df = self.pro.query("anns_d", ts_code=stock_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:anns_d")
            records = []
            for _, row in df.iterrows():
                records.append({
                    "title": row.get("title", ""),
                    "ann_date": row.get("ann_date", ""),
                    "url": row.get("url", ""),
                })
            return DataResult(data=records, source="tushare:anns_d")
        except Exception as e:
            err = str(e)
            if "权限" in err:
                logger.warning("tushare anns_d 权限不足（镜像 tushare.xyz 不支持该接口），将降级到 akshare")
            return DataResult(data=None, source=self.name, error=err)

    def get_market_announcements(self, date: str) -> DataResult:
        """按公告日获取全市场公告。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("anns_d", ann_date=d)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:anns_d")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_disclosure_dates(self, date: str) -> DataResult:
        """获取当前财报季披露计划。"""
        try:
            end_date = self._quarter_end_for_date(date)
            records = self._query_records("disclosure_date", end_date=end_date)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
                item["report_end"] = end_date
            return DataResult(data=records, source="tushare:disclosure_date")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_share_float(self, date: str) -> DataResult:
        """按解禁日期获取限售股解禁数据。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("share_float", float_date=d)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:share_float")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_st(self, date: str) -> DataResult:
        """获取当日 ST 股票列表。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("stock_st", trade_date=d)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:stock_st")
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

    def get_trade_calendar(self, date: str) -> DataResult:
        """获取目标日期所在自然年的上交所交易日历。"""
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            start = f"{dt.year}0101"
            end = f"{dt.year}1231"
            df = self.pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
            return DataResult(data=self._df_to_records(df), source="tushare:trade_cal")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_basic_list(self, _date: str) -> DataResult:
        """获取当前上市 A 股基础信息。"""
        try:
            df = self.pro.stock_basic(list_status="L")
            return DataResult(data=self._df_to_records(df), source="tushare:stock_basic")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))
