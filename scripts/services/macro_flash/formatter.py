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
                          topic_order: List[str]) -> str:
    lines = [
        f"# 宏观快讯速读 · {window_end.date().isoformat()}",
        "",
        f"> 窗口 {window_start:%m-%d %H:%M} → {window_end:%m-%d %H:%M}"
        f" · 原始 {raw_count} 条 · 命中 {len(candidates)} 条 · 状态 {source_status}",
        "",
    ]
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


def build_status_push(source_status: str, *, window_start: datetime,
                      window_end: datetime, error: str = None) -> str:
    detail = f"错误:{error}" if error else "请 `macro-flash doctor` 排查后手动补跑。"
    return (f"# 宏观快讯速读 · {window_end.date().isoformat()}\n\n"
            f"> ⚠️ 采集状态 {source_status}"
            f",窗口 {window_start:%m-%d %H:%M} → {window_end:%m-%d %H:%M}\n\n"
            f"{detail}\n")
