"""盘中监控规则定义与纯函数判定。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal

from utils.price_limit import compute_limit_prices


Direction = Literal["below", "above"]
ThresholdMode = Literal["fixed", "daily_up_limit", "previous_close_ma"]
ValueMode = Literal["price", "daily_pct_change"]


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
    threshold_window: int | None = None
    threshold_provider: str | None = None
    value_mode: ValueMode = "price"

    def __post_init__(self) -> None:
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("监控规则 valid_from 不能晚于 valid_until")
        if self.threshold_mode == "fixed":
            if self.threshold is None:
                raise ValueError("固定阈值规则必须提供 threshold")
        elif self.threshold_mode == "daily_up_limit":
            if self.threshold is not None:
                raise ValueError("每日涨停价规则不得同时提供固定 threshold")
        elif self.threshold_mode == "previous_close_ma":
            if self.threshold is not None:
                raise ValueError("前收盘均线规则不得同时提供固定 threshold")
            if (
                isinstance(self.threshold_window, bool)
                or not isinstance(self.threshold_window, int)
                or self.threshold_window <= 0
            ):
                raise ValueError("前收盘均线规则必须提供正整数 threshold_window")
            if not str(self.threshold_provider or "").strip():
                raise ValueError("前收盘均线规则必须提供 threshold_provider")
        else:
            raise ValueError(f"不支持的阈值模式: {self.threshold_mode}")
        if self.value_mode not in ("price", "daily_pct_change"):
            raise ValueError(f"不支持的比较值模式: {self.value_mode}")
        if self.value_mode == "daily_pct_change" and self.threshold_mode != "fixed":
            raise ValueError("单日涨跌幅比较值仅支持固定百分比阈值")
        if self.threshold is not None:
            value = float(self.threshold)
            if not math.isfinite(value):
                raise ValueError("固定阈值必须为有限数")
            if self.value_mode == "price" and value <= 0:
                raise ValueError("价格阈值必须为正数")

    def resolve_threshold(
        self,
        quote: dict,
        *,
        historical_closes: Iterable[float] | None = None,
    ) -> float:
        """从固定配置或当日实时行情解析本次比较阈值。"""
        if self.threshold_mode == "fixed":
            return float(self.threshold)
        if self.threshold_mode == "previous_close_ma":
            closes = list(historical_closes or [])
            if len(closes) != self.threshold_window:
                raise ValueError(
                    f"前收盘均线需要 {self.threshold_window} 个完整收盘价，实际 {len(closes)} 个"
                )
            values: list[float] = []
            for raw in closes:
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError("前收盘均线包含非法收盘价") from exc
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("前收盘均线包含非有限或非正收盘价")
                values.append(value)
            return sum(values) / len(values)
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

    def resolve_value(self, quote: dict) -> float:
        """解析规则实际比较值；涨跌幅统一由最新点位与昨收计算。"""
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError) as exc:
            raise ValueError("最新价非法") from exc
        if not math.isfinite(price) or price <= 0:
            raise ValueError("最新价非有限或非正数")
        if self.value_mode == "price":
            return price
        try:
            pre_close = float(quote.get("pre_close"))
        except (TypeError, ValueError) as exc:
            raise ValueError("实时前收盘价非法") from exc
        if not math.isfinite(pre_close) or pre_close <= 0:
            raise ValueError("实时前收盘价非有限或非正数")
        # 规范到 8 位小数，避免 96/100 在二进制浮点中变成
        # -4.0000000000000036，导致“恰好 -4%”被严格小于误判为触发。
        return round((price / pre_close - 1.0) * 100.0, 8)

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


KAILAIYING_BREAKOUT_172_26_20260821_24 = MonitorRule(
    rule_id="kailaiying-breakout-172-26-20260821-24",
    instrument_name="凯莱英",
    code="002821.SZ",
    threshold=172.26,
    direction="above",
    inclusive=False,
    emit_on_initial_match=True,
    action_label="突破",
    valid_from=date(2026, 8, 21),
    valid_until=date(2026, 8, 24),
    value_label="价格",
    value_unit="元",
)


GUOCI_MATERIALS_BELOW_67_22_20260831 = MonitorRule(
    rule_id="guoci-materials-below-67-22-20260831",
    instrument_name="国瓷材料",
    code="300285.SZ",
    threshold=67.22,
    direction="below",
    inclusive=False,
    emit_on_initial_match=True,
    valid_from=date(2026, 8, 31),
    valid_until=date(2026, 8, 31),
    value_label="价格",
    value_unit="元",
)


ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902 = MonitorRule(
    rule_id="zhongke-feice-below-previous-ma5-20260831-0902",
    instrument_name="中科飞测",
    code="688361.SH",
    threshold=None,
    direction="below",
    inclusive=False,
    emit_on_initial_match=True,
    valid_from=date(2026, 8, 31),
    valid_until=date(2026, 9, 2),
    value_label="价格",
    value_unit="元",
    threshold_mode="previous_close_ma",
    threshold_label="5日均线（前5个已收盘交易日）",
    threshold_window=5,
    threshold_provider="tushare",
)


THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT = MonitorRule(
    rule_id="ths-all-a-hushen-daily-drop-over-4pct",
    instrument_name="同花顺全A（沪深）",
    code="883421.THS",
    threshold=-4.0,
    direction="below",
    provider="tonghuashun",
    inclusive=False,
    emit_on_initial_match=True,
    action_label="跌破",
    value_label="单日涨跌幅",
    value_unit="%",
    threshold_label="单日涨跌幅监控线",
    value_mode="daily_pct_change",
)


# 长期规则保留上证指数站上 3955；历史个股规则不再启用。
# 动态涨停价与前收盘均线能力由 MonitorRule.threshold_mode 统一扩展。
# 科创50 1700 与凯莱英 172.26 临时规则覆盖 8 月 21 日与 24 日两个
# 开放交易日；周末自然日仍由只读交易日历门禁拦截，不请求行情。
DEFAULT_RULES: tuple[MonitorRule, ...] = (
    SSE_COMPOSITE_RECLAIM_3955,
    LITONG_ELECTRONICS_BELOW_123_92_20260811,
    STAR50_BREAKOUT_1700_20260821_24,
    KAILAIYING_BREAKOUT_172_26_20260821_24,
    GUOCI_MATERIALS_BELOW_67_22_20260831,
    ZHONGKE_FEICE_BELOW_PREVIOUS_MA5_20260831_0902,
    THS_ALL_A_HUSHEN_DAILY_DROP_OVER_4PCT,
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
