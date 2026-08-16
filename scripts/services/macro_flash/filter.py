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


def load_keyword_config(config: dict,
                        config_key: str = "macro_flash") -> "OrderedDict[str, List[str]]":
    """校验并载入 <config_key>.keywords;缺失/全空 fail fast,不静默空筛。

    config_key 参数化供 morning_brief 复用同一校验/清洗逻辑(门1 去重 finding)。
    """
    raw = ((config or {}).get(config_key) or {}).get("keywords")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"配置缺失:scripts/config.yaml 需含非空 {config_key}.keywords")
    cleaned: "OrderedDict[str, List[str]]" = OrderedDict()
    for topic, words in raw.items():
        valid = [str(w).strip() for w in (words or []) if str(w).strip()]
        if valid:
            cleaned[str(topic)] = valid
    if not cleaned:
        raise ValueError(f"配置无效:{config_key}.keywords 所有主题词表为空")
    return cleaned


def filter_items(items: List[dict], keywords: "OrderedDict[str, List[str]]") -> List[FlashCandidate]:
    out: List[FlashCandidate] = []
    for item in items:
        data = item.get("data") or {}
        text = f"{data.get('title') or ''}\n{data.get('content') or ''}"
        # 各主题命中关键词(保留声明顺序)
        matched = [(topic, [w for w in words if w in text])
                   for topic, words in keywords.items()]
        matched = [(t, hits) for t, hits in matched if hits]
        best_topic = _resolve_topic(matched)
        if best_topic is None and item.get("important"):
            best_topic = OTHER_TOPIC  # 金十标重要但无命中:强制入选兜底
        if best_topic is not None:
            out.append(FlashCandidate(item=item, topic=best_topic))
    return out


def _resolve_topic(matched: "List[tuple]"):
    """默认按声明顺序取首个命中主题;但若某主题的命中词全部只是其他主题更长命中词的
    子串(如 央行 仅作为 欧央行/日央行 的子串出现),该命中视为子串误命中并跳过,由更
    具体的主题胜出。既解决子串遮蔽,又保留非子串跨主题共现的声明顺序优先契约。"""
    if not matched:
        return None
    all_hits = [w for _, hits in matched for w in hits]
    for topic, hits in matched:  # 声明顺序
        if any(not _subsumed(w, all_hits) for w in hits):
            return topic
    return matched[0][0]  # 理论兜底:所有命中互为子串,退回声明序首个


def _subsumed(word: str, all_hits: "List[str]") -> bool:
    """word 是否存在一个「包含它的更长命中词」(可能只是更具体词的子串误命中)。"""
    return any(other != word and word in other for other in all_hits)
