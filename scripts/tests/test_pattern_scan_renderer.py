"""形态篇观察清单渲染单测：红线、出处、口径声明与三种结果分支。"""
from __future__ import annotations

from services.pattern_scan import constants as C
from services.pattern_scan import renderer


def _candidate(**over) -> dict:
    base = {
        "code": "600001",
        "name": "形态A",
        "sw_l2": "半导体",
        "branch_concepts": [],
        "pct_chg": 1.23,
        "today_amount": 2.4e5,   # Tushare amount 单位=千元 → 2.4 亿元
        "ma_values": {"ma5": 25.2, "ma10": 24.8, "ma20": 24.1, "ma30": 23.4, "ma55": 22.0},
        "macd_dif": 0.4321,
        "macd_dea": 0.3210,
        "macd_golden_cross": True,
        "rhythm_groups": 3,
        "yang_above_count": 12,
        "yang_total": 15,
        "yang_above_ratio": 0.8,
        "yin_shrink_count": 4,
        "yin_total": 5,
        "bar_count": 150,
    }
    base.update(over)
    return base


def _result(**over) -> dict:
    base = {
        "status": "ok",
        "date": "2026-06-09",
        "main_sectors": ["半导体"],
        "mainline": {"status": "disabled", "main_concepts": []},
        "main_sector_degraded": False,
        "candidates": [_candidate()],
        "rejects": {"not_main_sector": 5000, "ma_not_aligned": 12},
        "data_errors": [],
        "universe_count": 435,
        "alignment_breaks": {"ma5>ma10": 300, "ma20>ma30": 12},
        "source_errors": [],
    }
    base.update(over)
    return base


class TestRedlineAndProvenance:
    def test_redline_present(self):
        md = renderer.render_daily(_result())
        assert "[判断]" in md
        assert "不构成买卖建议" in md
        assert "不含价位" in md
        assert "不写交易计划层" in md

    def test_provenance_cited(self):
        """出处必须落在报告里——形态口径来自课程，不是系统自创。"""
        md = renderer.render_daily(_result())
        assert "teacher_notes#444" in md
        assert "cog_3b32e660" in md

    def test_no_buy_sell_wording(self):
        """正文不得出现操作指令词。

        只检查引用块（`>` 开头的红线/出处声明）之外的正文：声明本身是**否定式**
        表述（「不等于应当买入」「不构成买卖建议」），对它做关键词黑名单会反向误杀
        审慎表述——红线约束的是生成的操作指令，不是否定操作指令的免责声明。
        """
        md = renderer.render_daily(_result())
        body = "\n".join(ln for ln in md.splitlines() if not ln.startswith(">"))
        for word in ("买入", "卖出", "建仓", "加仓", "止损", "目标价"):
            assert word not in body

    def test_pattern_success_is_not_stated_as_buy_signal(self):
        md = renderer.render_daily(_result())
        assert "不等于应当买入" in md


class TestKouJing:
    def test_qfq_declared(self):
        """前复权口径必须写进报告——读者要知道均线是哪个坐标系算的。"""
        md = renderer.render_daily(_result())
        assert "前复权" in md
        assert str(C.RANGE_LOOKBACK_DAYS) in md

    def test_four_conditions_listed(self):
        md = renderer.render_daily(_result())
        assert "均线多头排列" in md
        assert "零轴上方金叉或零上运行" in md
        assert "尚未加速" in md

    def test_sort_declared_as_fact_not_ranking(self):
        """排序是成交额降序，不能被读成形态强弱排名。"""
        md = renderer.render_daily(_result())
        assert "非形态强弱排名" in md


class TestBranches:
    def test_candidate_row_rendered(self):
        md = renderer.render_daily(_result())
        assert "600001" in md and "形态A" in md
        assert "2.40亿" in md
        assert "+1.23%" in md
        assert "金叉" in md
        assert "3组" in md

    def test_amount_unit_is_thousand_yuan(self):
        """成交额换算锚点：Tushare `daily.amount` 单位是千元，1 亿元 = 1e5。

        真实数据（2026-07-24 全志科技 300458）：amount=5000999.2359，
        vol=1211571.13 手，close=40.99 → 1211571×100×40.99 ≈ 49.7 亿，印证 50.01 亿。
        首版误用 /1e8 渲染成 0.05 亿（小 1000 倍），靠真跑肉眼核对才发现，故锁死。
        """
        md = renderer.render_daily(_result(
            candidates=[_candidate(today_amount=5000999.2359)]))
        assert "50.01亿" in md

    def test_concept_branch_labelled(self):
        md = renderer.render_daily(_result(
            candidates=[_candidate(branch_concepts=["CPO"])]))
        assert "半导体·分支:CPO" in md

    def test_empty_candidates(self):
        md = renderer.render_daily(_result(candidates=[]))
        assert "今日无命中" in md

    def test_source_failed_not_reported_as_empty_pool(self):
        """数据源失败必须与「筛完为空」区分，否则故障会被当成正常空清单。"""
        md = renderer.render_daily(_result(
            status="source_failed", candidates=[], source_errors=["sw_map"]))
        assert "数据源失败" in md
        assert "不代表已完成筛选后的空池" in md
        assert "今日无命中" not in md

    def test_degraded_mainline_labelled(self):
        md = renderer.render_daily(_result(main_sector_degraded=True))
        assert "已回退最近一日" in md

    def test_missing_ma_value_marked_not_fabricated(self):
        md = renderer.render_daily(_result(
            candidates=[_candidate(ma_values={"ma5": 25.2})]))
        assert "55:·" in md

    def test_alignment_breaks_rendered_desc_and_marked_diagnostic(self):
        """断点分布按计数降序，且必须标明不参与筛选（纯口径调参观测）。"""
        md = renderer.render_daily(_result())
        assert "多头排列断点分布" in md
        assert "不参与筛选" in md
        line = next(ln for ln in md.splitlines() if "多头排列断点分布" in ln)
        assert line.index("ma5>ma10:300") < line.index("ma20>ma30:12")

    def test_alignment_breaks_absent_when_empty(self):
        md = renderer.render_daily(_result(alignment_breaks={}))
        assert "多头排列断点分布" not in md

    def test_data_errors_truncated_with_count(self):
        codes = [f"60{i:04d}" for i in range(30)]
        md = renderer.render_daily(_result(data_errors=codes))
        assert "另有 10 只未列出" in md


def test_write_report(tmp_path):
    md = renderer.render_daily(_result())
    path = renderer.write_report(md, "2026-06-09", root=tmp_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == md
    assert path.parent.name == "pattern-scan"
