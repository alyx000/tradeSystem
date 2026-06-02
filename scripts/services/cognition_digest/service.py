"""编排：窗口换算 → 采集 → 打分 → 建议 → 渲染。纯读，不写库、不依赖 provider。"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import collector, narrator, renderer, scorer
from .windows import resolve_window, window_bounds


@dataclass
class RenderedCognitionDigest:
    title: str
    markdown: str
    ranked: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    suggestions: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        # (self.stats or {}) 兜底：stats 显式传 None 时不抛 AttributeError（codex 轻微项）
        return not self.ranked and (self.stats or {}).get("instances", 0) == 0


def run_window_digest(db_path, window: str, anchor_date: str, *,
                      no_llm: bool = False, llm_runner=None) -> RenderedCognitionDigest:
    spec = resolve_window(window)
    start, end = window_bounds(spec, anchor_date)
    data = collector.collect(db_path, start, end)
    scored = scorer.score_activities(
        data.activities, anchor=end, start=start,
        lookback_days=spec.lookback_days, top_n=spec.top_n,
    )
    stats = {
        "active": len(data.activities),
        "new": sum(1 for a in data.activities if start <= a.created_at[:10] <= end),
        "instances": data.total_instances,
        "teachers": len(data.teacher_names),
    }
    suggestions = narrator.generate_suggestions(scored, no_llm=no_llm, llm_runner=llm_runner)
    title, markdown = renderer.render_md(
        spec, start, end, scored, stats, suggestions,
        llm_used=suggestions.get("_llm_used", False),
    )
    return RenderedCognitionDigest(title, markdown, scored, stats, suggestions)
