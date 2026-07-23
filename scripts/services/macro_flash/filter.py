"""关键词规则筛选:主题按 config 声明顺序归组;important 强制入选。"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import List

OTHER_TOPIC = "其他要闻"


@dataclass
class FlashCandidate:
    item: dict
    topic: str


def load_keyword_config(config: dict) -> "OrderedDict[str, List[str]]":
    """校验并载入 macro_flash.keywords;缺失/全空 fail fast,不静默空筛。"""
    raw = ((config or {}).get("macro_flash") or {}).get("keywords")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("配置缺失:scripts/config.yaml 需含非空 macro_flash.keywords")
    cleaned: "OrderedDict[str, List[str]]" = OrderedDict()
    for topic, words in raw.items():
        valid = [str(w).strip() for w in (words or []) if str(w).strip()]
        if valid:
            cleaned[str(topic)] = valid
    if not cleaned:
        raise ValueError("配置无效:macro_flash.keywords 所有主题词表为空")
    return cleaned


def filter_items(items: List[dict], keywords: "OrderedDict[str, List[str]]") -> List[FlashCandidate]:
    out: List[FlashCandidate] = []
    for item in items:
        data = item.get("data") or {}
        text = f"{data.get('title') or ''}\n{data.get('content') or ''}"
        # 最长命中关键词的主题胜出(specific-over-general):解决 央行⊂欧央行/日央行 的子串遮蔽。
        # 长度相同按声明顺序(严格 > 更新,先声明的主题先遍历即保留)。
        best_topic = None
        best_len = 0
        for topic, words in keywords.items():
            longest = max((len(w) for w in words if w in text), default=0)
            if longest > best_len:
                best_len = longest
                best_topic = topic
        if best_topic is None and item.get("important"):
            best_topic = OTHER_TOPIC  # 金十标重要但无命中:强制入选兜底
        if best_topic is not None:
            out.append(FlashCandidate(item=item, topic=best_topic))
    return out
