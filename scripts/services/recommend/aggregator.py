"""行业热度聚合服务（TDD 增量驱动）。

数据来源：
- teacher_notes.sectors (JSON 数组) → mentions 一路
- industry_info.sector_name + confidence → mentions 另一路 + 信心因子

输出：AggregateResult（含按 score 倒序的 SectorScore 列表）。
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────
# 评分公式常量（plan 第 ④ 节定稿）
# ─────────────────────────────────────────────────────────────
CONF_FLOOR = 0.7      # 信心系数下限，避免低信心一票否决高频提及
CONF_WEIGHT = 0.3     # 信心系数上调空间，0.7 + 0.3 = 1.0 封顶
DECAY_FLOOR = 0.3     # 时间衰减下限，避免窗口边缘归零

DEFAULT_CONFIDENCE = 0.5   # industry_info 缺路时的缺省置信度
SNIPPETS_MAX = 3           # 每个行业最多保留多少条原文摘要
SNIPPET_TRUNC = 120        # 单条摘要超长截断字符数

# industry_info.confidence 字段实为 TEXT（高/中/低），需映射到 0-1
CONFIDENCE_LABEL_MAP = {
    "高": 0.9,
    "中": 0.5,
    "低": 0.3,
}


def _parse_confidence(value) -> float | None:
    """把 industry_info.confidence 的多形态值（文本标签 / 数值字符串 / None）归一到 0-1。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # 优先文本标签
        if value in CONFIDENCE_LABEL_MAP:
            return CONFIDENCE_LABEL_MAP[value]
        # 兼容数值字符串
        try:
            return float(value)
        except ValueError:
            return None  # 无法识别的标签，按缺省处理
    return None


@dataclass(frozen=True)
class SectorScore:
    sector_name: str
    mentions: int
    avg_confidence: float
    recency_decay: float
    score: float
    latest_date: str
    snippets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AggregateResult:
    sectors: list[SectorScore]
    window_start: str
    window_end: str
    lookback_days: int


def aggregate(conn: sqlite3.Connection, *, lookback_days: int, top_k: int) -> AggregateResult:
    """跨 teacher_notes + industry_info 聚合行业热度，返回 Top K。

    窗口语义：`date >= window_start` SQL 层直接过滤，窗口外不计。
    """
    today = date.today()
    window_start = (today - timedelta(days=lookback_days)).isoformat()
    window_end = today.isoformat()

    # sector → {mentions, latest_date, confidences: list[float]}
    agg: dict[str, dict] = {}

    # 一路：teacher_notes.sectors (JSON 数组) + core_view 作摘要
    # date 双边过滤 + ORDER BY date DESC 保证 snippets 按时间倒序
    for row in conn.execute(
        "SELECT date, sectors, core_view FROM teacher_notes "
        "WHERE date >= ? AND date <= ? AND sectors IS NOT NULL "
        "ORDER BY date DESC",
        (window_start, window_end),
    ).fetchall():
        the_date = row["date"]
        try:
            sectors_list = json.loads(row["sectors"]) if row["sectors"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        snippet = _trim(row["core_view"])
        # 同一 note 内重复行业去重，避免虚增 mentions
        seen: set[str] = set()
        for sector in sectors_list:
            if not sector or not isinstance(sector, str):
                continue
            normalized = sector.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            _accumulate(agg, normalized, the_date, confidence=None, snippet=snippet)

    # 二路：industry_info.sector_name (单值 + confidence) + content 作摘要
    for row in conn.execute(
        "SELECT date, sector_name, confidence, content FROM industry_info "
        "WHERE date >= ? AND date <= ? AND sector_name IS NOT NULL "
        "ORDER BY date DESC",
        (window_start, window_end),
    ).fetchall():
        _accumulate(agg, row["sector_name"], row["date"],
                    confidence=_parse_confidence(row["confidence"]),
                    snippet=_trim(row["content"]))

    scores: list[SectorScore] = []
    for sector_name, slot in agg.items():
        mentions = slot["mentions"]
        latest = slot["latest_date"]
        confidences = slot["confidences"]
        avg_conf = sum(confidences) / len(confidences) if confidences else DEFAULT_CONFIDENCE
        # delta 钳为非负，防 latest > today (理论上 SQL 上界已挡，再加一层防御)
        delta_days = max((today - date.fromisoformat(latest)).days, 0)
        recency = max(math.exp(-delta_days / lookback_days), DECAY_FLOOR)
        score = mentions * (CONF_FLOOR + CONF_WEIGHT * avg_conf) * recency
        scores.append(SectorScore(
            sector_name=sector_name,
            mentions=mentions,
            avg_confidence=avg_conf,
            recency_decay=recency,
            score=score,
            latest_date=latest,
            snippets=slot["snippets"][:SNIPPETS_MAX],
        ))

    scores.sort(key=lambda s: s.score, reverse=True)
    return AggregateResult(
        sectors=scores[:top_k],
        window_start=window_start,
        window_end=window_end,
        lookback_days=lookback_days,
    )


def _accumulate(agg: dict[str, dict], sector: str, the_date: str, *,
                confidence: float | None, snippet: str | None) -> None:
    """累加单条提及到聚合字典；teacher_notes 一路 confidence=None 不计入 avg。"""
    slot = agg.setdefault(sector, {
        "mentions": 0,
        "latest_date": the_date,
        "confidences": [],
        "snippets": [],
    })
    slot["mentions"] += 1
    if the_date > slot["latest_date"]:
        slot["latest_date"] = the_date
    if confidence is not None:
        slot["confidences"].append(float(confidence))
    if snippet:
        slot["snippets"].append(snippet)


def _trim(text: str | None) -> str | None:
    """裁剪长文本以便钉钉单卡可读。"""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > SNIPPET_TRUNC:
        return text[:SNIPPET_TRUNC] + "…"
    return text
