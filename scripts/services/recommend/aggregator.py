"""行业热度聚合服务（三列表重构）。

把三种本质不同的数据拆成三个独立列表，避免「大盘观点冒充行业逻辑」：

- market_views ← teacher_notes.core_view（note 级大盘观点，去重、按 date desc）
- sectors      ← teacher_notes.sectors 提及次数（热度榜，score = mentions × recency_decay）
- catalysts    ← industry_info（行业催化，按 confidence → date 倒序）

关键语义：
- core_view 是 note 级大盘观点，**不再贴到任何板块当摘要**（旧实现的 bug 根源）。
- industry_info 不并入热度榜，单独成段；它才是真正的「行业逻辑」。
- 热度榜评分去掉了 confidence 因子：teacher_notes 无 confidence，旧公式里的
  信心系数对每个板块恒为常数 0.85，对排序零贡献，纯噪音。
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────
# 评分 / 截断常量
# ─────────────────────────────────────────────────────────────
DECAY_FLOOR = 0.3          # 时间衰减下限，避免窗口边缘归零
SNIPPET_TRUNC = 120        # 单条文本（core_view / content）超长截断字符数

# industry_info.confidence 字段实为 TEXT（高/中/低），需映射到 0-1 供排序
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
        if value in CONFIDENCE_LABEL_MAP:
            return CONFIDENCE_LABEL_MAP[value]
        try:
            return float(value)
        except ValueError:
            return None  # 无法识别的标签
    return None


@dataclass(frozen=True)
class MarketView:
    """大盘判断条目（来自 teacher_notes.core_view）。"""
    date: str
    text: str


@dataclass(frozen=True)
class SectorHeat:
    """热度榜条目（来自 teacher_notes.sectors 提及）。"""
    sector_name: str
    mentions: int
    recency_decay: float
    score: float
    latest_date: str


@dataclass(frozen=True)
class Catalyst:
    """行业催化条目（来自 industry_info）。confidence 保留原始标签供展示。"""
    date: str
    sector_name: str
    content: str
    confidence: str | None


@dataclass(frozen=True)
class AggregateResult:
    market_views: list[MarketView]
    sectors: list[SectorHeat]
    catalysts: list[Catalyst]
    window_start: str
    window_end: str
    lookback_days: int


def aggregate(conn: sqlite3.Connection, *, lookback_days: int, top_k: int) -> AggregateResult:
    """跨 teacher_notes + industry_info 聚合，返回三列表。

    窗口语义：`date >= window_start AND date <= window_end` SQL 层直接过滤。

    空集 / 去重约定（占位由 formatter 负责，本层只产出干净数据）：
    - market_views：core_view 为空（_trim → None）则跳过；trimmed text 完全相同
      只保留首次出现（按 date DESC，即保留最新一条）；不截断条数，全量返回供降级取用。
    - sectors：同一 note 内重复行业去重；按 score 倒序后 top_k 截断。
    - catalysts：按 (confidence 降序, date 降序) 排序；不截断（industry_info 覆盖稀疏）。
    """
    today = date.today()
    window_start = (today - timedelta(days=lookback_days)).isoformat()
    window_end = today.isoformat()

    # ── 一路：teacher_notes → market_views（去重）+ sectors 热度（mentions） ──
    # sector → {mentions, latest_date}
    heat: dict[str, dict] = {}
    market_views: list[MarketView] = []
    seen_views: set[str] = set()

    # 注意：不加 `sectors IS NOT NULL` —— 只写了大盘观点、没标板块的笔记
    # 仍要进 market_views（其 core_view 是纯大盘判断）；sectors 为空时跳过热度计数即可。
    for row in conn.execute(
        "SELECT date, sectors, core_view FROM teacher_notes "
        "WHERE date >= ? AND date <= ? "
        "ORDER BY date DESC",
        (window_start, window_end),
    ).fetchall():
        the_date = row["date"]

        # core_view → market_views（去重，按 date DESC 保留最新一条）
        view_text = _trim(row["core_view"])
        if view_text and view_text not in seen_views:
            seen_views.add(view_text)
            market_views.append(MarketView(date=the_date, text=view_text))

        # sectors → 热度计数（同 note 内去重）
        try:
            sectors_list = json.loads(row["sectors"]) if row["sectors"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        seen: set[str] = set()
        for sector in sectors_list:
            if not sector or not isinstance(sector, str):
                continue
            normalized = sector.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            slot = heat.setdefault(normalized, {"mentions": 0, "latest_date": the_date})
            slot["mentions"] += 1
            if the_date > slot["latest_date"]:
                slot["latest_date"] = the_date

    # ── 二路：industry_info → catalysts（不进热度榜） ──
    catalysts: list[Catalyst] = []
    for row in conn.execute(
        "SELECT date, sector_name, confidence, content FROM industry_info "
        "WHERE date >= ? AND date <= ? AND sector_name IS NOT NULL "
        "ORDER BY date DESC",
        (window_start, window_end),
    ).fetchall():
        content = _trim(row["content"])
        if not content:
            continue
        catalysts.append(Catalyst(
            date=row["date"],
            sector_name=row["sector_name"],
            content=content,
            confidence=row["confidence"],
        ))
    # confidence 降序 → date 降序（None 信心排最后）
    catalysts.sort(key=_catalyst_sort_key, reverse=True)

    # ── 热度榜评分：score = mentions × recency_decay ──
    scores: list[SectorHeat] = []
    for sector_name, slot in heat.items():
        mentions = slot["mentions"]
        latest = slot["latest_date"]
        # delta 钳为非负，防 latest > today（SQL 上界已挡，再加一层防御）
        delta_days = max((today - date.fromisoformat(latest)).days, 0)
        recency = max(math.exp(-delta_days / lookback_days), DECAY_FLOOR)
        scores.append(SectorHeat(
            sector_name=sector_name,
            mentions=mentions,
            recency_decay=recency,
            score=mentions * recency,
            latest_date=latest,
        ))
    scores.sort(key=lambda s: s.score, reverse=True)

    return AggregateResult(
        market_views=market_views,
        sectors=scores[:top_k],
        catalysts=catalysts,
        window_start=window_start,
        window_end=window_end,
        lookback_days=lookback_days,
    )


def _catalyst_sort_key(c: Catalyst) -> tuple[float, str]:
    """catalysts 排序键：confidence 降序（None 视为 -1.0 排最后）→ date 降序。

    单独成函数（而非 lambda 内联）以避免 _parse_confidence 被调两次。
    """
    conf = _parse_confidence(c.confidence)
    return (conf if conf is not None else -1.0, c.date)


def _trim(text: str | None) -> str | None:
    """裁剪长文本以便钉钉单卡可读。

    SNIPPET_TRUNC 是「内容字数」预算；省略号 "…" 是有意额外附加的 1 字标记
    （即超长结果为 121 字），不计入内容预算。钉钉单卡容限远大于此，无需精确到 120。
    """
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > SNIPPET_TRUNC:
        return text[:SNIPPET_TRUNC] + "…"
    return text
