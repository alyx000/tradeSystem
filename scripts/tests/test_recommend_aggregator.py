"""TDD: 行业推荐聚合服务单测（三列表重构后）

aggregate() 把三种本质不同的数据拆成三个独立列表：
- market_views ← teacher_notes.core_view（大盘观点，去重置顶）
- sectors      ← teacher_notes.sectors 提及次数（热度榜，score = mentions × recency_decay）
- catalysts    ← industry_info（行业催化，按 confidence → date 倒序）

核心回归：core_view 不再贴到任何板块当摘要；industry_info 不再并入热度榜。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from db.schema import init_schema


@pytest.fixture
def conn(tmp_path):
    """tmp_path SQLite + 初始化全 schema 的隔离连接。"""
    db_path = tmp_path / "test_recommend.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _insert_teacher_note(conn: sqlite3.Connection, *, the_date: str, sectors_json: str,
                         title: str = "测试观点", core_view: str | None = "test core") -> int:
    """工厂：插入 1 条 teacher_notes 并返回 id。"""
    cur = conn.execute(
        "INSERT INTO teacher_notes (title, date, sectors, core_view, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, the_date, sectors_json, core_view, "tdd-test"),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_industry_info(conn: sqlite3.Connection, *, the_date: str, sector_name: str,
                          confidence, content: str = "test") -> int:
    """工厂：插入 1 条 industry_info 并返回 id。confidence 可为 高/中/低/数值/None。"""
    cur = conn.execute(
        "INSERT INTO industry_info (date, sector_name, content, confidence, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (the_date, sector_name, content, confidence, "tdd-test"),
    )
    conn.commit()
    return int(cur.lastrowid)


# ─────────────────────────────────────────────────────────────
# T1: 单笔记单行业 → 热度榜骨架 + core_view 进 market_views
# ─────────────────────────────────────────────────────────────
def test_t1_single_note_single_sector(conn):
    today = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体"]', core_view="半导体逻辑")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    item = result.sectors[0]
    assert item.sector_name == "半导体"
    assert item.mentions == 1
    assert item.recency_decay == pytest.approx(1.0, abs=0.01)
    # score = mentions × recency_decay = 1 × 1.0 = 1.0（已去掉 confidence 因子）
    assert item.score == pytest.approx(1.0, abs=0.01)
    # 回归：热度榜条目不再带 snippets / avg_confidence
    assert not hasattr(item, "snippets")
    assert not hasattr(item, "avg_confidence")
    # core_view 进 market_views，不进板块
    assert [mv.text for mv in result.market_views] == ["半导体逻辑"]
    # 无 industry_info → catalysts 空
    assert result.catalysts == []


# ─────────────────────────────────────────────────────────────
# T2: 多笔记跨行业累加（回归保护多行累加逻辑）
# ─────────────────────────────────────────────────────────────
def test_t2_multiple_notes_cross_sectors(conn):
    """A=[半导体]、B=[半导体,军工]、C=[军工] → 两行业 mentions 各 2，score 相等。"""
    today = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体"]', title="A", core_view="观点A")
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体", "军工"]', title="B", core_view="观点B")
    _insert_teacher_note(conn, the_date=today, sectors_json='["军工"]', title="C", core_view="观点C")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    sectors_by_name = {s.sector_name: s for s in result.sectors}
    assert set(sectors_by_name.keys()) == {"半导体", "军工"}
    assert sectors_by_name["半导体"].mentions == 2
    assert sectors_by_name["军工"].mentions == 2
    assert sectors_by_name["半导体"].score == pytest.approx(sectors_by_name["军工"].score, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T3: industry_info 进 catalysts，不进热度榜
# ─────────────────────────────────────────────────────────────
def test_t3_industry_info_goes_to_catalysts_not_heat(conn):
    """仅 industry_info 两条 → 热度榜空，catalysts 收 2 条。"""
    today = date.today().isoformat()
    _insert_industry_info(conn, the_date=today, sector_name="新能源", confidence="高", content="催化A")
    _insert_industry_info(conn, the_date=today, sector_name="新能源", confidence="中", content="催化B")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    # industry_info 不再并入热度榜
    assert result.sectors == []
    # 两条都进 catalysts
    assert len(result.catalysts) == 2
    assert {c.sector_name for c in result.catalysts} == {"新能源"}
    assert {c.content for c in result.catalysts} == {"催化A", "催化B"}


# ─────────────────────────────────────────────────────────────
# T4: 窗口外过滤 + recency 用最新日期（teacher_notes 热度榜）
# ─────────────────────────────────────────────────────────────
def test_t4_window_filter_and_recency_uses_latest(conn):
    """军工：今日 + 3 天前；AI：5 天前。lookback=3 → 军工进窗口 mentions=2、recency≈1.0；AI 不出现。"""
    today = date.today()
    today_iso = today.isoformat()
    three_days_ago = (today - timedelta(days=3)).isoformat()
    five_days_ago = (today - timedelta(days=5)).isoformat()

    _insert_teacher_note(conn, the_date=today_iso, sectors_json='["军工"]', title="今日军工")
    _insert_teacher_note(conn, the_date=three_days_ago, sectors_json='["军工"]', title="3天前军工")
    _insert_teacher_note(conn, the_date=five_days_ago, sectors_json='["AI"]', title="5天前AI")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    sector_names = [s.sector_name for s in result.sectors]
    assert sector_names == ["军工"]  # AI 完全不出现
    item = result.sectors[0]
    assert item.mentions == 2
    assert item.latest_date == today_iso  # 最新日期，不是平均
    assert item.recency_decay == pytest.approx(1.0, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T5: Top K 截断 + score 严格倒序（mentions 梯度）
# ─────────────────────────────────────────────────────────────
def test_t5_top_k_truncation_and_score_ordering(conn):
    """8 个行业 mentions 8..1 → top_k=5 返回 [A..E]，score 单调递减。"""
    today_iso = date.today().isoformat()
    for idx, name in enumerate(["A", "B", "C", "D", "E", "F", "G", "H"]):
        mentions_count = 8 - idx  # A 提 8 次（最高分），H 提 1 次（最低分）
        for n in range(mentions_count):
            _insert_teacher_note(conn, the_date=today_iso, sectors_json=f'["{name}"]', title=f"{name}-{n}")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 5
    sector_names = [s.sector_name for s in result.sectors]
    assert sector_names == ["A", "B", "C", "D", "E"]   # 严格倒序
    scores = [s.score for s in result.sectors]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))


# ─────────────────────────────────────────────────────────────
# T6: catalysts 按 confidence 倒序（高 > 中 > 低 > None）
# 复用 _parse_confidence 的 TEXT 标签映射做排序键
# ─────────────────────────────────────────────────────────────
def test_t6_catalysts_ordered_by_confidence_desc(conn):
    """同日 4 条不同 confidence → catalysts 顺序应为 高 > 中 > 低 > None。"""
    today_iso = date.today().isoformat()
    _insert_industry_info(conn, the_date=today_iso, sector_name="低板块", confidence="低", content="低")
    _insert_industry_info(conn, the_date=today_iso, sector_name="无信心板块", confidence=None, content="无")
    _insert_industry_info(conn, the_date=today_iso, sector_name="高板块", confidence="高", content="高")
    _insert_industry_info(conn, the_date=today_iso, sector_name="中板块", confidence="中", content="中")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    ordered = [c.sector_name for c in result.catalysts]
    assert ordered == ["高板块", "中板块", "低板块", "无信心板块"]


# ─────────────────────────────────────────────────────────────
# T6b: catalysts 同 confidence 时按 date 倒序（次级排序键）
# ─────────────────────────────────────────────────────────────
def test_t6b_catalysts_same_confidence_ordered_by_date_desc(conn):
    """3 条 confidence 同为「中」、日期 t-2/t-0/t-1 乱序插入 → 输出按 date 倒序。"""
    today = date.today()
    _insert_industry_info(conn, the_date=(today - timedelta(days=2)).isoformat(),
                          sector_name="X", confidence="中", content="t-2")
    _insert_industry_info(conn, the_date=today.isoformat(),
                          sector_name="Y", confidence="中", content="t-0")
    _insert_industry_info(conn, the_date=(today - timedelta(days=1)).isoformat(),
                          sector_name="Z", confidence="中", content="t-1")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert [c.content for c in result.catalysts] == ["t-0", "t-1", "t-2"]


# ─────────────────────────────────────────────────────────────
# T7: 三路数据各归各位（核心回归）
# core_view→market_views、content→catalysts、热度榜仅来自 teacher mentions
# ─────────────────────────────────────────────────────────────
def test_t7_three_sources_routed_separately(conn):
    """半导体：1 条 teacher_note(core_view) + 1 条 industry_info(content)。
    预期：热度榜 mentions=1（不被 industry_info +1）、无 snippets；
    core_view 只进 market_views；content 只进 catalysts。
    """
    today_iso = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today_iso, sectors_json='["半导体"]',
                         title="周观点", core_view="半导体周期底部回升")
    _insert_industry_info(conn, the_date=today_iso, sector_name="半导体",
                          confidence="高", content="存储芯片涨价 30%")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    # 热度榜：半导体只计 teacher 提及 1 次（industry_info 不 +1）
    assert len(result.sectors) == 1
    assert result.sectors[0].sector_name == "半导体"
    assert result.sectors[0].mentions == 1
    assert not hasattr(result.sectors[0], "snippets")
    # core_view 只进 market_views
    assert [mv.text for mv in result.market_views] == ["半导体周期底部回升"]
    # content 只进 catalysts
    assert len(result.catalysts) == 1
    assert result.catalysts[0].content == "存储芯片涨价 30%"
    assert result.catalysts[0].sector_name == "半导体"


# ─────────────────────────────────────────────────────────────
# T7b: catalysts 不截断（industry_info 覆盖稀疏，全量保留）
# ─────────────────────────────────────────────────────────────
def test_t7b_catalysts_not_truncated(conn):
    """同行业 5 条 industry_info → catalysts 全 5 条（不截断）。"""
    today_iso = date.today().isoformat()
    for i in range(5):
        _insert_industry_info(conn, the_date=today_iso, sector_name="AI",
                              confidence="中", content=f"催化 {i}")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert result.sectors == []           # industry_info 不进热度榜
    assert len(result.catalysts) == 5     # 全量保留，不截断


# ─────────────────────────────────────────────────────────────
# T8: 未来日期不进窗口 + recency_decay ≤ 1
# ─────────────────────────────────────────────────────────────
def test_t8_future_date_excluded_and_recency_clamped(conn):
    today = date.today()
    today_iso = today.isoformat()
    tomorrow_iso = (today + timedelta(days=1)).isoformat()   # 用户错填未来日期
    _insert_teacher_note(conn, the_date=today_iso, sectors_json='["军工"]', title="今日")
    _insert_teacher_note(conn, the_date=tomorrow_iso, sectors_json='["未来板块"]', title="明日错填")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    sector_names = [s.sector_name for s in result.sectors]
    assert "未来板块" not in sector_names, "未来日期不应进入窗口"
    assert "军工" in sector_names
    for s in result.sectors:
        assert s.recency_decay <= 1.0001, f"{s.sector_name} recency_decay > 1: {s.recency_decay}"


# ─────────────────────────────────────────────────────────────
# T9: sectors JSON 内重复元素去重
# ─────────────────────────────────────────────────────────────
def test_t9_duplicate_sectors_in_same_note_dedup(conn):
    """同一条 note sectors=["半导体","半导体","半导体"] → mentions=1 不是 3。"""
    today_iso = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today_iso,
                         sectors_json='["半导体", "半导体", "半导体"]',
                         title="重复填写")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    assert result.sectors[0].mentions == 1


# ─────────────────────────────────────────────────────────────
# T10: market_views 按时间倒序（最近优先）
# ─────────────────────────────────────────────────────────────
def test_t10_market_views_ordered_by_date_desc(conn):
    """3 天内 3 条不同 core_view → market_views[0] 是最新的。"""
    today = date.today()
    _insert_teacher_note(conn, the_date=(today - timedelta(days=2)).isoformat(),
                         sectors_json='["AI"]', title="t-2", core_view="2 天前观点")
    _insert_teacher_note(conn, the_date=(today - timedelta(days=1)).isoformat(),
                         sectors_json='["AI"]', title="t-1", core_view="1 天前观点")
    _insert_teacher_note(conn, the_date=today.isoformat(),
                         sectors_json='["AI"]', title="t-0", core_view="今日观点")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    texts = [mv.text for mv in result.market_views]
    assert texts == ["今日观点", "1 天前观点", "2 天前观点"]


# ─────────────────────────────────────────────────────────────
# T11: market_views 去重（相同文本只保留 1 条，且保留最新那条）
# ─────────────────────────────────────────────────────────────
def test_t11_market_views_dedup_keeps_latest(conn):
    """两条 note core_view 文本完全相同、日期不同 → 只保留 1 条，且是最新那条。"""
    today = date.today()
    _insert_teacher_note(conn, the_date=today.isoformat(), sectors_json='["AI"]',
                         title="A", core_view="完全相同的大盘观点")
    _insert_teacher_note(conn, the_date=(today - timedelta(days=1)).isoformat(),
                         sectors_json='["半导体"]', title="B", core_view="完全相同的大盘观点")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert [mv.text for mv in result.market_views] == ["完全相同的大盘观点"]
    # 去重保留的是最新日期那条，而非最旧
    assert result.market_views[0].date == today.isoformat()


# ─────────────────────────────────────────────────────────────
# T12: core_view 为空/None 不进 market_views，但 mentions 照常计
# ─────────────────────────────────────────────────────────────
def test_t12_empty_core_view_skipped_but_mention_counted(conn):
    today_iso = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today_iso, sectors_json='["半导体"]',
                         title="无核心观点", core_view=None)

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert result.market_views == []          # None core_view 跳过
    assert len(result.sectors) == 1           # 但 mentions 照常
    assert result.sectors[0].mentions == 1


# ─────────────────────────────────────────────────────────────
# T13: 空库 → 三列表均空（占位由 formatter 负责）
# ─────────────────────────────────────────────────────────────
def test_t13_empty_db_returns_three_empty_lists(conn):
    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert result.market_views == []
    assert result.sectors == []
    assert result.catalysts == []


# ─────────────────────────────────────────────────────────────
# T14 (codex 严重 2): 只写大盘观点、未标板块的笔记 → core_view 仍进 market_views
# ─────────────────────────────────────────────────────────────
def test_t14_note_without_sectors_still_collects_market_view(conn):
    """sectors=NULL 的纯大盘观点笔记：core_view 进 market_views，但不产生热度榜条目。"""
    today_iso = date.today().isoformat()
    conn.execute(
        "INSERT INTO teacher_notes (title, date, sectors, core_view, input_by) "
        "VALUES (?, ?, NULL, ?, ?)",
        ("纯大盘观点", today_iso, "只有大盘判断没标板块", "tdd-test"),
    )
    conn.commit()

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert [mv.text for mv in result.market_views] == ["只有大盘判断没标板块"]
    assert result.sectors == []   # 无 sectors → 不进热度榜
