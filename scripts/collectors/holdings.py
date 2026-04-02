"""
持仓管理模块
管理持仓列表、自动获取持仓相关数据
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def collect_info_for_stocks(registry, stocks: list[tuple[str, str]], date: str) -> dict:
    """
    采集一组股票的信息面数据（新闻/互动易/研报），持仓和关注池共用。

    Args:
        registry: ProviderRegistry 实例
        stocks: [(code, name), ...] 列表
        date: YYYY-MM-DD 格式日期

    Returns:
        {code: {"name", "news", "investor_qa", "research_reports"}}
    """
    if not registry:
        return {}
    d = datetime.strptime(date, "%Y-%m-%d")
    start = (d - timedelta(days=7)).strftime("%Y-%m-%d")

    results: dict = {}
    for code, name in stocks:
        if not code or not code.strip():
            continue
        info: dict = {"name": name}

        news_r = registry.call("get_stock_news", code, date, 5)
        info["news"] = news_r.data if news_r.success and news_r.data else []

        qa_r = registry.call("get_investor_qa", code, start, date)
        info["investor_qa"] = qa_r.data if qa_r.success and qa_r.data else []

        rr_r = registry.call("get_research_reports", code)
        info["research_reports"] = rr_r.data if rr_r.success and rr_r.data else []

        if info["news"] or info["investor_qa"] or info["research_reports"]:
            results[code] = info

    return results

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

    def merge_sqlite_active_holdings(self, db_path: os.PathLike | str | None = None) -> None:
        """将 SQLite 中 active 持仓并入列表：与 Web/API 持仓池对齐。

        - 若 DB 无 active 记录，不改变当前列表（仍仅用 tracking/holdings.yaml）。
        - 若有 DB 记录：先按 DB 生成采集用条目，再把「仅存在于 YAML、不在 DB」的标的追加在后，
          避免 CLI 只维护 YAML 时被覆盖丢失。
        """
        from db.connection import get_db
        from db import queries as Q
        from db.dual_write import _normalize_stock_code_for_match

        try:
            with get_db(db_path) as conn:
                db_rows = Q.get_holdings(conn, status="active")
        except Exception as e:
            logger.warning("无法读取 SQLite 持仓以合并盘后采集: %s", e)
            return

        if not db_rows:
            return

        merged: list[dict] = []
        seen_norm: set[str] = set()
        for row in db_rows:
            code = (row.get("stock_code") or "").strip()
            if not code:
                continue
            n = _normalize_stock_code_for_match(code)
            seen_norm.add(n)
            merged.append({
                "code": code,
                "name": row.get("stock_name") or "",
                "shares": int(row["shares"] or 0),
                "cost": float(row["entry_price"] or 0),
                "sector": row.get("sector") or "",
            })

        for h in self._holdings:
            y_code = (h.get("code") or "").strip()
            n = _normalize_stock_code_for_match(y_code)
            if n and n not in seen_norm:
                seen_norm.add(n)
                merged.append(h)

        self._holdings = merged
        logger.info("盘后持仓列表已与 SQLite 合并: %d 只（含 YAML 独有标的）", len(merged))

    def sync_yaml_stock_to_sqlite(self, stock: dict, db_path: os.PathLike | str | None = None) -> None:
        """`holdings --add` 写入 YAML 后同步到 SQLite，与 Web / `db holdings-add` 对齐。"""
        from db.connection import get_db
        from db.migrate import migrate
        from db import queries as Q

        code = (stock.get("code") or "").strip()
        if not code:
            return
        try:
            with get_db(db_path) as conn:
                migrate(conn)
                Q.upsert_holding(
                    conn,
                    stock_code=code,
                    stock_name=stock.get("name") or "",
                    shares=int(stock.get("shares") or 0),
                    entry_price=float(stock.get("cost") or 0),
                    sector=(stock.get("sector") or "") or None,
                    status="active",
                )
        except Exception as e:
            logger.warning("同步持仓到 SQLite 失败（YAML 已保存）: %s", e)

    def sync_yaml_remove_from_sqlite(self, code: str, db_path: os.PathLike | str | None = None) -> None:
        """`holdings --remove` 更新 YAML 后将对应 active 行置为 closed。"""
        from db.connection import get_db
        from db.migrate import migrate
        from db import queries as Q

        try:
            with get_db(db_path) as conn:
                migrate(conn)
                Q.close_active_holdings_by_code(conn, code)
        except Exception as e:
            logger.warning("从 SQLite 移除持仓失败（YAML 已更新）: %s", e)

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

    def collect_stock_info(self, date: str) -> dict:
        """
        采集持仓个股的信息面数据（互动易、研报、新闻），用于盘前简报。
        返回 {code: {"name", "news", "investor_qa", "research_reports"}}
        """
        stocks = [(h["code"], h["name"]) for h in self._holdings]
        return collect_info_for_stocks(self.registry, stocks, date)

    def enrich_with_ma(self, holdings_data: list[dict], date: str) -> list[dict]:
        """为持仓行情数据补充均线和板块相对表现（原地修改）。"""
        if not self.registry:
            return holdings_data

        sector_map: dict[str, float] | None = None

        for item in holdings_data:
            code = item.get("code", "")
            if not code or "error" in item:
                continue
            r = self.registry.call("get_stock_ma", code, date)
            if r.success and r.data:
                for k in ("ma5", "ma10", "ma20", "volume_ma5"):
                    if k in r.data:
                        item[k] = r.data[k]
                if "volume_ma5" in r.data and item.get("volume"):
                    item["volume_vs_ma5"] = "以上" if item["volume"] > r.data["volume_ma5"] else "以下"
            sector = item.get("sector", "")
            if sector:
                if sector_map is None:
                    sr = self.registry.call("get_sector_rankings", date, "industry")
                    sector_map = {}
                    if sr.success and sr.data:
                        for s in sr.data.get("top", []) + sr.data.get("bottom", []):
                            sector_map[s.get("name", "")] = s.get("change_pct", 0)
                if sector in sector_map:
                    item["sector_change_pct"] = sector_map[sector]
        return holdings_data

    @staticmethod
    def compute_summary(holdings_data: list[dict]) -> dict:
        """计算持仓汇总统计。"""
        valid = [h for h in holdings_data if "error" not in h]
        total_cost = 0.0
        total_market_value = 0.0
        for h in valid:
            shares = h.get("shares", 0)
            cost = h.get("cost", 0)
            close = h.get("close", 0)
            total_cost += shares * cost
            total_market_value += shares * close

        total_pnl = total_market_value - total_cost
        total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost else 0.0

        return {
            "total_stocks": len(valid),
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": total_pnl_pct,
        }
