"""渲染层：五段优先级 markdown；空窗口日（无新增且无缺口命中）返回 None 不推送。

段序（信息质量优先）：
① 持仓/关注命中明细（命中不筛宁多勿漏）
② 预告次日缺口验证（市场投票 2×2，着重段）
③ 申万行业聚合 Top5
④ 全市场分类计数
⑤ 过滤后 Top 榜（净利中值阈值防低基数小票污染；forecast/express 分开不混榜）

红线自查：全部为事实层呈现（公告内容/价格反应/计数），不附买卖建议、不预测价格。
"""
from __future__ import annotations

import os
from pathlib import Path

from .collector import position_hit
from .normalize import is_top_up_candidate

DEFAULT_TOP_N = 10
DEFAULT_MIN_PROFIT_WAN = 5000.0  # Top 榜净利中值阈值（万元，用户确认 ≥5000 万）
GAP_DISPLAY_CAP = 30  # ② 段显示上限（年报高峰实测 68 条会刷屏；已按 |缺口| 降序，截断尾注计数）

# 锚定仓库根（对齐 db/connection.py 的 __file__ 锚定）：Makefile 入口 cwd=scripts/
# 而 launchd 入口 cwd=仓库根，相对路径会落到两个不同目录（scripts/data 历史遗留
# 即 research-digest 同形缺陷的产物）
_REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = _REPO_ROOT / "data" / "reports" / "earnings-digest"

_TYPE_BADGES = {
    "预增": "📈预增", "扭亏": "🔄扭亏", "续盈": "✅续盈", "略增": "↗略增",
    "预减": "📉预减", "首亏": "⚠️首亏", "续亏": "🔻续亏", "略减": "↘略减",
}


def _fmt_wan(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f}亿"
    return f"{value:.0f}万"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _fmt_range(lo: float | None, hi: float | None, fmt) -> str:
    if lo is None and hi is None:
        return "—"
    if lo == hi or hi is None:
        return fmt(lo)
    if lo is None:
        return fmt(hi)
    return f"{fmt(lo)}~{fmt(hi)}"


def _consensus_suffix(item: dict, consensus_labels: dict[str, dict] | None) -> str:
    """口径三附加列：vs 一致预期 [判断·H1占比折算]（仅有数据时）。"""
    if not consensus_labels:
        return ""
    info = consensus_labels.get(item["ts_code"])
    if not info:
        return ""
    return f" ｜ vs一致预期: {info['label']} [判断·H1占比折算]"


def _mmdd(digits: str) -> str:
    """YYYYMMDD → MM-DD（标题公告日窗用）；非 8 位原样返回。"""
    return f"{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else digits


def _code_name(item: dict) -> str:
    """代码 + 名称（名称由 service 从申万成分 map 注入；缺失则只显代码）。"""
    name = (item.get("name") or "").strip()
    return f"{item['ts_code']} {name}" if name else item["ts_code"]


def _forecast_line(item: dict, hit: str | None = None,
                   consensus_labels: dict[str, dict] | None = None) -> str:
    badge = _TYPE_BADGES.get(item["type"], item["type"])
    parts = [f"**{_code_name(item)}** {badge}"]
    if hit:
        parts.append(f"〔{hit}〕")
    if item.get("is_revision"):
        parts.append("🔁修正")
    parts.append(f"净利 { _fmt_range(item['net_profit_min_wan'], item['net_profit_max_wan'], _fmt_wan) }")
    parts.append(f"同比 { _fmt_range(item['p_change_min'], item['p_change_max'], _fmt_pct) }")
    if item.get("growth_trend"):
        parts.append(f"增速{item['growth_trend']}")
    line = " ".join(parts) + _consensus_suffix(item, consensus_labels)
    reason = item.get("change_reason")
    if reason:
        line += f"\n  - 原因：{reason[:80]}"
    return f"- {line}"


def _express_line(item: dict, hit: str | None = None) -> str:
    parts = [f"**{_code_name(item)}** 快报"]
    if hit:
        parts.append(f"〔{hit}〕")
    parts.append(f"净利 {_fmt_wan(item['n_income_wan'])}")
    parts.append(f"归母同比 {_fmt_pct(item['yoy_dedu_np'])}")
    if item.get("vs_forecast"):
        parts.append(f"｜{item['vs_forecast']}")
    if not item.get("is_audit"):
        parts.append("（未审计）")
    return f"- {' '.join(parts)}"


