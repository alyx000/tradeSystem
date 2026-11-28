"""盘中扫描默认精简报告和渠道摘要；明细保留在本地折叠区。"""
from __future__ import annotations

import re

from .formatter import _comparable, _headline, _pct, _stock_line, render_report


def _fold(title: str, content: str) -> str:
    return f'<details markdown="1">\n<summary>{title}</summary>\n\n{content.strip()}\n\n</details>\n'


def render_compact_preview(payload: dict, previous: dict | None, evidence: str) -> str:
    full = render_report(payload, previous)
    # 不完整报告直接保留原始可见缺口，不通过折叠隐藏降级原因。
    interval = payload.get("interval") or {}
    if (
        payload.get("status") != "complete" or interval.get("status") != "complete"
        or payload.get("source_gaps")
        or any(interval.get(key) for key in ("error", "index_error", "amount_error"))
    ):
        return full + "\n\n" + _fold("来源与生成记录", evidence)

    current = payload["interval"]
    comparable = _comparable(payload, previous)
    old = previous["interval"] if comparable else None
    headline = re.sub(r"；[^；]*区间(?:涨|跌)幅居前", "", _headline(payload))
    lines = [
        full.splitlines()[0], "", f"**[判断·机械口径] {headline}。**", "",
        f"> 数据状态：**完整**｜实时行情：{payload.get('quote_time') or '—'}｜扫描 {payload['scanned']} 只", "",
        "#### 核心变化", "",
        f"本期 {payload['previous_slot']}—{payload['slot']}"
        + (f" · 对照 {previous['previous_slot']}—{previous['slot']}" if comparable else " · 上一期完整对照缺失"), "",
        "| 指标 | 本期 | 较上一期 |", "| --- | ---: | ---: |",
    ]
    rows = [
        ("区间上涨占比", f"{current['up_ratio_pct']:.1f}%", f"{current['up_ratio_pct'] - old['up_ratio_pct']:+.1f}个百分点" if old else "未计算"),
        ("区间涨幅中位数", _pct(current['median_pct']), f"{current['median_pct'] - old['median_pct']:+.2f}个百分点" if old else "未计算"),
        ("区间跌幅≥1%", f"{current['down_1pct']}只", f"{current['down_1pct'] - old['down_1pct']:+d}只" if old else "未计算"),
        ("当日跌幅≥5%", f"{payload['current_breadth']['weak_5pct']}只", f"{payload['current_breadth']['weak_5pct'] - previous['current_breadth']['weak_5pct']:+d}只" if old else "未计算"),
    ]
    lines.extend(f"| {name} | {value} | {change} |" for name, value, change in rows)
    amount = current.get("amount_yi")
    lines += ["", f"区间成交额：**{amount:.1f}亿元**。" if amount is not None else "区间成交额：未计算。",
              "", "> 对照为两期报告汇总值，非固定样本；数量差不代表新增名单。", "", "#### 板块持续性", ""]
    sectors = current.get("sectors") or {}
    old_sectors = (old.get("sectors") or {}) if old else {}
    comparisons = {}
    ranks = (("strongest", "涨跌幅排名前五"), ("weakest", "涨跌幅排名后五"))
    for key, label in ranks:
        items = sectors.get(key) or []
        previous_items = old_sectors.get(key) or []
        names = {item["name"] for item in previous_items} if previous_items else None
        comparisons[key] = names
        if names is None or not items:
            lines.append(f"- {label}持续性：未计算。")
        else:
            continued = [item["name"] for item in items if item["name"] in names]
            lines.append(f"- **连续两期位列{label}：{'、'.join(continued) if continued else '无'}。**")
    lines += ["", "按行业成分股半小时涨跌幅中位数排名。", ""]
    for key, label in ranks:
        lines += [f"**{label}**", "", "| 行业 | 区间中位 | 上涨占比 | 榜单变化 |", "| --- | ---: | ---: | --- |"]
        for item in sectors.get(key) or []:
            names = comparisons[key]
            tag = "未计算" if names is None else ("连续两期" if item["name"] in names else "本期新入")
            lines.append(f"| {item['name']} | {_pct(item.get('median_pct'))} | {item['up_ratio_pct']:.1f}% | {tag} |")
        lines.append("")

    exits = []
    for key, label in ranks:
        current_names = {item["name"] for item in sectors.get(key) or []}
        names = comparisons[key]
        exited = [item["name"] for item in old_sectors.get(key, []) if item["name"] not in current_names]
        exits.append(f"- 退出{label}：" + (("、".join(exited) or "无") if names is not None else "未计算") + "。")
    lines.append(_fold("本期退出榜单", "\n".join(exits) + "\n\n退出不代表转强或转弱。"))

    # 完整事实段保留一次，支持现有归档解析器，首页不重复堆放指标。
    facts = full.split("#### 本期市场事实", 1)[1].split("#### 申万二级扩散", 1)[0]
    sector_detail = full.split("#### 申万二级扩散", 1)[1].split("#### 个股波动居前", 1)[0]
    stocks, method = full.split("#### 个股波动居前", 1)[1].split("> 口径：", 1)
    lines += [
        _fold("指数与完整市场明细", "#### 本期市场事实" + facts),
        _fold("行业成交额与完整榜单", "#### 申万二级扩散" + sector_detail),
        _fold("个股波动榜", "#### 个股波动居前" + stocks),
    ]
    notes = ["> 口径：" + method.strip()]
    if payload["slot"] == "15:00":
        notes.append("15:00档为实时快照，最终收盘数据尚未在本报告中另行核验。")
    if comparable:
        notes.append(f"两期扫描数：{previous['scanned']} / {payload['scanned']}；未核验逐股固定样本。")
    notes += ["当日指标均相对昨收；区间指标比较各自半小时，数量差不表示新进入名单。", evidence]
    lines.append(_fold("口径、来源与生成记录", "\n\n".join(notes)))
    return "\n".join(lines)


def render_channel_summary(report: str, *, interval: dict | None) -> str:
    """渠道发送精简正文和个股波动榜，表格转列表。"""
    body = report.split('<details markdown="1">', 1)[0].rstrip()
    lines = []
    headers = None
    for line in body.splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            if headers is None:
                headers = cells
                continue
            values = "｜".join(f"{name} {value}" for name, value in zip(headers[1:], cells[1:]))
            lines.append(f"- {cells[0]}：{values}")
        else:
            headers = None
            lines.append(line)
    # 部分数据的正文可能已含完整榜单，避免重复附加。
    if "\n#### 个股波动居前\n" not in body:
        lines.extend(["", "#### 个股波动榜", ""])
        if interval and interval.get("status") in {"complete", "partial"}:
            for key, label in (("rising", "上行"), ("falling", "下行")):
                items = interval.get(key) or []
                lines.extend([f"**{label}**", ""])
                lines.extend([_stock_line(item) for item in items] or ["- 未计算。"])
                lines.append("")
        else:
            lines.append("- 半小时波动未计算。")
    lines += ["", "> 完整明细与来源已保存在本地同日同槽位报告；本摘要不预测方向，不构成买卖建议。"]
    return "\n".join(lines)
