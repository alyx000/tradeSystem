"""月线模式纯逻辑层的数据模型。

这里只表达已完成月 K、指标快照和可解释检测结果；不承载交易指令、持久化或外部 I/O。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MonthlyBar:
    """由同一前复权口径的日 OHLCV 聚合而成的一根月 K。"""

    month: str
    end_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    is_complete: bool
    trading_days: int
    # 月末复权因子只足以统一 close/MA 口径。若相邻月末因子变化，说明本月内
    # 可能发生除权除息，raw monthly open/high/low 不能据此还原为可信形态。
    price_shape_valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MonthlyIndicator:
    """单月对应的均线、均量线与 MACD(12/26/9) 快照。"""

    month: str
    ma5: float | None
    ma10: float | None
    ma20: float | None
    volume_ma5: float | None
    volume_ma10: float | None
    macd_dif: float
    macd_dea: float
    macd_histogram: float
    macd_golden_cross: bool
    macd_zero_axis_golden_cross: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionResult:
    """模式检测结果；evidence 只存事实值、阈值和布尔门。"""

    pattern: str
    matched: bool
    status: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolStateResult:
    """完成月上的观察状态，不表示买卖建议。"""

    state: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
