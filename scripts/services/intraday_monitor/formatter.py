"""盘中阈值告警的钉钉 Markdown。"""
from __future__ import annotations


def render_alert(events: list[dict]) -> str:
    lines = ["### ⚠️ 盘中阈值监控告警", ""]
    for event in events:
        lines.extend(
            [
                f"- [事实] **{event['instrument_name']}**（{event['code']}）"
                f"最新点位 **{event['price']:.2f}**，已{event['action_text']}"
                f"监控线 **{event['threshold']:.2f}**",
                f"  - 行情时间：{event['quote_at']}",
                f"  - 数据来源：{event['source']}",
            ]
        )
    lines.extend(["", "> 仅为条件触发提醒，不构成买卖建议。"])
    return "\n".join(lines)


def render_e2e_test_alert(event: dict, *, production_threshold: float, input_by: str) -> str:
    """渲染真实行情端到端测试消息；明确区分临时测试线与正式监控线。"""
    return "\n".join(
        [
            "### ✅ 【测试】盘中监控端到端验证",
            "",
            f"- [事实] **{event['instrument_name']}**（{event['code']}）"
            f"实时点位 **{event['price']:.2f}**",
            f"- [测试] 本次临时测试线 **{event['threshold']:.2f}**，"
            "仅用于验证真实行情 → 阈值判断 → 钉钉送达链路",
            f"- [事实] 正式监控线仍为 **{production_threshold:.2f}**，未修改正式规则或去重状态",
            f"- 行情时间：{event['quote_at']}",
            f"- 数据来源：{event['source']}",
            f"- 测试请求者：{input_by}",
            "",
            "> 这是一条链路测试消息，不是正式跌破告警，不构成买卖建议。",
        ]
    )
