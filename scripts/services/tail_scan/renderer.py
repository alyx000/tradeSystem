"""tail-scan 只读观察清单渲染（全标 [判断]，守红线，附数据时效声明）。"""
from __future__ import annotations

import pathlib

_DISCLAIMER = (
    "> [判断] 盘中 14:30 单次快照观察清单，仅供复盘参考，不构成买卖建议、不含价位。\n"
    "> 数据时效：涨幅/成交额/尾盘强度为**实时快照**；逻辑/板块（主线、概念）为 **T-1** 口径；"
    "盘中形态为单次快照代理（无分时数据）。\n"
)


def _rank_note(pk_result, code):
    if not pk_result or pk_result.get("status") != "ok":
        return ""
    r = pk_result.get("ranks", {}).get(code)
    return f" · PK名次 **{r}**" if r else ""


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
    for c in scored:
        tags = []
        if c.get("in_main_sector"):
            tags.append("主线")
        if c.get("concept_names"):
            tags.append("概念:" + "/".join(c["concept_names"][:2]))
        if c.get("is_limit_up"):
            tags.append("已涨停")
        tag_s = ("｜" + " ".join(tags)) if tags else ""
        lines.append(
            f"- **{c.get('name','')}**（{c.get('code','')}）涨{c.get('pct_chg')}% "
            f"成交{c.get('amount_yi')}亿 近5日{c.get('gain5')}% 连涨{c.get('up_days')}天"
            f"{tag_s}{_rank_note(pk_result, c.get('code'))}\n")
    return "".join(lines)


def render_source_failed(scan_result: dict) -> str:
    return (f"# 尾盘强势股扫描 · 数据失败\n\n> [判断] 数据源失败，未产出候选清单。\n\n"
            f"原因：{scan_result.get('error', '未知')}\n")


def save_report(md: str, date: str, out_root: str = "data/reports/tail-scan") -> pathlib.Path:
    root = pathlib.Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{date}.md"
    path.write_text(md, encoding="utf-8")
    return path
