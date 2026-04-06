"""
持仓管理模块
管理持仓列表、自动获取持仓相关数据
"""
from __future__ import annotations

import os
import logging
from collections import defaultdict
from datetime import date as _date, datetime, timedelta
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
    from db.dual_write import _normalize_stock_code_for_match

    d = datetime.strptime(date, "%Y-%m-%d")
    start = (d - timedelta(days=7)).strftime("%Y-%m-%d")
    limit_map: dict[str, dict] = {}

    try:
        limit_r = registry.call("get_stock_limit_prices", date)
        if limit_r.success and isinstance(limit_r.data, list):
            for row in limit_r.data:
                if not isinstance(row, dict):
                    continue
                norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
                if not norm:
                    continue
                limit_map[norm] = {
                    "up_limit": row.get("up_limit"),
                    "down_limit": row.get("down_limit"),
                    "pre_close": row.get("pre_close"),
                }
    except Exception as e:
        logger.debug("盘前涨跌停价获取失败: %s", e)

    results: dict = {}
    for code, name in stocks:
        if not code or not code.strip():
            continue
        info: dict = {"name": name}
        limit_prices = limit_map.get(_normalize_stock_code_for_match(code))
        if limit_prices:
            info["limit_prices"] = limit_prices

        news_r = registry.call("get_stock_news", code, date, 5)
        info["news"] = news_r.data if news_r.success and news_r.data else []

        qa_r = registry.call("get_investor_qa", code, start, date)
        info["investor_qa"] = qa_r.data if qa_r.success and qa_r.data else []

        rr_r = registry.call("get_research_reports", code)
        info["research_reports"] = rr_r.data if rr_r.success and rr_r.data else []

        if info.get("limit_prices") or info["news"] or info["investor_qa"] or info["research_reports"]:
            results[code] = info

    return results

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _date_digits(date_text: str) -> str:
    return str(date_text or "").replace("-", "").strip()


def _quarter_end_for_date(date_text: str) -> str:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    if dt.month <= 3:
        return f"{dt.year - 1}1231"
    if dt.month <= 6:
        return f"{dt.year}0331"
    if dt.month <= 9:
        return f"{dt.year}0630"
    return f"{dt.year}0930"


def _announcement_sort_key(item: dict) -> tuple[str, str]:
    return (
        str(item.get("ann_date") or ""),
        str(item.get("title") or ""),
    )


def _disclosure_sort_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get("ann_date") or item.get("pre_date") or ""),
        str(item.get("report_end") or item.get("end_date") or ""),
        str(item.get("ts_code") or item.get("code") or ""),
    )


def _merge_source_tags(*sources: str) -> str:
    seen: list[str] = []
    for source in sources:
        if source and source not in seen:
            seen.append(source)
    return ",".join(seen)


