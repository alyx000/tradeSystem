"""盘中半小时扫描 Markdown。"""
from __future__ import annotations

from .analytics import market_tone


INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.2f}%"


def _ratio(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def _sector_line(item: dict) -> str:
    amount = "—" if item.get("amount_yi") is None else f"{item['amount_yi']:.1f}亿"
    return (
        f"- {item['name']}：中位 {_pct(item.get('median_pct'))}，"
        f"上涨占比 {item.get('up_ratio_pct', 0):.1f}%，区间成交额 {amount}"
    )


def _stock_line(item: dict) -> str:
    return (
        f"- {item.get('name') or item['code']}（{item['code']}）："
        f"半小时 {_pct(item.get('interval_pct'))}，当日 {_pct(item.get('daily_pct'))}"
    )


def render_report(payload: dict) -> str:
    slot = payload["slot"]
    breadth = payload["current_breadth"]
    interval = payload.get("interval")
    status_label = "完整" if payload.get("status") == "complete" else "部分"
    lines = [
        f"### A股盘中半小时扫描｜{payload['trade_date']} {slot}",
        "",
        f"> 数据状态：**{status_label}**｜实时行情：{payload.get('quote_time') or '—'}｜扫描 {payload.get('scanned', 0)} 只",
        "",
    ]
    if interval and interval.get("status") in {"complete", "partial"}:
        amount_yi = interval.get("amount_yi")
        amount_text = (
            f"约 {amount_yi:.1f} 亿元"
            if amount_yi is not None
            else "未计算"
        )
        amount_coverage = interval.get("amount_coverage_pct")
        amount_coverage_text = "—" if amount_coverage is None else f"{amount_coverage:.1f}%"
        lines.extend([
            f"**[判断·机械口径] {market_tone(interval)}。**",
            "",
            f"- [事实] 区间：{payload.get('previous_slot')} → {slot}；上涨 {interval['up']} / 下跌 {interval['down']} / 平盘 {interval['flat']}，上涨占比 {interval['up_ratio_pct']:.1f}%",
            f"- [事实] 个股半小时涨幅中位数 {_pct(interval.get('median_pct'))}，≥+1% {interval['up_1pct']} 只，≤-1% {interval['down_1pct']} 只",
            f"- [事实] 区间成交额增量{amount_text}（累计成交额差分，覆盖 {amount_coverage_text}）",
            f"- [事实] 当前相对昨收：上涨 {breadth['up']} / 下跌 {breadth['down']} / 平盘 {breadth['flat']}；大涨≥5% {breadth['strong_5pct']} 只，大跌≤-5% {breadth['weak_5pct']} 只",
            "",
            "#### 宽基指数",
            "",
        ])
        indices = interval.get("indices") or []
        if indices:
            for item in indices:
                name = INDEX_NAMES.get(item["code"], item.get("name") or item["code"])
                lines.append(f"- {name}：半小时 {_pct(item.get('interval_pct'))}，当日 {_pct(item.get('daily_pct'))}")
        else:
            lines.append("- 指数实时行情缺失，未计算。")
        if interval.get("index_error"):
            lines.append(f"- [数据缺口] {interval['index_error']}")
        sectors = interval.get("sectors") or {}
        lines.extend(["", "#### 申万二级扩散", ""])
        if sectors:
            lines.append("**走强居前（按成分股半小时涨幅中位数）**")
            lines.extend(_sector_line(item) for item in sectors.get("strongest") or [])
            lines.extend(["", "**走弱居前（同口径）**"])
            lines.extend(_sector_line(item) for item in sectors.get("weakest") or [])
        else:
            lines.append(f"- 未计算：{interval.get('error') or '行业映射不可用'}")
        if interval.get("amount_error"):
            lines.extend(["", f"- [数据缺口] {interval['amount_error']}"])
        lines.extend(["", "#### 个股波动居前", "", "**上行**"])
        lines.extend(_stock_line(item) for item in interval.get("rising") or [])
        lines.extend(["", "**下行**"])
        lines.extend(_stock_line(item) for item in interval.get("falling") or [])
    else:
        gap = payload.get("gap") or (interval or {}).get("error") or "上一时点基线不可用"
        lines.extend([
            f"**⚠️ [数据缺口] 最近半小时变化未计算：{gap}。**",
            "",
            f"- [事实·当前快照] 相对昨收上涨 {breadth['up']} / 下跌 {breadth['down']} / 平盘 {breadth['flat']}，上涨占比 {_ratio(breadth.get('up_ratio_pct'))}",
            f"- [事实·当前快照] 涨跌幅中位数 {_pct(breadth.get('median_pct'))}；大涨≥5% {breadth['strong_5pct']} 只，大跌≤-5% {breadth['weak_5pct']} 只",
        ])
    gaps = payload.get("source_gaps") or []
    if gaps:
        lines.extend(["", "#### 数据缺口", ""])
        lines.extend(f"- {gap}" for gap in gaps)
    lines.extend([
        "",
        "> 口径：两次全市场实时快照做价格与累计成交额差分；机械判断仅用“中位涨幅±0.15% + 上涨占比60%/40%”归纳扩散，不预测后续方向，不构成买卖建议。",
    ])
    return "\n".join(lines)
