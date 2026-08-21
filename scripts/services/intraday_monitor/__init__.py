"""可扩展的盘中实时阈值监控。"""

from .rules import (
    DEFAULT_RULES,
    KAILAIYING_BREAKOUT_172_26_20260821_24,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    MonitorRule,
    SSE_COMPOSITE_RECLAIM_3955,
    STAR50_BREAKOUT_1700_20260821_24,
)
from .service import run_check, run_e2e_test

__all__ = [
    "DEFAULT_RULES",
    "KAILAIYING_BREAKOUT_172_26_20260821_24",
    "LITONG_ELECTRONICS_BELOW_123_92_20260811",
    "MonitorRule",
    "SSE_COMPOSITE_RECLAIM_3955",
    "STAR50_BREAKOUT_1700_20260821_24",
    "run_check",
    "run_e2e_test",
]
