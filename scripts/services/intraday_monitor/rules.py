"""盘中监控规则定义与纯函数判定。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from utils.price_limit import compute_limit_prices


Direction = Literal["below", "above"]
ThresholdMode = Literal["fixed", "daily_up_limit"]


@dataclass(frozen=True)
class MonitorRule:
    """单个监控规则；新增标的时只需追加规则，不改编排逻辑。"""

    rule_id: str
    instrument_name: str
    code: str
    threshold: float | None
    direction: Direction = "below"
    provider: str = "sina"
    inclusive: bool = False
    emit_on_initial_match: bool = True
    action_label: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    value_label: str = "点位"
    value_unit: str = ""
    threshold_mode: ThresholdMode = "fixed"
    threshold_label: str = "监控线"

    def __post_init__(self) -> None:
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("监控规则 valid_from 不能晚于 valid_until")
        if self.threshold_mode == "fixed" and self.threshold is None:
            raise ValueError("固定阈值规则必须提供 threshold")
        if self.threshold_mode == "daily_up_limit" and self.threshold is not None:
            raise ValueError("每日涨停价规则不得同时提供固定 threshold")
        if self.threshold_mode not in {"fixed", "daily_up_limit"}:
            raise ValueError(f"不支持的阈值模式: {self.threshold_mode}")

    def resolve_threshold(self, quote: dict) -> float:
        """从固定配置或当日实时行情解析本次比较阈值。"""
        if self.threshold_mode == "fixed":
            return float(self.threshold)
        try:
            pre_close = float(quote.get("pre_close"))
        except (TypeError, ValueError):
            pre_close = None
        prices = compute_limit_prices(
            pre_close,
            self.code,
            name=str(quote.get("name") or self.instrument_name),
        )
        up_limit = prices.get("up_limit")
        if up_limit is None:
            raise ValueError("无法根据前收盘价计算当日涨停价")
        return float(up_limit)

    def is_active(self, price: float, *, threshold: float | None = None) -> bool:
        resolved = self.threshold if threshold is None else threshold
        if resolved is None:
            raise ValueError("动态阈值规则必须先解析本次阈值")
        if self.direction == "below":
            return price <= resolved if self.inclusive else price < resolved
        if self.direction == "above":
            return price >= resolved if self.inclusive else price > resolved
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


STAR50_BREAKOUT_1700_20260821_24 = MonitorRule(
    rule_id="star50-breakout-1700-20260821-24",
    instrument_name="科创50",
    code="000688.SH",
    threshold=1700.0,
    direction="above",
    inclusive=False,
    emit_on_initial_match=True,
    action_label="突破",
    valid_from=date(2026, 8, 21),
    valid_until=date(2026, 8, 24),
)


# 长期规则保留上证指数站上 3955；历史个股规则不再启用。
# 动态涨停价能力仍由 MonitorRule.threshold_mode 保留，供后续配置复用。
# 科创50 1700 临时规则覆盖 8 月 21 日与 24 日两个开放交易日；
# 周末自然日仍由只读交易日历门禁拦截，不请求行情。
DEFAULT_RULES: tuple[MonitorRule, ...] = (
    SSE_COMPOSITE_RECLAIM_3955,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    STAR50_BREAKOUT_1700_20260821_24,
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
