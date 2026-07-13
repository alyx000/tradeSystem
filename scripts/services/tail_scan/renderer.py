"""tail-scan 只读观察清单渲染（全标 [判断]，守红线，附数据时效声明）。"""
from __future__ import annotations

import pathlib

_DISCLAIMER = (
    "> [判断] 盘中单次快照观察清单（尾盘前触发），仅供复盘参考，不构成买卖建议、不含价位。\n"
    "> 数据时效：涨幅/成交额/尾盘强度为**实时快照**；逻辑/板块（主线、概念）为 **T-1** 口径；"
    "盘中形态为单次快照代理（无分时数据）。\n"
)


def _fmt(value, nd: int = 2, suffix: str = "") -> str:
    """数值展示：None → 「—」；否则定点四舍五入（避免 8.1596/30.3661162… 这类长尾小数）。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _rank_note(pk_result, code):
    if not pk_result or pk_result.get("status") != "ok":
        return ""
    r = pk_result.get("ranks", {}).get(code)
    return f" · PK名次 **{r}**" if r else ""


def _display_order(scored, pk_result):
    """展示顺序：PK status=ok 时按 PK 名次排（履行"按 PK 名次排"契约）；
    无名次的票（不在强池/被 excluded）用大 key 排后、`sorted` 稳定保留粗分序兜底。
    melted/skipped/no-llm/None → 保持传入的粗分序。
    （codex 门2 高危：此前只标注 PK名次却不重排，导致 #2 显示在 #3 之上，自相矛盾。）"""
    if pk_result and pk_result.get("status") == "ok":
        ranks = pk_result.get("ranks") or {}
        return sorted(scored, key=lambda c: ranks.get(c.get("code"), 10 ** 9))
    return scored


def render_daily(scan_result: dict, scored: list, pk_result: dict | None) -> str:
    date = scan_result.get("quote_date", "")
    qt = scan_result.get("quote_time", "")
    lines = [f"# 尾盘强势股观察清单 · {date} {qt}\n", _DISCLAIMER,
             f"\n筛选：涨幅>7% ∩ 非ST ∩ 成交额>20亿 · 全市场扫 {scan_result.get('scanned', 0)} 只 → "
             f"命中 **{scan_result.get('matched', 0)}** 只\n"]
    if pk_result and pk_result.get("status") == "melted":
        lines.append("\n> [判断] PK 循环赛熔断（预算/无效场率超限），仅按粗分排序展示。\n")
    if not scored:
        lines.append("\n本次无满足条件个股。\n")
        return "".join(lines)
    lines.append("\n## 候选（按 PK 名次 / 粗分排序，全为 [判断]）\n")
    for c in _display_order(scored, pk_result):
        tags = []
        if c.get("in_main_sector"):
            tags.append("主线")
        if c.get("concept_names"):
            tags.append("概念:" + "/".join(c["concept_names"][:2]))
        if c.get("is_limit_up"):
            tags.append("已涨停")
        tag_s = ("｜" + " ".join(tags)) if tags else ""
        lines.append(
            f"- **{c.get('name','')}**（{c.get('code','')}）涨{_fmt(c.get('pct_chg'), 2, '%')} "
            f"成交{_fmt(c.get('amount_yi'), 2, '亿')} 近5日{_fmt(c.get('gain5'), 1, '%')} "
            f"连涨{c.get('up_days')}天"
            f"{tag_s}{_rank_note(pk_result, c.get('code'))}\n")

    lines.append(_render_pk_detail(pk_result, scored))
    return "".join(lines)


def _render_pk_detail(pk_result, scored) -> str:
    """PK 对局明细：LLM 逐场依据事实判相对强弱（理由已过红线，全 [判断]）。
    仅在 status==ok 且有有效场时渲染。"""
    if not pk_result or pk_result.get("status") != "ok":
        return ""
    valid = [m for m in pk_result.get("matches", []) if m.get("state") == "valid"]
    if not valid:
        return ""
    names = {c.get("code"): c.get("name", "") for c in scored}
    out = ["\n## PK 对局明细（LLM 依据事实判相对强弱，全为 [判断]）\n"]
    for m in valid:
        a, b, w = m.get("a"), m.get("b"), m.get("winner")
        out.append(
            f"- {names.get(a, a)} vs {names.get(b, b)} → 胜：**{names.get(w, w)}**"
            f" ｜ {m.get('reason', '')}\n")
    return "".join(out)


def render_source_failed(scan_result: dict) -> str:
    return (f"# 尾盘强势股扫描 · 数据失败\n\n> [判断] 数据源失败，未产出候选清单。\n\n"
            f"原因：{scan_result.get('error', '未知')}\n")


def save_report(md: str, date: str, out_root: str = "data/reports/tail-scan") -> pathlib.Path:
    root = pathlib.Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{date}.md"
    path.write_text(md, encoding="utf-8")
    return path
