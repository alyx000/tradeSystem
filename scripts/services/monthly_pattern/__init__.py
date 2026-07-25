"""月线模式观察池的纯逻辑组件。

模块不访问网络、数据库或推送出口；调用方负责提供前复权日线与“末月已完成”的交易日历事实。
"""

from .models import DetectionResult, MonthlyBar, MonthlyIndicator, PoolStateResult

__all__ = [
    "DetectionResult",
    "MonthlyBar",
    "MonthlyIndicator",
    "PoolStateResult",
]
