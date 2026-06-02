from __future__ import annotations
from services.cognition_digest import renderer
from services.cognition_digest.windows import resolve_window
from services.cognition_digest.scorer import ScoredCognition


def _sc(cid="c1", *, is_new=True):
    return ScoredCognition(
        cognition_id=cid, title=f"认知{cid}", category="signal", sub_category="rotation",
        pattern="高位退潮回流低位", confidence=0.6, heat=3, consensus=2,
        is_new=is_new, score=1.0,
    )


def _stats(**kw):
    base = {"active": 1, "new": 1, "instances": 3, "teachers": 2}
    base.update(kw)
    return base


def test_render_has_sections():
    spec = resolve_window("weekly")
    sug = {"system_suggestions": ["加强验证"], "direction_suggestions": ["聚焦算力"]}
    title, md = renderer.render_md(spec, "2026-05-27", "2026-06-02",
                                   [_sc()], _stats(), sug, llm_used=True)
    assert "近1周" in title
    assert "概览" in md and "活跃认知 1 条" in md
    assert "🏆 值得沉淀 Top1" in md
    assert "加强验证" in md and "聚焦算力" in md
    assert "LLM" in md


def test_render_empty_window_graceful():
    spec = resolve_window("recent3d")
    sug = {"system_suggestions": [], "direction_suggestions": []}
    title, md = renderer.render_md(spec, "2026-05-31", "2026-06-02",
                                   [], _stats(active=0, new=0, instances=0, teachers=0),
                                   sug, llm_used=False)
    assert "无新增认知沉淀" in md
    assert "🏆" not in md


def test_render_no_llm_marker():
    spec = resolve_window("monthly")
    sug = {"system_suggestions": ["x"], "direction_suggestions": []}
    _, md = renderer.render_md(spec, "2026-05-04", "2026-06-02",
                               [_sc()], _stats(), sug, llm_used=False)
    assert "纯结构化" in md
