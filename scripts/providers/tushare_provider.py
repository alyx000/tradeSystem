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


def _normalize_trade_date(raw) -> str | None:
    """index_global 的 trade_date 规范化为 YYYY-MM-DD；脏值返回 None。

    覆盖 NaN/NaT/None/Timestamp/带时分秒字符串/「20260522」「2026-05-22」等形态，
    避免「截至 nan」「截至 NaT」「截至 2026-05-22 00:00:00」泄漏到盘前简报。
    """
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    if hasattr(raw, "strftime"):  # pandas Timestamp / datetime.date
        try:
            raw = raw.strftime("%Y-%m-%d")
        except Exception:
            return None
    digits = str(raw).strip().replace("-", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        d = digits[:8]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


# 业绩预告/快报分页参数（STEP0 2026-06-12 实测：镜像 *_vip 接口 period 拉满 4404 行
# 单次返回未截断，cap≥4404；显式传 limit 防默认值漂移，页数上限纯防御兜底）
EARNINGS_PAGE_LIMIT = 5000
EARNINGS_MAX_PAGES = 50
_EARNINGS_DEFAULT_LOOKBACK_DAYS = 3


def _to_clean_str(val) -> str:
    """NaN / None → 空串，其余 str() 去空白；兜底过滤脏值文本。

    与 akshare provider 同名 helper 行为对齐：不能用 `str(val or "")`（NaN truthy 会漏成
    字面 'nan'）。tushare 未引入 pandas，故用 math.isnan 判 NaN。
    """
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "<na>", "none") else s