def collect_announcements_for_stocks(
    registry,
    stocks: list[tuple[str, str]],
    start_date: str,
    end_date: str,
    db_path: os.PathLike | str | None = None,
) -> dict:
    """
    采集一组股票的公告与披露计划。

    优先复用 ingest 原始层：
    - `anns_d`: 按日期范围展开 raw payload 并按股票过滤
    - `disclosure_date`: 读取截止 end_date 最近一次落库快照
    若原始层没有对应数据，再降级到 provider 在线查询。
    """
    from db.connection import get_db
    from db.migrate import migrate
    from db import queries as Q
    from db.dual_write import _normalize_stock_code_for_match

    if not stocks:
        return {}

    start_digits = _date_digits(start_date)
    end_digits = _date_digits(end_date)
    code_map = {
        _normalize_stock_code_for_match(code): (code, name)
        for code, name in stocks
        if _normalize_stock_code_for_match(code)
    }
    results: dict[str, dict] = {
        code: {
            "name": name,
            "announcements": [],
            "disclosure_dates": [],
            "_source": "",
        }
        for code, name in stocks
        if code and code.strip()
    }

    try:
        with get_db(db_path) as conn:
            migrate(conn)
            ann_rows = Q.list_raw_interface_rows(
                conn,
                interface_name="anns_d",
                biz_date_from=start_date,
                biz_date_to=end_date,
            )
            disclosure_rows = Q.get_latest_raw_interface_rows(
                conn,
                interface_name="disclosure_date",
                biz_date=end_date,
            )
    except Exception as e:
        logger.debug("读取 ingest 原始层公告/披露计划失败，将降级到 provider: %s", e)
        ann_rows = []
        disclosure_rows = []

    ann_by_code: dict[str, list[dict]] = defaultdict(list)
    for row in ann_rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if not norm or norm not in code_map:
            continue
        ann_date = str(row.get("ann_date") or "")
        if ann_date and not (start_digits <= ann_date <= end_digits):
            continue
        ann_by_code[norm].append(row)

    disclosure_by_code: dict[str, list[dict]] = defaultdict(list)
    for row in disclosure_rows:
        norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
        if not norm or norm not in code_map:
            continue
        plan_date = str(row.get("ann_date") or row.get("pre_date") or "")
        if plan_date and plan_date < start_digits:
            continue
        disclosure_by_code[norm].append(row)

    for norm_code, (raw_code, _name) in code_map.items():
        anns = sorted(ann_by_code.get(norm_code, []), key=_announcement_sort_key, reverse=True)
        disclosures = sorted(disclosure_by_code.get(norm_code, []), key=_disclosure_sort_key)
        if raw_code in results:
            results[raw_code]["announcements"] = anns
            results[raw_code]["disclosure_dates"] = disclosures[:5]
            if anns:
                results[raw_code]["_source"] = _merge_source_tags(results[raw_code]["_source"], "ingest:anns_d")
            if disclosures:
                results[raw_code]["_source"] = _merge_source_tags(results[raw_code]["_source"], "ingest:disclosure_date")

    if registry:
        missing_anns = [
            (code, name)
            for code, name in stocks
            if code in results and not results[code]["announcements"]
        ]
        for code, _name in missing_anns:
            r = registry.call("get_stock_announcements", code, start_date, end_date)
            if r.success and r.data is not None:
                results[code]["announcements"] = r.data
                results[code]["_source"] = _merge_source_tags(results[code]["_source"], r.source)

        missing_disclosures = [code for code, _name in stocks if code in results and not results[code]["disclosure_dates"]]
        if missing_disclosures:
            quarter_end = _quarter_end_for_date(end_date)
            disclosure_result = registry.call("get_disclosure_dates", end_date)
            if disclosure_result.success and disclosure_result.data:
                rows = disclosure_result.data if isinstance(disclosure_result.data, list) else [disclosure_result.data]
                grouped: dict[str, list[dict]] = defaultdict(list)
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    norm = _normalize_stock_code_for_match(row.get("ts_code") or row.get("code"))
                    if not norm:
                        continue
                    grouped[norm].append(row)
                for code in missing_disclosures:
                    norm = _normalize_stock_code_for_match(code)
                    plans = sorted(grouped.get(norm, []), key=_disclosure_sort_key)
                    if plans:
                        results[code]["disclosure_dates"] = plans[:5]
                        results[code]["_source"] = _merge_source_tags(
                            results[code]["_source"],
                            disclosure_result.source or f"tushare:disclosure_date:{quarter_end}",
                        )

    return {
        code: info
        for code, info in results.items()
        if info["announcements"] or info["disclosure_dates"] or info["_source"]
    }


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

    def refresh_sqlite_quotes(self, date: str, db_path: os.PathLike | str | None = None) -> dict:
        """安全回填 SQLite 持仓现价，不改 YAML 归档。"""
        from db.connection import get_db
        from db.migrate import migrate
        from db import queries as Q
        from db.dual_write import _normalize_stock_code_for_match

        self.merge_sqlite_active_holdings(db_path=db_path)
        holdings_data = self.collect_holdings_data(date)
        try:
            holdings_data = self.enrich_with_ma(holdings_data, date)
        except Exception as e:
            logger.warning("持仓均线/板块数据补充失败（现价回填继续）: %s", e)

        with get_db(db_path) as conn:
            migrate(conn)
            active_rows = Q.get_holdings(conn, status="active")
            should_update_current_price = date == _date.today().isoformat()
            active_by_norm: dict[str, list[dict]] = defaultdict(list)
            for row in active_rows:
                norm = _normalize_stock_code_for_match(row.get("stock_code"))
                if norm:
                    active_by_norm[norm].append(row)

            updated = 0
            current_price_updated = 0
            failed = 0
            skipped = 0
            items: list[dict] = []

            for item in holdings_data:
                code = str(item.get("code") or "").strip()
                name = str(item.get("name") or "").strip()
                norm = _normalize_stock_code_for_match(code)
                base = {
                    "code": code,
                    "name": name,
                    "close": item.get("close"),
                    "pnl_pct": item.get("pnl_pct"),
                    "turnover_rate": item.get("turnover_rate"),
                    "ma5": item.get("ma5"),
                    "ma10": item.get("ma10"),
                    "ma20": item.get("ma20"),
                    "volume_vs_ma5": item.get("volume_vs_ma5"),
                }

                if "error" in item:
                    failed += 1
                    items.append({
                        **base,
                        "status": "error",
                        "error": item.get("error"),
                    })
                    continue

                if not norm or not active_by_norm.get(norm):
                    skipped += 1
                    items.append({
                        **base,
                        "status": "skipped",
                        "reason": "未找到 active 持仓记录",
                    })
                    continue

                close = item.get("close")
                if close in (None, ""):
                    skipped += 1
                    items.append({
                        **base,
                        "status": "skipped",
                        "reason": "缺少收盘价",
                    })
                    continue

                matched_rows = active_by_norm[norm]
                Q.upsert_holding_quote_snapshot(
                    conn,
                    trade_date=date,
                    stock_code=code,
                    stock_name=name or None,
                    close=float(close),
                    pnl_pct=float(item["pnl_pct"]) if item.get("pnl_pct") not in (None, "") else None,
                    turnover_rate=float(item["turnover_rate"]) if item.get("turnover_rate") not in (None, "") else None,
                    ma5=float(item["ma5"]) if item.get("ma5") not in (None, "") else None,
                    ma10=float(item["ma10"]) if item.get("ma10") not in (None, "") else None,
                    ma20=float(item["ma20"]) if item.get("ma20") not in (None, "") else None,
                    volume_vs_ma5=item.get("volume_vs_ma5"),
                )
                if should_update_current_price:
                    for row in matched_rows:
                        Q.update_holding(conn, int(row["id"]), current_price=float(close))
                    current_price_updated += len(matched_rows)
                updated += len(matched_rows)
                items.append({
                    **base,
                    "status": "updated",
                    "matched_rows": len(matched_rows),
                    "current_price_updated": should_update_current_price,
                })

        return {
            "date": date,
            "updated": updated,
            "current_price_updated": current_price_updated,
            "failed": failed,
            "skipped": skipped,
            "items": items,
        }

    def collect_holdings_announcements(
        self,
        start_date: str,
        end_date: str,
        db_path: os.PathLike | str | None = None,
    ) -> dict:
        """采集持仓股的公告"""
        stocks = [(h["code"], h["name"]) for h in self._holdings if h.get("code")]
        return collect_announcements_for_stocks(self.registry, stocks, start_date, end_date, db_path=db_path)

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
