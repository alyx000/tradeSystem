"""
板块节奏分析器

基于过去 N 个交易日的每日板块涨幅排名快照，计算量化信号并判断板块所处节奏阶段：
启动 / 发酵 / 高潮 / 首次分歧 / 震荡 / 衰退 / 观察中
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as calendar_date, datetime
import logging
import math
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from utils.post_archive import read_archive

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


@dataclass
class _RankingWindow:
    as_of: str
    dates: list[str]
    calendar_dates: list[str]
    calendar_issue: str | None
    # 只留两份排名，每个大信封解析后即可释放。
    rankings: dict[str, dict]
    coverage: dict[str, dict] = field(default_factory=dict)


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

    def _sorted_daily_dirs(self, as_of: str | None = None) -> list[Path]:
        if not self.daily_dir.is_dir():
            return []
        dirs = []
        for directory in self.daily_dir.iterdir():
            if as_of is not None and directory.name > as_of:
                continue
            try:
                if calendar_date.fromisoformat(directory.name).isoformat() != directory.name:
                    continue
            except ValueError:
                continue
            if (directory / "post-market.yaml").is_file():
                dirs.append(directory)
        return sorted(dirs)[-self.history_days:]

    def load_ranking_window(self, as_of: str, today: dict | None = None) -> _RankingWindow:
        """当日榜占窗口内一个位置。只打开目标日及之前的信封，各日期仅读一次。"""
        from analyzers.sector_rhythm_metrics import open_window

        if calendar_date.fromisoformat(as_of).isoformat() != as_of:
            raise ValueError("板块节奏目标日须为 YYYY-MM-DD")
        if today is not None and str(today.get("date", as_of)) != as_of:
            raise ValueError("板块节奏输入日期与目标日不符")
        calendar_days, issue = open_window(as_of, self.history_days, base_dir=self.base_dir)
        dates = list(calendar_days)
        if not dates and not (self.base_dir / "data" / "trade.db").exists():
            # 独立旧归档可能没有交易日历。保留档案视图，显式标记日历未核验。
            dates = sorted({d.name for d in self._sorted_daily_dirs(as_of)} | {as_of})[-self.history_days:]
        rankings = {}
        for day in dates:
            if day == as_of and today is not None:
                raw = today
            else:
                path = self.daily_dir / day / "post-market.yaml"
                try:
                    envelope = read_archive(path)
                    raw = envelope.get("raw_data", envelope)
                    if not isinstance(raw, dict):
                        raise ValueError("盘后来源结构无效")
                    stamps = [str(doc["date"]) for doc in (envelope, raw) if "date" in doc]
                    if not stamps or any(stamp != day for stamp in stamps):
                        raise ValueError("盘后信封日期与目录不符")
                    generated = envelope.get("generated_at", raw.get("generated_at"))
                    if generated is not None:
                        stamp = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
                        shanghai = ZoneInfo("Asia/Shanghai")
                        local = stamp.replace(tzinfo=shanghai) if stamp.tzinfo is None else stamp.astimezone(shanghai)
                        if local.date().isoformat() != day or local.hour < 15:
                            raise ValueError("盘后榜单未在事实日收盘后归档")
                    elif calendar_days:
                        raise ValueError("盘后榜单缺少生成时间")
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    logger.warning("板块节奏归档 %s 不可用：%s", day, exc)
                    continue
            rankings[day] = {t: raw.get(f"sector_{t}") for t in ("industry", "concept")}
            if day != as_of or today is None:
                del envelope, raw
        return _RankingWindow(as_of, dates, list(calendar_days), issue, rankings)

    def _load_history(self, sector_type: str, window: _RankingWindow | None = None):
        """将指定板块榜逐日展开，保留真实可用榜单日期。"""
        if window is None:
            dirs = self._sorted_daily_dirs()
            if not dirs:
                return {}, []
            window = self.load_ranking_window(dirs[-1].name)
        history: dict[str, list[dict]] = {}
        observed = []
        for day, ranking in window.rankings.items():
            block = ranking.get(sector_type)
            if (not isinstance(block, dict) or block.get("error")
                    or block.get("status") in ("partial", "failed", "source_failed")):
                continue
            rows = block.get("data")
            if rows == [] and (block.get("status") == "empty" and block.get("_source")
                               and type(block.get("row_count")) is int and block["row_count"] == 0
                               and str(block.get("trade_date", "")).replace("-", "") == day.replace("-", "")):
                observed.append(day)
                continue
            if not isinstance(rows, list) or not rows:
                continue
            entries = {}
            for idx, row in enumerate(rows):
                name = row.get("name") if isinstance(row, dict) else None
                if (not isinstance(name, str) or not name.strip() or name in entries or
                        str(row.get("trade_date", day)).replace("-", "") != day.replace("-", "")):
                    break
                try:
                    change = float(row.get("change_pct"))
                    if not math.isfinite(change) or isinstance(row.get("change_pct"), bool):
                        change = None
                except (TypeError, ValueError, OverflowError):
                    change = None
                entries[name] = {
                    "date": day, "rank": idx + 1, "change_pct": change or 0.0,
                    "observed_change_pct": change, "top_stock": row.get("top_stock") or "",
                    "volume_billion": row.get("volume_billion"), "ts_code": row.get("ts_code"),
                    "_source": block.get("_source"),
                }
            else:
                observed.append(day)
                for name, entry in entries.items():
                    history.setdefault(name, []).append(entry)
        return history, observed

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
        change_today = today_entry.get("observed_change_pct", today_entry["change_pct"]) if today_entry else None
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
        streak_known = not sig.get("consecutive_is_lower_bound", False)
        five_days_known = sig.get("five_day_ranks_observed", True)

        scores: dict[str, int] = {p: 0 for p in _PHASE_PRIORITY}
        # 各阶段独立 evidence，避免混合
        ev: dict[str, list[str]] = {p: [] for p in _PHASE_PRIORITY}

        # ---- 启动信号 ----
        if streak_known and consecutive == 1:
            # 绝对首次上榜，最强启动信号（无法被发酵/高潮的"累积涨幅"虚假积累干扰）
            scores[PHASE_LAUNCH] += 2
            ev[PHASE_LAUNCH].append("今日首次进入前30（新兴启动）")
        elif streak_known and 2 <= consecutive <= 3:
            scores[PHASE_LAUNCH] += 1
            ev[PHASE_LAUNCH].append(f"连续上榜仅 {consecutive} 天（短期新兴）")
        # 排名靠前 / 短期跃升仅对「短连续」生效，避免老主线因名次波动被算成启动
        if streak_known and consecutive <= 3:
            if rank is not None and rank <= 15:
                scores[PHASE_LAUNCH] += 1
                ev[PHASE_LAUNCH].append(f"今日排名 #{rank}，位置靠前")
            if rank_change_3d is not None and rank_change_3d < -5:
                scores[PHASE_LAUNCH] += 2
                ev[PHASE_LAUNCH].append(f"排名 3 日跃升 {abs(int(rank_change_3d))} 位")

        # ---- 发酵信号 ----
        if streak_known and 3 < consecutive <= 12:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"连续上榜 {consecutive} 天（持续活跃）")
        if rank is not None and rank <= 12:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"今日排名 #{rank}，位置稳定")
        if five_days_known and cumul_5d > 3:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append(f"历史榜单窗口上榜日涨幅合计 +{cumul_5d}%（持续累积）")
        if accel is not None and accel >= 0:
            scores[PHASE_FERMENT] += 1
            ev[PHASE_FERMENT].append("涨速仍在加速或平稳")

        # ---- 高潮信号 ----
        if consecutive > 8:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append(f"连续上榜已达 {consecutive} 天（演绎充分）")
        if five_days_known and cumul_5d > 10:
            scores[PHASE_PEAK] += 2
            ev[PHASE_PEAK].append(f"历史榜单窗口上榜日涨幅合计 +{cumul_5d}%（幅度偏大）")
        if accel is not None and accel < 0:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append("涨速开始减缓")
        if stability is not None and stability < 0.4:
            scores[PHASE_PEAK] += 1
            ev[PHASE_PEAK].append("领涨股频繁切换（板块扩散）")

        # ---- 首次分歧信号 ----
        if streak_known and max_hist_consec > 5 and consecutive <= 2:
            scores[PHASE_DIVERGE] += 2
            ev[PHASE_DIVERGE].append(f"历史最长连续 {max_hist_consec} 天后近期掉榜（出现分歧）")
        if rank_change_3d is not None and rank_change_3d > 8:
            scores[PHASE_DIVERGE] += 2
            ev[PHASE_DIVERGE].append(f"排名 3 日下滑 {int(rank_change_3d)} 位")
        if change < 0 and days_in_top > 5:
            scores[PHASE_DIVERGE] += 1
            ev[PHASE_DIVERGE].append("有上榜历史但今日收跌")

        # ---- 震荡信号 ----
        if streak_known and days_in_top > 5 and consecutive <= 2 and max_hist_consec >= 3:
            scores[PHASE_OSCILLATE] += 2
            ev[PHASE_OSCILLATE].append(
                f"20天内上榜 {days_in_top} 天但近期中断，历史曾连续 {max_hist_consec} 天"
            )
        if five_days_known and rank_change_3d is not None and abs(rank_change_3d) <= 3 and cumul_5d < 3:
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
        *, window: _RankingWindow | None = None,
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
        date = str(today_date or today_raw_data.get("date", ""))
        window = window or self.load_ranking_window(date, today_raw_data)
        if window.as_of != date or str(today_raw_data.get("date", date)) != date:
            raise ValueError("板块节奏历史窗口与本次目标日不一致")
        history, all_dates = self._load_history(sector_type, window)
        # 只从日期和身份已核验的当日榜单选目标，保留原榜顺序。
        ranked = sorted((rows[-1]["rank"], name) for name, rows in history.items()
                        if rows[-1]["date"] == date and rows[-1]["rank"] <= 10)
        target_names = list(dict.fromkeys([name for _, name in ranked] + list(extra_names or [])))
        # 已核验日历中缺榜的日期保留原位，不能将前后两段误判成连续上榜。
        signal_dates = window.calendar_dates or all_dates
        missing = [d for d in window.dates if d not in all_dates]
        today_status = "unavailable"
        if date in all_dates:
            today_status = "covered" if window.rankings[date][sector_type]["data"] else "empty"
        coverage = dict(expected=self.history_days, observed=len(all_dates), missing_dates=missing,
                        calendar_issue=window.calendar_issue, as_of=date, today_status=today_status)
        window.coverage[sector_type] = coverage
        if not target_names:
            return []

        results = []
        for name in target_names:
            series = history.get(name, [])
            sig = self._compute_signals(series, date, signal_dates)
            sig["data_days"] = len(all_dates)
            streak = sig.get("consecutive_in_top30", 0)
            if window.calendar_dates:
                before = len(signal_dates) - streak - 1
                sig["consecutive_is_lower_bound"] = (
                    streak > 0 and ((before >= 0 and signal_dates[before] in missing)
                    or (before < 0 and window.calendar_issue is not None))
                )
                sig["five_day_ranks_observed"] = len(signal_dates) >= 5 and not any(d in missing for d in signal_dates[-5:])
                if not sig["five_day_ranks_observed"]:
                    sig["top_stock_stability_5d"] = None
                if len(signal_dates) < 3 or any(d in missing for d in signal_dates[-3:]):
                    sig["daily_accel_3d"] = None
            if date in all_dates:
                classification = self._classify_phase(sig)
            else:
                classification = {"phase": PHASE_WATCH, "confidence": "低",
                                  "evidence": ["目标日板块榜缺失，无法判断当日阶段"]}
                sig["consecutive_in_top30"] = None

            results.append({
                "name": name,
                "sector_type": sector_type,
                "history_coverage": coverage,
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

        from analyzers.sector_rhythm_metrics import project_rhythm_metrics
        return project_rhythm_metrics(results, today_raw_data, sector_type, date, history, all_dates,
                                      base_dir=self.base_dir, window_dates=window.calendar_dates)

    def load_main_theme_names(self, as_of: str | None = None) -> list[str]:
        """
        当日使用当前主线与潜在主线，回放仅使用带目标日期的历史记录。
        """
        path = self.base_dir / "tracking" / "main-theme.yaml"
        names: list[str] = []
        if not path.exists():
            return names
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if as_of is not None and as_of != datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
                for row in data.get("history") or []:
                    if not isinstance(row, dict) or str(row.get("date")) != as_of:
                        continue
                    name = row.get("main_theme")
                    if isinstance(name, str) and name.strip() and name.strip() not in names:
                        names.append(name.strip())
                return names
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
