"""
板块节奏分析器

基于过去 N 个交易日的每日板块涨幅排名快照，计算量化信号并判断板块所处节奏阶段：
启动 / 发酵 / 高潮 / 首次分歧 / 震荡 / 衰退 / 观察中
"""
from __future__ import annotations

import bisect
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# 阶段标签
PHASE_LAUNCH = "启动"
PHASE_FERMENT = "发酵"
PHASE_PEAK = "高潮"
PHASE_DIVERGE = "首次分歧"
PHASE_OSCILLATE = "震荡"
PHASE_DECLINE = "衰退"
PHASE_WATCH = "观察中"

# 置信度阈值（命中信号数）
CONF_HIGH = 4
CONF_MID = 2

# 阶段优先级（同分时的决胜顺序，数字越小优先级越高）
_PHASE_PRIORITY = {
    PHASE_DECLINE: 0,
    PHASE_DIVERGE: 1,
    PHASE_PEAK: 2,
    PHASE_OSCILLATE: 3,
    PHASE_FERMENT: 4,
    PHASE_LAUNCH: 5,
    PHASE_WATCH: 6,
}


class SectorRhythmAnalyzer:
    """
    板块节奏分析器。

    数据来源：daily/YYYY-MM-DD/post-market.yaml 中的 sector_industry / sector_concept 列表。
    每个板块的每日快照字段：name, change_pct, volume_billion, top_stock, rank（列表位置+1）。

    Usage:
        analyzer = SectorRhythmAnalyzer(base_dir="/path/to/tradeSystem")
        results = analyzer.analyze(today_raw_data, sector_type="industry", extra_names=["AI算力"])
    """

    def __init__(self, base_dir: str | Path, history_days: int = 20):
        self.base_dir = Path(base_dir)
        self.daily_dir = self.base_dir / "daily"
        self.history_days = history_days

    # ------------------------------------------------------------------
    # 历史数据加载
    # ------------------------------------------------------------------

    def _sorted_daily_dirs(self) -> list[Path]:
        """返回 daily/ 下按日期升序排列的目录（只取有 post-market.yaml 的）。"""
        if not self.daily_dir.is_dir():
            return []
        dirs = sorted(
            [d for d in self.daily_dir.iterdir()
             if d.is_dir() and (d / "post-market.yaml").exists()],
            key=lambda d: d.name,
        )
        return dirs[-self.history_days:]  # 最多保留最近 N 天

    def _load_day_sectors(self, day_dir: Path, sector_type: str) -> list[dict]:
        """从某天的 post-market.yaml 读取板块排名列表。"""
        yaml_path = day_dir / "post-market.yaml"
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            key = f"sector_{sector_type}"
            # post-market.yaml 结构：raw_data.sector_industry.data 或顶层 sector_industry.data
            raw = data.get("raw_data", data)
            return raw.get(key, {}).get("data", [])
        except Exception as e:
            logger.warning(f"读取 {yaml_path} 失败: {e}")
            return []

    def _load_history(self, sector_type: str) -> tuple[dict[str, list[dict]], list[str]]:
        """
        加载所有历史日期的板块数据，按板块名汇总成时间序列。

        返回:
            history: {sector_name: [{date, rank, change_pct, volume_billion, top_stock}, ...]}
            all_dates: 升序排列的历史交易日列表（包含所有有 post-market.yaml 的目录）
        rank 从 1 开始（列表位置+1），不在前30则该天无记录。
        """
        history: dict[str, list[dict]] = {}
        all_dates: list[str] = []
        for day_dir in self._sorted_daily_dirs():
            date = day_dir.name
            sectors = self._load_day_sectors(day_dir, sector_type)
            if not sectors:
                continue
            all_dates.append(date)
            for idx, s in enumerate(sectors):
                name = s.get("name", "")
                if not name:
                    continue
                entry = {
                    "date": date,
                    "rank": idx + 1,
                    "change_pct": float(s.get("change_pct", 0) or 0),
                    "volume_billion": float(s.get("volume_billion", 0) or 0),
                    "top_stock": s.get("top_stock", ""),
                }
                history.setdefault(name, []).append(entry)
        return history, all_dates

    # ------------------------------------------------------------------
    # 信号计算
    # ------------------------------------------------------------------

    def _compute_signals(
        self,
        series: list[dict],
        today_date: str,
        all_dates: list[str],
    ) -> dict:
        """
        基于时间序列计算节奏判断所需的 6 个量化信号。

        Args:
            series: 该板块的历史记录（升序），仅包含上榜的天。
            today_date: 锚定日期 YYYY-MM-DD，必须是 all_dates 中的某一天。
            all_dates: 所有历史交易日列表（升序），包含未上榜的天，用于判断连续性。
        """
        data_days = len(all_dates)
        if not series or data_days == 0:
            return {"data_days": data_days}

        by_date = {e["date"]: e for e in series}

        # 截取到 today_date 为止的日期列表（避免未来日期干扰连续性计算）
        if today_date in all_dates:
            idx_today = all_dates.index(today_date)
            dates_up_to_today = all_dates[: idx_today + 1]
        else:
            dates_up_to_today = all_dates

        today_entry = by_date.get(today_date)
        rank_today = today_entry["rank"] if today_entry else None
        change_today = today_entry["change_pct"] if today_entry else None
        top_stock_today = today_entry["top_stock"] if today_entry else ""

        # 1. 连续上榜天数（从 today_date 往前数，遇到未上榜停止）
        consecutive = 0
        for d in reversed(dates_up_to_today):
            if d in by_date:
                consecutive += 1
            else:
                break

        # 2. 20天内总上榜天数（仅计 dates_up_to_today 内的记录）
        days_in_top = sum(1 for e in series if e["date"] in set(dates_up_to_today))

        # 3. 排名变化：今日排名 - 3日前排名（负=上升，正=下滑）
        rank_change_3d: Optional[float] = None
        if today_entry and len(dates_up_to_today) >= 4:
            date_3d_ago = dates_up_to_today[-4]  # 升序末4个: [-4]是3天前
            if date_3d_ago in by_date:
                rank_change_3d = rank_today - by_date[date_3d_ago]["rank"]

        # 4. 近5/10日累计涨幅（含今日）
        # 口径：取全市场最近 N 个有归档的交易日窗口内，该板块**实际出现在前30**的日期
        # 的 change_pct 之和。未上榜日不计入（不补 0），因此与「任意 N 日区间真实涨跌幅」
        # 有差异——适合衡量「上榜期间」累计强度，而非完整区间收益。
        recent_5_dates = dates_up_to_today[-5:]
        recent_5 = [by_date[d] for d in recent_5_dates if d in by_date]
        cumulative_pct_5d = sum(e["change_pct"] for e in recent_5)

        recent_10_dates = dates_up_to_today[-10:]
        recent_10 = [by_date[d] for d in recent_10_dates if d in by_date]
        cumulative_pct_10d = sum(e["change_pct"] for e in recent_10)

        # 5. 近3日涨速加速度（一阶差分均值，正=加速，负=减速）
        recent_3_dates = dates_up_to_today[-3:]
        recent_3 = [by_date[d] for d in recent_3_dates if d in by_date]
        if len(recent_3) >= 2:
            diffs = [
                recent_3[i]["change_pct"] - recent_3[i - 1]["change_pct"]
                for i in range(1, len(recent_3))
            ]
            daily_accel_3d: Optional[float] = sum(diffs) / len(diffs)
        else:
            daily_accel_3d = None

        # 6. 近5日领涨股稳定性（与今日相同领涨股的天数占比）
        if top_stock_today and recent_5:
            stability: Optional[float] = (
                sum(1 for e in recent_5 if e["top_stock"] == top_stock_today) / len(recent_5)
            )
        else:
            stability = None

        # 历史最长连续上榜段（基于 dates_up_to_today，用于震荡/分歧判断）
        max_consecutive_hist = _max_consecutive_in_dates(by_date, dates_up_to_today)

        return {
            "data_days": data_days,
            "rank_today": rank_today,
            "change_today": change_today,
            "top_stock_today": top_stock_today,
            "consecutive_in_top30": consecutive,
            "days_in_top30_of_20": days_in_top,
            "rank_change_3d": rank_change_3d,
            "cumulative_pct_5d": round(cumulative_pct_5d, 2),
            "cumulative_pct_10d": round(cumulative_pct_10d, 2),
            "daily_accel_3d": round(daily_accel_3d, 3) if daily_accel_3d is not None else None,
            "top_stock_stability_5d": round(stability, 2) if stability is not None else None,
            "max_consecutive_hist": max_consecutive_hist,
        }

    # ------------------------------------------------------------------
    # 阶段分类
    # ------------------------------------------------------------------

    def _classify_phase(self, sig: dict) -> dict:
        """
        根据信号字典映射到节奏阶段。

        每个阶段维护独立的 evidence 列表，最终只返回获胜阶段的 evidence，
        避免跨阶段证据混入。

        返回: {phase, confidence, evidence, hit_count}
        """
        data_days = sig.get("data_days", 0)
        if data_days < 3:
            return {
                "phase": PHASE_WATCH,
                "confidence": "低",
                "evidence": ["历史数据不足（<3天），无法判断"],
                "hit_count": 0,
            }

        rank = sig.get("rank_today")
        change = sig.get("change_today", 0) or 0
        consecutive = sig.get("consecutive_in_top30", 0)
        days_in_top = sig.get("days_in_top30_of_20", 0)
        rank_change_3d = sig.get("rank_change_3d")
        cumul_5d = sig.get("cumulative_pct_5d", 0) or 0
        accel = sig.get("daily_accel_3d")
        stability = sig.get("top_stock_stability_5d")
        max_hist_consec = sig.get("max_consecutive_hist", 0)

        scores: dict[str, int] = {p: 0 for p in _PHASE_PRIORITY}
        # 各阶段独立 evidence，避免混合
        ev: dict[str, list[str]] = {p: [] for p in _PHASE_PRIORITY}

        # ---- 启动信号 ----
        if consecutive == 1:
            # 绝对首次上榜，最强启动信号（无法被发酵/高潮的"累积涨幅"虚假积累干扰）
            scores[PHASE_LAUNCH] += 2
            ev[PHASE_LAUNCH].append("今日首次进入前30（新兴启动）")
        elif 2 <= consecutive <= 3:
            scores[PHASE_LAUNCH] += 1
            ev[PHASE_LAUNCH].append(f"连续上榜仅 {consecutive} 天（短期新兴）")
        # 排名靠前 / 短期跃升仅对「短连续」生效，避免老主线因名次波动被算成启动
        if consecutive <= 3:
            if rank is not None and rank <= 15:
                scores[PHASE_LAUNCH] += 1
                ev[PHASE_LAUNCH].append(f"今日排名 #{rank}，位置靠前")
            if rank_change_3d is not None and rank_change_3d < -5:
                scores[PHASE_LAUNCH] += 2
                ev[PHASE_LAUNCH].append(f"排名 3 日跃升 {abs(int(rank_change_3d))} 位")

        # ---- 发酵信号 ----
        if 3 < consecutive <= 12:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"连续上榜 {consecutive} 天（持续活跃）")
        if rank is not None and rank <= 12:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"今日排名 #{rank}，位置稳定")
        if cumul_5d > 3:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"5日累计涨幅 +{cumul_5d}%（持续累积）")
        if accel is not None and accel >= 0:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append("涨速仍在加速或平稳")

        # ---- 高潮信号 ----
        if consecutive > 8:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append(f"连续上榜已达 {consecutive} 天（演绎充分）")
        if cumul_5d > 10:
            scores[PHASE_PEAK] += 2
            ev[PHASE_PEAK].append(f"5日累计涨幅 +{cumul_5d}%（幅度偏大）")
        if accel is not None and accel < 0:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append("涨速开始减缓")
        if stability is not None and stability < 0.4:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append("领涨股频繁切换（板块扩散）")

        # ---- 首次分歧信号 ----
        if max_hist_consec > 5 and consecutive <= 2:
            scores[PHASE_DIVERGE] += 2
            ev[PHASE_DIVERGE].append(f"历史最长连续 {max_hist_consec} 天后近期掉榜（出现分歧）")
        if rank_change_3d is not None and rank_change_3d > 8:
            scores[PHASE_DIVERGE] += 2
            ev[PHASE_DIVERGE].append(f"排名 3 日下滑 {int(rank_change_3d)} 位")
        if change < 0 and days_in_top > 5:
            scores[PHASE_DIVERGE] += 1
            ev[PHASE_DIVERGE].append("有上榜历史但今日收跌")

        # ---- 震荡信号 ----
        if days_in_top > 5 and consecutive <= 2 and max_hist_consec >= 3:
            scores[PHASE_OSCILLATE] += 2
            ev[PHASE_OSCILLATE].append(
                f"20天内上榜 {days_in_top} 天但近期中断，历史曾连续 {max_hist_consec} 天"
            )
        if rank_change_3d is not None and abs(rank_change_3d) <= 3 and cumul_5d < 3:
            scores[PHASE_OSCILLATE] += 1
            ev[PHASE_OSCILLATE].append("排名波动小，累积涨幅有限（震荡特征）")

        # ---- 衰退信号 ----
        if rank is None and days_in_top > 3:
            scores[PHASE_DECLINE] += 3
            ev[PHASE_DECLINE].append(f"曾上榜 {days_in_top} 天，近期已不在前30")
        if consecutive == 0 and days_in_top > 5:
            scores[PHASE_DECLINE] += 1
            ev[PHASE_DECLINE].append("当前连续上榜中断")

        # ---- 选最高分阶段（同分时按 _PHASE_PRIORITY 决胜）----
        best_phase = max(
            scores,
            key=lambda p: (scores[p], -_PHASE_PRIORITY.get(p, 99)),
        )
        hit_count = scores[best_phase]

        if hit_count < 1:
            best_phase = PHASE_WATCH
            confidence = "低"
        elif hit_count >= CONF_HIGH:
            confidence = "高"
        elif hit_count >= CONF_MID:
            confidence = "中"
        else:
            confidence = "低"

        return {
            "phase": best_phase,
            "confidence": confidence,
            "evidence": ev[best_phase][:3],  # 只返回获胜阶段的证据
            "hit_count": hit_count,
        }

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def analyze(
        self,
        today_raw_data: dict,
        sector_type: str,
        extra_names: list[str] | None = None,
        today_date: str | None = None,
    ) -> list[dict]:
        """
        对今日前10板块 + extra_names 逐一进行节奏分析。

        Args:
            today_raw_data: collect_post_market() 返回的 result dict
            sector_type: "industry" 或 "concept"
            extra_names: 强制追加分析的板块名列表（如来自 main-theme.yaml）
            today_date: 日期字符串 YYYY-MM-DD，None 则取 today_raw_data["date"]

        Returns:
            list[dict]，每项包含：
                name, sector_type, rank_today, consecutive_in_top30,
                cumulative_pct_5d, phase, confidence, evidence, signals
        """
        if extra_names is None:
            extra_names = []

        date = today_date or today_raw_data.get("date", "")
        key = f"sector_{sector_type}"
        today_sectors = today_raw_data.get(key, {}).get("data", [])

        # 今日前10 + extra_names 合并去重（保持顺序）
        target_names: list[str] = []
        seen: set[str] = set()
        for s in today_sectors[:10]:
            n = s.get("name", "")
            if n and n not in seen:
                target_names.append(n)
                seen.add(n)
        for n in extra_names:
            if n and n not in seen:
                target_names.append(n)
                seen.add(n)

        if not target_names:
            return []

        # 加载所有历史数据（一次性，避免重复 IO）
        history, all_dates = self._load_history(sector_type)

        # 将今日数据注入历史序列（今日 YAML 在 analyze 调用时尚未保存）
        if date and date not in set(all_dates):
            bisect.insort(all_dates, date)
            for idx, s in enumerate(today_sectors):
                name_s = s.get("name", "")
                if not name_s or name_s not in seen:
                    continue
                history.setdefault(name_s, []).append({
                    "date": date,
                    "rank": idx + 1,
                    "change_pct": float(s.get("change_pct", 0) or 0),
                    "volume_billion": float(s.get("volume_billion", 0) or 0),
                    "top_stock": s.get("top_stock", ""),
                })

        results = []
        for name in target_names:
            series = history.get(name, [])
            sig = self._compute_signals(series, date, all_dates)
            classification = self._classify_phase(sig)

            results.append({
                "name": name,
                "sector_type": sector_type,
                "rank_today": sig.get("rank_today"),
                "change_today": sig.get("change_today"),
                "top_stock_today": sig.get("top_stock_today", ""),
                "consecutive_in_top30": sig.get("consecutive_in_top30", 0),
                "cumulative_pct_5d": sig.get("cumulative_pct_5d", 0),
                "cumulative_pct_10d": sig.get("cumulative_pct_10d", 0),
                "phase": classification["phase"],
                "confidence": classification["confidence"],
                "evidence": classification["evidence"],
                "signals": {
                    k: v for k, v in sig.items()
                    if k != "data_days"
                },
            })

        return results

    def load_main_theme_names(self) -> list[str]:
        """
        从 tracking/main-theme.yaml 读取当前主线和潜在主线的板块名。
        """
        path = self.base_dir / "tracking" / "main-theme.yaml"
        names: list[str] = []
        if not path.exists():
            return names
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            current = data.get("current_main_theme", {})
            name = current.get("name", "").strip()
            if name:
                names.append(name)
            for pt in data.get("potential_themes", []):
                n = (pt.get("name") or "").strip()
                if n and n not in names:
                    names.append(n)
        except Exception as e:
            logger.warning(f"读取 main-theme.yaml 失败: {e}")
        return names


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _max_consecutive_in_dates(by_date: set | dict, all_dates: list[str]) -> int:
    """计算在给定日期序列中，板块最长的连续上榜天数。"""
    if not all_dates:
        return 0
    date_set = set(by_date) if not isinstance(by_date, set) else by_date
    max_c = cur = 0
    for d in all_dates:
        if d in date_set:
            cur += 1
            max_c = max(max_c, cur)
        else:
            cur = 0
    return max_c
