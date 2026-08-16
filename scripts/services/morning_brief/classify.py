"""morning-brief 分类：金十快讯复用 macro_flash 词表筛选器；公告标题走噪音排除 + 分组去重。"""
from __future__ import annotations

from collections import OrderedDict
from typing import List

from services.macro_flash.filter import load_keyword_config
from services.morning_brief import constants as C


def load_news_keywords(config: dict) -> "OrderedDict[str, List[str]]":
    """校验并载入 morning_brief.keywords（海外要闻/国内要闻主题词表）；缺失 fail fast。

    复用 macro_flash 同一校验/清洗逻辑，只换配置键（单一真源，门1 去重 finding）。
    """
    return load_keyword_config(config, config_key="morning_brief")


def is_noise_announcement(title: str) -> bool:
    return any(w in (title or "") for w in C.ANN_NOISE_KEYWORDS)


def classify_announcements(items: List[dict]) -> "OrderedDict[str, List[dict]]":
    """公告分组：噪音排除 → 按声明顺序首个命中组归属 → 同股同组只留最新一条。

    输入须为新→旧序（provider 契约）；未命中任何组的标题丢弃（例行公告噪音 >> 信号）。
    """
    grouped: "OrderedDict[str, List[dict]]" = OrderedDict(
        (g, []) for g in C.ANN_GROUPS)
    seen: set = set()
    for item in items:
        title = item.get("title") or ""
        if not title or is_noise_announcement(title):
            continue
        for group, words in C.ANN_GROUPS.items():
            if any(w in title for w in words):
                key = (item.get("code"), group)
                if key in seen:
                    break
                seen.add(key)
                grouped[group].append(item)
                break
    return OrderedDict((g, rows) for g, rows in grouped.items() if rows)
