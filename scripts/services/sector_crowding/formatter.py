"""拥挤度 Markdown 渲染。命名与 volume-watch 严格区分：本报告=「全行业交易拥挤度」
（行业成交额÷全市场），volume-watch=「Top20 主线集中度」（Top20 内部占比）。"""
from __future__ import annotations

from .analyzer import ETF_JUMP_RATIO, SHARE_EXTREME_PCT, SHARE_WARN_PCT, SLOPE_PCTILE_WINDOW

PROXY_DISCLAIMER = "（资金流代理，非公募持仓真值）"
L2_TOP_N = 10

_SLOPE_KEY = f"gain_pctile_{SLOPE_PCTILE_WINDOW}d"


def _share_cell(s: dict) -> str:
    v = s.get("share_pct")
    if v is None:
        return "-"
    mark = " 🔴极值区" if v >= SHARE_EXTREME_PCT else (" ⚠高" if v >= SHARE_WARN_PCT else "")
    return f"{v:.1f}%{mark}"


def _pct(v) -> str:
    return f"{v:.0f}" if v is not None else "-"


def _sector_line(s: dict) -> str:
    gains = "/".join(_pct(s.get(f"gain_{n}d")) for n in (5, 20, 60))
    return (f"| {s.get('name')} | {_share_cell(s)} | {_pct(s.get('share_pctile'))} "
            f"| {gains} | {_pct(s.get(_SLOPE_KEY))} |")


_TABLE_HEADER = ["| 板块 | 占比 | 占比分位 | 涨幅5/20/60日% | 20日斜率分位 |",
                 "|---|---|---|---|---|"]


def format_report(view: dict) -> str:
    lines = [f"## 全行业交易拥挤度 · {view['date']}", ""]
    total = view.get("market_total_billion")
    lines.append(f"两市总成交额:{total:.0f} 亿" if total is not None
                 else "两市总成交额缺失,当日占比不可算")
    lines.append("")
    dh = view.get("double_high") or []
    lines.append(f"### 双高拥挤(交易分位≥90 且 {SLOPE_PCTILE_WINDOW}日斜率分位≥90)[判断]")
    if dh:
        lines += [f"- {s['name']}({s['code']}) 占比 {_share_cell(s)} / "
                  f"{SLOPE_PCTILE_WINDOW}日涨幅 {_pct(s.get(f'gain_{SLOPE_PCTILE_WINDOW}d'))}%"
                  for s in dh]
    else:
        lines.append("- 无双高拥挤板块")
    meta = view.get("meta") or {}
    l1 = _by_share(view["sectors"], "L1")
    l2 = _by_share(view["sectors"], "L2")[:L2_TOP_N]
    lines += ["", "### 申万一级(全量)"]
    if l1:
        lines += _TABLE_HEADER + [_sector_line(s) for s in l1]
        if meta.get("l1_status") == "synthesized":
            lines.append("> L1 由 L2 成交额归并合成(close 缺席,斜率维度不可用)")
    else:
        lines.append("L1 数据缺失(映射不可靠,禁止合成)")
    lines += ["", f"### 申万二级 TOP{L2_TOP_N}"]
    lines += _TABLE_HEADER + [_sector_line(s) for s in l2]
    lines += ["", f"### 资金流代理{PROXY_DISCLAIMER}"]
    lines += _proxy_lines(view.get("proxy"), view["date"])
    return "\n".join(lines)


def _by_share(sectors: list, level: str) -> list:
    return sorted((s for s in sectors if s.get("level") == level),
                  key=lambda s: -(s.get("share_pct") or 0))


def _proxy_lines(proxy, date: str) -> list[str]:
    if not proxy:
        return ["- 代理数据缺失"]
    lines = []
    mf = proxy.get("moneyflow") or []
    top = sorted(mf, key=lambda r: -r["net_amount_yi"])[:5]
    lines += [f"- {r['name']} 主力净流入 {r['net_amount_yi']:+.1f} 亿{PROXY_DISCLAIMER}"
              for r in top] or ["- 行业资金流缺失"]
    # ETF:get_etf_flow 现有 watchlist 直出;shares_change=最近两条披露记录之差(日度语义)
    for e in proxy.get("etf") or []:
        total, chg = e.get("total_shares_billion"), e.get("shares_change_billion")
        anomaly = (" ⚠份额跳变(疑拆分/异常,勿直读)"
                   if total and chg and abs(chg) > abs(total) * ETF_JUMP_RATIO else "")
        chg_txt = f"{chg:+.2f}" if chg is not None else "-"
        lines.append(f"- ETF {e.get('name')}({e.get('code')}) 份额变动 "
                     f"{chg_txt} 亿份{anomaly}{PROXY_DISCLAIMER}")
    m = proxy.get("margin")
    if m:
        stale = f"(数据日 {m['trade_date']})" if m.get("trade_date") != date else ""
        lines.append(f"- 全市场两融余额 {m['total_rzrqye_yi']:.0f} 亿{stale}{PROXY_DISCLAIMER}")
    if proxy.get("errors"):
        lines.append(f"- 代理缺口: {'; '.join(proxy['errors'])}")
    return lines


def format_trend(rows: list[dict], sector: str) -> str:
    data_lines = []
    for rec in rows:
        for s in rec.get("sectors") or []:
            if s.get("code") == sector or s.get("name") == sector:
                share = f"{s['share_pct']:.1f}%" if s.get("share_pct") is not None else "-"
                close = f"{s['close']:.1f}" if s.get("close") is not None else "-"
                data_lines.append(f"| {rec['date']} | {share} | {close} |")
    if not data_lines:
        return f"{sector} 无历史拥挤度数据。"
    return "\n".join([f"## 拥挤度趋势 · {sector}", "",
                      "| 日期 | 占比 | 收盘 |", "|---|---|---|"] + data_lines)
