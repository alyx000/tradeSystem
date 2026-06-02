"""热度+共识+新增 打分排序（无验证信号场景）。常量化魔法数，单测锁定。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

HEAT_W = 1.0       # 实例条数权重（被反复印证强度）
CONSENSUS_W = 0.8  # distinct 老师数权重（多老师共识）
CONF_W = 0.5       # confidence 权重
NEW_BONUS = 0.6    # 本期新捕获加分
RECENCY_W = 0.4    # 时近衰减权重
DECAY_FLOOR = 0.2  # recency 衰减地板


@dataclass
class ScoredCognition:
    cognition_id: str
    title: str
    category: str
    sub_category: str | None
    pattern: str | None
    confidence: float
    heat: int
    consensus: int
    is_new: bool
    score: float
    created_at: str = ""  # 仅用于排序并列兜底，默认空（renderer 不展示）


def _distinct_teachers(instances: list[dict]) -> int:
    ids: set = set()
    names: set = set()
    for it in instances:
        tid = it.get("teacher_id")
        if tid is not None:
            ids.add(tid)
        elif it.get("teacher_name"):
            names.add(it["teacher_name"])
    return len(ids) + len(names)


def _recency_decay(instances: list[dict], anchor: str, lookback_days: int) -> float:
    if not instances:
        return 0.0
    anchor_d = datetime.date.fromisoformat(anchor)
    latest = max(datetime.date.fromisoformat(it["observed_date"]) for it in instances)
    days_since = (anchor_d - latest).days
    return max(DECAY_FLOOR, 1.0 - days_since / lookback_days)


def score_activities(activities, *, anchor: str, start: str,
                     lookback_days: int, top_n: int) -> list[ScoredCognition]:
    scored: list[ScoredCognition] = []
    for a in activities:
        heat = len(a.instances)
        consensus = _distinct_teachers(a.instances)
        is_new = start <= a.created_at[:10] <= anchor
        recency = _recency_decay(a.instances, anchor, lookback_days)
        score = (
            HEAT_W * heat
            + CONSENSUS_W * consensus
            + CONF_W * a.confidence
            + NEW_BONUS * (1 if is_new else 0)
            + RECENCY_W * recency
        )
        scored.append(
            ScoredCognition(
                cognition_id=a.cognition_id, title=a.title, category=a.category,
                sub_category=a.sub_category, pattern=a.pattern, confidence=a.confidence,
                heat=heat, consensus=consensus, is_new=is_new, score=round(score, 4),
                created_at=a.created_at,
            )
        )
    # 并列兜底：score → heat → consensus → created_at（更新者靠前），全 reverse
    scored.sort(key=lambda s: (s.score, s.heat, s.consensus, s.created_at), reverse=True)
    return scored[:top_n]
