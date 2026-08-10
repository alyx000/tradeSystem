"""盘中监控规则定义与纯函数判定。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


Direction = Literal["below", "above"]


@dataclass(frozen=True)
class MonitorRule:
    """单个监控规则；新增标的时只需追加规则，不改编排逻辑。"""

    rule_id: str
    instrument_name: str
    code: str
    threshold: float
    direction: Direction = "below"
    provider: str = "sina"
    inclusive: bool = False
    emit_on_initial_match: bool = True
    action_label: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    value_label: str = "点位"
    value_unit: str = ""

    def __post_init__(self) -> None:
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("监控规则 valid_from 不能晚于 valid_until")

    def is_active(self, price: float) -> bool:
        if self.direction == "below":
            return price <= self.threshold if self.inclusive else price < self.threshold
        if self.direction == "above":
            return price >= self.threshold if self.inclusive else price > self.threshold
        raise ValueError(f"不支持的监控方向: {self.direction}")

    def is_effective_on(self, trade_date: date) -> bool:
        """判断规则是否在给定上海自然日有效，日期边界均包含。"""
        if self.valid_from is not None and trade_date < self.valid_from:
            return False
        if self.valid_until is not None and trade_date > self.valid_until:
            return False
        return True

    @property
    def action_text(self) -> str:
        if self.action_label:
            return self.action_label
        return "跌破" if self.direction == "below" else "突破"


SSE_COMPOSITE_RECLAIM_3955 = MonitorRule(
    rule_id="sse-composite-reclaim-3955",
    instrument_name="上证指数",
    code="000001.SH",
    threshold=3955.0,
    direction="above",
    inclusive=True,
    emit_on_initial_match=False,
    action_label="站上",
)


LITONG_ELECTRONICS_BELOW_123_92_20260811 = MonitorRule(
    rule_id="litong-electronics-below-123-92-20260811",
    instrument_name="利通电子",
    code="603629.SH",
    threshold=123.92,
    direction="below",
    inclusive=False,
    emit_on_initial_match=True,
    valid_from=date(2026, 8, 11),
    valid_until=date(2026, 8, 11),
    value_label="价格",
    value_unit="元",
)


# 长期规则保留上证指数站上 3955；利通电子规则仅在 2026-08-11 生效。
# 已下线的科创50规则不在此恢复。
DEFAULT_RULES: tuple[MonitorRule, ...] = (
    SSE_COMPOSITE_RECLAIM_3955,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
)


def should_emit(
    *,
    previous_active: bool | None,
    current_active: bool,
    emit_on_initial_match: bool = True,
) -> bool:
    """按规则的首次命中策略告警；持续命中不重复。"""
    if not current_active:
        return False
    if previous_active is None:
        return emit_on_initial_match
    return previous_active is False
