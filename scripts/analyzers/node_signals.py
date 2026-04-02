"""
客观节点信号分析器

在盘后数据采集完成后运行，对比今日数据与历史序列，
输出结构化的节点事件列表（node_signals），供复盘第六步「节点判断」使用。

每条信号结构：
    {
        "type":      "ma_cross" | "volume_extreme" | "limit_extreme" | "breadth_extreme",
        "signal":    "突破MA20" | "成交额创5日新高" | ...,
        "direction": "positive" | "negative" | "neutral",
        "value":     <数值>,
        "description": <可读描述>
    }
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class NodeSignalAnalyzer:
    """基于历史 YAML 序列生成客观节点信号"""

    def __init__(self, base_dir: Path, history_days: int = 20):
        self.base_dir = base_dir
        self.history_days = history_days

    def analyze(self, today_result: dict, date: str) -> list[dict]:
        """
        分析今日数据，生成节点信号列表。

        Args:
            today_result: collect_post_market 返回的当日数据字典
            date: 当日日期 YYYY-MM-DD

        Returns:
            信号列表，无信号时返回空列表
        """
        history = self._load_history(date)
        signals: list[dict] = []

        signals += self._check_ma_cross(today_result, history)
        signals += self._check_volume_extreme(today_result, history)
        signals += self._check_limit_extreme(today_result, history)
        signals += self._check_breadth_extreme(today_result, history)

        logger.info(f"节点信号分析完成，共 {len(signals)} 条信号")
        return signals

    # ------------------------------------------------------------------
    # 历史数据加载
    # ------------------------------------------------------------------

    def _load_history(self, date: str) -> list[dict]:
        """加载最近 history_days 个交易日的快照（不含今日）"""
        daily_dir = self.base_dir / "daily"
        snapshots: list[dict] = []
        try:
            dirs = sorted(
                [d for d in daily_dir.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            return []

        for d in dirs[: self.history_days]:
            pm_file = d / "post-market.yaml"
            if not pm_file.exists():
                continue
            try:
                with open(pm_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw = data.get("raw_data", data)
                snap = {
                    "date": d.name,
                    "ma": raw.get("moving_averages", {}).get("shanghai", {}),
                    "volume": raw.get("total_volume", {}),
                    "limit_up": raw.get("limit_up", {}),
                    "breadth": raw.get("breadth", {}),
                }
                snapshots.append(snap)
            except Exception:
                continue

        return snapshots

    # ------------------------------------------------------------------
    # MA 突破/跌破信号
    # ------------------------------------------------------------------

    def _check_ma_cross(self, today: dict, history: list[dict]) -> list[dict]:
        signals: list[dict] = []
        ma_today = today.get("moving_averages", {}).get("shanghai", {})
        if not ma_today or not history:
            return signals

        ma_prev = history[0].get("ma", {}) if history else {}

        for period, label in [(5, "MA5"), (10, "MA10"), (20, "MA20"),
                               (60, "MA60"), ("5w", "5周均线")]:
            key = f"above_ma{period}"
            today_above = ma_today.get(key)
            prev_above = ma_prev.get(key)
            ma_val = ma_today.get(f"ma{period}")

            if today_above is None or prev_above is None:
                continue

            if today_above and not prev_above:
                signals.append({
                    "type": "ma_cross",
                    "signal": f"突破{label}",
                    "direction": "positive",
                    "value": ma_val,
                    "description": f"上证指数收盘价上穿{label}（{ma_val}）",
                })
            elif not today_above and prev_above:
                signals.append({
                    "type": "ma_cross",
                    "signal": f"跌破{label}",
                    "direction": "negative",
                    "value": ma_val,
                    "description": f"上证指数收盘价下穿{label}（{ma_val}）",
                })

        return signals

    # ------------------------------------------------------------------
    # 成交额极值信号
    # ------------------------------------------------------------------

    def _check_volume_extreme(self, today: dict, history: list[dict]) -> list[dict]:
        signals: list[dict] = []
        vol_today = today.get("total_volume", {})
        today_vol = _safe_float(vol_today.get("total_billion"))
        if today_vol is None or not history:
            return signals

        hist_vols: list[float] = []
        for s in history:
            v = _safe_float(s["volume"].get("total_billion"))
            if v is not None:
                hist_vols.append(v)
        if not hist_vols:
            return signals

        for n, label in [(5, "5日"), (10, "10日"), (20, "20日")]:
            window = hist_vols[:n]
            if len(window) < n:
                continue
            if today_vol > max(window):
                signals.append({
                    "type": "volume_extreme",
                    "signal": f"成交额创{label}新高",
                    "direction": "positive",
                    "value": round(today_vol, 0),
                    "description": f"全市场成交额 {today_vol:.0f}亿，创近{label}新高",
                })
                break
            if today_vol < min(window):
                signals.append({
                    "type": "volume_extreme",
                    "signal": f"成交额创{label}新低",
                    "direction": "negative",
                    "value": round(today_vol, 0),
                    "description": f"全市场成交额 {today_vol:.0f}亿，创近{label}新低",
                })
                break

        # 偏离5日均量超 ±30%
        if len(hist_vols) >= 5:
            ma5 = sum(hist_vols[:5]) / 5
            if ma5 > 0:
                deviation = (today_vol - ma5) / ma5 * 100
                if deviation >= 30:
                    signals.append({
                        "type": "volume_extreme",
                        "signal": "放量（超5日均量30%）",
                        "direction": "positive",
                        "value": round(deviation, 1),
                        "description": f"今日成交额较5日均量 {ma5:.0f}亿 放量 {deviation:.1f}%",
                    })
                elif deviation <= -30:
                    signals.append({
                        "type": "volume_extreme",
                        "signal": "缩量（低于5日均量30%）",
                        "direction": "negative",
                        "value": round(deviation, 1),
                        "description": f"今日成交额较5日均量 {ma5:.0f}亿 缩量 {abs(deviation):.1f}%",
                    })

        return signals

    # ------------------------------------------------------------------
    # 涨跌停极值信号
    # ------------------------------------------------------------------

    def _check_limit_extreme(self, today: dict, history: list[dict]) -> list[dict]:
        signals: list[dict] = []
        lu_today = today.get("limit_up", {})
        lu_count = _safe_int(lu_today.get("count"))
        broken_rate = _safe_float(lu_today.get("broken_rate_pct"))
        seal_rate = _safe_float(lu_today.get("seal_rate_pct"))

        if not history:
            return signals

        hist_lu_counts = [
            _safe_int(s["limit_up"].get("count"))
            for s in history if _safe_int(s["limit_up"].get("count")) is not None
        ]

        if lu_count is not None and hist_lu_counts:
            for n, label in [(5, "5日"), (10, "10日"), (20, "20日")]:
                window = hist_lu_counts[:n]
                if len(window) < n:
                    continue
                if lu_count > max(window):
                    signals.append({
                        "type": "limit_extreme",
                        "signal": f"涨停数创{label}新高",
                        "direction": "positive",
                        "value": lu_count,
                        "description": f"涨停数 {lu_count} 只，创近{label}新高（前高 {max(window)}）",
                    })
                    break
                if lu_count < min(window):
                    signals.append({
                        "type": "limit_extreme",
                        "signal": f"涨停数创{label}新低",
                        "direction": "negative",
                        "value": lu_count,
                        "description": f"涨停数 {lu_count} 只，创近{label}新低（前低 {min(window)}）",
                    })
                    break

        if broken_rate is not None and broken_rate > 50:
            signals.append({
                "type": "limit_extreme",
                "signal": f"炸板率异常高（{broken_rate:.1f}%）",
                "direction": "negative",
                "value": broken_rate,
                "description": f"炸板率 {broken_rate:.1f}% > 50%，情绪偏弱",
            })

        if seal_rate is not None and seal_rate < 30:
            signals.append({
                "type": "limit_extreme",
                "signal": f"封板率偏低（{seal_rate:.1f}%）",
                "direction": "negative",
                "value": seal_rate,
                "description": f"封板率 {seal_rate:.1f}% < 30%，资金分歧较大",
            })

        return signals

    # ------------------------------------------------------------------
    # 市场宽度极值信号
    # ------------------------------------------------------------------

    def _check_breadth_extreme(self, today: dict, history: list[dict]) -> list[dict]:
        signals: list[dict] = []
        breadth = today.get("breadth", {})
        advance = _safe_int(breadth.get("advance"))
        total = _safe_int(breadth.get("total"))
        if advance is None or total is None or total == 0:
            return signals

        ratio = advance / total * 100

        if ratio >= 80:
            signals.append({
                "type": "breadth_extreme",
                "signal": f"上涨家数占比极高（{ratio:.1f}%）",
                "direction": "positive",
                "value": round(ratio, 1),
                "description": f"上涨家数占比 {ratio:.1f}%，普涨格局",
            })
        elif ratio <= 20:
            signals.append({
                "type": "breadth_extreme",
                "signal": f"上涨家数占比极低（{ratio:.1f}%）",
                "direction": "negative",
                "value": round(ratio, 1),
                "description": f"上涨家数占比 {ratio:.1f}%，普跌格局",
            })

        return signals


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    return int(f) if f is not None else None
