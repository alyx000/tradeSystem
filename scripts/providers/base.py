"""
数据提供者基类
所有数据源（tushare, akshare, eastmoney等）都继承此基类
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DataType(Enum):
    """数据类型标注"""
    FACT = "[事实]"
    JUDGMENT = "[判断]"
    RUMOR = "[传闻]"
    OPINION = "[观点]"


class Confidence(Enum):
    """置信度等级"""
    HIGH = "★★★"
    MEDIUM = "★★☆"
    LOW = "★☆☆"


class Timeliness(Enum):
    """时效性标注"""
    REALTIME = "[实时]"
    RECENT = "[近期]"
    DELAYED = "[滞后]"
    HISTORICAL = "[历史]"


@dataclass
class DataResult:
    """标准化数据结果，所有 provider 返回此格式"""
    data: Any
    source: str  # 数据来源名称
    source_url: str = ""  # 来源URL（如有）
    data_type: DataType = DataType.FACT
    confidence: Confidence = Confidence.HIGH
    timeliness: Timeliness = Timeliness.REALTIME
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    note: str = ""
    error: str = ""  # 如果获取失败，记录错误信息

    @property
    def success(self) -> bool:
        return self.error == ""

    def to_dict(self) -> dict:
        result = {
            "data": self.data,
            "source": self.source,
            "data_type": self.data_type.value,
            "confidence": self.confidence.value,
            "timeliness": self.timeliness.value,
            "fetched_at": self.fetched_at,
        }
        if self.source_url:
            result["source_url"] = self.source_url
        if self.note:
            result["note"] = self.note
        if self.error:
            result["error"] = self.error
        return result


class DataProvider(ABC):
    """数据提供者基类"""

    name: str = "base"
    priority: int = 99

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._initialized = False

    @abstractmethod
    def initialize(self) -> bool:
        """初始化连接，返回是否成功"""
        ...

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """返回此 provider 支持的数据类型列表"""
        ...

    def supports(self, data_key: str) -> bool:
        return data_key in self.get_capabilities()

    # ---- 行情数据 ----
    def get_index_daily(self, index_code: str, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_market_volume(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_daily(self, stock_code: str, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_ma(self, stock_code: str, date: str, periods: list[int] | None = None) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_realtime_quotes(self, codes: list[str]) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_daily_basic(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_adj_factor(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_adj_factor_range(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股区间复权因子（board-break 打分序列前复权用）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 股东数据 ----
    def get_holder_trade(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股区间股东增减持（in_de: IN=增持 / DE=减持）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 涨跌停数据 ----
    def get_limit_up_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_limit_down_list(self, date: str) -> DataResult:
        """当日跌停清单。契约：data = dict{"count": int, "stocks": list[...]}。
        消费端按 dict 取 count（market_timing/scanner、collectors/market 等）；勿返裸 list。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_limit_prices(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_limit_step(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_limit_cpt_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 板块数据 ----
    def get_sector_rankings(self, date: str, sector_type: str = "industry") -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_sector_fund_flow(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_sector_moneyflow_ths(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_sector_moneyflow_dc(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_concept_moneyflow_ths(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_concept_moneyflow_dc(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_ths_index(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_ths_member(self, date: str, concept_names: list[str] | None = None) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_concept_memberships(self, ts_codes: list[str]) -> DataResult:
        """按股票代码反查当前同花顺概念归属。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_index_classify(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 市场宽度 ----
    def get_market_breadth(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_daily_info(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_market_moneyflow_dc(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 资金流向 ----
    def get_northbound(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_northbound_top_stocks(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_margin_data(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_margin_detail(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 龙虎榜 ----
    def get_dragon_tiger(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_block_trade(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 公告 ----
    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_market_announcements(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_disclosure_dates(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_earnings_forecast(self, date: str) -> DataResult:
        """业绩预告（全市场，按公告日回看窗口）"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_earnings_express(self, date: str) -> DataResult:
        """业绩快报（全市场，按公告日回看窗口）"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_share_float(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_st(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_suspend_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_suspend_change_reasons(
        self,
        trade_date: str,
        ts_codes: list[str] | None = None,
    ) -> DataResult:
        """Tushare suspend 表：按停牌日索引 change_reason（与 suspend_d 互补）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stk_shock(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stk_shock_range(self, start_date: str, end_date: str) -> DataResult:
        """交易所个股异常波动区间快照。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stk_alert(self, trade_date: str) -> DataResult:
        """交易所重点提示证券（stk_alert，与 App「重点监控」列表同源类数据）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stk_alert_range(self, start_date: str, end_date: str) -> DataResult:
        """交易所重点提示证券区间快照（按监控开始日期查询）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stk_high_shock_range(self, start_date: str, end_date: str) -> DataResult:
        """交易所严重异常波动区间快照。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_market_daily_changes(self, date: str) -> DataResult:
        """全市场个股当日涨跌幅（用于监管异动初筛降级路径）。
        契约：data = 逐股 list[{"ts_code","name","pct_chg"}]（非聚合 dict）；消费端自行聚合
        涨跌家数（market_timing/scanner._advance_decline）。date 键控、历史安全。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_daily_range(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股区间日线涨跌幅序列。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_index_daily_range(self, index_ts_code: str, start_date: str, end_date: str) -> DataResult:
        """指数区间日线涨跌幅序列。index_ts_code 如 000001.SH。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_index_weekly(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        """获取指数周线数据"""
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 外盘 ----
    def get_global_index(self, index_name: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_us_tickers_overnight(self, tickers: list[str]) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_commodity(self, name: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_forex(self, pair: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_fx_swap(self, pair: str, tenor: str = "1Y") -> DataResult:
        """获取指定货币对和期限的外汇掉期点。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 新闻 ----
    def get_market_news(self, date: str, limit: int = 10) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_news(self, stock_code: str, date: str, limit: int = 10) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 互动易 ----
    def get_investor_qa(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 研报 ----
    def get_research_reports(self, stock_code: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_research_report_list(self, date: str) -> DataResult:
        """获取全市场当日研报列表"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_us_rating_changes(self, tickers: list[str], date_window: tuple[str, str]) -> DataResult:
        """美股分析师评级变动（yfinance upgrades_downgrades，窗口/方向过滤）。

        date_window: (start, end)，每端可为 'YYYY-MM-DD' 字符串或 datetime.date（闭区间，美东日历）。
        """
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 宏观日历 ----
    def get_macro_calendar(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 宏观经济指标 ----
    def get_macro_indicators(self, date: str = "", periods: int = 6) -> DataResult:
        """宏观经济指标（PMI/CPI/M2/LPR/社融），各取最近 periods 期及同比走势。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_macro_calendar_range(self, from_date: str, to_date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 交易日历 ----
    def is_trade_day(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_trade_calendar(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_basic_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_basic_batch(self, ts_codes: list[str]) -> DataResult:
        """按 ts_code 列表批量查询 stock_basic（名称补全）。"""
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_stock_business_profiles(self, ts_codes: list[str]) -> DataResult:
        """按 ts_code 批量查询主营资料。

        成功时 ``data`` 为规范代码到逐票字典的映射；逐票统一包含 ``ts_code``、
        ``profile_status``（ok/missing/source_failed）、简介、主营、经营范围、产品列表、
        来源与错误字段。部分逐票失败不改变顶层成功语义；全部逐票均失败或
        Provider 整体不可用等批次级故障必须返回顶层 ``error`` 以允许注册表降级。
        """
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 成交额排名 ----
    def get_top_volume_stocks(self, date: str, top_n: int = 20) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- ETF 净申购 ----
    def get_etf_flow(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 港股指数 ----
    def get_hk_indices(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")
