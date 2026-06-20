"""集中度日报 Markdown 渲染。

- 前3行业集中度(CR3)排除「未分类」(codex 中3:映射缺口不当真实板块);
- 「未分类」金额/只数单列,显式标注不计入;
- 行业覆盖率标注;占两市为近似(get_market_volume 指数口径,深证成指不全);
- 趋势不足 2 个交易日 → 兜底文案(dec-10),不渲染跨日分析块。
- v2 分析优先(全事实层,守红线,不给买卖建议/价格预测):
  · 头部摘要:Top20 合计/占两市 + 涨跌分布(红/绿/平+均值+最强弱);
  · CR3 行:环比(pp)+ 窗口分位 + 近期连升/连降;
  · 🔥 板块热度趋势:各行业占比 vs 前期(|delta|>1pp)升温/降温;
  · 头部资金:量级 vs 近期均值(放/缩量)+ 新陈代谢(核心/新晋)+ 今日新进资金流向行业;
  · 异动个股:今日新进(带行业+涨跌)/ 退出,替代逐只罗列 Top20;
  · 连续在榜:streak≥2、截断 Top8。
"""
from __future__ import annotations

from . import ranking
from .aggregator import UNCLASSIFIED

_RETENTION_TOP_N = 8  # 连续在榜最多展示只数(超出截断,避免移动端过长)
_GAIN_PERIOD_LABELS = [("5d", "5日"), ("10d", "10日"), ("20d", "20日")]
_GAIN_SECTOR_TOP_N = 8     # 每档区间涨幅排名展示板块数上限(移动端,超出截断)
_GAIN_OTHER_TOP_N = 2      # 领涨股之外额外展示的板块内个股数


def _change_distribution(stocks: list[dict]) -> dict | None:
    """Top N 当日涨跌分布:红/绿/平 计数 + 均值 + 最强/最弱(含 name)。无评级数据返 None。"""
    rated = [s for s in stocks if s.get("change_pct") is not None]
    if not rated:
        return None
    cps = [s["change_pct"] for s in rated]

    def _tag(s):
        cp = s["change_pct"]
        return f"{s.get('name') or s.get('code')} {'+' if cp > 0 else ''}{cp}%"

    return {
        "up": sum(1 for c in cps if c > 0),
        "down": sum(1 for c in cps if c < 0),
        "flat": sum(1 for c in cps if c == 0),
        "avg": round(sum(cps) / len(cps), 2),
        "top": _tag(max(rated, key=lambda s: s["change_pct"])),
        "bottom": _tag(min(rated, key=lambda s: s["change_pct"])),
    }


def _signed_pct(v: float) -> str:
    """带符号百分比(原始值用,非环比):正数加 +,负/零不加(与现有 vs_avg/环比口径一致)。"""
    return f"{'+' if v > 0 else ''}{v}%"


def _delta_label(v: float, unit: str) -> str:
    """环比变化标签:四舍五入后为 0 → 『持平』(避免『12.92% vs 12.89%(0.0pp)』的突兀);
    否则带符号 + 单位(unit 为 'pp' 或 '%')。"""
    if v == 0:
        return "持平"
    return f"{'+' if v > 0 else ''}{v}{unit}"


def _format_prev_compare(pc: dict) -> list[str]:
    """📅 环比前一交易日小块:头部成交额 / 占两市 / CR3 / 涨跌均值 的今日 vs 昨日。
    pc 由 trend.compute_trend 提供且已保证 not None;缺数据的子项(None)对应行略过。"""
    out = [f"### 📅 环比前一交易日({pc['prev_date'][5:]})"]  # YYYY-MM-DD → MM-DD

    ha = pc["head_amount"]
    paren = f"({_delta_label(ha['change_pct'], '%')})" if ha["change_pct"] is not None else ""
    out.append(f"- 头部成交额:{ha['current']} 亿 vs {ha['previous']} 亿{paren}")

    # 占两市 / 涨跌均值仅当两侧都有值才出对比行:本块是「环比」块,缺一侧无可比性;
    # 且当日值已在表头(占两市行 / 涨跌分布行)展示,省略此处不丢数据(审查高-2 反驳:非隐藏当日值)。
    ms = pc["market_share"]
    if ms["current"] is not None and ms["previous"] is not None:
        paren = f"({_delta_label(ms['delta_pp'], 'pp')})" if ms["delta_pp"] is not None else ""
        out.append(f"- 占两市:{ms['current']}% vs {ms['previous']}%{paren}")

    cr = pc["cr3"]
    out.append(f"- CR3:{cr['current']}% vs {cr['previous']}%({_delta_label(cr['delta_pp'], 'pp')})")

    ca = pc["change_avg"]
    if ca["current"] is not None and ca["previous"] is not None:
        out.append(f"- 涨跌均值:{_signed_pct(ca['current'])} vs {_signed_pct(ca['previous'])}")

    out.append("")
    return out


