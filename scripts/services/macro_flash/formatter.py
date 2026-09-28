"""速读渲染:归档全量 digest + 18KB 预算推送版(整块截断)。

钉钉 markdown 兼容:不用表格(手机端渲染差,tail_scan 同先例),用标题/列表/加粗。
formatter 不添加买卖建议、价位预测;内容为转述事实层,v1 无 LLM 生成段。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List

from services.macro_flash.filter import OTHER_TOPIC, FlashCandidate

PUSH_BODY_MAX_BYTES = 18_000   # 与 tail_scan/renderer.py 同预算
ITEM_TEXT_LIMIT = 200          # 单条正文截断字数
# 推送精选(归档 digest.md 恒全量,只精简推送体;用户反馈全量 500+ 条不可读):
# 仅金十标 important 的条目进推送,每主题各取最新前 N 条;其他要闻(无关键词命中的
# important 兜底)噪音占比最高,上限更严。
PUSH_PER_TOPIC_LIMIT = 8
PUSH_OTHER_TOPIC_LIMIT = 3


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")     # 金十 content 可能带 HTML 标签
    return re.sub(r"\s+", " ", text).strip()


def _item_line(cand: FlashCandidate) -> str:
    data = cand.item.get("data") or {}
    hhmm = (cand.item.get("time") or "")[11:16]
    star = "⭐ " if cand.item.get("important") else ""
    text = _clean_text(data.get("title") or data.get("content") or "")
    if len(text) > ITEM_TEXT_LIMIT:
        text = text[:ITEM_TEXT_LIMIT] + "…"
    return f"- **{hhmm}** {star}{text}"


def build_digest_markdown(candidates: List[FlashCandidate], *,
                          window_start: datetime, window_end: datetime,
                          source_status: str, raw_count: int,
                          topic_order: List[str],
                          extra_note: str = None,
                          matched_count: int = None) -> str:
    # matched_count:头部「命中」数默认取传入条目数;推送精选版传全量命中数防误读
    matched = len(candidates) if matched_count is None else matched_count
    lines = [
        f"# 宏观快讯速读 · {window_end.date().isoformat()}",
        "",
        f"> 窗口 {window_start:%m-%d %H:%M} → {window_end:%m-%d %H:%M}"
        f" · 原始 {raw_count} 条 · 命中 {matched} 条 · 状态 {source_status}",
    ]
    if extra_note:
        lines.append(f"> {extra_note}")  # 紧跟窗口行的第二条引用(推送精选说明等)
    lines.append("")
    if not candidates:
        lines.append(f"窗口内无命中宏观快讯(原始 {raw_count} 条)。")
        return "\n".join(lines)
    grouped: dict = {}
    for c in candidates:
        grouped.setdefault(c.topic, []).append(c)
    ordered = [t for t in topic_order if t != OTHER_TOPIC] + [OTHER_TOPIC]
    for topic in ordered:
        if topic not in grouped:
            continue
        lines.append(f"## {topic}({len(grouped[topic])})")
        lines.extend(_item_line(c) for c in grouped[topic])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_push_markdown(digest_md: str, archive_hint: str) -> str:
    """预算内原样推送;超限按主题块整块截断,尾部提示完整文件路径。"""
    if len(digest_md.encode("utf-8")) <= PUSH_BODY_MAX_BYTES:
        return digest_md
    blocks = digest_md.split("\n## ")

    def _hint(dropped: int) -> str:
        return f"\n\n> ⚠️ 超推送预算,截断 {dropped} 个主题块;完整版见 `{archive_hint}`\n"

    # 预留按最坏情况(截断全部块)的提示真实字节长度算,保证最终输出不越过 18KB 硬上限
    # (固定预留常量在 archive_hint 很长或丢弃计数位数变化时会算少,导致越界)
    reserve = len(_hint(len(blocks) - 1).encode("utf-8"))
    out = blocks[0]
    kept = 0
    for blk in blocks[1:]:
        candidate = out + "\n## " + blk
        if len(candidate.encode("utf-8")) > PUSH_BODY_MAX_BYTES - reserve:
            break
        out = candidate
        kept += 1
    dropped = len(blocks) - 1 - kept
    return out.rstrip() + _hint(dropped)


def _select_important(candidates: List[FlashCandidate]) -> List[FlashCandidate]:
    """推送精选:仅 important 条目,每主题按原序(collector 已新→旧)取前 N。"""
    per_topic: dict = {}
    selected: List[FlashCandidate] = []
    for c in candidates:
        if not c.item.get("important"):
            continue
        limit = PUSH_OTHER_TOPIC_LIMIT if c.topic == OTHER_TOPIC else PUSH_PER_TOPIC_LIMIT
        n = per_topic.get(c.topic, 0)
        if n >= limit:
            continue
        per_topic[c.topic] = n + 1
        selected.append(c)
    return selected


def build_push_plan(candidates: List[FlashCandidate], *,
                    window_start: datetime, window_end: datetime,
                    source_status: str, raw_count: int,
                    topic_order: List[str], archive_hint: str,
                    window_count: int = None, summary_md: str = "",
                    error: str = None) -> tuple:
    """返回预算裁剪后的正文及同口径 coverage；selected 是计划展示数，不代表送达。

    保留 important/8/3、无 important 回退全量、18KB 按主题前缀整块截断。
    覆盖与只读归纳是保留头部；正文展示计数不包括归纳中重复引用的证据。
    """
    important = _select_important(candidates)
    eligible = important or candidates
    mode = "important" if important else "full_fallback"
    if source_status == "source_failed":
        eligible, mode = [], "status_only"
    ordered = list(dict.fromkeys(topic_order + [c.topic for c in candidates]))
    ordered = [t for t in ordered if t != OTHER_TOPIC] + [OTHER_TOPIC]
    grouped = {t: [c for c in eligible if c.topic == t] for t in ordered}
    blocks = [(t, f"## {t}({len(grouped[t])})\n" +
               "\n".join(_item_line(c) for c in grouped[t]))
              for t in ordered if grouped[t]]

    def render(kept):
        topics = {}
        for topic in ordered:
            matched = sum(c.topic == topic for c in candidates)
            selected = len(grouped[topic]) if topic in kept else 0
            topics[topic] = {"matched_count": matched, "selected_count": selected,
                             "omitted_count": matched - selected}
        selected = sum(t["selected_count"] for t in topics.values())
        coverage = {"raw_count": raw_count, "window_count": window_count,
                    "matched_count": len(candidates), "selected_count": selected,
                    "omitted_count": len(candidates) - selected,
                    "eligible_count": len(eligible),
                    "budget_omitted_count": len(eligible) - selected,
                    "selection_mode": mode, "source_status": source_status,
                    "error": error, "topics": topics}
        lines = [f"# 宏观快讯速读 · {window_end.date().isoformat()}", "",
                 f"> 窗口 {window_start:%m-%d %H:%M} → {window_end:%m-%d %H:%M}"
                 f" · 状态 {source_status} · error={_clean_text(error or 'none')[:300]}",
                 f"> raw={raw_count}（翻页返回，含窗口外/重复）；"
                 f"window={window_count if window_count is not None else 'unknown'}（窗口内去重）；"
                 f"matched={len(candidates)}；selected={selected}；omitted={len(candidates) - selected}",
                 "> selected/omitted 仅计下方快讯正文（预算裁剪后计划展示/未展示），不含归纳引用；送达见 push_status。",
                 f"> 全量命中 {len(candidates)} 条见 `{archive_hint}`；全量原文见同目录 flash_raw.json。"]
        if mode == "important":
            lines.append(f"> 📌 推送精选 {selected} 条(仅金十标重要,主题内最新优先);"
                         f"预算前 {len(eligible)} 条")
        else:
            lines.append(f"> selection_mode={mode}（无重要条目时回退全量；源失败仅报状态）")
        for topic, counts in topics.items():
            lines.append(f"> {topic}: matched={counts['matched_count']} / "
                         f"selected={counts['selected_count']} / omitted={counts['omitted_count']}")
        if len(kept) < len(blocks):
            lines.append(f"> ⚠️ 超推送预算,截断 {len(blocks) - len(kept)} 个主题块;"
                         f"完整版见 `{archive_hint}`")
        if summary_md:
            lines.extend(["", summary_md.rstrip()])
        lines.extend("\n" + block for topic, block in blocks if topic in kept)
        if not candidates:
            lines.append("窗口内无命中宏观快讯；仅代表已采集部分。")
        return "\n".join(lines) + "\n", coverage

    # 从完整主题前缀缩短，最终头部计数与保留正文同时重算，不靠解析 Markdown 反推。
    for size in range(len(blocks), -1, -1):
        md, coverage = render({topic for topic, _ in blocks[:size]})
        if len(md.encode("utf-8")) <= PUSH_BODY_MAX_BYTES:
            return md, coverage
    raise ValueError("macro-flash 覆盖/归纳头部超过推送预算，拒绝发送")


def build_push_digest(candidates: List[FlashCandidate], *,
                      full_digest_md: str = None, **kwargs) -> str:
    """兼容既有调用；完整归档不能代替推送自身的覆盖统计。"""
    return build_push_plan(candidates, **kwargs)[0]


def build_status_push(source_status: str, *, window_start: datetime,
                      window_end: datetime, error: str = None) -> str:
    detail = f"错误:{error}" if error else "请 `macro-flash doctor` 排查后手动补跑。"
    return (f"# 宏观快讯速读 · {window_end.date().isoformat()}\n\n"
            f"> ⚠️ 采集状态 {source_status}"
            f",窗口 {window_start:%m-%d %H:%M} → {window_end:%m-%d %H:%M}\n\n"
            f"{detail}\n")
