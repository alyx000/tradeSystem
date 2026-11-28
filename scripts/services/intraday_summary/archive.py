"""只读旧版/新版 Markdown 中保留的事实段；不从结论或对照段反推数据。"""
from __future__ import annotations

import re
from pathlib import Path


NUMBER = r"[+-]?\d+(?:\.\d+)?"


def parse_report(text: str, *, day: str, slot: str) -> dict:
    def one(pattern: str):
        matches = list(re.finditer(pattern, text, re.M))
        if len(matches) != 1:
            raise ValueError("报告事实段缺失、重复或格式不兼容")
        return matches[0]

    title = one(r"^### A股盘中半小时扫描｜(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})$")
    if title.groups() != (day, slot):
        raise ValueError("报告日期或时点不匹配")
    meta = one(r"^> 数据状态：\*\*(完整|部分)\*\*｜实时行情：([^｜]+)｜扫描 (\d+) 只$")
    interval = one(r"^- \[事实\] 区间：(\d{2}:\d{2}) → (\d{2}:\d{2})；上涨 (\d+) / 下跌 (\d+) / 平盘 (\d+)，上涨占比 (" + NUMBER + r")%$")
    if interval[2] != slot:
        raise ValueError("报告区间终点不匹配")
    from .schedule import SLOTS
    expected = next((item.previous_label for item in SLOTS if item.label == slot), None)
    if interval[1] != expected:
        raise ValueError("报告不是指定的半小时区间")
    movement = one(r"^- \[事实\] 个股半小时涨幅中位数 (" + NUMBER + r")%，≥\+1% (\d+) 只，≤-1% (\d+) 只$")
    amount = one(r"^- \[事实\] 区间成交额增量约 (" + NUMBER + r") 亿元（累计成交额差分，覆盖 (" + NUMBER + r")%）$")
    breadth = one(r"^- \[事实\] 当前相对昨收：上涨 (\d+) / 下跌 (\d+) / 平盘 (\d+)；大涨≥5% (\d+) 只，大跌≤-5% (\d+) 只$")
    scanned = int(meta[3])
    counts = [int(interval[i]) for i in (3, 4, 5)]
    daily_counts = [int(breadth[i]) for i in (1, 2, 3)]
    if not 0 < sum(counts) <= scanned or not 0 < sum(daily_counts) <= scanned:
        raise ValueError("报告样本数量不一致")
    if abs(counts[0] / sum(counts) * 100 - float(interval[6])) > 0.11:
        raise ValueError("报告上涨占比与样本不一致")
    if int(movement[2]) > counts[0] or int(movement[3]) > counts[1]:
        raise ValueError("报告区间尾部计数不一致")
    if int(breadth[4]) > daily_counts[0] or int(breadth[5]) > daily_counts[1]:
        raise ValueError("报告当日尾部计数不一致")
    sector_blocks = one(r"\*\*(?:走强居前（按成分股半小时涨幅中位数）|涨跌幅排名前五（按成分股半小时涨跌幅中位数）)\*\*\n([\s\S]*?)\*\*(?:走弱居前|涨跌幅排名后五)（同口径）\*\*\n([\s\S]*?)#### 个股波动居前")
    sectors = {}
    for key, block in zip(("strongest", "weakest"), sector_blocks.groups()):
        rows = re.findall(r"^- (.+?)：中位 (" + NUMBER + r")%，上涨占比 (" + NUMBER + r")%，区间成交额 (" + NUMBER + r"亿|—)(?:｜[^\n]*)?$", block, re.M)
        if len(rows) != 5 or len({row[0] for row in rows}) != 5:
            raise ValueError("行业前五事实段不完整")
        sectors[key] = [dict(name=name, median_pct=float(pct), up_ratio_pct=float(ratio), amount_yi=None if value == "—" else float(value[:-1])) for name, pct, ratio, value in rows]
    stocks = one(r"\*\*上行\*\*\n([\s\S]*?)\*\*下行\*\*\n([\s\S]*?)(?=\n> 口径：|\n#### 数据缺口)")
    stock_groups = {}
    for key, block in zip(("rising", "falling"), stocks.groups()):
        rows = re.findall(r"^- (.+?)（([^）]+)）：半小时 (" + NUMBER + r")%，当日 (" + NUMBER + r")%$", block, re.M)
        if len(rows) != 5:
            raise ValueError("个股前五事实段不完整")
        stock_groups[key] = [dict(name=name, code=code, interval_pct=float(pct), daily_pct=float(daily)) for name, code, pct, daily in rows]
    index_codes = {"上证指数": "000001.SH", "深证成指": "399001.SZ", "创业板指": "399006.SZ", "科创50": "000688.SH"}
    indices = []
    for name, code in index_codes.items():
        match = one(r"^- " + name + r"：半小时 (" + NUMBER + r")%，当日 (" + NUMBER + r")%$")
        indices.append(dict(code=code, interval_pct=float(match[1]), daily_pct=float(match[2])))
    # Markdown 已损失精度。显示值落在阈值上时不能重判原始方向。
    archived_headlines = re.findall(r"^\*\*\[判断·机械口径\] (.+)。\*\*$", text, re.M)
    displayed_threshold = (
        abs(float(movement[1])) == 0.15
        or float(interval[6]) in {40.0, 60.0}
        or abs(indices[0]["interval_pct"]) == 0.05
    )
    override = {}
    if displayed_threshold:
        override["headline_override"] = archived_headlines[0] if len(archived_headlines) == 1 else "归档显示值位于判断阈值附近，缺少唯一原判断，本次不重判方向"
    return dict(
        trade_date=day, slot=slot, previous_slot=interval[1], quote_time=meta[2], scanned=scanned,
        status="complete" if meta[1] == "完整" else "partial",
        current_breadth=dict(zip(("up", "down", "flat", "strong_5pct", "weak_5pct"), map(int, breadth.groups()))),
        interval=dict(status="complete" if meta[1] == "完整" else "partial", up=counts[0], down=counts[1], flat=counts[2],
                      up_ratio_pct=float(interval[6]), median_pct=float(movement[1]), up_1pct=int(movement[2]), down_1pct=int(movement[3]),
                      amount_yi=float(amount[1]), amount_coverage_pct=float(amount[2]), indices=indices, sectors=sectors, **stock_groups),
        **override,
    )


def read_previous_report(root: Path, *, day: str, slot: str) -> dict | None:
    try:
        return parse_report((root / day / f"{slot.replace(':', '')}.md").read_text(encoding="utf-8"), day=day, slot=slot)
    except (OSError, UnicodeError, ValueError):
        return None