class TushareProvider(DataProvider):
    name = "tushare"
    priority = 1

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.pro = None
        self._sw_l2_codes: set | None = None
        self._ths_concept_map: dict | None = None
        self._sw_member_map: dict | None = None

    def initialize(self) -> bool:
        self._initialized = False
        self.pro = None
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
            self.pro = None
            self._initialized = False
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
            "get_stock_adj_factor_range",
            "get_holder_trade",
            "get_limit_up_list",
            "get_limit_down_list",
            "get_stock_limit_prices",
            "get_limit_step",
            "get_limit_cpt_list",
            "get_sector_rankings",
            "get_sector_moneyflow_ths",
            "get_concept_moneyflow_ths",
            "get_sector_moneyflow_dc",
            "get_concept_moneyflow_dc",
            "get_ths_index",
            "get_ths_member",
            "get_index_classify",
            "get_stock_sw_industry_map",
            "get_market_breadth",
            "get_daily_info",
            "get_market_moneyflow_dc",
            "get_northbound",
            "get_northbound_top_stocks",
            "get_margin_data",
            "get_margin_series",
            "get_margin_detail",
            "get_dragon_tiger",
            "get_block_trade",
            "get_etf_flow",
            "get_stock_announcements",
            "get_market_announcements",
            "get_disclosure_dates",
            "get_earnings_forecast",
            "get_earnings_express",
            "get_market_daily_quotes",
            "get_analyst_forecasts",
            "get_income_history",
            "get_share_float",
            "get_stock_st",
            "get_global_index",
            "is_trade_day",
            "get_trade_calendar",
            "get_stock_basic_list",
            "get_stock_basic_batch",
            "get_top_volume_stocks",
            "get_suspend_list",
            "get_suspend_change_reasons",
            "get_stk_shock",
            "get_stk_alert",
            "get_market_daily_changes",
            "get_stock_daily_range",
            "get_index_daily_range",
            "get_index_weekly",
        ]

    def _date_fmt(self, date: str) -> str:
        """统一日期格式为 YYYYMMDD"""
        return date.replace("-", "")

    def _normalize_stock_code(self, stock_code: str) -> str:
        """兼容不带交易所后缀的 6 位代码。"""
        code = str(stock_code or "").strip().upper()
        if not code:
            return code
        if "." in code:
            return code
        if code.startswith(("43", "82", "83", "87", "88", "89", "92")):
            return f"{code}.BJ"
        if code.startswith(("60", "68", "90", "51", "52", "53", "56", "58")):
            return f"{code}.SH"
        return f"{code}.SZ"

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

    def _ensure_pro(self, method_name: str) -> DataResult | None:
        if self.pro is not None and self._initialized:
            return None
        return DataResult(
            data=None,
            source=self.name,
            error=f"provider_not_initialized: {method_name}",
        )

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
            # a50 故意不在此：盘前「隔夜」语境要 SGX A50 夜盘期货（含美股时段情绪），
            # 由 akshare futures_global_spot_em 提供；XIN9 指数只有中国时段前日收盘，
            # 当作「A50期货」会语义错位。tushare 返回 unsupported 触发 registry 降级。
            # 镜像 index_global 实测支持 N225/KS11，作日经/韩国的可靠主源；
            # 否则只能退到不稳的 yfinance + 已退化的东财 index_global_spot_em。
            "nikkei": ("N225", "日经225"),
            "kospi": ("KS11", "韩国综指"),
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
            close_val = float(row["close"])
            change_val = float(row["pct_chg"])
            # 镜像偶发返回 NaN 行：float(NaN) 不抛异常会让 success=True，
            # registry 不再降级、报告输出 nan%。显式返回 error 以触发 AkShare 兜底。
            if math.isnan(close_val) or math.isnan(change_val):
                return DataResult(
                    data=None,
                    source=self.name,
                    error=f"index_global 返回 NaN 行: {ts_code}",
                )
            # 数据交易日：美股跨周末/美国节假日时取到的是上一个美股交易日（如周一休市→上周五），
            # 带 as_of 让简报标注「截至 日期」，避免被误读为「日期不对」。脏值规范化为 None
            # （见 _normalize_trade_date），不让 nan/NaT/带时分秒透传成「截至 nan」。
            data = {
                "name": display_name,
                "close": close_val,
                "change_pct": change_val,
                "as_of": _normalize_trade_date(row.get("trade_date")),
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
            ts_code = self._normalize_stock_code(stock_code)
            df = self.pro.daily(ts_code=ts_code, start_date=d, end_date=d)
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无数据: {ts_code} {date}")
            daily_basic = self.pro.query("daily_basic", ts_code=ts_code, trade_date=d)
            row = df.iloc[0]
            turnover_rate = None
            if daily_basic is not None and not daily_basic.empty:
                basic_row = daily_basic.iloc[0]
                turnover_rate = basic_row.get("turnover_rate")
                if turnover_rate is None or (isinstance(turnover_rate, float) and math.isnan(turnover_rate)):
                    turnover_rate = basic_row.get("turnover_rate_f")
            data = {
                "code": ts_code,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "change_pct": float(row["pct_chg"]),
                "volume": float(row["vol"]),  # 手
                "amount_billion": float(row["amount"]) / 100000,  # 千元 -> 亿
                "turnover_rate": float(turnover_rate) if turnover_rate is not None else None,
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
            ts_code = self._normalize_stock_code(stock_code)
            df = self.pro.daily(ts_code=ts_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无历史数据: {ts_code}")
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

    def get_stock_adj_factor_range(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股区间复权因子。空=success 空列表；异常=error。"""
        try:
            ts_code = self._normalize_stock_code(stock_code)
            records = self._query_records(
                "adj_factor", ts_code=ts_code,
                start_date=self._date_fmt(start_date), end_date=self._date_fmt(end_date))
            return DataResult(data=records, source="tushare:adj_factor")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_holder_trade(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股区间股东增减持（stk_holdertrade）。空=success 空列表；异常=error。"""
        try:
            ts_code = self._normalize_stock_code(stock_code)
            records = self._query_records(
                "stk_holdertrade", ts_code=ts_code,
                start_date=self._date_fmt(start_date), end_date=self._date_fmt(end_date))
            out = [{"ann_date": r.get("ann_date"), "holder_name": r.get("holder_name"),
                    "holder_type": r.get("holder_type"), "in_de": r.get("in_de"),
                    "change_vol": r.get("change_vol")} for r in records]
            return DataResult(data=out, source="tushare:stk_holdertrade")
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
                    "industry": _to_clean_str(row.get("industry", "")),  # 与 akshare 降级口径对齐；字段缺失留空
                    "limit_stat": _to_clean_str(row.get("up_stat", "")),  # N天M板
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
                    "amount_billion": round(float(row["amount"]) / 1e5, 2),  # 千元 → 亿
                    "change_pct": round(float(row.get("pct_chg", 0) or 0), 2),
                })
            return DataResult(data=stocks, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_moneyflow_ths(self, date: str) -> DataResult:
        """获取同花顺行业资金流向，含领涨股和涨跌幅。"""
        try:
            d = self._date_fmt(date)
            fields = "trade_date,ts_code,industry,lead_stock,close,pct_change,company_num,pct_change_stock,close_price,net_buy_amount,net_sell_amount,net_amount"
            records = self._df_to_records(
                self.pro.query("moneyflow_ind_ths", trade_date=d, fields=fields)
            )
            for row in records:
                row["name"] = row.get("industry") or row.get("name") or ""
                row["net_amount_yi"] = round(float(row.get("net_amount") or 0), 2)
            return DataResult(data=records, source="tushare:moneyflow_ind_ths")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_concept_moneyflow_ths(self, date: str) -> DataResult:
        """获取同花顺概念板块资金流向。"""
        try:
            d = self._date_fmt(date)
            fields = "trade_date,ts_code,name,lead_stock,close_price,pct_change,industry_index,company_num,pct_change_stock,net_buy_amount,net_sell_amount,net_amount"
            records = self._df_to_records(
                self.pro.query("moneyflow_cnt_ths", trade_date=d, fields=fields)
            )
            for row in records:
                row["name"] = row.get("name") or ""
                row["net_amount_yi"] = round(float(row.get("net_amount") or 0), 2)
            return DataResult(data=records, source="tushare:moneyflow_cnt_ths")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_moneyflow_dc(self, date: str) -> DataResult:
        """获取东财行业板块资金流向（仅行业类型，排除概念和地域）。"""
        try:
            d = self._date_fmt(date)
            records = self._df_to_records(
                self.pro.query("moneyflow_ind_dc", trade_date=d, content_type="行业")
            )
            for row in records:
                row["name"] = row.get("name") or ""
                raw = float(row.get("net_amount") or 0)
                row["net_amount_yi"] = round(raw / 1e8, 2)
            return DataResult(data=records, source="tushare:moneyflow_ind_dc")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_concept_moneyflow_dc(self, date: str) -> DataResult:
        """获取东财概念板块资金流向。"""
        try:
            d = self._date_fmt(date)
            records = self._df_to_records(
                self.pro.query("moneyflow_ind_dc", trade_date=d, content_type="概念")
            )
            for row in records:
                row["name"] = row.get("name") or ""
                raw = float(row.get("net_amount") or 0)
                row["net_amount_yi"] = round(raw / 1e8, 2)
            return DataResult(data=records, source="tushare:moneyflow_ind_dc:concept")
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

    def _ensure_sw_member_map(self) -> dict:
        """惰性加载个股 → 申万一/二级映射 {ts_code: {name, sw_l1, sw_l2}}。

        index_member_all 默认单页 2000 行,**必须 offset/limit 分页拉满**,否则映射缺一半
        (实测全 A ~5847 只,3 页;参见 memory reference_tushare_index_member_all_pagination)。
        """
        if self._sw_member_map is None:
            result_map: dict = {}
            offset, limit = 0, 2000
            while True:
                df = self.pro.index_member_all(
                    is_new="Y",
                    fields="ts_code,name,l1_name,l2_name,l2_code,is_new",
                    offset=offset,
                    limit=limit,
                )
                records = self._df_to_records(df)  # df is None / empty → []
                if not records:
                    break
                for r in records:
                    code = _to_clean_str(r.get("ts_code"))
                    if not code:
                        continue
                    result_map[code] = {
                        "name": _to_clean_str(r.get("name")),
                        "sw_l1": _to_clean_str(r.get("l1_name")),  # additive：申万一级（研报覆盖行业聚合用）
                        "sw_l2": _to_clean_str(r.get("l2_name")),
                    }
                if len(records) < limit:
                    break
                offset += limit
            self._sw_member_map = result_map  # 仅成功构建后缓存
        return self._sw_member_map

    def get_stock_sw_industry_map(self) -> DataResult:
        """个股 → 申万二级行业映射(成交额集中度联动 get_sector_rankings 申万口径)。

        分页拉全 A 股 index_member_all → {ts_code: {name, sw_l2}}。缺成分(多为次新,
        如 688635.SH C长进 上市2天未归类)由上层 collector 三级降级:① 命中→申万二级+name
        ② 不在成分→name 退 get_stock_basic_batch、行业「未分类」③ 仍失败→name=""、「未分类」。

        失败语义:接口异常 → 返回 error DataResult(success=False),让上层能区分「申万源全挂」
        与「全部未分类」(申万成分无 akshare 降级源);失败不缓存,下次调用可重试。
        """
        missing = self._ensure_pro("get_stock_sw_industry_map")
        if missing is not None:
            return missing
        try:
            return DataResult(
                data=self._ensure_sw_member_map(),
                source="tushare:index_member_all",
            )
        except Exception as e:
            logger.warning(f"获取申万成分映射失败: {e}")
            return DataResult(
                data=None,
                source=self.name,
                error=f"index_member_all_failed: {e}",
            )

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

    @staticmethod
    def _margin_expected_exchanges(df) -> set[str]:
        """两融「应到交易所集合」：以沪深为下限不塌缩 ∪ 窗口内出现过的交易所。

        必须以沪深为下限——否则镜像整窗只返沪市时 expected 会塌缩成 {SSE}，把半额
        自我认证为「完整」（系统性降级测不出）。get_margin_data 与 get_margin_series
        共用此判定，确保交易所规则（如北交所纳入）变化时单点维护。
        """
        return {"SSE", "SZSE"} | {str(x) for x in df["exchange_id"].dropna().unique()}

    def get_margin_data(self, date: str, lookback_days: int = 15) -> DataResult:
        """
        沪深北三市融资融券每日汇总（pro.margin），余额单位为元，输出附带亿元字段。

        完整性保证：各交易所发布有时间差（最新日常只有沪市 SSE，深市 SZSE / 北交所 BSE
        滞后一天），单查 trade_date 会把"沪市单边"误当全市场汇总，导致总额腰斩。
        因此改为窗口区间查询：以窗口内出现过的交易所集合为"应到集合"（对北交所历史鲁棒），
        返回 ≤ 请求日 的最近一个完整交易日。窗口内无完整日则返回 error——半额总额绝不冒充
        全市场汇总（避免下游把残缺数据当权威总额渲染/落库）。
        trade_date 为返回数据的真实日期（非请求日期，可 < requested_date 表示回退到了更早的完整日）；
        requested_date 记录调用方所请求的日期，供消费方判定数据是否陈旧。
        """
        try:
            d_end = self._date_fmt(date)
            d_start = (
                datetime.strptime(d_end, "%Y%m%d") - timedelta(days=lookback_days)
            ).strftime("%Y%m%d")
            df = self.pro.margin(start_date=d_start, end_date=d_end)
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无融资融券汇总数据: {date}")

            df = df.copy()
            df["trade_date"] = df["trade_date"].astype(str)
            # 应到集合 = 法定必有的沪深 ∪ 窗口内实际出现过的交易所（北交所等按其上市后是否出现自动纳入）。
            # 必须以沪深为下限，不能纯靠窗口推导——否则镜像整窗只返沪市时 expected 会塌缩成 {SSE}，
            # 把半额自我认证为"完整"（系统性降级测不出）。
            expected = self._margin_expected_exchanges(df)
            dates_desc = sorted({td for td in df["trade_date"] if td <= d_end}, reverse=True)

            chosen = next(
                (
                    td
                    for td in dates_desc
                    if {str(x) for x in df.loc[df["trade_date"] == td, "exchange_id"]} == expected
                ),
                None,
            )
            if chosen is None:
                return DataResult(
                    data=None,
                    source=self.name,
                    error=f"无完整融资融券数据（窗口内各交易所覆盖不齐）: {date}",
                )

            sub = df[df["trade_date"] == chosen]
            exchanges: list[dict] = []
            total_rzye = total_rqye = total_rzrqye = 0.0
            for _, row in sub.iterrows():
                rzye = float(row.get("rzye", 0) or 0)
                rqye = float(row.get("rqye", 0) or 0)
                rzrqye = float(row.get("rzrqye", 0) or 0)
                total_rzye += rzye
                total_rqye += rqye
                total_rzrqye += rzrqye
                exchanges.append({
                    "exchange_id": str(row.get("exchange_id", "") or ""),
                    "rzye_yi": round(rzye / 1e8, 2),
                    "rqye_yi": round(rqye / 1e8, 2),
                    "rzrqye_yi": round(rzrqye / 1e8, 2),
                })

            data = {
                "trade_date": f"{chosen[:4]}-{chosen[4:6]}-{chosen[6:8]}",
                "requested_date": date,
                # 全市场口径（与 akshare 降级源的 "SSE+SZSE" 区分）；含 BSE 即为沪深北全量。
                "market_scope": "+".join(sorted(expected)),
                "exchanges": exchanges,
                "total_rzye_yi": round(total_rzye / 1e8, 2),
                "total_rqye_yi": round(total_rqye / 1e8, 2),
                "total_rzrqye_yi": round(total_rzrqye / 1e8, 2),
            }
            return DataResult(data=data, source="tushare:margin")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_margin_series(self, start_date: str, end_date: str) -> DataResult:
        """两融余额区间时间序列（供与指数的联动性相关分析）。

        与 get_margin_data 的单日快照不同，返回区间内**所有完整交易日**的升序序列。
        复用同一完整性逻辑：以「应到交易所集合」(沪深为下限 ∪ 窗口内出现过的交易所)
        判定每日是否完整，只保留应到集合齐全的日，剔除「仅沪市」等半额日防止污染序列。
        每日给三市合计 + 沪/深/北各自 rzrqye 合计（亿元），支持沪深各自对照。
        """
        try:
            d_start = self._date_fmt(start_date)
            d_end = self._date_fmt(end_date)
            df = self.pro.margin(start_date=d_start, end_date=d_end)
            if df is None or df.empty:
                return DataResult(
                    data=None, source=self.name,
                    error=f"无融资融券汇总数据: {start_date}~{end_date}",
                )

            df = df.copy()
            df["trade_date"] = df["trade_date"].astype(str)
            # 应到集合以沪深为下限不塌缩（同 get_margin_data：防整窗只返沪市时半额自证完整）。
            expected = self._margin_expected_exchanges(df)
            market_scope = "+".join(sorted(expected))

            series: list[dict] = []
            for td in sorted({t for t in df["trade_date"] if t <= d_end}):
                sub = df[df["trade_date"] == td]
                if {str(x) for x in sub["exchange_id"]} != expected:
                    continue  # 不完整日剔除，不进序列
                by_ex: dict[str, float] = {}
                total_rzye = total_rqye = total_rzrqye = 0.0
                for _, row in sub.iterrows():
                    ex = str(row.get("exchange_id", "") or "")
                    total_rzye += float(row.get("rzye", 0) or 0)
                    total_rqye += float(row.get("rqye", 0) or 0)
                    rzrqye = float(row.get("rzrqye", 0) or 0)
                    total_rzrqye += rzrqye
                    by_ex[ex] = rzrqye
                series.append({
                    "trade_date": f"{td[:4]}-{td[4:6]}-{td[6:8]}",
                    "total_rzye_yi": round(total_rzye / 1e8, 2),
                    "total_rqye_yi": round(total_rqye / 1e8, 2),
                    "total_rzrqye_yi": round(total_rzrqye / 1e8, 2),
                    "sse_rzrqye_yi": round(by_ex.get("SSE", 0.0) / 1e8, 2),
                    "szse_rzrqye_yi": round(by_ex.get("SZSE", 0.0) / 1e8, 2),
                    "bse_rzrqye_yi": round(by_ex.get("BSE", 0.0) / 1e8, 2),
                    "market_scope": market_scope,
                })

            if not series:
                return DataResult(
                    data=None, source=self.name,
                    error=f"无完整融资融券数据（窗口内各交易所覆盖不齐）: {start_date}~{end_date}",
                )
            return DataResult(data=series, source="tushare:margin")
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

    # ---- ETF 净申购（份额变动） ----

    # 重点 ETF（bare code → 名称）；后缀按交易所：5xxxxx→.SH，其余→.SZ
    _ETF_WATCHLIST: dict[str, str] = {
        "510300": "沪深300ETF(华泰)",
        "159919": "沪深300ETF(嘉实)",
        "588000": "科创50ETF",
        "512880": "证券ETF",
    }

    @staticmethod
    def _etf_ts_code(code: str) -> str:
        return f"{code}.SH" if code.startswith("5") else f"{code}.SZ"

    def get_etf_flow(self, date: str) -> DataResult:
        """重点 ETF 日度净申购：基于 tushare fund_share 的 fd_share(万份) 末两日差。

        fund_share.fd_share 单位为万份，/1e4 换算亿份；正=净申购、负=净赎回。
        相比 akshare fund_etf_fund_info_em（返回净值非份额，曾被误当份额导致恒 0），
        fund_share 是份额变动的正确数据源。

        语义说明（codex review M1 的处置）：取窗口内"最近两条份额记录之差"，而非强制
        等于请求日。理由：fund_share 按日披露且 ETF 极少停牌，相邻两条记录几乎总是相邻
        交易日，其差即日度净申购；而 fund_share 常有 T+1 披露滞后，若强制 last==请求日，
        正常滞后日 ETF 段会整体消失（体验更差）。因此采用"最近一次份额变动"语义，
        接受极端停牌缺口下差值可能跨多日的小概率偏差。"""
        d = self._date_fmt(date)
        start = (datetime.strptime(d, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d")

        results: list[dict] = []
        errors: list[str] = []
        for code, name in self._ETF_WATCHLIST.items():
            ts_code = self._etf_ts_code(code)
            try:
                df = self.pro.fund_share(ts_code=ts_code, start_date=start, end_date=d)
                if df is None or df.empty or len(df) < 2:
                    errors.append(f"{code} 份额历史不足两行")
                    continue
                df = df.sort_values("trade_date")
                last_share = float(df.iloc[-1]["fd_share"])
                prev_share = float(df.iloc[-2]["fd_share"])
                # 提主源后补健壮性守卫：fd_share 为 NaN/inf 时 float() 不抛异常但会污染输出，
                # 显式剔除非有限值，避免净申购渲染出 nan 亿份
                if not (math.isfinite(last_share) and math.isfinite(prev_share)):
                    errors.append(f"{code} 份额含非有限值，跳过")
                    continue
                results.append({
                    "code": code,
                    "name": name,
                    "total_shares_billion": round(last_share / 1e4, 4),
                    "shares_change_billion": round((last_share - prev_share) / 1e4, 4),
                })
            except Exception as e:
                errors.append(f"{code} 异常: {e}")
                logger.debug(f"ETF {code} fund_share 获取失败: {e}")

        if not results:
            return DataResult(
                data=None, source=self.name,
                error="; ".join(errors) or "无 ETF 份额数据",
            )
        return DataResult(
            data=results,
            source="tushare:fund_share",
            note="; ".join(errors) if errors else "",
        )

    # ---- 公告 ----

    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """获取个股公告（anns_d 接口，tushare.xyz 镜像可能不支持）"""
        try:
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            ts_code = self._normalize_stock_code(stock_code)
            df = self.pro.query("anns_d", ts_code=ts_code, start_date=sd, end_date=ed)
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

    # ---- 业绩预告 / 业绩快报 ----

    def get_earnings_forecast(self, date: str) -> DataResult:
        """业绩预告（forecast_vip 全市场，公告日回看窗口 [T-LOOKBACK+1, T]，净利单位=万元）。

        镜像实测（2026-06-12 STEP0）：`forecast` 必填 ts_code 不能全市场拉，必须走
        `forecast_vip`（支持 start_date/end_date 区间，区间 1920 行实测未截断）。
        同一公告可能返回 update_flag 0/1 双行（修正前后快照），raw 层全保留由消费端去重。
        """
        return self._query_earnings_window("forecast_vip", "get_earnings_forecast", date)

    def get_earnings_express(self, date: str) -> DataResult:
        """业绩快报（express_vip 全市场，窗口同 forecast；营收/净利单位=元）。"""
        return self._query_earnings_window("express_vip", "get_earnings_express", date)

    def _earnings_lookback_days(self) -> int:
        # config 优先、env 兜底（对齐 __init__ 的 token 取值模式）。
        # 0/负值/非整数视为非法回退默认：lookback 最小有意义取值是 1（仅当日），
        # 0 天窗口无业务语义，故 falsy-0 走 or 短路进 env/默认是预期行为而非陷阱。
        raw = self.config.get("earnings_lookback_days") or os.getenv("EARNINGS_LOOKBACK_DAYS", "")
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):  # TypeError 兜未来 config 显式 null 等非串值
            pass
        if raw not in ("", None):
            logger.warning("earnings_lookback_days 配置非法（%r），回退默认 %d 天",
                           raw, _EARNINGS_DEFAULT_LOOKBACK_DAYS)
        return _EARNINGS_DEFAULT_LOOKBACK_DAYS

    def _query_earnings_window(self, api_name: str, method_name: str, date: str) -> DataResult:
        guard = self._ensure_pro(method_name)
        if guard:
            return guard
        try:
            lookback = self._earnings_lookback_days()
            end_dt = datetime.strptime(self._date_fmt(date), "%Y%m%d")
            start_day = (end_dt - timedelta(days=lookback - 1)).strftime("%Y%m%d")
            end_day = end_dt.strftime("%Y%m%d")
            # 分页契约（codex review 修订）：不可假设服务器精确遵守 limit——若镜像
            # 真实单页 cap < limit，"短页即取完"会静默截断。故：① 空页是唯一可信的
            # 终止信号；② offset 按实际收到行数推进（兼容 cap<limit）；③ 连续两页
            # 首行相同 = offset 未生效，显式报错而非静默重复（区间 >4404 行的多页
            # 场景 2026-06-12 未实测，实测峰值 3 日窗 1920 行单页内）。
            records: list[dict] = []
            offset = 0
            prev_first_row: dict | None = None
            for _page in range(EARNINGS_MAX_PAGES):
                df = self.pro.query(
                    api_name,
                    start_date=start_day,
                    end_date=end_day,
                    offset=offset,
                    limit=EARNINGS_PAGE_LIMIT,
                )
                page = self._df_to_records(df)
                if not page:
                    break
                if prev_first_row is not None and page[0] == prev_first_row:
                    return DataResult(
                        data=None,
                        source=self.name,
                        error=f"{api_name} window=[{start_day},{end_day}] offset 未生效（连续两页首行相同），疑似镜像不支持分页",
                    )
                prev_first_row = page[0]
                records.extend(page)
                offset += len(page)
            else:
                return DataResult(
                    data=None,
                    source=self.name,
                    error=f"{api_name} window=[{start_day},{end_day}] 分页超过 {EARNINGS_MAX_PAGES} 页上限，疑似异常",
                )
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(
                data=records,
                source=f"tushare:{api_name}",
                note=f"ann_date_window=[{start_day},{end_day}]",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_analyst_forecasts(self, ts_code: str, start_date: str, end_date: str) -> DataResult:
        """个股券商盈利预测明细（report_rc，按报告日窗口）。

        镜像实测（2026-06-12 STEP0）：np 为**全年**归母净利预测（单位万元），
        quarter 形如 2026Q4；无中报预测——中报一致预期由消费端用 H1 占比折算。
        """
        guard = self._ensure_pro("get_analyst_forecasts")
        if guard:
            return guard
        try:
            records = self._query_records(
                "report_rc", ts_code=self._normalize_stock_code(ts_code),
                start_date=self._date_fmt(start_date), end_date=self._date_fmt(end_date),
            )
            return DataResult(data=records, source="tushare:report_rc")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_income_history(self, ts_code: str, start_date: str, end_date: str) -> DataResult:
        """个股历史利润表（income，按报告期窗口；归母净利 n_income_attr_p 单位元）。

        同一报告期可能返回多行（原始 vs 调整），消费端按 end_date + update_flag 去重。
        """
        guard = self._ensure_pro("get_income_history")
        if guard:
            return guard
        try:
            records = self._query_records(
                "income", ts_code=self._normalize_stock_code(ts_code),
                start_date=self._date_fmt(start_date), end_date=self._date_fmt(end_date),
                fields="ts_code,end_date,report_type,update_flag,n_income_attr_p",
            )
            return DataResult(data=records, source="tushare:income")
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

    def get_suspend_list(self, date: str) -> DataResult:
        """按交易日获取停牌记录（suspend_d，suspend_type=S）。"""
        try:
            d = self._date_fmt(date)
            records = self._query_records("suspend_d", trade_date=d, suspend_type="S")
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:suspend_d")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def _yyyymmdd_cell(self, val) -> str | None:
        if val is None:
            return None
        s = str(val).strip().replace("-", "").replace("/", "")[:8]
        return s if len(s) == 8 and s.isdigit() else None

    def _suspend_row_matches_day(self, row: dict, yyyymmdd: str) -> bool:
        for k in ("suspend_date", "trade_date"):
            if self._yyyymmdd_cell(row.get(k)) == yyyymmdd:
                return True
        return False

    def get_suspend_change_reasons(
        self,
        trade_date: str,
        ts_codes: list[str] | None = None,
    ) -> DataResult:
        """按停牌日拉取 suspend 的 change_reason。

        服务端通常要求 start_date < end_date，且单传 suspend_date 无 ts_code 时易报参数校验失败。
        策略：先按「交易日±窗口」批量拉取并过滤；对仍未命中的代码再按 ts_code+suspend_date 单查。
        """
        d = self._date_fmt(trade_date)
        try:
            dt = datetime.strptime(d, "%Y%m%d")
        except ValueError:
            return DataResult(data=None, source=self.name, error=f"invalid trade_date: {trade_date}")

        # 严格 start < end，覆盖目标日及前后若干自然日（停牌日记 suspend_date）
        lo = (dt - timedelta(days=10)).strftime("%Y%m%d")
        hi = (dt + timedelta(days=3)).strftime("%Y%m%d")
        merged: list[dict] = []

        def _row_has_text_reason(row: dict) -> bool:
            for k in ("change_reason", "suspend_reason", "reason"):
                v = row.get(k)
                if v is not None and str(v).strip():
                    return True
            return False

        def _add_filtered(rows: list[dict]) -> None:
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if not self._suspend_row_matches_day(item, d):
                    continue
                merged.append(item)

        try:
            bulk = self._query_records("suspend", start_date=lo, end_date=hi)
            _add_filtered(bulk)
        except Exception as e:
            logger.debug("suspend 批量(start/end)失败: %s", e)

        codes_done = {
            str(r.get("ts_code") or r.get("code") or "").strip().upper()
            for r in merged
            if _row_has_text_reason(r)
        }

        codes = [str(c or "").strip().upper() for c in (ts_codes or []) if str(c or "").strip()]
        missing_codes = [code for code in codes if code not in codes_done]
        try:
            fallback_limit = int(self.config.get("suspend_reason_single_fallback_limit", 20))
        except (TypeError, ValueError):
            fallback_limit = 20
        if fallback_limit <= 0 or len(missing_codes) > fallback_limit:
            logger.warning(
                "suspend 单票原因补查跳过：缺失 %s 只，超过上限 %s；仅使用批量结果",
                len(missing_codes),
                fallback_limit,
            )
            for item in merged:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=merged, source="tushare:suspend")

        for code in missing_codes:
            row_added = False
            for kwargs in (
                {"ts_code": code, "suspend_date": d},
                {
                    "ts_code": code,
                    "start_date": (dt - timedelta(days=1)).strftime("%Y%m%d"),
                    "end_date": (dt + timedelta(days=1)).strftime("%Y%m%d"),
                },
            ):
                try:
                    part = self._query_records("suspend", **kwargs)
                except Exception as ex:
                    logger.debug("suspend 单票 %s %s: %s", code, kwargs, ex)
                    continue
                before = len(merged)
                _add_filtered(part)
                new_rows = merged[before:]
                if new_rows and any(_row_has_text_reason(r) for r in new_rows):
                    row_added = True
                    codes_done.add(code)
                    break
            if not row_added:
                logger.debug("suspend 单票未取到与 %s 匹配的停牌原因: %s", d, code)

        for item in merged:
            item.setdefault("code", item.get("ts_code"))
        return DataResult(data=merged, source="tushare:suspend")

    def get_stk_alert(self, date: str) -> DataResult:
        """交易所重点提示证券（stk_alert，重点监控期起止；约 6000 积分）。"""
        try:
            d = self._date_fmt(date)
            try:
                df = self.pro.stk_alert(trade_date=d)
            except AttributeError:
                df = self.pro.query("stk_alert", trade_date=d)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:stk_alert")
            records = self._df_to_records(df)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:stk_alert")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stk_shock(self, date: str) -> DataResult:
        """交易所公布的个股异常波动（stk_shock，约 6000 积分）。"""
        try:
            d = self._date_fmt(date)
            df = self.pro.stk_shock(trade_date=d)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:stk_shock")
            records = self._df_to_records(df)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
                td = item.get("trade_date")
                if td is not None:
                    s = str(td).replace("-", "")[:8]
                    if len(s) == 8:
                        item["trade_date_norm"] = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            return DataResult(data=records, source="tushare:stk_shock")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_market_daily_quotes(self, date: str) -> DataResult:
        """全市场个股当日 OHLC + 昨收（业绩预告次日缺口验证用）。

        日志记返回行数：单次 pro.daily(trade_date=) 对全市场 A 股（~5400 只 < 单页
        ~6000 上限）通常一次返完，但若镜像截断/部分返回，下游缺口验证对缺行情候选静默
        跳过——行数落日志使截断可事后诊断（消费端再做地板校验，见 service）。
        """
        try:
            d = self._date_fmt(date)
            df = self.pro.daily(trade_date=d, fields="ts_code,open,high,low,close,pre_close")
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:daily")
            records = self._df_to_records(df)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            logger.info("[market_daily_quotes] %s 返回 %d 行", d, len(records))
            return DataResult(data=records, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_market_daily_changes(self, date: str) -> DataResult:
        """全市场个股当日涨跌幅。"""
        try:
            d = self._date_fmt(date)
            df = self.pro.daily(trade_date=d, fields="ts_code,name,pct_chg")
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:daily")
            records = self._df_to_records(df)
            for item in records:
                item.setdefault("code", item.get("ts_code"))
            return DataResult(data=records, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_daily_range(
        self, stock_code: str, start_date: str, end_date: str
    ) -> DataResult:
        """个股区间日线，含完整 OHLCV（open/high/low/close/pre_close/vol/amount）+ pct_chg。

        底层 Tushare daily 接口本就返回全列；趋势主升 scanner 的贴 MA5 / 缩量阴线 /
        远离 MA5 检测器需要 close 与 vol，故在此透传 OHLCV（旧消费方仅读 trade_date+pct_chg，向后兼容）。
        """
        try:
            ts_code = self._normalize_stock_code(stock_code)
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            records = self._query_records("daily", ts_code=ts_code, start_date=sd, end_date=ed)

            # 说明：pandas NaN 已由 _df_to_records → _clean_scalar 清成 None（见 _clean_scalar），
            # 故此处只需 float 容错，下游 detectors 的 `is None` 守卫兜底，无需再判 NaN。
            def _f(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            out = []
            for item in records:
                td = item.get("trade_date")
                if td is None:
                    continue
                s = str(td).replace("-", "")[:8]
                norm = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(td)
                out.append({
                    "trade_date": norm,
                    "ts_code": ts_code,
                    "open": _f(item.get("open")),
                    "high": _f(item.get("high")),
                    "low": _f(item.get("low")),
                    "close": _f(item.get("close")),
                    "pre_close": _f(item.get("pre_close")),
                    "vol": _f(item.get("vol")),
                    "amount": _f(item.get("amount")),
                    "pct_chg": _f(item.get("pct_chg")),
                })
            # Tushare daily 通常按 trade_date 倒序返回；统一归一为升序，
            # 满足下游趋势主升 detector「bars 升序、最后一根=今日」契约。
            out.sort(key=lambda r: r["trade_date"])
            return DataResult(data=out, source="tushare:daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_index_daily_range(
        self, index_ts_code: str, start_date: str, end_date: str
    ) -> DataResult:
        """指数区间日线 OHLCV。返回每行含 open/high/low/close/vol/amount/pct_chg/ts_code。
        单位沿用 tushare index_daily 原生：vol=手、amount=千元（消费方负责换算）。
        保留 pct_chg/ts_code 向后兼容 regulatory 偏离度消费方。"""
        try:
            code = str(index_ts_code or "").strip().upper()
            if not code:
                return DataResult(data=None, source=self.name, error="index_ts_code 为空")
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            df = self.pro.index_daily(ts_code=code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:index_daily")

            def _f(row, key):
                v = row.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            out = []
            for _, row in df.iterrows():
                td = row.get("trade_date")
                s = str(td).replace("-", "")[:8]
                norm = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(td)
                out.append({
                    "trade_date": norm,
                    "open": _f(row, "open"),
                    "high": _f(row, "high"),
                    "low": _f(row, "low"),
                    "close": _f(row, "close"),
                    "vol": _f(row, "vol"),
                    "amount": _f(row, "amount"),
                    "pct_chg": _f(row, "pct_chg"),
                    "ts_code": code,
                })
            # tushare index_daily 默认倒序（最新在前）；统一升序（最旧在前、最新在末尾），
            # 与检测器 bars[-1]=今日 契约对齐，避免消费方把最旧行当今日算出错误信号。
            # trade_date 为 YYYY-MM-DD，字符串序即时间序。regulatory 按 trade_date 建 dict 取值，
            # 与顺序无关，不受影响。
            out.sort(key=lambda r: r["trade_date"])
            return DataResult(data=out, source="tushare:index_daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_index_weekly(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        """获取指数周线数据（Tushare index_weekly）"""
        try:
            code_map = {
                "shanghai": "000001.SH",
                "shenzhen": "399001.SZ",
                "chinext": "399006.SZ",
                "star50": "000688.SH",
            }
            ts_code = code_map.get(index_code, index_code)
            sd = self._date_fmt(start_date)
            ed = self._date_fmt(end_date)
            df = self.pro.index_weekly(ts_code=ts_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=[], source="tushare:index_weekly")
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "trade_date": str(row["trade_date"]),
                    "close": float(row["close"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                })
            return DataResult(data=rows, source="tushare:index_weekly")
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
        missing = self._ensure_pro("get_stock_basic_list")
        if missing is not None:
            return missing
        try:
            df = self.pro.stock_basic(list_status="L")
            return DataResult(data=self._df_to_records(df), source="tushare:stock_basic")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_basic_batch(self, ts_codes: list[str]) -> DataResult:
        """按代码批量拉取简称（逗号分隔 ts_code，拆块避免超长）。"""
        if not ts_codes:
            return DataResult(data=[], source="tushare:stock_basic")
        missing = self._ensure_pro("get_stock_basic_batch")
        if missing is not None:
            return missing
        try:
            norm: list[str] = []
            seen: set[str] = set()
            for raw in ts_codes:
                c = str(raw or "").strip().upper()
                if not c or c in seen:
                    continue
                seen.add(c)
                norm.append(self._normalize_stock_code(c))
            if not norm:
                return DataResult(data=[], source="tushare:stock_basic")
            chunk_size = 50
            all_rows: list[dict] = []
            for i in range(0, len(norm), chunk_size):
                chunk = norm[i : i + chunk_size]
                df = self.pro.stock_basic(
                    ts_code=",".join(chunk),
                    fields="ts_code,name,symbol",
                )
                all_rows.extend(self._df_to_records(df))
            got = {
                str(r.get("ts_code") or "").strip().upper()
                for r in all_rows
                if isinstance(r, dict)
            }
            for c in norm:
                if c.upper() in got:
                    continue
                try:
                    one = self.pro.stock_basic(ts_code=c, fields="ts_code,name,symbol")
                    recs = self._df_to_records(one)
                    if recs:
                        all_rows.extend(recs)
                        got.add(c.upper())
                        continue
                    one_d = self.pro.stock_basic(
                        ts_code=c, list_status="D", fields="ts_code,name,symbol"
                    )
                    all_rows.extend(self._df_to_records(one_d))
                except Exception as ex:
                    logger.debug("stock_basic 单票补查 %s: %s", c, ex)
            return DataResult(data=all_rows, source="tushare:stock_basic")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))
