"""MA 突破观察清单 Markdown 渲染。"""
from __future__ import annotations


_REDLINE = (
    "> 盘后只读观察清单 · 全部为 [判断] · "
    "不构成买卖建议、不含价位、不预测点位、不写交易计划层。"
)


def _amount_yi(value) -> str:
    try:
        return f"{float(value) / 1e5:.2f}亿"
    except (TypeError, ValueError):
        return "—"


def _pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def render_daily(summary: dict) -> str:
    date = summary.get("date", "")
    windows = summary.get("windows") or [5, 10]
    lines = [f"# 4日均线模式观察池 · {date}  [判断]", "", _REDLINE, ""]
    if summary.get("status") == "source_failed":
        lines += [
            "## 数据状态",
            f"- 行情源失败：{'、'.join(summary.get('source_errors') or [])}",
            "- 未生成正常观察清单。",
        ]
        return "\n".join(lines).rstrip() + "\n"

    lines += [
        "## 扫描概览",
        f"- 历史龙头窗口：近 {summary.get('leader_lookback_days') or '默认'} 自然日内复盘第 5 步人工确认的最票/龙头。",
        f"- 历史龙头宇宙：{summary.get('leader_universe_count', 0)}",
        f"- 条件：历史龙头池内，MA4 重新拐头向上（今日 MA4 上行，且上拐前至少两根 MA4 连续下行）；今日成交额同时大于 MA{windows[0]} / MA{windows[1]} 成交额均线；当日未涨停。",
        f"- 扫描股票：{summary.get('scanned_count', 0)}",
        f"- 命中股票：{summary.get('matched_count', 0)}",
        f"- 历史不足未评估：{summary.get('insufficient_count', 0)}",
        "",
    ]
    if summary.get("source_errors"):
        lines += ["## 运营提示", f"- 部分历史行情日期获取失败：{'、'.join(summary.get('source_errors') or [])}", ""]
    if summary.get("leader_unresolved_count"):
        if "## 运营提示" not in lines:
            lines += ["## 运营提示"]
        lines += [f"- {summary.get('leader_unresolved_count')} 条复盘最票未能解析为唯一股票代码，已从二波宇宙剔除。", ""]

    candidates = summary.get("candidates") or []
    if not candidates:
        lines += ["## 命中清单", "今日无命中。"]
        return "\n".join(lines).rstrip() + "\n"

    amount_headers = [f"MA{w}额" for w in windows]
    lines += [
        "## 命中清单",
        "| 代码 | 名称 | 申万二级 | 历史龙头依据 | 涨跌幅 | 今日成交额 | " + " | ".join(amount_headers) + " |",
        "| " + " | ".join(["---"] * (6 + len(windows))) + " |",
    ]
    for item in candidates:
        amount_mas = item.get("amount_mas") or {}
        ma_cells = " | ".join(_amount_yi(amount_mas.get(str(w))) for w in windows)
        leader_source = "/".join(item.get("former_leader_sources") or []) or "历史龙头"
        if item.get("former_leader_first_seen"):
            leader_source = f"{leader_source}({item.get('former_leader_first_seen')})"
        lines.append(
            f"| {item.get('code', '')} | {item.get('name', '')} | {item.get('sw_l2', '')} | {leader_source} | "
            f"{_pct(item.get('pct_chg'))} | {_amount_yi(item.get('today_amount'))} | "
            f"{ma_cells} |"
        )
    if summary.get("truncated"):
        lines += ["", f"> 命中较多，当前仅展示前 {len(candidates)} 只（按今日成交额降序）。"]
    return "\n".join(lines).rstrip() + "\n"
