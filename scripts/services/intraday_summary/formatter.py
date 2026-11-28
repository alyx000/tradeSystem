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


def _sector_line(item: dict, previous_names: set[str] | None = None) -> str:
    amount = "—" if item.get("amount_yi") is None else f"{item['amount_yi']:.1f}亿"
    label = ""
    if previous_names is not None:
        label = "｜连续两期上榜" if item["name"] in previous_names else "｜本期新上榜"
    return (
        f"- {item['name']}：中位 {_pct(item.get('median_pct'))}，"
        f"上涨占比 {item.get('up_ratio_pct', 0):.1f}%，区间成交额 {amount}{label}"
    )


def _stock_line(item: dict) -> str:
    return (
        f"- {item.get('name') or item['code']}（{item['code']}）："
        f"半小时 {_pct(item.get('interval_pct'))}，当日 {_pct(item.get('daily_pct'))}"
    )


def _headline(payload: dict) -> str:
    if payload.get("headline_override"):
        return payload["headline_override"]
    interval = payload.get("interval") or {}
    if interval.get("status") not in {"complete", "partial"}:
        return "半小时变化未计算，当前快照仅供查看"
    tone = market_tone(interval)
    median = interval.get("median_pct")
    up_ratio = interval.get("up_ratio_pct")
    shanghai = next((item for item in interval.get("indices", []) if item["code"] == "000001.SH"), {})
    index_pct = shanghai.get("interval_pct")
    if median is not None and up_ratio is not None and index_pct is not None:
        if median <= -0.15 and up_ratio <= 40 and index_pct >= -0.05:
            index_description = "上证基本持平" if index_pct <= 0.05 else "上证上涨"
            tone = f"多数个股走弱，{index_description}，指数与个股广度分化"
        elif median >= 0.15 and up_ratio >= 60 and index_pct <= 0.05:
            index_description = "上证基本持平" if index_pct >= -0.05 else "上证下跌"
            tone = f"多数个股走强，{index_description}，指数与个股广度分化"
    sectors = interval.get("sectors") or {}
    strong = [item["name"] for item in sectors.get("strongest", []) if item.get("median_pct", 0) > 0][:2]
    weak = [item["name"] for item in sectors.get("weakest", []) if item.get("median_pct", 0) < 0][:2]
    if strong:
        tone += f"；{'、'.join(strong)}区间涨幅居前"
    if weak:
        tone += f"；{'、'.join(weak)}区间跌幅居前"
    return tone


def _comparable(payload: dict, previous: dict | None) -> bool:
    return bool(
        previous and payload.get("status") == previous.get("status") == "complete"
        and previous.get("trade_date") == payload.get("trade_date")
        and previous.get("slot") == payload.get("previous_slot")
        and (payload.get("interval") or {}).get("status") == "complete"
        and (previous.get("interval") or {}).get("status") == "complete"
    )


def _comparison_lines(payload: dict, previous: dict | None) -> list[str]:
    lines = ["#### 上一期对照", ""]
    if not _comparable(payload, previous):
        return lines + ["- 未计算：缺少同日紧邻上一期的完整报告，或本期数据不完整；不跨缺期、午休拼接。", ""]
    current, old = payload["interval"], previous["interval"]
    lines += [
        f"区间指标：{previous['previous_slot']}—{previous['slot']} 对照 {payload['previous_slot']}—{payload['slot']}；当日指标：两个时点分别相对昨收。",
        "",
        "| 指标 | 上一期 | 本期 | 数值变化 |",
        "| --- | ---: | ---: | ---: |",
        f"| 区间上涨占比 | {old['up_ratio_pct']:.1f}% | {current['up_ratio_pct']:.1f}% | {current['up_ratio_pct'] - old['up_ratio_pct']:+.1f}个百分点 |",
        f"| 区间涨幅中位数 | {_pct(old['median_pct'])} | {_pct(current['median_pct'])} | {current['median_pct'] - old['median_pct']:+.2f}个百分点 |",
        f"| 区间跌幅≥1% | {old['down_1pct']}只 | {current['down_1pct']}只 | {current['down_1pct'] - old['down_1pct']:+d}只 |",
        f"| 当日跌幅≥5% | {previous['current_breadth']['weak_5pct']}只 | {payload['current_breadth']['weak_5pct']}只 | {payload['current_breadth']['weak_5pct'] - previous['current_breadth']['weak_5pct']:+d}只 |",
        "",
        f"> 两期扫描数：{previous['scanned']} / {payload['scanned']}。以上为报告汇总值对照；未核验逐股固定样本，数量差不代表新增股票名单。",
        "",
    ]
    return lines


def render_report(payload: dict, previous_report: dict | None = None) -> str:
    slot = payload["slot"]
    breadth = payload["current_breadth"]
    interval = payload.get("interval")
    status_label = "完整" if payload.get("status") == "complete" else "部分"
    lines = [
        f"### A股盘中半小时扫描｜{payload['trade_date']} {slot}",
        "",
        f"**[判断·机械口径] {_headline(payload)}。**",
        "",
        f"> 数据状态：**{status_label}**｜实时行情：{payload.get('quote_time') or '—'}｜扫描 {payload.get('scanned', 0)} 只",
        "",
    ]
    if slot == "15:00":
        lines.extend(["> 15:00档为实时快照，最终收盘数据尚未在本报告中另行核验。", ""])
    lines.extend(_comparison_lines(payload, previous_report))
    comparable = _comparable(payload, previous_report)
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
            "#### 本期市场事实",
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
            lines.extend(["> 按行业成分股半小时涨跌幅中位数排名，比较相邻两期的前五和后五；上榜不代表连续上涨/下跌。", ""])
            if not comparable:
                lines.extend(["- 板块持续性未计算：相邻两期完整报告不足。", ""])
            lines.append("**涨跌幅排名前五（按成分股半小时涨跌幅中位数）**")
            lines.append("")
            previous_sectors = (previous_report['interval'].get('sectors') or {}) if comparable else {}
            for key in ("strongest", "weakest"):
                if key == "weakest":
                    lines.extend(["", "**涨跌幅排名后五（同口径）**", ""])
                previous_items = previous_sectors.get(key)
                names = {item['name'] for item in previous_items} if previous_items else None
                current_items = sectors.get(key) or []
                lines.extend(_sector_line(item, names) for item in current_items)
                if names is not None:
                    exited = [item['name'] for item in previous_items if item['name'] not in {row['name'] for row in current_items}]
                    lines.append(f"\n退出本榜：{'、'.join(exited) if exited else '无'}。退出不代表转强或转弱。")
        else:
            lines.append(f"- 未计算：{interval.get('error') or '行业映射不可用'}")
        if interval.get("amount_error"):
            lines.extend(["", f"- [数据缺口] {interval['amount_error']}"])
        lines.extend(["", "#### 个股波动居前", "", "**上行**", ""])
        lines.extend(_stock_line(item) for item in interval.get("rising") or [])
        lines.extend(["", "**下行**", ""])
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
        "> 口径：两次全市场实时快照做价格与累计成交额差分；扩散判断使用“中位涨幅±0.15% + 上涨占比60%/40%”，上证对照使用±0.05%阈值；板块摘要只复述区间排名。均为阅读辅助规则，不预测后续方向，不构成买卖建议。",
    ])
    return "\n".join(lines)
