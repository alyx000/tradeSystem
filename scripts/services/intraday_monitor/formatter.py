"""盘中阈值告警的钉钉 Markdown。"""
from __future__ import annotations


def _display_digits(event: dict, *, threshold_mode: str | None = None) -> int:
    """百分比严格边界保留计算精度，避免触发值与阈值显示成相等。"""
    if event.get("value_mode") == "daily_pct_change":
        return 8
    if threshold_mode == "previous_close_ma":
        return 3
    return 2


def render_alert(events: list[dict]) -> str:
    lines = ["### ⚠️ 盘中阈值监控告警", ""]
    for event in events:
        value_label = str(event.get("value_label") or "点位")
        value_unit = str(event.get("value_unit") or "")
        threshold_label = str(event.get("threshold_label") or "监控线")
        value_digits = _display_digits(event)
        threshold_digits = _display_digits(
            event,
            threshold_mode=str(event.get("threshold_mode") or "fixed"),
        )
        observed_value = float(event.get("value", event["price"]))
        lines.extend(
            [
                f"- [事实] **{event['instrument_name']}**（{event['code']}）"
                f"最新{value_label} **{observed_value:.{value_digits}f}**{value_unit}，"
                f"已{event['action_text']}{threshold_label} "
                f"**{event['threshold']:.{threshold_digits}f}**{value_unit}",
                f"  - 行情时间：{event['quote_at']}",
                f"  - 数据来源：{event['source']}",
            ]
        )
        if event.get("value_mode") == "daily_pct_change":
            lines.append(f"  - 最新点位：{event['price']:.3f}")
        if event.get("threshold_mode") == "daily_up_limit":
            if event.get("observation_phase") == "close":
                lines.append("  - [事实·收盘] 收盘价低于当日涨停价，按本监控口径确认为当日断板")
            else:
                lines.append(
                    "  - [判断·盘中] 当前未封涨停；盘中仍可能回封，最终是否断板以收盘为准"
                )
        elif event.get("threshold_mode") == "previous_close_ma":
            basis_dates = list(event.get("threshold_basis_dates") or [])
            if basis_dates:
                lines.append(
                    f"  - 均线样本：{basis_dates[0]} 至 {basis_dates[-1]}，"
                    f"共 {len(basis_dates)} 个已收盘交易日（前复权）"
                )
            if event.get("threshold_source"):
                lines.append(f"  - 均线数据来源：{event['threshold_source']}")
    lines.extend(["", "> 仅为条件触发提醒，不构成买卖建议。"])
    return "\n".join(lines)


def render_e2e_test_alert(
    event: dict,
    *,
    production_threshold: float,
    production_threshold_mode: str = "fixed",
    input_by: str,
) -> str:
    """渲染真实行情端到端测试消息；明确区分临时测试线与正式监控线。"""
    value_label = str(event.get("value_label") or "点位")
    value_unit = str(event.get("value_unit") or "")
    value_digits = _display_digits(event)
    production_digits = _display_digits(
        event,
        threshold_mode=production_threshold_mode,
    )
    observed_value = float(event.get("value", event["price"]))
    return "\n".join(
        [
            "### ✅ 【测试】盘中监控端到端验证",
            "",
            f"- [事实] **{event['instrument_name']}**（{event['code']}）"
            f"实时{value_label} **{observed_value:.{value_digits}f}**{value_unit}",
            *(
                [f"- [事实] 实时点位 **{event['price']:.3f}**"]
                if event.get("value_mode") == "daily_pct_change"
                else []
            ),
            f"- [测试] 本次临时测试线 "
            f"**{event['threshold']:.{value_digits}f}**{value_unit}，"
            "仅用于验证真实行情 → 阈值判断 → 钉钉送达链路",
            f"- [事实] 正式监控线仍为 "
            f"**{production_threshold:.{production_digits}f}**{value_unit}，"
            "未修改正式规则或去重状态",
            f"- 行情时间：{event['quote_at']}",
            f"- 数据来源：{event['source']}",
            f"- 测试请求者：{input_by}",
            "",
            "> 这是一条链路测试消息，不是正式监控告警，不构成买卖建议。",
        ]
    )