def _gap_line(item: dict, hit: str | None = None) -> str:
    badge = _TYPE_BADGES.get(item["type"], item["type"])
    parts = [f"**{_code_name(item)}** {item['vote_label']}"]
    if hit:
        parts.append(f"〔{hit}〕")
    parts.append(f"{badge}后跳空 {item['gap_pct']:+.1f}%")
    if item.get("one_word_board"):
        parts.append("一字板")
    elif item.get("strict_gap"):
        parts.append("严格缺口")
    return f"- {' '.join(parts)}"


def render_digest(
    *,
    target_date: str,
    forecast_items: list[dict],
    express_items: list[dict],
    gap_hits: list[dict],
    position_codes: dict[str, set[str]],
    industry_map: dict[str, dict] | None = None,
    top_n: int = DEFAULT_TOP_N,
    min_profit_wan: float = DEFAULT_MIN_PROFIT_WAN,
    gap_display_cap: int = GAP_DISPLAY_CAP,
    gap_error: str | None = None,
    gap_error_pushable: bool = True,
    gap_note: str | None = None,
    consensus_labels: dict[str, dict] | None = None,
) -> tuple[str, str] | None:
    """渲染 (title, markdown)；无新增公告且无缺口命中（且无缺口故障警示）→ None。

    :param gap_error: 交易日行情获取失败且存在缺口候选时的错误信息——必须可见
        （静默缺席会让故障与非交易日同貌），渲染为 ② 段警示行。
    :param gap_error_pushable: gap_error 能否**单独**触发推送。False=该故障告警同日已推过，
        不再单独触发推送，但若 digest 因其它内容推送则仍展示该告警（避免新公告速报丢失
        故障提示，codex round2）。仅影响「是否因 gap_error 而非空」，不影响展示。
    :param gap_note: 行情完整性提示（疑似截断/部分返回时缺口命中可能不完整），有命中时
        渲染为 ② 段提示行；与 gap_error（无数据）互斥。
    :param consensus_labels: {ts_code: 一致预期判定}，附加到 ①⑤ 的预告行（口径三 [判断]）。
    """
    if (not forecast_items and not express_items and not gap_hits
            and not (gap_error and gap_error_pushable)):
        return None

    # 标题附「公告日」窗：取本期新公告（forecast+express）的真实 ann_date 跨度，
    # 让标题日期(target/截至日)与公告实际发布日不再混淆。缺口命中(②)是对更早公告的
    # 价格跟踪、非本期新公告，故不计入窗口。无新公告(仅缺口/故障)则不加窗。
    ann_digits = sorted(
        d for d in (i.get("ann_date") for i in (*forecast_items, *express_items)) if d
    )
    title = f"业绩预告/快报速报 {target_date}"
    if ann_digits:
        lo, hi = ann_digits[0], ann_digits[-1]
        win = _mmdd(lo) if lo == hi else f"{_mmdd(lo)}~{_mmdd(hi)}"
        title += f"（公告日 {win}）"
    lines = [f"## {title}", ""]

    # ① 持仓/关注命中（不筛）
    fc_hits = [(i, position_hit(i["ts_code"], position_codes)) for i in forecast_items]
    ex_hits = [(i, position_hit(i["ts_code"], position_codes)) for i in express_items]
    hit_fc = [(i, h) for i, h in fc_hits if h]
    hit_ex = [(i, h) for i, h in ex_hits if h]
    if hit_fc or hit_ex:
        lines.append("### ① 持仓/关注命中")
        lines.extend(_forecast_line(i, h, consensus_labels) for i, h in hit_fc)
        lines.extend(_express_line(i, h) for i, h in hit_ex)
        lines.append("")

    # ② 预告次日缺口验证（着重段；按 |缺口| 降序截断防刷屏，截断量入尾注不静默）
    if gap_error:
        lines.append("### ② 预告次日缺口验证（市场投票）")
        lines.append(f"- ⚠️ 当日行情获取失败，缺口验证本期缺席（{str(gap_error)[:80]}）")
        lines.append("")
    elif gap_hits:
        lines.append("### ② 预告次日缺口验证（市场投票）")
        if gap_note:  # 行情完整性提示（疑似截断时缺口命中可能不完整）
            lines.append(f"- ⚠️ {gap_note}")
        shown = gap_hits[:gap_display_cap]
        lines.extend(
            _gap_line(item, position_hit(item["ts_code"], position_codes))
            for item in shown
        )
        overflow = len(gap_hits) - len(shown)
        if overflow > 0:
            lines.append(f"- …另有 {overflow} 条缺口命中（|缺口| 较小，已截断）")
        lines.append("")
    elif gap_note:
        # 行情疑似截断但零命中：截断警示仍须随本次 digest 出现（否则只剩日志），
        # 借 digest 其它内容触发的本次推送捎带；gap_note 不进上文 None 判定，
        # 故不会单独触发推送（同日重跑里孤立警示不会被重推）
        lines.append("### ② 预告次日缺口验证（市场投票）")
        lines.append(f"- ⚠️ {gap_note}")
        lines.append("- （本期无满足阈值的缺口命中）")
        lines.append("")

    # ③ 申万行业聚合 Top5（仅 forecast 新增）
    if forecast_items and industry_map:
        counts: dict[str, int] = {}
        for item in forecast_items:
            info = industry_map.get(item["ts_code"]) or {}
            industry = info.get("sw_l1") or ""
            if industry:
                counts[industry] = counts.get(industry, 0) + 1
        top_industries = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        if top_industries:
            lines.append("### ③ 预告集中行业 Top5（申万一级）")
            lines.append(" / ".join(f"{name} {count}家" for name, count in top_industries))
            lines.append("")

    # ④ 全市场分类计数
    if forecast_items or express_items:
        lines.append("### ④ 新增公告计数")
        type_counts: dict[str, int] = {}
        for item in forecast_items:
            type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1
        count_parts = [
            f"{_TYPE_BADGES.get(t, t)} {c}" for t, c in
            sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
        if count_parts:
            lines.append("预告：" + " / ".join(count_parts))
        if express_items:
            lines.append(f"快报：{len(express_items)} 家")
        lines.append("")

    # ⑤ 过滤后 Top 榜（净利中值阈值；forecast/express 分开）
    # 阈值口径故意不对称：top_up 看本期净利中值（预增票有正中值）；top_down 看
    # 上年基数（亏损票中值为负无意义，大基数下滑才有预警价值，且排除低基数扭亏噪声）
    qualified = [i for i in forecast_items if is_top_up_candidate(i, min_profit_wan)]
    top_up = sorted(qualified, key=lambda x: x["p_change_mid"], reverse=True)[:top_n]
    top_down = sorted(
        (i for i in forecast_items
         if i["p_change_mid"] is not None and i["p_change_mid"] < 0
         and i["last_parent_net_wan"] is not None and abs(i["last_parent_net_wan"]) >= min_profit_wan),
        key=lambda x: x["p_change_mid"],
    )[:top_n]
    if top_up:
        lines.append(f"### ⑤ 预增 Top（净利中值≥{_fmt_wan(min_profit_wan)}）")
        lines.extend(
            f"- {_code_name(i)} {_TYPE_BADGES.get(i['type'], i['type'])} 同比中值 {_fmt_pct(i['p_change_mid'])} 净利 {_fmt_wan(i['net_profit_mid_wan'])}"
            f"{_consensus_suffix(i, consensus_labels)}"
            for i in top_up
        )
        lines.append("")
    if top_down:
        lines.append(f"### ⑤ 预警 Top（上年基数≥{_fmt_wan(min_profit_wan)}）")
        lines.extend(
            f"- {_code_name(i)} {_TYPE_BADGES.get(i['type'], i['type'])} 同比中值 {_fmt_pct(i['p_change_mid'])}"
            for i in top_down
        )
        lines.append("")
    ex_ranked = [i for i in express_items if i["yoy_dedu_np"] is not None
                 and i["n_income_wan"] is not None and i["n_income_wan"] >= min_profit_wan]
    if ex_ranked:
        top_ex = sorted(ex_ranked, key=lambda x: x["yoy_dedu_np"], reverse=True)[:top_n]
        lines.append(f"### ⑤ 快报实绩 Top（净利≥{_fmt_wan(min_profit_wan)}）")
        lines.extend(
            f"- {_code_name(i)} 归母同比 {_fmt_pct(i['yoy_dedu_np'])} 净利 {_fmt_wan(i['n_income_wan'])}"
            for i in top_ex
        )
        lines.append("")

    return title, "\n".join(lines).rstrip() + "\n"


def resolve_report_dir(report_dir: str | Path | None = None) -> Path:
    """报告/标记落盘目录（env 覆盖 > 默认锚定仓库根）；MD 与 .pushed 标记共用同目录。"""
    return Path(report_dir or os.getenv("EARNINGS_DIGEST_REPORT_DIR", REPORT_DIR))


def write_md(markdown: str, target_date: str, report_dir: str | Path | None = None) -> Path:
    """落盘 data/reports/earnings-digest/YYYY-MM-DD.md（对齐 research-digest 范式）。"""
    base = resolve_report_dir(report_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{target_date}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
