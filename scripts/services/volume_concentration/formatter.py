"""集中度日报 Markdown 渲染。

- 前3行业集中度(CR3)排除「未分类」(codex 中3:映射缺口不当真实板块);
- 「未分类」金额/只数单列,显式标注不计入;
- 行业覆盖率标注;占两市为近似(get_market_volume 指数口径,深证成指不全);
- 趋势不足 2 个交易日 → 兜底文案(dec-10);连续在榜仅展示 streak≥2。
"""
from __future__ import annotations

from .aggregator import UNCLASSIFIED


def format_daily_report(record: dict | None, trend_result: dict) -> str:
    if not record:
        return "当日无成交额数据,跳过。"

    top_n = record["top_n"]
    total = record["total_amount_billion"]
    lines: list[str] = [f"## 📊 成交额 Top{top_n} 板块集中度 · {record['date']}", ""]

    header = f"- Top{top_n} 合计成交额:{total} 亿"
    market_total = record.get("market_total_billion")
    if market_total:
        header += f" · 占两市 {round(total / market_total * 100, 2)}%(近似)"
    lines.append(header)

    coverage = (record.get("source") or {}).get("industry_coverage")
    if coverage is not None:
        lines.append(f"- 行业覆盖率:{round(coverage * 100, 1)}%")
    lines.append("")

    sectors = [s for s in record["sector_summary"] if s["industry"] != UNCLASSIFIED]
    cr3 = round(sum(s["share_in_top_n"] for s in sectors[:3]) * 100, 1)
    lines.append(f"### 板块集中度(前3行业 {cr3}%)")
    lines.append("| 行业 | 只数 | 成交额(亿) | 占Top20 |")
    lines.append("|---|---|---|---|")
    for s in sectors:
        lines.append(
            f"| {s['industry']} | {s['count']} | {s['amount_billion']} | {round(s['share_in_top_n'] * 100, 1)}% |"
        )
    unc = next((s for s in record["sector_summary"] if s["industry"] == UNCLASSIFIED), None)
    if unc:
        lines.append(
            f"> 未分类:{unc['count']} 只 / {unc['amount_billion']} 亿(次新等未归类,不计入板块集中度)"
        )
    lines.append("")

    stocks = record.get("stocks") or []
    if stocks:
        lines.append(f"### Top{top_n} 个股明细(成交额降序)")
        lines.append("| # | 名称(代码) | 行业 | 成交额(亿) | 涨跌 |")
        lines.append("|---|---|---|---|---|")
        # 按成交额降序重排,# 即成交额排名(与 provider 原始 rank 同序;重排仅防输入未排序)。
        # 全部用 .get 兜底:陈旧/异常 record 可能缺 code 或 amount_billion=None,不应让报告崩。
        for i, s in enumerate(sorted(stocks, key=lambda x: x.get("amount_billion") or 0, reverse=True), 1):
            code = s.get("code") or ""
            name = s.get("name") or ""
            label = f"{name}({code})" if name else code
            amount = s.get("amount_billion")
            amount_str = amount if amount is not None else "—"
            cp = s.get("change_pct")
            cp_str = f"{'+' if cp > 0 else ''}{cp}%" if cp is not None else "—"  # + 仅正涨幅,0 不带号
            lines.append(
                f"| {i} | {label} | {s.get('industry', '')} | {amount_str} | {cp_str} |"
            )
        lines.append("")

    lines.append(f"### 趋势(近 {trend_result['days']} 交易日)")
    if not trend_result["sufficient"]:
        lines.append(
            f"> 集中度趋势:累积交易日仅 {trend_result['days']} 天,满 ≥2 日起逐步展示(目标 30+)。"
        )
    else:
        rot = trend_result["sector_rotation"]
        lines.append(
            f"- 板块轮动:新进 {'、'.join(rot['new']) or '无'} / "
            f"退出 {'、'.join(rot['dropped']) or '无'} / 持续 {'、'.join(rot['持续']) or '无'}"
        )
        at = trend_result["amount_trend"]
        cp = at["change_pct"]
        cp_str = f"{'+' if cp >= 0 else ''}{cp}%" if cp is not None else "—"
        lines.append(f"- 头部量级:{at['latest']} 亿,环比 {cp_str}")
        held = [r for r in trend_result["stock_retention"] if r["streak"] >= 2]
        if held:
            lines.append(
                "- 连续在榜:" + "、".join(f"{r['name'] or r['code']}({r['streak']}天)" for r in held)
            )

    return "\n".join(lines)
