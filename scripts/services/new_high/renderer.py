from __future__ import annotations


def _pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _num(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def render_daily(record: dict, top_n: int = 10) -> str:
    date = record.get("date", "")
    lines = [
        f"# 前复权历史新高统计 · {date}  [事实]",
        "",
        "> 盘后只读统计 · 前复权口径 · 不构成买卖建议、不含价位目标、不写交易计划层。",
        "",
        "## 概览",
        f"- 当日有效行情股票数：{record.get('market_count', 0)}",
        f"- 创前复权历史新高：{record.get('new_high_count', 0)}",
        f"- 报告展示：每行业 Top {top_n}，完整明细见 JSON/数据库。",
        "",
    ]
    source = record.get("source") or {}
    if record.get("status") == "source_failed":
        lines += [
            "## 数据状态",
            f"- 数据源未返回有效行情：{source.get('failed_source') or 'unknown'}",
            f"- 来源：{source.get('quote_source') or source.get('adj_factor_source') or '—'}",
            f"- 错误：{source.get('error') or '空数据'}",
            "- 未生成正常新高统计。",
        ]
        return "\n".join(lines).rstrip() + "\n"

    if source:
        lines += [
            "## 数据状态",
            f"- 行情源：{source.get('quote_source') or '—'}",
            f"- 复权因子源：{source.get('adj_factor_source') or '—'}",
            f"- 行业源：{source.get('industry_source') or '—'}",
            f"- 复权因子缺失：{source.get('adj_factor_missing', 0)}",
            f"- 初始化水位股票：{source.get('initialized_count', 0)}",
            "",
        ]
    sectors = record.get("sector_summary") or []
    if not sectors:
        lines += ["## 板块分布", "当日无创前复权历史新高个股。"]
        return "\n".join(lines).rstrip() + "\n"

    lines.append("## 板块分布")
    for sector in sectors:
        stocks = sector.get("stocks") or []
        lines.append(f"### {sector.get('industry')} · {sector.get('count', 0)}只")
        lines.append("| 代码 | 名称 | 涨跌幅 | 当日最高 |")
        lines.append("|---|---|---:|---:|")
        for item in stocks[:top_n]:
            lines.append(
                f"| {item.get('code', '')} | {item.get('name', '')} | "
                f"{_pct(item.get('pct_chg'))} | {_num(item.get('raw_high'))} |"
            )
        remaining = max(0, len(stocks) - top_n)
        if remaining:
            lines.append(f"> 其余 {remaining} 只见 JSON/数据库。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
