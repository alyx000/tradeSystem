"""只读、确定性的归档证据归纳；不调用模型、行情、数据库或外部源。

输入必须是 flash_raw.json.items 的完整窗口快照，而非 matched/selected。
规则只定位来源陈述，不能证明产业趋势成立或个股具有市场核心地位。
"""
from __future__ import annotations

import re

CATEGORIES = {
    "宏观政策/信号": ("央行", "美联储", "财政", "国债", "地方债", "降准", "降息",
                    "关税", "贸易", "CPI", "PPI", "非农", "国务院", "发改委", "政策",
                    "MLF", "LPR", "存款利率", "公开市场", "逆回购", "专项债", "赤字",
                    "化债", "投标利率", "商务部", "出口管制", "证监会", "金融监管总局"),
    "产业趋势": ("产业", "行业", "产能", "供需", "订单", "出货", "半导体", "芯片",
               "算力", "人工智能", "新能源", "光伏", "储能", "机器人", "医药"),
    "核心个股": (),
}
# 仅显式 A 股证券代码/交易所标记，不根据新闻关键词臆造关联公司或核心排名。
STOCK_CODE = re.compile(
    r"(?<![A-Za-z0-9])(?:[036]\d{5}\.(?:SH|SZ)|[489]\d{5}\.BJ)(?![A-Za-z0-9])"
    r"|(?:证券代码|股票代码)[：:\s]*[034689]\d{5}(?!\d)")


def build_summary(items: list, *, source_status: str, error=None) -> dict:
    categories = {}
    classified_ids = set()
    for name, words in CATEGORIES.items():
        evidence = []
        for item in items:
            data = item.get("data") or {}
            text = " ".join(str(data.get(k) or "") for k in ("title", "content"))
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
            hits = (STOCK_CODE.findall(text) if name == "核心个股"
                    else [word for word in words if word.casefold() in text.casefold()])
            if not hits:
                continue
            classified_ids.add(item["id"])
            evidence.append({"id": item["id"], "source_date": item["time"][:10],
                             "source": "金十快讯", "signals": hits,
                             "text": text})
        categories[name] = {
            "coverage": (source_status if source_status != "complete"
                         else "rule_matched" if evidence else "missing-data"),
            "error": error if source_status != "complete" else (
                None if evidence else "全量窗口归档无规则证据，不能推断无事件"),
            "count": len(evidence), "evidence": evidence,
        }
    return {
        "method": "archive_rules_v1", "source": "金十快讯",
        "source_dates": sorted({i["time"][:10] for i in items}),
        "input": "flash_raw.json.items", "input_count": len(items),
        "unclassified_count": sum(i["id"] not in classified_ids for i in items),
        "coverage": source_status, "coverage_scope": "archive_scan_only",
        "error": error, "categories": categories,
        "optional_sources": {name: {"source": name, "source_date": None,
                                    "coverage": "not_collected", "error": "本版未接入可选增强"}
                             for name in ("research-digest", "trend-leader")},
    }


def render_summary(summary: dict, *, compact: bool = False) -> str:
    lines = ["### 只读归纳（全量窗口归档）",
             f"> source={summary['source']} · 归档扫描 coverage={summary['coverage']}"
             f" · 扫描 {summary['input_count']} 条 · 未分类 {summary['unclassified_count']} 条",
             f"> source_date={', '.join(summary['source_dates']) or 'unknown'}（快讯发布日期）",
             f"> error={str(summary['error'] or 'none')[:300]}",
             "> 规则摘录仅为来源陈述；分类可重叠，不证明趋势成立或市场核心地位，不构成交易建议。"]
    for name, group in summary["categories"].items():
        evidence = group["evidence"]
        shown = evidence[:1] if compact else evidence
        lines.append(f"#### {name} · coverage={group['coverage']} · 证据 {len(evidence)} 条"
                     f" · 展示 {len(shown)} 条")
        if group["error"]:
            lines.append(f"> error={str(group['error'])[:300]}")
        for row in shown:
            # Markdown 归档也只摘录；原文完整保留在 flash_raw.json。
            text = row["text"][:160] + ("…" if len(row["text"]) > 160 else "")
            lines.append(f"- [来源陈述] {text}"
                         f"（source_date={row['source_date']}；source={row['source']}；id={row['id']}；"
                         f"规则命中={', '.join(row['signals'][:5])}）")
    lines.append("> 可选增强：research-digest / trend-leader coverage=not_collected（本版未接入）；缺口不补猜。")
    return "\n".join(lines) + "\n"
