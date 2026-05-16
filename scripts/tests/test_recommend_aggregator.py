"""TDD: 行业推荐聚合服务单测

每一条测试 = 一个 R-G-R 微循环 = 一次 commit。
按 plan 第 ⑪ 节的 5 个场景顺序逐个驱动 aggregator 实现。
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
                         title: str = "测试观点", core_view: str = "test core") -> int:
    """工厂：插入 1 条 teacher_notes 并返回 id。"""
    cur = conn.execute(
        "INSERT INTO teacher_notes (title, date, sectors, core_view, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, the_date, sectors_json, core_view, "tdd-test"),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_industry_info(conn: sqlite3.Connection, *, the_date: str, sector_name: str,
                          confidence: float | None, content: str = "test") -> int:
    """工厂：插入 1 条 industry_info 并返回 id。"""
    cur = conn.execute(
        "INSERT INTO industry_info (date, sector_name, content, confidence, input_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (the_date, sector_name, content, confidence, "tdd-test"),
    )
    conn.commit()
    return int(cur.lastrowid)


# ─────────────────────────────────────────────────────────────
# T1: 单笔记单行业 → 驱动 aggregate() 与 SectorScore/AggregateResult 骨架
# ─────────────────────────────────────────────────────────────
def test_t1_single_note_single_sector(conn):
    today = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体"]')

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    item = result.sectors[0]
    assert item.sector_name == "半导体"
    assert item.mentions == 1
    assert item.avg_confidence == pytest.approx(0.5)
    assert item.recency_decay == pytest.approx(1.0, abs=0.01)
    # score = 1 × (0.7 + 0.3 × 0.5) × 1.0 = 0.85
    assert item.score == pytest.approx(0.85, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T2: 多笔记跨行业 + 交叉（回归保护：T1 的 dict 累加实现已覆盖）
# ─────────────────────────────────────────────────────────────
def test_t2_multiple_notes_cross_sectors(conn):
    """3 条笔记交叉：A=[半导体]、B=[半导体,军工]、C=[军工]。
    两行业 mentions 各 2，score 相等。本测试守护回归 —— 若未来谁退掉
    aggregator 的多行累加逻辑，这条会立刻爆红。
    """
    today = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体"]', title="A")
    _insert_teacher_note(conn, the_date=today, sectors_json='["半导体", "军工"]', title="B")
    _insert_teacher_note(conn, the_date=today, sectors_json='["军工"]', title="C")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    sectors_by_name = {s.sector_name: s for s in result.sectors}
    assert set(sectors_by_name.keys()) == {"半导体", "军工"}
    assert sectors_by_name["半导体"].mentions == 2
    assert sectors_by_name["军工"].mentions == 2
    assert sectors_by_name["半导体"].score == pytest.approx(sectors_by_name["军工"].score, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T3: industry_info + 信心因子 → 驱动 industry_info 一路 SQL + avg_confidence
# ─────────────────────────────────────────────────────────────
def test_t3_industry_info_with_confidence(conn):
    """仅 industry_info 一路，2 条 confidence 0.9 / 0.8 → avg=0.85；mentions=2。
    T1 实现仅读 teacher_notes，本测试预期 RED：mentions=0 或 KeyError。
    """
    today = date.today().isoformat()
    _insert_industry_info(conn, the_date=today, sector_name="新能源", confidence=0.9)
    _insert_industry_info(conn, the_date=today, sector_name="新能源", confidence=0.8)

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    item = result.sectors[0]
    assert item.sector_name == "新能源"
    assert item.mentions == 2
    assert item.avg_confidence == pytest.approx(0.85, abs=0.01)
    # score = 2 × (0.7 + 0.3 × 0.85) × 1.0 = 2 × 0.955 × 1.0 = 1.91
    assert item.score == pytest.approx(1.91, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T4: 窗口外过滤 + recency 用最新日期（不是平均）
# ─────────────────────────────────────────────────────────────
def test_t4_window_filter_and_recency_uses_latest(conn):
    """军工：今日 + 3 天前；AI：5 天前。lookback=3。
    预期：军工进窗口，mentions=2；recency 用今日（最新）算 ≈ 1.0；
    AI 完全不出现（SQL WHERE date >= today-3 过滤）。
    """
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
# T5: Top K 截断 + score 严格倒序
# ─────────────────────────────────────────────────────────────
def test_t5_top_k_truncation_and_score_ordering(conn):
    """8 个不同行业各 1 条，但 mentions/confidence 不同造成 score 分级。
    top_k=5 → 返回长度=5；按 score 严格倒序。
    """
    today_iso = date.today().isoformat()
    # 故意让 score 排列：A(industry_info conf=1.0 mentions=1) > B(conf=0.5) > ... > H
    # 用 mentions 数量制造梯度更直观
    for idx, name in enumerate(["A", "B", "C", "D", "E", "F", "G", "H"]):
        mentions_count = 8 - idx  # A 提 8 次（虚高但最高分），H 提 1 次（最低分）
        for _ in range(mentions_count):
            _insert_teacher_note(conn, the_date=today_iso, sectors_json=f'["{name}"]', title=f"{name}-{_}")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 5
    sector_names = [s.sector_name for s in result.sectors]
    assert sector_names == ["A", "B", "C", "D", "E"]   # 严格倒序
    # 校验 score 单调递减
    scores = [s.score for s in result.sectors]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))


# ─────────────────────────────────────────────────────────────
# T6: industry_info.confidence 实为 TEXT (高/中/低/None) 而非数值
# 端到端 dry-run 发现的真实数据 schema 失配，回归保护这个映射
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("conf_label, expected_value", [
    ("高", 0.9),
    ("中", 0.5),
    ("低", 0.3),
    (None, 0.5),     # None 走缺省
    ("0.85", 0.85),  # 兼容数值字符串（未来若改 schema）
])
def test_t6_confidence_text_label_mapping(conn, conf_label, expected_value):
    today_iso = date.today().isoformat()
    _insert_industry_info(conn, the_date=today_iso, sector_name="测试行业",
                          confidence=conf_label)

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    assert result.sectors[0].avg_confidence == pytest.approx(expected_value, abs=0.01)


# ─────────────────────────────────────────────────────────────
# T7: snippets 收集（plan 验收清单：每行业至少 1 条原文摘要）
# ─────────────────────────────────────────────────────────────
def test_t7_snippets_collected_from_both_sources(conn):
    """半导体行业有 1 条 teacher_notes (core_view) + 1 条 industry_info (content)。
    预期 snippets 至少含两条原文（每路一条），并保持顺序。
    """
    today_iso = date.today().isoformat()
    _insert_teacher_note(conn, the_date=today_iso, sectors_json='["半导体"]',
                         title="周观点", core_view="半导体周期底部回升")
    _insert_industry_info(conn, the_date=today_iso, sector_name="半导体",
                          confidence="高", content="存储芯片涨价 30%")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    snippets = result.sectors[0].snippets
    assert len(snippets) >= 2
    joined = " | ".join(snippets)
    assert "半导体周期底部回升" in joined   # core_view 来源
    assert "存储芯片涨价 30%" in joined      # content 来源


def test_t7b_snippets_truncated_to_three(conn):
    """同一行业 5 条记录 → snippets 截断到最多 3 条。"""
    today_iso = date.today().isoformat()
    for i in range(5):
        _insert_industry_info(conn, the_date=today_iso, sector_name="AI",
                              confidence="中", content=f"摘要 {i}")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    assert len(result.sectors[0].snippets) == 3


# ─────────────────────────────────────────────────────────────
# T8 (Codex review 严重 2): 未来日期不进窗口 + recency_decay ≤ 1
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
    # 所有结果的 recency_decay ≤ 1.0（delta 钳为非负）
    for s in result.sectors:
        assert s.recency_decay <= 1.0001, f"{s.sector_name} recency_decay > 1: {s.recency_decay}"


# ─────────────────────────────────────────────────────────────
# T9 (Codex review 中等 3): sectors JSON 内重复元素去重
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
# T10 (Codex review 中等 4): snippets 按时间倒序（最近优先）
# ─────────────────────────────────────────────────────────────
def test_t10_snippets_ordered_by_date_desc(conn):
    """3 天内同一行业 3 条 industry_info 不同日期 → snippets[0] 是最新的。"""
    today = date.today()
    _insert_industry_info(conn, the_date=(today - timedelta(days=2)).isoformat(),
                          sector_name="AI", confidence="高", content="2 天前摘要")
    _insert_industry_info(conn, the_date=(today - timedelta(days=1)).isoformat(),
                          sector_name="AI", confidence="高", content="1 天前摘要")
    _insert_industry_info(conn, the_date=today.isoformat(),
                          sector_name="AI", confidence="高", content="今日摘要")

    from services.recommend.aggregator import aggregate

    result = aggregate(conn, lookback_days=3, top_k=5)

    assert len(result.sectors) == 1
    snippets = result.sectors[0].snippets
    assert len(snippets) == 3
    # 最新日期的摘要应排在最前
    assert snippets[0] == "今日摘要"
    assert snippets[1] == "1 天前摘要"
    assert snippets[2] == "2 天前摘要"
