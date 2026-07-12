"""
风格化因子分析器

聚合溢价率回填、指数相对强弱、涨停板结构等数据，
输出 style_factors 字典供盘后报告渲染和风格切换信号判断。
"""
from __future__ import annotations

import logging
from datetime import date as _date
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
        # 昨日人气股今日表现（具名）：来自 T-1 yaml 的 popularity_backfill。
        # 渲染文案语义固定为「T-1 高标 → T 日结局」，因此只读紧邻前一交易日，
        # 缺失/空即返回 []，禁止向更早日期回退（否则会把 T-2 旧值伪装成 T-1→T）。
        popularity, popularity_provenance = (
            self._load_popularity_with_provenance(date)
        )
        result["popularity"] = popularity or []
        if popularity and popularity_provenance:
            result["popularity_provenance"] = popularity_provenance
        # 晋级率（T-1 首板→T 二板等）：与 popularity 同为严格 T-1 时序，禁止回退
        result["promotion"] = self._load_prev_field(
            date, "promotion_backfill", first_only=True
        )

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
        """近 5 个交易日（严格最近 5 个前序目录）首板溢价中位走势。

        F 修复：旧实现经 _load_recent_backfills 的 count>0 过滤会跳过空/缺失日、
        向更早回溯，使「5日」实际跨越 >2 周。改为限定 _prev_dirs[:5]（最近 5 个交易日目录），
        空/缺失日剔除但不外扩窗口；medians 与 dates（取 backfill.trade_date，即溢价实现日）
        等长对齐。medians 保持 most-recent-first（_judge_trend 约定 series[0] 为最近一天）。
        """
        medians: list[float] = []
        dates: list[str] = []
        for d in self._prev_dirs(date)[:5]:
            pm = d / "post-market.yaml"
            if not pm.exists():
                continue
            try:
                with open(pm, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            bf = data.get("premium_backfill")
            if not isinstance(bf, dict):
                continue
            fb = bf.get("first_board", {})
            if not isinstance(fb, dict):
                continue
            med = fb.get("premium_median") if fb.get("count") else None
            if med is None:
                continue
            medians.append(med)
            dates.append(bf.get("trade_date") or d.name)

        if not medians:
            return {}

        return {
            "first_board_median_5d": medians,
            "dates": dates,
            "direction": self._judge_trend(medians),
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

    def _prev_dirs(self, date: str) -> list[Path]:
        """按日期倒序返回 date 之前的交易日目录（不判断 yaml 是否存在/可读）。"""
        try:
            return sorted(
                [d for d in DAILY_DIR.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            return []

    def _iter_prev_yaml(self, date: str):
        """按时间倒序产出 date 之前每个交易日的 post-market.yaml 解析结果。

        只负责"按倒序产出已存在且可解析的 yaml"（跳过缺失/损坏），是否计入「最近 n 个」
        由调用方的过滤条件决定。当前仅 `_load_prev_field(first_only=False)` 复用本迭代器
        （premium 快照/趋势已改走 `_prev_dirs[:N]` 的严格窗口，不再经此迭代器）。

        ⚠ 本迭代器会跳过缺失/损坏的 yaml，因此不适合需要"严格锁紧邻前一日"的场景
        （见 _load_prev_field first_only 分支——它直接走 _prev_dirs[0]，不复用本迭代器）。
        """
        for d in self._prev_dirs(date):
            pm = d / "post-market.yaml"
            if not pm.exists():
                continue
            try:
                with open(pm, encoding="utf-8") as f:
                    yield yaml.safe_load(f) or {}
            except Exception:
                continue

    def _load_prev_backfill(self, date: str) -> dict | None:
        """严格读取紧邻前一交易日（_prev_dirs[0]）的 premium_backfill。

        D 修复：旧实现走 _load_recent_backfills(n=1)，其 first_board.count>0 过滤会跳过
        缺失/真空日、静默回退到更早某天的旧值（陈旧数据冒充当日）。节假日缺口 / 当日 backfill
        缺失 / count==0 真空日都会触发，且无 source_date 标记，DB 与 UI 都看不出是陈旧值。
        改为严格只看紧邻前一日，绝不跨日回退：
        - 该日 yaml 缺失 / 无 premium_backfill → None；
        - backfill 携带 trade_date 且与当前 date 不一致（陈旧错位写入）→ None；
        count==0（真空日）仍返回该 backfill，由下游 _build_premium_snapshot 按空处理
        （只会让快照为空，不会冒充旧值）。与 popularity/promotion 的 first_only 范式一致。
        """
        dirs = self._prev_dirs(date)
        if not dirs:
            return None
        pm = dirs[0] / "post-market.yaml"  # 紧邻前一交易日，不跳过、不回退
        if not pm.exists():
            return None
        try:
            with open(pm, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        bf = data.get("premium_backfill")
        if not isinstance(bf, dict) or not bf:
            return None
        # 严格要求 backfill 的 trade_date 恰等于当前 date：premium.py 落盘时总会原子写入
        # trade_date（见 collect() result 字典），故缺失/错位都视为「非本日的可信回填」→ None，
        # 不让任何无法定位到本日的陈旧/损坏数据穿透守卫。
        if bf.get("trade_date") != date:
            return None
        return bf

    @staticmethod
    def _strict_iso_date(value) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        try:
            parsed = _date.fromisoformat(text)
        except ValueError:
            return None
        return text if parsed.isoformat() == text else None

    def _load_popularity_with_provenance(
        self,
        date: str,
    ) -> tuple[object | None, dict[str, str] | None]:
        """从同一次 YAML 读取返回人气股及经回填元数据验证的来源日期。"""
        dirs = self._prev_dirs(date)
        if not dirs:
            return None, None
        source_dir = dirs[0]
        pm = source_dir / "post-market.yaml"
        if not pm.exists():
            return None, None
        try:
            with open(pm, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return None, None
        if not isinstance(data, dict):
            return None, None
        popularity = data.get("popularity_backfill")
        if not popularity:
            return None, None

        backfill = data.get("premium_backfill")
        if not isinstance(backfill, dict):
            return popularity, None
        source_trade_date = self._strict_iso_date(backfill.get("prev_date"))
        outcome_trade_date = self._strict_iso_date(backfill.get("trade_date"))
        directory_date = self._strict_iso_date(source_dir.name)
        target_date = self._strict_iso_date(date)
        if (
            source_trade_date is None
            or outcome_trade_date is None
            or source_trade_date != directory_date
            or outcome_trade_date != target_date
        ):
            return popularity, None
        return popularity, {
            "source_trade_date": source_trade_date,
            "outcome_trade_date": outcome_trade_date,
        }

    def _load_prev_field(self, date: str, field: str, first_only: bool = False):
        """读取 date 之前 yaml 的指定 field。

        - first_only=False：按倒序返回第一个含非空 field 的值（可回退到更早日期）。
        - first_only=True：严格只看紧邻的前一个交易日目录（_prev_dirs[0]）；
          其 yaml 缺失 / 损坏 / 无该字段一律返回 None，**绝不回退到更早日期**。
          用于有严格 T-1 时序语义的字段（如 popularity_backfill / promotion_backfill）——
          这些字段渲染文案固定为「T-1 → T」，回退到 T-2 会把旧值伪装成 T-1→T。
        找不到返回 None。

        注：以 `if val`（truthiness）判定——空 dict/空 list 视为「无数据」等同缺失，
        这是有意为之：promotion/popularity 的生产端（premium.py）只在非空时才写入
        （`if promotion:` / `if popularity:`），故空值不会真实落盘；即便落盘，
        空回填对渲染也无意义，按缺失处理即可。
        """
        if first_only:
            dirs = self._prev_dirs(date)
            if not dirs:
                return None
            pm = dirs[0] / "post-market.yaml"  # 紧邻前一交易日，不跳过、不回退
            if not pm.exists():
                return None
            try:
                with open(pm, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                return None
            val = data.get(field)
            return val if val else None

        for data in self._iter_prev_yaml(date):
            val = data.get(field)
            if val:
                return val
        return None
