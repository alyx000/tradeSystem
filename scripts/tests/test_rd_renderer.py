"""research_digest.renderer：红线运行时拦截（H1）+ 中性化 + 不输出目标价 + mkdir 落盘（H4）。"""
from __future__ import annotations

from pathlib import Path

from services.research_digest import renderer


def _us(**kw):
    base = {"market": "US", "ticker": "NVDA", "firm": "Morgan Stanley",
            "action": "up", "to_grade": "Buy", "grade_date": "2026-05-29"}
    base.update(kw)
    return base


def test_us_renders_theme_not_one_liner():
    """美股只渲染 LLM 的 theme（板块归类）；one_liner 收紧后与 theme+action 冗余，已砍不渲染。"""
    us = [_us(theme="AI算力", one_liner="AI算力·评级上调")]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    assert "AI算力" in md                  # theme 渲染
    assert "AI算力·评级上调" not in md      # one_liner 不渲染（冗余已砍）


def test_redline_theme_dropped():
    """theme 命中红线（目标价等）即丢，不 surface。"""
    us = [_us(theme="目标价上看")]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    assert "目标价" not in md


def test_no_target_price_numbers_in_output():
    us = [_us(current_pt=425.0, prior_pt=360.0)]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    assert "425" not in md and "360" not in md


def test_neutralize_rating():
    assert renderer.neutralize_rating("买入") == "偏多档"
    assert renderer.neutralize_rating("Underweight") == "偏空档"
    assert renderer.neutralize_rating("Hold") == "中性档"


def test_empty_sections_explicit_placeholders():
    _, md = renderer.render_md("2026-05-29", [], [], [])
    assert "今日两市均无符合条件" in md
    assert "今日 A股无研报评级数据" in md
    assert "今日美股无符合条件的评级变动" in md


def test_cn_redline_theme_dropped():
    """A股 narration（theme，cn_narrate 开时）命中红线即丢。"""
    cn = [{"market": "A", "stock_code": "600519", "stock_name": "贵州茅台",
           "org_count": 3, "rating_changes": ["调高"], "theme": "满仓必涨"}]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "满仓" not in md and "必涨" not in md


def test_english_redline_target_price_dropped():
    """M4：英文 target price 也拦截（theme 英文盲区）。"""
    us = [_us(theme="target price")]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    assert "target price" not in md.lower()


def test_sell_side_not_false_killed():
    """不误杀中性术语 sell-side（_RD_REDLINE_EXTRA_EN 不收裸 buy/sell）。"""
    assert renderer._scan_redline("sell-side 普遍看好") is None
    us = [_us(theme="sell-side 看好")]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    assert "sell-side 看好" in md


def _cn(**kw):
    base = {"market": "A", "stock_code": "600030", "stock_name": "中信证券",
            "org_count": 6, "rating_changes": ["维持"], "signals": ["多家覆盖"]}
    base.update(kw)
    return base


def test_cn_viewpoint_title_rendered_with_source():
    """A股 viewpoint（真实研报标题，事实层）渲染进 A股段 + Top 行，带出处机构。"""
    cn = [_cn(viewpoint={"title": "再融资落地，业务全面高增", "institution": "东吴证券", "date": "2026-05-29"})]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "再融资落地，业务全面高增" in md
    assert "东吴证券" in md


def test_cn_viewpoint_absent_no_crash_no_tail():
    """无 viewpoint → A股行不带观点尾，不报错。"""
    cn = [_cn()]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "中信证券" in md and "观点：" not in md


def test_cn_viewpoint_redline_title_dropped():
    """出口兜底：标题含目标价/买入（极少数）→ 整条观点丢，不 surface（系统最硬红线）。"""
    cn = [_cn(viewpoint={"title": "首次覆盖，目标价 120 元，给予买入", "institution": "东吴", "date": "2026-05-29"})]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "目标价" not in md and "买入" not in md and "120" not in md
    assert "观点：" not in md  # 整条观点被丢，不残留半句


def test_signal_badge_first_coverage_prominent():
    """首次覆盖渲染为 🆕 **首次覆盖**（emoji+加粗）显著突出；多家覆盖 emoji 不加粗（不抢焦点）。"""
    cn = [_cn(signals=["首次覆盖", "多家覆盖"])]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "🆕 **首次覆盖**" in md
    assert "👥 多家覆盖" in md
    assert "〔多家覆盖〕" not in md  # 旧朴素括号格式已弃


def test_signal_badge_rendered_in_us_section():
    """美股正文行也带信号徽章（此前仅 Top3 有）；init→🆕 首次覆盖。"""
    us = [_us(action="init", signals=["首次覆盖"])]
    _, md = renderer.render_md("2026-05-29", [], us, us)
    us_section = md.split("🇺🇸")[1]
    assert "🆕 **首次覆盖**" in us_section


def test_strong_hint_phrases_emphasized_in_viewpoint():
    """鞠磊②：研报观点里的强提示词（重点推荐/重点关注/重点跟踪）就地加粗突出。"""
    cn = [_cn(viewpoint={"title": "重点推荐：AI算力主线，建议重点关注与重点跟踪", "institution": "中信", "date": "2026-05-29"})]
    _, md = renderer.render_md("2026-05-29", cn, [], cn)
    assert "**重点推荐**" in md
    assert "**重点关注**" in md
    assert "**重点跟踪**" in md


def test_emphasize_hints_no_phrase_unchanged():
    assert renderer._emphasize_hints("2026年一季报点评：业绩高增") == "2026年一季报点评：业绩高增"
    assert renderer._emphasize_hints("") == ""


def test_redline_hit_does_not_leak_raw_text_to_log(caplog):
    """codex 轻微：命中红线丢弃时，原文（含目标价数值/操作词）不得写进日志旁路（/tmp/*.log 本机可读）。"""
    import logging
    cn = [_cn(viewpoint={"title": "首次覆盖，目标价 120 元，给予买入", "institution": "东吴", "date": "2026-05-29"})]
    with caplog.at_level(logging.WARNING):
        renderer.render_md("2026-05-29", cn, [], cn)
    log_text = caplog.text
    assert "120" not in log_text and "给予买入" not in log_text  # 原文不入日志
    assert "命中红线" in log_text  # 但仍记录命中事实（含通用关键词）供排障


def test_write_md_creates_dir(tmp_path):
    out_root = str(tmp_path / "nested" / "research-digest")
    p = renderer.write_md("# hi\n内容", "2026-05-29", out_root=out_root)
    assert Path(p).exists()
    assert Path(p).name == "2026-05-29.md"
    assert Path(p).read_text(encoding="utf-8") == "# hi\n内容"
