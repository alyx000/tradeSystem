"""
板块节奏分析器测试

覆盖场景：
  T1. _max_consecutive_in_dates — 纯函数边界验证
  T2. _compute_signals — 数据不足（series 为空）
  T3. _compute_signals — 典型启动信号
  T4. _compute_signals — 典型发酵信号
  T5. _classify_phase — 高潮判断（高置信）
  T6. _classify_phase — 首次分歧判断
  T7. _classify_phase — 衰退判断（板块掉出前30）
  T8. analyze() 端到端集成测试（临时目录 + 多天假数据）
  T9. daily/ 不存在时 _sorted_daily_dirs 不抛错，analyze 仍可跑
  T10. 连续上榜>3 天不因排名跃升被判为启动
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from analyzers.sector_rhythm import (
    PHASE_DECLINE,
    PHASE_DIVERGE,
    PHASE_FERMENT,
    PHASE_LAUNCH,
    PHASE_OSCILLATE,
    PHASE_PEAK,
    PHASE_WATCH,
    SectorRhythmAnalyzer,
    _max_consecutive_in_dates,
)


# =====================================================================
# 测试辅助函数
# =====================================================================

def _make_sector_entry(name: str, rank: int, change_pct: float, top_stock: str = "A股") -> dict:
    return {
        "name": name,
        "change_pct": change_pct,
        "volume_billion": 100.0,
        "top_stock": top_stock,
    }


def _write_post_market(day_dir: Path, date: str, sector_rows: list[dict]) -> None:
    """在 day_dir 下写入包含 sector_industry 数据的 post-market.yaml。"""
    day_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "date": date,
        "raw_data": {
            "sector_industry": {
                "_source": "test",
                "data": sector_rows,
            },
            "sector_concept": {
                "_source": "test",
                "data": [],
            },
        },
        "holdings_data": [],
    }
    with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def _build_analyzer(tmp_path: Path, history_days: int = 20) -> SectorRhythmAnalyzer:
    return SectorRhythmAnalyzer(base_dir=tmp_path, history_days=history_days)


def _make_series(dates: list[str], ranks: list[int], changes: list[float],
                 top_stocks: list[str] | None = None) -> list[dict]:
    """快捷构造板块时间序列，仅保留「上榜」记录。"""
    if top_stocks is None:
        top_stocks = ["领涨股A"] * len(dates)
    return [
        {"date": d, "rank": r, "change_pct": c, "volume_billion": 100.0, "top_stock": ts}
        for d, r, c, ts in zip(dates, ranks, changes, top_stocks)
    ]


# =====================================================================
# T1. _max_consecutive_in_dates
# =====================================================================

class TestMaxConsecutive:
    def test_all_present(self):
        by_date = {"2026-01-01", "2026-01-02", "2026-01-03"}
        all_dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert _max_consecutive_in_dates(by_date, all_dates) == 3

    def test_gap_in_middle(self):
        by_date = {"2026-01-01", "2026-01-03"}
        all_dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert _max_consecutive_in_dates(by_date, all_dates) == 1

    def test_leading_streak(self):
        by_date = {"2026-01-01", "2026-01-02", "2026-01-05"}
        all_dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
        assert _max_consecutive_in_dates(by_date, all_dates) == 2

    def test_empty_series(self):
        assert _max_consecutive_in_dates(set(), ["2026-01-01", "2026-01-02"]) == 0

    def test_empty_dates(self):
        assert _max_consecutive_in_dates({"2026-01-01"}, []) == 0

    def test_dict_input(self):
        """by_date 也可以是 dict（兼容 _compute_signals 的用法）。"""
        by_date = {"2026-01-01": 1, "2026-01-02": 2}
        all_dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert _max_consecutive_in_dates(by_date, all_dates) == 2


# =====================================================================
# T2. _compute_signals — 数据不足
# =====================================================================

class TestComputeSignalsEmpty:
    def test_empty_series_returns_data_days_zero(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        # daily/ 目录完全为空，all_dates 也为空
        sig = analyzer._compute_signals([], "2026-03-27", [])
        assert sig["data_days"] == 0

    def test_empty_series_with_dates(self, tmp_path):
        """series 为空但 all_dates 非空：data_days 反映历史天数，其余字段缺失。"""
        analyzer = _build_analyzer(tmp_path)
        all_dates = ["2026-03-25", "2026-03-26", "2026-03-27"]
        sig = analyzer._compute_signals([], "2026-03-27", all_dates)
        assert sig["data_days"] == 3
        # series 为空时提前返回，后续字段不存在
        assert "rank_today" not in sig


# =====================================================================
# T3. _compute_signals — 启动场景
# =====================================================================

class TestComputeSignalsLaunch:
    def test_launch_signals(self, tmp_path):
        """
        场景：板块最近 1 天突然进入前15，3日前不在前30。
        期望：consecutive=1, rank_today=5, rank_change_3d=None（3日前未上榜）。
        """
        analyzer = _build_analyzer(tmp_path)
        all_dates = ["2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27"]
        series = _make_series(
            dates=["2026-03-27"],
            ranks=[5],
            changes=[4.5],
        )
        sig = analyzer._compute_signals(series, "2026-03-27", all_dates)
        assert sig["consecutive_in_top30"] == 1
        assert sig["rank_today"] == 5
        assert sig["rank_change_3d"] is None  # 3日前未上榜，无法计算
        assert sig["cumulative_pct_5d"] == pytest.approx(4.5)

    def test_rank_change_computed_when_3d_present(self, tmp_path):
        """3日前有记录时，rank_change_3d 应正确计算。"""
        analyzer = _build_analyzer(tmp_path)
        all_dates = ["2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27"]
        # 3日前排名20，今日排名5 → rank_change = 5-20 = -15（上升15位）
        series = _make_series(
            dates=["2026-03-24", "2026-03-27"],
            ranks=[20, 5],
            changes=[1.0, 4.5],
        )
        sig = analyzer._compute_signals(series, "2026-03-27", all_dates)
        assert sig["rank_change_3d"] == pytest.approx(-15)
        assert sig["consecutive_in_top30"] == 1  # 25、26未上榜，中断了连续性


# =====================================================================
# T4. _compute_signals — 发酵场景
# =====================================================================

class TestComputeSignalsFerment:
    def test_ferment_signals(self, tmp_path):
        """
        场景：板块连续7天上榜，每天涨2%，同一领涨股，排名稳定在前5。
        期望：consecutive=7, cumulative_pct_5d≈10, stability=1.0。
        """
        analyzer = _build_analyzer(tmp_path)
        dates = [f"2026-03-{d:02d}" for d in range(21, 28)]  # 21~27，7天
        all_dates = dates
        series = _make_series(
            dates=dates,
            ranks=[5] * 7,
            changes=[2.0] * 7,
            top_stocks=["工业富联"] * 7,
        )
        sig = analyzer._compute_signals(series, "2026-03-27", all_dates)
        assert sig["consecutive_in_top30"] == 7
        assert sig["days_in_top30_of_20"] == 7
        assert sig["cumulative_pct_5d"] == pytest.approx(10.0)
        assert sig["top_stock_stability_5d"] == pytest.approx(1.0)

    def test_consecutive_breaks_on_gap(self, tmp_path):
        """中间有一天缺口，consecutive 应从缺口后重新计数。"""
        analyzer = _build_analyzer(tmp_path)
        all_dates = ["2026-03-21", "2026-03-22", "2026-03-23",
                     "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27"]
        # 22日未上榜（缺口），之后连续5天
        series = _make_series(
            dates=["2026-03-21", "2026-03-23", "2026-03-24",
                   "2026-03-25", "2026-03-26", "2026-03-27"],
            ranks=[3] * 6,
            changes=[2.0] * 6,
        )
        sig = analyzer._compute_signals(series, "2026-03-27", all_dates)
        assert sig["consecutive_in_top30"] == 5  # 23~27连续5天


# =====================================================================
# T5. _classify_phase — 高潮
# =====================================================================

class TestClassifyPhasePeak:
    def _peak_sig(self) -> dict:
        return {
            "data_days": 15,
            "rank_today": 2,
            "change_today": 1.5,
            "top_stock_today": "A",
            "consecutive_in_top30": 12,
            "days_in_top30_of_20": 12,
            "rank_change_3d": 1,       # 轻微下滑
            "cumulative_pct_5d": 14.0,  # 大幅累积
            "daily_accel_3d": -0.5,    # 涨速减缓
            "top_stock_stability_5d": 0.3,  # 领涨股切换频繁
            "max_consecutive_hist": 12,
        }

    def test_peak_phase(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._peak_sig())
        assert result["phase"] == PHASE_PEAK

    def test_peak_confidence_high(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._peak_sig())
        assert result["confidence"] == "高"
        assert result["hit_count"] >= 4

    def test_peak_evidence_is_peak_specific(self, tmp_path):
        """获胜阶段的 evidence 应只包含高潮相关信息，不含启动/发酵的证据。"""
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._peak_sig())
        for e in result["evidence"]:
            assert "新兴" not in e, f"启动证据混入了高潮结果: {e}"
            assert "持续活跃" not in e, f"发酵证据混入了高潮结果: {e}"


# =====================================================================
# T6. _classify_phase — 首次分歧
# =====================================================================

class TestClassifyPhaseDiverge:
    def _diverge_sig(self) -> dict:
        return {
            "data_days": 15,
            "rank_today": 18,
            "change_today": -1.2,      # 今日收跌
            "top_stock_today": "B",
            "consecutive_in_top30": 1,
            "days_in_top30_of_20": 10,
            "rank_change_3d": 14,      # 排名大幅下滑
            "cumulative_pct_5d": 3.0,
            "daily_accel_3d": -1.5,
            "top_stock_stability_5d": 0.2,
            "max_consecutive_hist": 9,  # 曾连续9天
        }

    def test_diverge_phase(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._diverge_sig())
        assert result["phase"] == PHASE_DIVERGE

    def test_diverge_confidence(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._diverge_sig())
        assert result["confidence"] in ("高", "中")
        assert result["hit_count"] >= 2


# =====================================================================
# T7. _classify_phase — 衰退
# =====================================================================

class TestClassifyPhaseDecline:
    def _decline_sig(self) -> dict:
        return {
            "data_days": 15,
            "rank_today": None,        # 今日不在前30
            "change_today": None,
            "top_stock_today": "",
            "consecutive_in_top30": 0,
            "days_in_top30_of_20": 8,  # 曾经上榜多天
            "rank_change_3d": None,
            "cumulative_pct_5d": 0.0,
            "daily_accel_3d": None,
            "top_stock_stability_5d": None,
            "max_consecutive_hist": 8,
        }

    def test_decline_phase(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._decline_sig())
        assert result["phase"] == PHASE_DECLINE

    def test_decline_high_confidence(self, tmp_path):
        analyzer = _build_analyzer(tmp_path)
        result = analyzer._classify_phase(self._decline_sig())
        assert result["hit_count"] >= 3

    def test_insufficient_data_returns_watch(self, tmp_path):
        """data_days < 3 时始终返回观察中。"""
        analyzer = _build_analyzer(tmp_path)
        sig = {"data_days": 2, "rank_today": None, "days_in_top30_of_20": 10}
        result = analyzer._classify_phase(sig)
        assert result["phase"] == PHASE_WATCH
        assert result["confidence"] == "低"


# =====================================================================
# T8. analyze() — 端到端集成测试
# =====================================================================

class TestAnalyzeIntegration:
    def _setup_tmpdir(self, tmp_path: Path) -> tuple[SectorRhythmAnalyzer, dict]:
        """
        构造 10 天历史数据：
        - "AI算力"：连续10天上榜，排名稳定前5，累计涨幅大 → 应判为 高潮/发酵
        - "锂矿概念"：仅最后1天首次上榜，排名1 → 应判为 启动
        - "旧主线"：前8天上榜，最近2天消失 → 应判为 首次分歧/震荡/衰退
        """
        daily_dir = tmp_path / "daily"

        dates = [f"2026-03-{d:02d}" for d in range(18, 28)]  # 18~27，10天

        for i, date in enumerate(dates):
            day_dir = daily_dir / date
            sectors = []

            # AI算力：连续10天，排名3~5
            sectors.append(_make_sector_entry("AI算力", i + 1, 2.0 + i * 0.1, "工业富联"))

            # 旧主线：前8天上榜（18~25），后2天（26、27）消失
            if i < 8:
                sectors.append(_make_sector_entry("旧主线", 2, 3.0, "老龙头"))

            # 锂矿概念：仅最后一天
            if i == 9:
                sectors.append(_make_sector_entry("锂矿概念", 1, 5.5, "江特电机"))

            _write_post_market(day_dir, date, sectors)

        analyzer = _build_analyzer(tmp_path, history_days=20)

        today_raw = {
            "date": "2026-03-27",
            "sector_industry": {
                "data": [
                    {"name": "AI算力", "change_pct": 2.9, "volume_billion": 100.0, "top_stock": "工业富联"},
                    {"name": "锂矿概念", "change_pct": 5.5, "volume_billion": 80.0, "top_stock": "江特电机"},
                ]
            },
        }
        return analyzer, today_raw

    def test_result_count(self, tmp_path):
        """前10 + extra_names 去重后，结果数量应正确。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry", extra_names=["旧主线"])
        names = [r["name"] for r in results]
        assert "AI算力" in names
        assert "锂矿概念" in names
        assert "旧主线" in names
        assert len(names) == len(set(names)), "结果中出现重复板块"

    def test_launch_detected(self, tmp_path):
        """锂矿概念首次上榜，应识别为启动。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry")
        lm = next(r for r in results if r["name"] == "锂矿概念")
        assert lm["phase"] == PHASE_LAUNCH
        assert lm["consecutive_in_top30"] == 1

    def test_ai_sector_phase(self, tmp_path):
        """AI算力连续10天上榜，应判为发酵或高潮（不应是启动或衰退）。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry")
        ai = next(r for r in results if r["name"] == "AI算力")
        assert ai["phase"] in (PHASE_FERMENT, PHASE_PEAK)
        assert ai["consecutive_in_top30"] == 10

    def test_old_theme_phase(self, tmp_path):
        """旧主线近2天消失，应判为首次分歧、震荡或衰退（不应是启动或发酵）。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry", extra_names=["旧主线"])
        old = next(r for r in results if r["name"] == "旧主线")
        assert old["phase"] in (PHASE_DIVERGE, PHASE_OSCILLATE, PHASE_DECLINE)

    def test_extra_name_not_in_today(self, tmp_path):
        """extra_names 中的板块即使今日未上榜，也应出现在结果中。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry", extra_names=["旧主线"])
        names = [r["name"] for r in results]
        assert "旧主线" in names
        old = next(r for r in results if r["name"] == "旧主线")
        assert old["rank_today"] is None  # 今日未上榜

    def test_unknown_extra_name(self, tmp_path):
        """从未出现过的板块名也不应导致崩溃，返回观察中。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry", extra_names=["完全不存在的板块"])
        unknown = next((r for r in results if r["name"] == "完全不存在的板块"), None)
        assert unknown is not None
        assert unknown["phase"] == PHASE_WATCH

    def test_no_duplicate_in_results(self, tmp_path):
        """extra_names 与今日前10有重叠时，结果中不应出现重复。"""
        analyzer, today_raw = self._setup_tmpdir(tmp_path)
        results = analyzer.analyze(today_raw, "industry", extra_names=["AI算力", "锂矿概念"])
        names = [r["name"] for r in results]
        assert len(names) == len(set(names))

# =====================================================================
# T9. daily/ 不存在时不应抛错
# =====================================================================

class TestMissingDailyDir:
    def test_sorted_daily_dirs_empty_when_no_daily(self, tmp_path):
        """无 daily/ 目录时 _sorted_daily_dirs 返回空列表，不触发 FileNotFoundError。"""
        # 故意不创建 daily/
        analyzer = SectorRhythmAnalyzer(tmp_path, history_days=20)
        assert analyzer._sorted_daily_dirs() == []

    def test_analyze_runs_when_no_daily(self, tmp_path):
        """无历史归档时仍可分析今日快照，阶段为观察中等。"""
        analyzer = SectorRhythmAnalyzer(tmp_path, history_days=20)
        today_raw = {
            "date": "2026-03-27",
            "sector_industry": {
                "data": [
                    {"name": "测试板块", "change_pct": 3.0, "top_stock": "A", "volume_billion": 1.0},
                ]
            },
        }
        results = analyzer.analyze(today_raw, "industry", today_date="2026-03-27")
        assert len(results) == 1
        assert results[0]["name"] == "测试板块"
        assert results[0]["phase"] == PHASE_WATCH

class TestLaunchNotLongStreak:
    """rank 跃升加分不应让连续多日上榜的板块被判为启动（见磷肥及磷化工类案例）。"""

    def test_long_streak_rank_jump_not_launch(self, tmp_path):
        analyzer = SectorRhythmAnalyzer(tmp_path, history_days=20)
        sig = {
            "data_days": 20,
            "rank_today": 9,
            "change_today": 4.53,
            "top_stock_today": "",
            "consecutive_in_top30": 15,
            "days_in_top30_of_20": 15,
            "rank_change_3d": -20,
            "cumulative_pct_5d": 2.8,
            "daily_accel_3d": 1.025,
            "top_stock_stability_5d": None,
            "max_consecutive_hist": 15,
        }
        r = analyzer._classify_phase(sig)
        assert r["phase"] != PHASE_LAUNCH
