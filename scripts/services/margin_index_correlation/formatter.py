"""两融余额与指数联动性 渲染层（钉钉手机端 markdown）。

报告顺序（用户指定）：① 背离预警(头条) ② 余额水位+趋势 ③ 领先/滞后 ④ 同步相关。
全标 [判断]，守红线：不出价位、不给买卖建议、不写计划层；末尾脚注声明不构成投资建议。
"""
from __future__ import annotations


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _index_name_map(record: dict) -> dict:
    return {p["pair_key"]: p for p in record.get("indices", [])}


def _margin_label(p: dict) -> str:
    """pairing 的两融口径中文标签（cross 用 margin_label，broad 默认两融合计）。"""
    return p.get("margin_label", "两融合计" if p.get("margin_key") == "total" else p.get("margin_key", ""))


_UNEVALUATED = {"日期缺口", "样本不足", "无法评估"}


def _format_divergence(record: dict, pmap: dict) -> list[str]:
    """三态渲染：① 背离命中 ② 已评估无背离 ③ 未评估(日期缺口/样本不足/无法评估)。

    未评估状态绝不能渲染成「未见背离」——那是把数据缺口伪装成无风险（codex round2 #2）。
    """
    lines = ["## ⚠️ 一、两融×指数背离预警 [判断]"]
    hits: list[str] = []
    gaps: list[str] = []
    evaluated_no_div = 0
    for pair_key, by_win in record.get("divergence", {}).items():
        p = pmap.get(pair_key, {})
        name = p.get("index_name", pair_key)
        mlabel = _margin_label(p)
        for win, d in by_win.items():
            typ = d.get("type")
            if d.get("diverged"):
                hits.append(
                    f"- **{mlabel} × {name}**（近{win}日）：{typ} "
                    f"｜指数累计 {_fmt_pct(d.get('index_cum'))}、两融累计 {_fmt_pct(d.get('margin_cum'))} [判断]"
                )
            elif typ in _UNEVALUATED:
                gaps.append(f"{mlabel}×{name}(近{win}日){typ}")
            else:  # type == 无背离
                evaluated_no_div += 1
    if hits:
        lines += hits
    elif evaluated_no_div:
        lines.append("- 已评估口径近窗内**未见背离**，两融与指数方向一致 [判断]")
    elif not gaps:
        lines.append("- 暂无可评估的对照口径 [判断]")
    if gaps:
        lines.append(f"- ⚠️ 数据质量提示：以下窗口**未评估**（不等于无背离）：{'｜'.join(gaps)}")
    return lines


def _format_balance(record: dict) -> list[str]:
    lines = ["## 二、两融余额水位 + 趋势 [判断]"]
    label = {"total": "两融合计", "sse": "沪市两融", "szse": "深市两融"}
    for key in ("total", "sse", "szse"):
        b = record.get("balance", {}).get(key)
        if not b:
            continue
        streak = ""
        if b.get("up_streak", 0) >= 2:
            streak = f"、连增 {b['up_streak']} 日"
        elif b.get("down_streak", 0) >= 2:
            streak = f"、连降 {b['down_streak']} 日"
        pctile = b.get("pctile_20d")
        pctile_str = "—" if pctile is None else f"{pctile * 100:.0f}%"
        lines.append(
            f"- **{label[key]}** {b.get('latest_yi', '—')} 亿｜日环比 {_fmt_pct(b.get('dod_pct'))}"
            f"｜近20日分位 {pctile_str}｜偏离MA20 {_fmt_pct(b.get('vs_ma20'))}{streak} [判断]"
        )
    return lines


def _format_lag(record: dict, pmap: dict) -> list[str]:
    lines = ["## 三、两融对指数的领先/滞后 [判断]"]
    for pair_key, lag in record.get("lag", {}).items():
        p = pmap.get(pair_key, {})
        name = p.get("index_name", pair_key)
        mlabel = _margin_label(p)
        relation = lag.get("relation", "—")
        if lag.get("best_lag") is None:
            lines.append(f"- {mlabel} × {name}：{relation} [判断]")
        else:
            lines.append(
                f"- **{mlabel} × {name}**：{relation} {abs(lag['best_lag'])} 日"
                f"（best_corr {lag.get('best_corr')}） [判断]"
            )
    return lines


def _format_sync(record: dict, pmap: dict) -> list[str]:
    lines = ["## 四、同步相关系数（多窗） [判断]"]
    for pair_key, by_win in record.get("sync_corr", {}).items():
        p = pmap.get(pair_key, {})
        name = p.get("index_name", pair_key)
        mlabel = _margin_label(p)
        parts = []
        for win, c in by_win.items():
            corr = c.get("corr")
            parts.append(f"{win}日 {'—' if corr is None else corr}（{c.get('label', '')}）")
        lines.append(f"- **{mlabel} × {name}**：" + "｜".join(parts) + " [判断]")
    return lines


def format_daily_report(record: dict) -> str:
    pmap = _index_name_map(record)
    meta = record.get("meta", {})
    head = [f"# 两融×指数联动 · {record['date']}"]
    if meta.get("stale"):
        head.append(
            f"> ⏱ 两融数据为 **{record.get('data_trade_date')}**（非当日 T-1，交易所盘后发布滞后），"
            f"相关分析以该真实日为脊柱对齐。"
        )
    if meta.get("market_scope") and meta["market_scope"] != "BSE+SSE+SZSE":
        head.append(f"> ℹ️ 两融口径 {meta['market_scope']}（降级源，不含北交所，量级 <0.5%）。")

    blocks = [
        head,
        _format_divergence(record, pmap),
        _format_balance(record),
        _format_lag(record, pmap),
        _format_sync(record, pmap),
        ["---",
         "> 全部结论标 [判断]，基于两融余额日变化率与指数涨跌幅的统计联动，"
         "**不构成任何买卖建议、不预测价位**，仅供盘后观察参考。"],
    ]
    return "\n\n".join("\n".join(b) for b in blocks)


def format_signals(records: list[dict]) -> str:
    """只读回看：最近 N 日两融×指数联动摘要（背离命中 + 合计余额趋势）。"""
    if not records:
        return "近窗内无两融×指数联动快照。"
    lines = ["# 两融×指数联动 · 最近趋势 [判断]"]
    for r in records:
        pmap = _index_name_map(r)
        # 三态（与 daily 一致）：背离命中 / 已评估无背离 / 未评估——未评估不得伪装无背离（codex round3）。
        hits: list[str] = []
        gaps: list[str] = []
        evaluated_no_div = 0
        for pair_key, by_win in r.get("divergence", {}).items():
            name = pmap.get(pair_key, {}).get("index_name", pair_key)
            for win, d in by_win.items():
                typ = d.get("type")
                if d.get("diverged"):
                    hits.append(f"{name}({win}日){typ}")
                elif typ in _UNEVALUATED:
                    gaps.append(f"{name}({win}日){typ}")
                else:
                    evaluated_no_div += 1
        if hits:
            tag = "｜".join(hits)
        elif evaluated_no_div:
            tag = "无背离"
        else:
            tag = "—"
        if gaps:
            tag += f"｜⚠️未评估:{'｜'.join(gaps)}"
        total = r.get("balance", {}).get("total", {})
        lines.append(
            f"- **{r['date']}**：合计两融 {total.get('latest_yi', '—')} 亿"
            f"（日环比 {_fmt_pct(total.get('dod_pct'))}）｜{tag} [判断]"
        )
    lines.append("\n> [判断]，不构成买卖建议。")
    return "\n".join(lines)
