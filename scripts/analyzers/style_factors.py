"""
风格化因子分析器

聚合溢价率回填、指数相对强弱、涨停板结构等数据，
输出 style_factors 字典供盘后报告渲染和风格切换信号判断。
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DAILY_DIR = BASE_DIR / "daily"

_PREMIUM_KEYS = [
    "first_board", "first_board_10cm", "first_board_20cm", "first_board_30cm",
    "first_board_yizi", "second_board",
    "third_board_plus", "third_board", "fourth_board", "fifth_board_plus",
    "yizi_first_open", "capacity_top10",
]


class StyleAnalyzer:
    """基于已采集数据计算风格化因子"""

    def analyze(self, raw_data: dict, date: str) -> dict:
        result: dict = {}

        prev_backfill = self._load_prev_backfill(date)

        result["premium_snapshot"] = self._build_premium_snapshot(prev_backfill)
        result["premium_trend"] = self._build_premium_trend(date)
        result["board_preference"] = self._build_board_preference(raw_data)
        result["cap_preference"] = self._build_cap_preference(raw_data)
        result["switch_signals"] = self._build_switch_signals(result)

        return result

    # ------------------------------------------------------------------
    # 溢价率快照：来自最近一次 premium_backfill
    # ------------------------------------------------------------------

    def _build_premium_snapshot(self, backfill: dict | None) -> dict:
        if not backfill:
            return {}
        snapshot: dict = {}
        for key in _PREMIUM_KEYS:
            grp = backfill.get(key, {})
            if not grp or not grp.get("count"):
                continue
            snapshot[key] = {
                "count": grp["count"],
                "premium_median": grp.get("premium_median"),
                "premium_mean": grp.get("premium_mean"),
                "open_up_rate": grp.get("open_up_rate"),
            }
        return snapshot

    # ------------------------------------------------------------------
    # 溢价率趋势：近 5 个交易日首板溢价中位的走势
    # ------------------------------------------------------------------

    def _build_premium_trend(self, date: str) -> dict:
        medians: list[float] = []
        for backfill in self._load_recent_backfills(date, n=5):
            fb = backfill.get("first_board", {})
            med = fb.get("premium_median")
            if med is not None:
                medians.append(med)

        if not medians:
            return {}

        direction = self._judge_trend(medians)
        return {
            "first_board_median_5d": medians,
            "direction": direction,
        }

    @staticmethod
    def _judge_trend(series: list[float]) -> str:
        """根据最近 N 个值判断趋势方向。series[0] 是最近一天。"""
        if len(series) < 2:
            return "数据不足"
        ups = sum(1 for i in range(len(series) - 1) if series[i] > series[i + 1])
        downs = len(series) - 1 - ups
        if ups >= downs + 2:
            return "走强"
        if downs >= ups + 2:
            return "走弱"
        return "震荡"

    # ------------------------------------------------------------------
    # 涨停板偏好：当日 10/20/30cm 涨停占比
    # ------------------------------------------------------------------

    @staticmethod
    def _build_board_preference(raw_data: dict) -> dict:
        lu = raw_data.get("limit_up", {})
        fb_count = lu.get("first_board_count", 0)
        if not fb_count:
            return {}
        c10 = lu.get("first_board_10cm", 0)
        c20 = lu.get("first_board_20cm", 0)
        c30 = lu.get("first_board_30cm", 0)
        total = c10 + c20 + c30 or 1
        pct_10 = round(c10 / total * 100, 1)
        pct_20 = round(c20 / total * 100, 1)
        pct_30 = round(c30 / total * 100, 1)
        dominant = "10cm"
        if pct_20 >= pct_10 and pct_20 >= pct_30:
            dominant = "20cm"
        elif pct_30 >= pct_10 and pct_30 >= pct_20:
            dominant = "30cm"
        return {
            "dominant_type": dominant,
            "pct_10cm": pct_10,
            "pct_20cm": pct_20,
            "pct_30cm": pct_30,
        }

    # ------------------------------------------------------------------
    # 大小盘偏好：CSI300 vs CSI1000
    # ------------------------------------------------------------------

    @staticmethod
    def _build_cap_preference(raw_data: dict) -> dict:
        indices = raw_data.get("indices", {})
        csi300 = indices.get("csi300", {})
        csi1000 = indices.get("csi1000", {})
        chg300 = csi300.get("change_pct")
        chg1000 = csi1000.get("change_pct")
        if chg300 is None or chg1000 is None:
            return {}
        spread = round(chg1000 - chg300, 2)
        if spread > 0.5:
            relative = "偏小盘"
        elif spread < -0.5:
            relative = "偏大盘"
        else:
            relative = "均衡"
        return {
            "csi300_chg": round(chg300, 2),
            "csi1000_chg": round(chg1000, 2),
            "spread": spread,
            "relative": relative,
        }

    # ------------------------------------------------------------------
    # 风格切换信号
    # ------------------------------------------------------------------

    @staticmethod
    def _build_switch_signals(style: dict) -> list[str]:
        signals: list[str] = []

        snap = style.get("premium_snapshot", {})
        fb = snap.get("first_board", {})
        fb_med = fb.get("premium_median")
        if fb_med is not None and fb_med < 0:
            signals.append(f"题材首板溢价转负（{fb_med:+.2f}%），短线赚钱效应下降")

        fb_rate = fb.get("open_up_rate")
        if fb_rate is not None and fb_rate < 0.4:
            signals.append(f"首板高开率仅 {fb_rate:.0%}，情绪偏冷")

        trend = style.get("premium_trend", {})
        direction = trend.get("direction")
        if direction == "走弱":
            signals.append("近5日首板溢价趋势走弱")

        third_plus = snap.get("third_board_plus", {})
        tp_med = third_plus.get("premium_median")
        if tp_med is not None and tp_med < -3:
            signals.append(f"连板高标大幅亏钱（三板+溢价 {tp_med:+.2f}%），连板风格可能退潮")

        cap = style.get("cap_preference", {})
        if cap.get("relative") == "偏大盘":
            spread = cap.get("spread", 0)
            signals.append(f"大盘股跑赢小盘股（价差 {spread:+.2f}%），审美偏向容量票")

        return signals

    # ------------------------------------------------------------------
    # 历史数据加载
    # ------------------------------------------------------------------

    def _load_prev_backfill(self, date: str) -> dict | None:
        """读取距离 date 最近的一次 premium_backfill。"""
        for bf in self._load_recent_backfills(date, n=1):
            return bf
        return None

    def _load_recent_backfills(self, date: str, n: int = 5) -> list[dict]:
        """按时间倒序读取最近 n 个交易日的 premium_backfill。"""
        results: list[dict] = []
        try:
            dirs = sorted(
                [d for d in DAILY_DIR.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            return results

        for d in dirs:
            if len(results) >= n:
                break
            pm = d / "post-market.yaml"
            if not pm.exists():
                continue
            try:
                with open(pm, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                bf = data.get("premium_backfill")
                if bf and bf.get("first_board", {}).get("count", 0) > 0:
                    results.append(bf)
            except Exception:
                continue
        return results
