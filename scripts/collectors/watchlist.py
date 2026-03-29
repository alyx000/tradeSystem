"""
关注池采集器

晚间任务（18:00）读取 tracking/watchlist.yaml，
为 tier1_core 和 tier2_watch 中的有效股票更新当日行情，
并在价格触及目标价/止损位时生成到价提醒。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WATCHLIST_FILE = BASE_DIR / "tracking" / "watchlist.yaml"

# 到价阈值：价格距离目标/止损在此比例内触发提醒
ALERT_THRESHOLD = 0.02  # 2%


class WatchlistCollector:
    """关注池行情采集 + 到价提醒"""

    def __init__(self, registry):
        self.registry = registry
        self._data: dict = {}
        self._alerts: list[dict] = []

    def load(self) -> dict:
        """加载 watchlist.yaml"""
        if WATCHLIST_FILE.exists():
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}
        return self._data

    def save(self) -> None:
        """保存更新后的 watchlist.yaml"""
        self._data["last_updated"] = datetime.now().isoformat()
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def collect(self, trade_date: str) -> dict:
        """
        采集关注池行情，返回采集摘要。

        Args:
            trade_date: 交易日期，YYYY-MM-DD

        Returns:
            {
                "tier1": [...],   # 核心关注采集结果
                "tier2": [...],   # 观察池采集结果
                "alerts": [...],  # 触发的到价提醒
            }
        """
        self.load()
        self._alerts = []

        tier1_results = self._collect_tier1(trade_date)
        tier2_results = self._collect_tier2(trade_date)

        self.save()

        return {
            "date": trade_date,
            "tier1": tier1_results,
            "tier2": tier2_results,
            "alerts": self._alerts,
        }

    def _collect_tier1(self, trade_date: str) -> list[dict]:
        """采集核心关注（tier1_core）行情并更新字段"""
        results = []
        stocks = self._data.get("tier1_core", [])

        for stock in stocks:
            code = stock.get("stock_code", "").strip()
            name = stock.get("stock_name", "")
            if not code:
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  tier1 {code} 行情获取失败：{r.error}")
                results.append({"code": code, "name": name, "error": r.error})
                continue

            data = r.data
            close = data.get("close", 0)
            change_pct = data.get("change_pct", 0)

            # 自动判断走势状态
            if change_pct >= 3:
                status = "走强"
            elif change_pct <= -3:
                status = "走弱"
            else:
                status = "震荡"

            # 自动判断量能状态（用换手率粗判）
            turnover = data.get("turnover_rate", 0)
            if turnover >= 5:
                vol_status = "放量"
            elif turnover <= 1:
                vol_status = "缩量"
            else:
                vol_status = "正常"

            # 更新 YAML 字段
            stock["current_price"] = close
            stock["current_status"] = status
            stock["volume_status"] = vol_status

            entry = {
                "code": code,
                "name": name,
                "close": close,
                "change_pct": change_pct,
                "status": status,
                "vol_status": vol_status,
            }

            # 检查到价条件
            alerts = self._check_alerts(stock, close, change_pct)
            entry["alerts"] = alerts
            self._alerts.extend(alerts)

            results.append(entry)

        return results

    def _collect_tier2(self, trade_date: str) -> list[dict]:
        """采集观察池（tier2_watch）行情，异动时记录"""
        results = []
        stocks = self._data.get("tier2_watch", [])

        for stock in stocks:
            code = stock.get("stock_code", "").strip()
            name = stock.get("stock_name", "")
            if not code:
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  tier2 {code} 行情获取失败：{r.error}")
                continue

            data = r.data
            close = data.get("close", 0)
            change_pct = data.get("change_pct", 0)

            entry = {
                "code": code,
                "name": name,
                "close": close,
                "change_pct": change_pct,
            }

            # 涨幅 > 5% 或涨停，记录异动提醒
            if change_pct >= 5:
                alert = {
                    "type": "tier2_surge",
                    "code": code,
                    "name": name,
                    "change_pct": change_pct,
                    "message": f"观察池异动：{name}({code}) +{change_pct:.1f}%，关注是否满足升级条件",
                }
                self._alerts.append(alert)
                entry["alert"] = alert["message"]

            results.append(entry)

        return results

    def _check_alerts(self, stock: dict, close: float, change_pct: float) -> list[dict]:
        """检查 tier1 股票的到价/止损提醒条件"""
        alerts = []
        code = stock.get("stock_code", "")
        name = stock.get("stock_name", "")
        target = stock.get("target_price", 0)
        stop_loss = stock.get("stop_loss", 0)

        # 涨停提醒
        if change_pct >= 9.9:
            alerts.append({
                "type": "limit_up",
                "code": code,
                "name": name,
                "message": f"涨停！{name}({code}) {close:.2f} 涨停",
            })
            return alerts

        # 跌停提醒
        if change_pct <= -9.9:
            alerts.append({
                "type": "limit_down",
                "code": code,
                "name": name,
                "message": f"跌停！{name}({code}) {close:.2f} 跌停",
            })
            return alerts

        # 接近目标价
        if target and target > 0 and close >= target * (1 - ALERT_THRESHOLD):
            pct_from_target = (close - target) / target * 100
            alerts.append({
                "type": "near_target",
                "code": code,
                "name": name,
                "close": close,
                "target": target,
                "message": (
                    f"接近目标价：{name}({code}) 现价 {close:.2f}，"
                    f"目标价 {target:.2f}（{pct_from_target:+.1f}%）"
                ),
            })

        # 接近止损位
        if stop_loss and stop_loss > 0 and close <= stop_loss * (1 + ALERT_THRESHOLD):
            pct_from_stop = (close - stop_loss) / stop_loss * 100
            alerts.append({
                "type": "near_stop_loss",
                "code": code,
                "name": name,
                "close": close,
                "stop_loss": stop_loss,
                "message": (
                    f"接近止损位：{name}({code}) 现价 {close:.2f}，"
                    f"止损位 {stop_loss:.2f}（{pct_from_stop:+.1f}%）"
                ),
            })

        return alerts

    def format_report(self, result: dict) -> str:
        """生成可推送的关注池日报文本"""
        if not result:
            return ""

        date = result.get("date", "")
        tier1 = result.get("tier1", [])
        tier2 = result.get("tier2", [])
        alerts = result.get("alerts", [])

        lines = [f"**关注池日报 {date}**", ""]

        if tier1:
            lines.append("【核心关注】")
            for s in tier1:
                if s.get("error"):
                    lines.append(f"• {s['name']}({s['code']}) 数据获取失败")
                    continue
                pct = s.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"• {s['name']} {s.get('close', 0):.2f} ({sign}{pct:.1f}%) "
                    f"{s.get('status', '')} / {s.get('vol_status', '')}"
                )
        else:
            lines.append("【核心关注】无")

        if tier2:
            lines.append("")
            lines.append("【观察池】")
            for s in tier2:
                pct = s.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                alert_mark = " ⚠️" if s.get("alert") else ""
                lines.append(
                    f"• {s['name']} {s.get('close', 0):.2f} ({sign}{pct:.1f}%){alert_mark}"
                )

        if alerts:
            lines.append("")
            lines.append("【提醒】")
            for a in alerts:
                lines.append(f"• {a['message']}")

        return "\n".join(lines)
