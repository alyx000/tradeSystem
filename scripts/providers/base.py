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

    # ---- 涨跌停数据 ----
    def get_limit_up_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_limit_down_list(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 板块数据 ----
    def get_sector_rankings(self, date: str, sector_type: str = "industry") -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_sector_fund_flow(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 资金流向 ----
    def get_northbound(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_margin_data(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 龙虎榜 ----
    def get_dragon_tiger(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 公告 ----
    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
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

    # ---- 新闻 ----
    def get_market_news(self, date: str, limit: int = 10) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 宏观日历 ----
    def get_macro_calendar(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    def get_macro_calendar_range(self, from_date: str, to_date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")

    # ---- 交易日历 ----
    def is_trade_day(self, date: str) -> DataResult:
        return DataResult(data=None, source=self.name, error="not implemented")