def _fmt_gain(v: float) -> str:
    """区间涨幅带符号百分比(已 None 过滤);正/零加 +,负不加。"""
    return f"{'+' if v >= 0 else ''}{v}%"


def _render_ranking(rankings: dict, title: str, group_word: str) -> list[str]:
    """通用区间涨幅榜渲染(申万板块 / 同花顺题材共用):5/10/20 三档,组按领涨股降序。
    全事实层(真实收盘价算得),守红线不出价位目标/不给买卖建议。
    某周期全 None 的组不展示;每档截断 Top_N 组,超出标注略。三档全空 → 不出空标题返 []。"""
    out: list[str] = [f"### {title}"]
    rendered = False
    for key, label in _GAIN_PERIOD_LABELS:
        groups = [s for s in rankings.get(key, []) if s.get("max_gain") is not None]
        if not groups:
            continue
        rendered = True
        out.append(f"**{label}**")
        for s in groups[:_GAIN_SECTOR_TOP_N]:
            stocks = s["stocks"]
            lead = stocks[0]
            line = f"- {s['industry']} · 领涨 {lead['name']} {_fmt_gain(lead['gain'])}"
            others = stocks[1:1 + _GAIN_OTHER_TOP_N]
            if others:
                line += "(其余 " + " / ".join(f"{x['name']} {_fmt_gain(x['gain'])}" for x in others) + ")"
            out.append(line)
        if len(groups) > _GAIN_SECTOR_TOP_N:
            out.append(f"> 等 {len(groups) - _GAIN_SECTOR_TOP_N} 个{group_word}略")
        out.append("")
    return out if rendered else []


def _format_gain_ranking(universe: list[dict]) -> list[str]:
    """📈 板块区间涨幅排名(成交额前50,申万二级)。无原始集返 []。"""
    if not universe:
        return []
    return _render_ranking(ranking.build_sector_gain_ranking(universe),
                           "📈 板块区间涨幅排名(成交额前50)", "板块")


