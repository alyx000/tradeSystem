"""盘中监控规则定义与纯函数判定。"""
from __future__ import annotations

from dataclasses import dataclass
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

    def is_active(self, price: float) -> bool:
        if self.direction == "below":
            return price <= self.threshold if self.inclusive else price < self.threshold
        if self.direction == "above":
            return price >= self.threshold if self.inclusive else price > self.threshold
        raise ValueError(f"不支持的监控方向: {self.direction}")

    @property
    def action_text(self) -> str:
        if self.action_label:
            return self.action_label
        return "跌破" if self.direction == "below" else "突破"


DEFAULT_RULES: tuple[MonitorRule, ...] = (
    MonitorRule(
        rule_id="star50-below-1572",
        instrument_name="科创50",
        code="000688.SH",
        threshold=1572.0,
        direction="below",
        provider="sina",
    ),
    MonitorRule(
        rule_id="star50-reclaim-1582",
        instrument_name="科创50",
        code="000688.SH",
        threshold=1582.0,
        direction="above",
        provider="sina",
        inclusive=True,
        emit_on_initial_match=False,
        action_label="收复",
    ),
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
