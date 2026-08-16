"""morning-brief 渲染：隔夜行情 + 海外/国内要闻 + 公告分组，18KB 推送预算复用 macro_flash。

钉钉 markdown 兼容:不用表格,用标题/列表/加粗(macro_flash/tail_scan 同先例)。
内容为转述事实层,不添加买卖建议、价位预测;v1 无 LLM 生成段。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from services.macro_flash.filter import OTHER_TOPIC, FlashCandidate
from services.macro_flash.formatter import _clean_text, build_push_markdown
from services.morning_brief import constants as C

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = REPO_ROOT / "data" / "reports" / C.REPORT_DIR_NAME


def _fmt_quote_line(label: str, info: Optional[dict]) -> str:
    if not info or "error" in info or "_error" in info:
        return f"- {label}: 数据获取失败"
    pct = info.get("change_pct", 0)
    sign = "+" if isinstance(pct, (int, float)) and pct >= 0 else ""
    as_of = info.get("as_of")
    as_of_str = f"，截至 {as_of}" if as_of else ""
    return f"- [事实] {label}: {info.get('close', 'N/A')} ({sign}{pct}%{as_of_str})"


def _news_line(cand: FlashCandidate) -> str:
    data = cand.item.get("data") or {}
    hhmm = (cand.item.get("time") or "")[11:16]
    star = "⭐ " if cand.item.get("important") else ""
    text = _clean_text(data.get("title") or data.get("content") or "")
    if len(text) > 200:
        text = text[:200] + "…"
    return f"- **{hhmm}** {star}{text}"


def _ann_line(item: dict) -> str:
    title = (item.get("title") or "").strip()
    if len(title) > C.ANN_TITLE_LIMIT:
        title = title[:C.ANN_TITLE_LIMIT] + "…"
    hhmm = (item.get("time") or "")[11:16]
    # 巨潮隔夜披露常以次日 00:00 计时（源粒度只到日），零点时间无信息量不展示
    suffix = f"（{hhmm}）" if hhmm and hhmm != "00:00" else ""
    return f"- {item.get('name')}（{item.get('code')}）{title}{suffix}"


def _news_section(lines: List[str], title: str, cands: List[FlashCandidate],
                  limit: int) -> None:
    lines.append(f"## {title}（{min(len(cands), limit)}/{len(cands)} 条）")
    lines.append("")
    if not cands:
        lines.append("窗口内无命中条目。")
    else:
        lines.extend(_news_line(c) for c in cands[:limit])
    lines.append("")


def render(result: dict) -> str:
    """整份早报 markdown（报告本体；推送体经 build_push_body 预算裁剪）。"""
    date = result["date"]
    news_start, news_end = result["news_window"]
    lines = [
        f"# 盘前早报 · {date}",
        "",
        f"> 窗口 {news_start:%m-%d %H:%M} → {news_end:%m-%d %H:%M}"
        f" · 状态 {result['status']}",
    ]
    for gap in result.get("gaps") or []:
        lines.append(f"> ⚠️ {gap}")
    lines.append("> 新闻与公告为转述事实层；不构成投资建议。")
    lines.append("")

    # 隔夜行情
    overnight = result.get("overnight") or {}
    lines.append("## 隔夜行情")
    lines.append("")
    if result.get("backfill"):
        lines.append("> ⚠️ 补跑档：隔夜行情为取数时最新快照，可能晚于目标窗口场次，以各行 as_of 标注为准")
        lines.append("")
    for label, info in overnight.get("indices") or []:
        lines.append(_fmt_quote_line(label, info))
    us_cn = overnight.get("us_china")
    if us_cn is not None:
        name = (us_cn or {}).get("name") or "纳斯达克中国金龙"
        lines.append(_fmt_quote_line(str(name), us_cn))
    for label, info in overnight.get("commodities") or []:
        lines.append(_fmt_quote_line(label, info))
    lines.append("")

    # 海外/国内要闻（金十）
    news = result.get("news") or {}
    grouped: dict = {}
    for c in news.get("candidates") or []:
        grouped.setdefault(c.topic, []).append(c)
    topic_order = [t for t in (news.get("topic_order") or []) if t != OTHER_TOPIC]
    for topic in topic_order:
        _news_section(lines, topic, grouped.get(topic, []), C.NEWS_PER_TOPIC_LIMIT)
    if grouped.get(OTHER_TOPIC):
        _news_section(lines, OTHER_TOPIC, grouped[OTHER_TOPIC], C.NEWS_OTHER_TOPIC_LIMIT)

    # 上市公司公告（巨潮）
    ann = result.get("announcements") or {}
    ann_start, ann_end = result["ann_window"]
    lines.append(f"## 上市公司公告（{ann_start:%m-%d %H:%M} → {ann_end:%m-%d %H:%M}）")
    lines.append("")
    if ann.get("error"):
        lines.append(f"公告源失败：{ann['error']}")
    else:
        ann_grouped = ann.get("grouped") or {}
        if ann.get("status") == "truncated":
            lines.append("> ⚠️ 公告采集触达预算被截断，以下为部分结果")
        if not ann_grouped:
            lines.append(f"窗口内无命中重点公告（原始 {ann.get('raw_count', 0)} 条）。")
        for group, rows in ann_grouped.items():
            lines.append(f"### {group}（{min(len(rows), C.ANN_PER_GROUP_LIMIT)}/{len(rows)}）")
            lines.extend(_ann_line(r) for r in rows[:C.ANN_PER_GROUP_LIMIT])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_push_body(md: str, date: str) -> str:
    """推送体：18KB 预算按 `## ` 块整块截断（复用 macro_flash 同款逻辑）。"""
    archive_hint = f"data/reports/{C.REPORT_DIR_NAME}/{date}.md"
    return build_push_markdown(md, archive_hint)


def build_failure_push(date: str, error: str) -> str:
    return (f"# 盘前早报 · {date}\n\n"
            f"> ⚠️ 采集失败（source_failed）\n\n错误：{error}\n")


def write_report(md: str, date: str) -> Path:
    """原子落盘 data/reports/morning-brief/YYYY-MM-DD.md。

    临时文件用进程唯一路径:共享 .tmp 在 launchd 与手动补跑并发时会互相覆盖/删除
    对方的半成品(codex 门2 finding),唯一临时名 + os.replace 保证各自原子完整。
    """
    import tempfile

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{date}.md"
    fd, tmp_name = tempfile.mkstemp(prefix=f"{date}.", suffix=".md.tmp",
                                    dir=REPORT_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(md)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
