"""
关注池采集器

晚间任务（由 main.py post 在 20:00 流程前半段执行）读取 tracking/watchlist.yaml，
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

ALERT_THRESHOLD = 0.02  # 2%


def _get_limit_pct(code: str, name: str = "") -> float:
    """根据股票代码和名称确定涨跌停阈值百分比。"""
    if "ST" in name.upper():
        return 5.0
    code_num = code.split(".")[0]
    if code_num.startswith("688") or code_num.startswith("689"):
        return 20.0
    if code_num.startswith("300") or code_num.startswith("301"):
        return 20.0
    if code_num.startswith("8") or code_num.startswith("43"):
        return 30.0
    return 10.0


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

    def _is_blacklisted(self, code: str, ref_date: str = "") -> bool:
        """检查股票是否在黑名单中（未过期）。ref_date 为参考日期，默认当天。"""
        check_date = ref_date or datetime.now().strftime("%Y-%m-%d")
        for item in self._data.get("blacklist", []) or []:
            bl_code = (item.get("stock_code") or "").strip()
            if not bl_code or bl_code != code:
                continue
            until = (item.get("until") or "").strip()
            if not until or until >= check_date:
                return True
        return False

    def collect(self, trade_date: str) -> dict:
        """
        采集关注池行情，返回采集摘要。

        Args:
            trade_date: 交易日期，YYYY-MM-DD

        Returns:
            {
                "tier1": [...],   # 核心关注采集结果
                "tier2": [...],   # 观察池采集结果
                "tier3": [...],   # 板块龙头采集结果
                "alerts": [...],  # 触发的到价提醒
            }
        """
        self.load()
        self._alerts = []

        tier1_results = self._collect_tier1(trade_date)
        tier2_results = self._collect_tier2(trade_date)
        tier3_results = self._collect_tier3(trade_date)

        self.save()

        return {
            "date": trade_date,
            "tier1": tier1_results,
            "tier2": tier2_results,
            "tier3": tier3_results,
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

            if self._is_blacklisted(code, trade_date):
                logger.debug(f"  tier1 {code} 在黑名单中，跳过")
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  tier1 {code} 行情获取失败：{r.error}")
                results.append({"code": code, "name": name, "error": r.error})
                continue

            data = r.data
            close = data.get("close", 0)
            change_pct = data.get("change_pct", 0)

            if change_pct >= 3:
                status = "走强"
            elif change_pct <= -3:
                status = "走弱"
            else:
                status = "震荡"

            turnover = data.get("turnover_rate", 0)
            if turnover >= 5:
                vol_status = "放量"
            elif turnover <= 1:
                vol_status = "缩量"
            else:
                vol_status = "正常"

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

            alerts = self._check_alerts(stock, close, change_pct)
            entry["alerts"] = alerts
            self._alerts.extend(alerts)

            results.append(entry)

        return results

    def collect_watchlist_announcements(
        self,
        start_date: str,
        end_date: str,
        db_path: str | None = None,
    ) -> dict:
        """采集 tier1_core + tier2_watch 公告（与持仓公告逻辑一致，代码去重）"""
        self.load()
        from collectors.holdings import collect_announcements_for_stocks

        seen: set[str] = set()
        stocks: list[tuple[str, str]] = []
        for tier in ("tier1_core", "tier2_watch"):
            for stock in self._data.get(tier, []) or []:
                code = (stock.get("stock_code") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                stocks.append((code, stock.get("stock_name", code)))
        return collect_announcements_for_stocks(self.registry, stocks, start_date, end_date, db_path=db_path)

    def collect_watchlist_info(self, date: str) -> dict:
        """
        采集关注池个股的信息面数据（互动易、研报、新闻），用于盘前简报。
        去重 tier1 + tier2，返回 {code: {"name", "news", "investor_qa", "research_reports"}}
        """
        if not self.registry:
            return {}
        self.load()
        from collectors.holdings import collect_info_for_stocks

        seen: set[str] = set()
        stocks: list[tuple[str, str]] = []
        for tier in ("tier1_core", "tier2_watch"):
            for stock in self._data.get(tier, []) or []:
                code = (stock.get("stock_code") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                stocks.append((code, stock.get("stock_name", code)))

        return collect_info_for_stocks(self.registry, stocks, date)

    def _collect_tier2(self, trade_date: str) -> list[dict]:
        """采集观察池（tier2_watch）行情并更新 YAML 字段，异动时记录提醒"""
        results = []
        stocks = self._data.get("tier2_watch", [])

        for stock in stocks:
            code = stock.get("stock_code", "").strip()
            name = stock.get("stock_name", "")
            if not code:
                continue

            if self._is_blacklisted(code, trade_date):
                logger.debug(f"  tier2 {code} 在黑名单中，跳过")
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  tier2 {code} 行情获取失败：{r.error}")
                continue

            data = r.data
            close = data.get("close", 0)
            change_pct = data.get("change_pct", 0)

            stock["current_price"] = close
            stock["current_change_pct"] = change_pct

            entry = {
                "code": code,
                "name": name,
                "close": close,
                "change_pct": change_pct,
            }

            surge_threshold = _get_limit_pct(code, name) * 0.5
            if change_pct >= surge_threshold:
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

    def _collect_tier3(self, trade_date: str) -> list[dict]:
        """采集板块龙头（tier3_sector_leaders）行情"""
        results = []
        leaders = self._data.get("tier3_sector_leaders", [])

        for item in leaders:
            code_raw = item.get("leader_stock", "")
            if not code_raw:
                continue
            parts = code_raw.split(" ", 1)
            code = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else code
            sector = item.get("sector", "")
            if not code:
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  tier3 {code} 行情获取失败：{r.error}")
                continue

            data = r.data
            close = data.get("close", 0)
            change_pct = data.get("change_pct", 0)

            results.append({
                "code": code,
                "name": name,
                "sector": sector,
                "close": close,
                "change_pct": change_pct,
                "leader_type": item.get("leader_type", ""),
                "status": item.get("status", ""),
            })

        return results

    def _check_alerts(self, stock: dict, close: float, change_pct: float) -> list[dict]:
        """检查 tier1 股票的到价/止损提醒条件"""
        alerts = []
        code = stock.get("stock_code", "")
        name = stock.get("stock_name", "")
        target = stock.get("target_price", 0)
        stop_loss = stock.get("stop_loss", 0)

        limit_pct = _get_limit_pct(code, name)
        limit_threshold = limit_pct * 0.99

        if change_pct >= limit_threshold:
            alerts.append({
                "type": "limit_up",
                "code": code,
                "name": name,
                "message": f"涨停！{name}({code}) {close:.2f} 涨停",
            })
            return alerts

        if change_pct <= -limit_threshold:
            alerts.append({
                "type": "limit_down",
                "code": code,
                "name": name,
                "message": f"跌停！{name}({code}) {close:.2f} 跌停",
            })
            return alerts

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

    def get_watchlist_summary(self) -> dict:
        """返回当前关注池摘要（tier1 + tier2），供外部使用。"""
        self.load()
        tier1 = [
            {"code": s.get("stock_code", ""), "name": s.get("stock_name", ""),
             "current_price": s.get("current_price", 0), "current_status": s.get("current_status", "")}
            for s in self._data.get("tier1_core", []) if (s.get("stock_code") or "").strip()
        ]
        tier2 = [
            {"code": s.get("stock_code", ""), "name": s.get("stock_name", ""),
             "current_price": s.get("current_price", 0)}
            for s in self._data.get("tier2_watch", []) if (s.get("stock_code") or "").strip()
        ]
        return {"tier1": tier1, "tier2": tier2}

    def format_report(self, result: dict) -> str:
        """生成可推送的关注池日报文本"""
        if not result:
            return ""

        date = result.get("date", "")
        tier1 = result.get("tier1", [])
        tier2 = result.get("tier2", [])
        tier3 = result.get("tier3", [])
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

        if tier3:
            lines.append("")
            lines.append("【板块龙头】")
            for s in tier3:
                pct = s.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"• [{s.get('sector', '')}] {s['name']} {s.get('close', 0):.2f} ({sign}{pct:.1f}%)"
                )

        if alerts:
            lines.append("")
            lines.append("【提醒】")
            for a in alerts:
                lines.append(f"• {a.get('message', '(无消息)')}")

        return "\n".join(lines)