def _format_concept_ranking(universe: list[dict]) -> list[str]:
    """📈 题材区间涨幅排名(成交额前50,同花顺概念,多标签)。无概念标签返 []。"""
    if not universe:
        return []
    return _render_ranking(ranking.build_concept_gain_ranking(universe),
                           "📈 题材区间涨幅排名(成交额前50 · 同花顺概念)", "题材")


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

    dist = _change_distribution(record.get("stocks") or [])
    if dist:
        line = f"- 涨跌分布:{dist['up']} 红 {dist['down']} 绿"
        if dist["flat"]:
            line += f" {dist['flat']} 平"
        line += f",均 {'+' if dist['avg'] > 0 else ''}{dist['avg']}%"
        if dist["up"] or dist["down"]:  # 全平时最强=最弱,无区分意义,省略(审查高-2)
            line += f";最强 {dist['top']} / 最弱 {dist['bottom']}"
        lines.append(line)
    lines.append("")

    sectors = [s for s in record["sector_summary"] if s["industry"] != UNCLASSIFIED]
    cr3 = round(sum(s["share_in_top_n"] for s in sectors[:3]) * 100, 1)

    # 集中度 CR3 摘要行:环比 + 窗口分位 + 近期连升/连降
    ct = trend_result.get("cr3_trend") or {}
    cr3_line = f"- 集中度 CR3 {cr3}%"
    if ct.get("previous") is not None:
        delta = ct["delta_pp"]
        cr3_line += f" · 环比 {'+' if delta > 0 else ''}{delta}pp · 近{ct['window']}日第{ct['rank']}高"
        dir_cn = {"up": "连升", "down": "连降"}.get(ct.get("streak_dir"))
        if dir_cn and ct.get("streak_days"):
            cr3_line += f" · {dir_cn}{ct['streak_days']}日"
    lines.append(cr3_line)
    lines.append("")

    # 板块集中度(今日快照):列表形式 —— 钉钉手机端不渲染 markdown 表格,故不用表格
    lines.append(f"### 板块集中度(前3行业 {cr3}%)")
    for s in sectors:
        lines.append(
            f"- {s['industry']} · {s['count']}只 · {s['amount_billion']}亿 · {round(s['share_in_top_n'] * 100, 1)}%"
        )
    unc = next((s for s in record["sector_summary"] if s["industry"] == UNCLASSIFIED), None)
    if unc:
        lines.append(
            f"> 未分类:{unc['count']}只 / {unc['amount_billion']}亿(次新等未归类,不计入板块集中度)"
        )
    lines.append("")

    # 📈 区间涨幅排名(成交额前50,独立于上方 Top20 集中度):申万板块榜 + 同花顺题材榜,恒展示
    _universe = record.get("gain_universe") or []
    lines.extend(_format_gain_ranking(_universe))
    lines.extend(_format_concept_ranking(_universe))

    # 不足 2 日:跨日趋势分析无意义,出兜底文案收尾
    if not trend_result.get("sufficient"):
        lines.append(f"### 趋势(近 {trend_result['days']} 交易日)")
        lines.append(
            f"> 集中度趋势:累积交易日仅 {trend_result['days']} 天,满 ≥2 日起逐步展示(目标 30+)。"
        )
        return "\n".join(lines)

    # 📅 环比前一交易日(独立对比小块):头部成交额 / 占两市 / CR3 / 涨跌均值 今日 vs 昨日
    pc = trend_result.get("prev_compare")
    if pc:
        lines.extend(_format_prev_compare(pc))

    # 🔥 板块热度趋势(占 Top20 比重 vs 前期):升温 / 降温(|delta|>1pp 才列,各取前 5)
    heat = trend_result.get("sector_heat") or []
    rising = [h for h in heat if h["delta_pp"] > 1][:5]
    falling = sorted((h for h in heat if h["delta_pp"] < -1), key=lambda x: x["delta_pp"])[:5]
    if rising or falling:
        lines.append("### 🔥 板块热度趋势(占 Top20 比重 vs 前期)")
        if rising:  # 🔴=升温(A股红=涨/积极)
            lines.append("- 🔴 升温:" + " / ".join(f"{h['industry']} +{h['delta_pp']}pp" for h in rising))
        if falling:  # 🟢=降温(A股绿=跌)
            lines.append("- 🟢 降温:" + " / ".join(f"{h['industry']} {h['delta_pp']}pp" for h in falling))
        lines.append("")

    # 头部资金:量级 vs 近期均值 + 新陈代谢 + 今日新进资金流向
    at = trend_result.get("amount_trend") or {}
    meta = trend_result.get("metabolism") or {}
    fund: list[str] = []
    if at.get("avg") is not None:
        vp = at.get("vs_avg_pct")
        if vp is None:
            vs = "—"
        else:  # 三态:>0 放量 / <0 缩量 / ==0 持平(0 不误报放量,codex 中等)
            label = "放量" if vp > 0 else ("缩量" if vp < 0 else "持平")
            vs = f"{'+' if vp > 0 else ''}{vp}%,{label}"
        fund.append(f"- 头部量级:{at['latest']} 亿,近期均值 {at['avg']} 亿({vs})")
    if meta.get("core") or meta.get("fresh"):
        # 「今日新进」=streak==1(今日在榜、昨日不在),含回归股,不写「首进」以免过度声称(codex 中等)
        fund.append(f"- 新陈代谢:核心(在榜≥10日){meta.get('core', 0)} 只 / 今日新进{meta.get('fresh', 0)} 只")
    if meta.get("new_by_sector"):
        fund.append("- 今日新进资金流向:" + "、".join(f"{ind}×{n}" for ind, n in meta["new_by_sector"]))
    if fund:
        lines.append("### 💰 头部资金")
        lines.extend(fund)
        lines.append("")

    # 异动个股(替代逐只罗列):今日新进(带行业+涨跌)/ 退出
    srot = trend_result.get("stock_rotation") or {}
    if srot.get("new") or srot.get("dropped"):
        lines.append("### 🔄 异动个股")
        if srot.get("new"):
            meta_by_code = {s.get("code"): s for s in (record.get("stocks") or [])}
            parts = []
            for x in srot["new"]:
                s = meta_by_code.get(x["code"], {})
                cp = s.get("change_pct")
                cp_str = f" {'+' if cp > 0 else ''}{cp}%" if cp is not None else ""
                detail = (s.get("industry", "") + cp_str).strip()
                name = x.get("name") or x["code"]
                parts.append(f"{name}({detail})" if detail else name)
            lines.append("- 今日新进:" + "、".join(parts))
        if srot.get("dropped"):
            lines.append("- 今日退出:" + "、".join(x.get("name") or x["code"] for x in srot["dropped"]))
        lines.append("")

    # 连续在榜(Top8)
    lines.append(f"### 📌 连续在榜(近 {trend_result['days']} 交易日)")
    held = [r for r in trend_result["stock_retention"] if r["streak"] >= 2]
    if held:
        shown = held[:_RETENTION_TOP_N]
        tail = f" 等 {len(held) - len(shown)} 只" if len(held) > _RETENTION_TOP_N else ""
        lines.append("- " + "、".join(f"{r['name'] or r['code']}({r['streak']}天)" for r in shown) + tail)
    else:
        lines.append("- 无(头部当日换手充分)")

    return "\n".join(lines)
