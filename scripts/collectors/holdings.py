"""
持仓管理模块
管理持仓列表、自动获取持仓相关数据
"""
from __future__ import annotations

import os
import logging
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class HoldingsCollector:
    """持仓信息管理"""

    def __init__(self, registry=None):
        self.registry = registry
        self.holdings_file = BASE_DIR / "tracking" / "holdings.yaml"
        self._holdings = []

    def load(self) -> list[dict]:
        """加载当前持仓"""
        if self.holdings_file.exists():
            with open(self.holdings_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                self._holdings = data.get("holdings", [])
        return self._holdings

    def save(self) -> None:
        """保存持仓"""
        data = {
            "last_updated": datetime.now().isoformat(),
            "update_source": "manual",
            "holdings": self._holdings,
        }
        with open(self.holdings_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    def update_holdings(self, holdings: list[dict]) -> None:
        """
        更新持仓列表。

        每条记录格式:
        {
            "code": "688041.SH",
            "name": "海光信息",
            "shares": 200,
            "cost": 225.0,
            "sector": "国产AI链",
        }
        """
        self._holdings = holdings
        self.save()
        logger.info(f"持仓已更新: {len(holdings)} 只")

    def add_stock(self, stock: dict) -> None:
        """添加一只持仓"""
        # 检查是否已存在
        for h in self._holdings:
            if h["code"] == stock["code"]:
                h.update(stock)
                self.save()
                return
        self._holdings.append(stock)
        self.save()

    def remove_stock(self, code: str) -> None:
        """移除持仓"""
        self._holdings = [h for h in self._holdings if h["code"] != code]
        self.save()

    def get_codes(self) -> list[str]:
        """获取持仓代码列表"""
        return [h["code"] for h in self._holdings]

    def get_names(self) -> list[str]:
        """获取持仓名称列表（用于新闻搜索）"""
        return [h["name"] for h in self._holdings]

    def collect_holdings_data(self, date: str) -> list[dict]:
        """采集所有持仓股的行情数据"""
        if not self.registry:
            return []
        results = []
        for h in self._holdings:
            r = self.registry.call("get_stock_daily", h["code"], date)
            if r.success:
                stock_data = r.data
                stock_data["name"] = h["name"]
                stock_data["cost"] = h.get("cost", 0)
                stock_data["shares"] = h.get("shares", 0)
                stock_data["sector"] = h.get("sector", "")
                # 计算盈亏
                if h.get("cost") and stock_data.get("close"):
                    stock_data["pnl_pct"] = round(
                        (stock_data["close"] - h["cost"]) / h["cost"] * 100, 2
                    )
                results.append(stock_data)
            else:
                results.append({"code": h["code"], "name": h["name"], "error": r.error})
        return results

    def collect_holdings_announcements(self, start_date: str, end_date: str) -> dict:
        """采集持仓股的公告"""
        if not self.registry:
            return {}
        results = {}
        for h in self._holdings:
            r = self.registry.call("get_stock_announcements", h["code"], start_date, end_date)
            if r.success:
                results[h["code"]] = {
                    "name": h["name"],
                    "announcements": r.data,
                    "_source": r.source,
                }
        return results
