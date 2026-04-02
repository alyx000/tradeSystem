"""
溢价率回填采集器

由 main.py post 在 20:00 流程前半段执行（T 日），计算 T-1 日涨停板 → T 日开盘溢价率，
回填写入 daily/T-1/post-market.yaml 的 premium_backfill 字段。

溢价率定义：
    premium_pct = (T_open - T-1_close) / T-1_close × 100

按连板天数分组统计：
    - 首板（limit_times=1）
    - 二板（limit_times=2）
    - 三板以上（limit_times≥3）
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DAILY_DIR = BASE_DIR / "daily"


class PremiumCollector:
    """T-1 涨停板次日开盘溢价率回填"""

    def __init__(self, registry):
        self.registry = registry

    def collect(self, trade_date: str, prev_date: str) -> Optional[dict]:
        """
        计算并回填溢价率。

        Args:
            trade_date: 当日日期 (T)，YYYY-MM-DD
            prev_date:  前一交易日 (T-1)，YYYY-MM-DD

        Returns:
            premium_backfill 字典，写入失败时返回 None
        """
        logger.info(f"溢价率回填：T-1={prev_date}，T={trade_date}")

        # 1. 读取 T-1 涨停股列表（已存于 post-market.yaml）
        prev_yaml = DAILY_DIR / prev_date / "post-market.yaml"
        if not prev_yaml.exists():
            logger.warning(f"T-1 盘后文件不存在，跳过溢价率回填：{prev_yaml}")
            return None

        with open(prev_yaml, "r", encoding="utf-8") as f:
            prev_data = yaml.safe_load(f) or {}

        limit_up_raw = prev_data.get("raw_data", {}).get("limit_up", {})
        stocks = limit_up_raw.get("stocks", [])
        if not stocks:
            logger.warning(f"T-1 涨停股列表为空，跳过：{prev_date}")
            return None

        logger.info(f"T-1 涨停股数量：{len(stocks)}")

        # 2. 按 limit_times + 涨幅类型分组，拉取 T 日开盘价
        groups: dict[str, list[dict]] = {
            "first_10cm": [], "first_20cm": [], "first_30cm": [],
            "second": [], "third_plus": [],
            "third_board": [], "fourth_board": [], "fifth_board_plus": [],
            "first_board_yizi": [],
            "yizi_first_open": [],
        }
        all_entries: list[dict] = []

        for stock in stocks:
            code = stock.get("code")
            prev_close = stock.get("close", 0)
            limit_times = stock.get("limit_times", 1)
            if not code or not prev_close:
                continue

            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success:
                logger.debug(f"  {code} T 日行情获取失败：{r.error}")
                continue

            t_open = r.data.get("open", 0)
            if not t_open:
                continue

            premium_pct = round((t_open - prev_close) / prev_close * 100, 2)
            pct_chg = abs(stock.get("pct_chg", stock.get("change_pct", 0)))
            first_time = str(stock.get("first_time", ""))
            last_time = str(stock.get("last_time", ""))
            is_yizi = first_time == last_time and first_time != ""

            entry = {
                "code": code,
                "name": stock.get("name", ""),
                "limit_times": limit_times,
                "prev_close": prev_close,
                "t_open": t_open,
                "premium_pct": premium_pct,
                "pct_chg": pct_chg,
                "is_yizi": is_yizi,
                "amount_billion": stock.get("amount_billion", 0),
            }
            all_entries.append(entry)

            if limit_times == 1:
                if pct_chg > 25:
                    groups["first_30cm"].append(entry)
                elif pct_chg > 15:
                    groups["first_20cm"].append(entry)
                else:
                    groups["first_10cm"].append(entry)
                if is_yizi:
                    groups["first_board_yizi"].append(entry)
            elif limit_times == 2:
                groups["second"].append(entry)
            else:
                groups["third_plus"].append(entry)
                if limit_times == 3:
                    groups["third_board"].append(entry)
                elif limit_times == 4:
                    groups["fourth_board"].append(entry)
                else:
                    groups["fifth_board_plus"].append(entry)

            if is_yizi and limit_times >= 2:
                groups["yizi_first_open"].append(entry)

        # 容量票：按 T-1 成交额降序取前 10
        capacity_top10 = sorted(
            all_entries,
            key=lambda x: x.get("amount_billion", 0),
            reverse=True,
        )[:10]

        # 3. 聚合统计
        def _agg(items: list[dict]) -> dict:
            if not items:
                return {"count": 0}
            prems = [x["premium_pct"] for x in items]
            up_count = sum(1 for p in prems if p > 0)
            return {
                "count": len(items),
                "open_up_rate": round(up_count / len(items), 3),
                "premium_median": round(statistics.median(prems), 2),
                "premium_mean": round(statistics.mean(prems), 2),
                "premium_max": round(max(prems), 2),
                "premium_min": round(min(prems), 2),
                "detail": sorted(items, key=lambda x: x["premium_pct"], reverse=True)[:10],
            }

        all_first = groups["first_10cm"] + groups["first_20cm"] + groups["first_30cm"]
        result = {
            "computed_at": datetime.now().isoformat(),
            "trade_date": trade_date,
            "prev_date": prev_date,
            "first_board": _agg(all_first),
            "first_board_10cm": _agg(groups["first_10cm"]),
            "first_board_20cm": _agg(groups["first_20cm"]),
            "first_board_30cm": _agg(groups["first_30cm"]),
            "first_board_yizi": _agg(groups["first_board_yizi"]),
            "second_board": _agg(groups["second"]),
            "third_board_plus": _agg(groups["third_plus"]),
            "third_board": _agg(groups["third_board"]),
            "fourth_board": _agg(groups["fourth_board"]),
            "fifth_board_plus": _agg(groups["fifth_board_plus"]),
            "yizi_first_open": _agg(groups["yizi_first_open"]),
            "capacity_top10": _agg(capacity_top10),
        }

        # 4. 人气股次日表现回填（A龙虎榜净买 + B连板股 + C成交额前10）
        popularity = self._collect_popularity_backfill(prev_data, trade_date, prev_date)
        if popularity:
            prev_data["popularity_backfill"] = popularity

        # 5. 写回 T-1 的 post-market.yaml
        prev_data["premium_backfill"] = result
        with open(prev_yaml, "w", encoding="utf-8") as f:
            yaml.dump(prev_data, f, allow_unicode=True, default_flow_style=False)

        logger.info(
            f"溢价率回填完成 → {prev_yaml}｜"
            f"首板 {result['first_board'].get('count', 0)} 只，"
            f"高开率 {result['first_board'].get('open_up_rate', '-')}"
        )
        if popularity:
            logger.info(f"人气股回填完成，共 {len(popularity)} 只")
        return result

    def _collect_popularity_backfill(
        self, prev_data: dict, trade_date: str, prev_date: str | None = None
    ) -> list[dict]:
        """
        收集 T-1 人气股并追踪其 T 日表现。

        人气股来源（去重合并，source 字段多标签）：
            A. T-1 龙虎榜净买股（net_amount > 0）
            B. T-1 连板股（limit_times >= 2）
            C. T-1 成交额前10（top_volume_stocks[:10]）

        每条 entry 含字段：
            code, name, source[], prev_close,
            t_open, t_open_premium_pct,
            t_close, t_close_change_pct,
            t_is_limit_up, t_is_limit_down
        """
        raw = prev_data.get("raw_data", {})

        def _to_float(value, *, zero_is_none: bool = False) -> float | None:
            if value in (None, ""):
                return None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if zero_is_none and parsed == 0:
                return None
            return parsed

        def _load_prev_top_volume_stocks() -> list[dict]:
            cached = raw.get("top_volume_stocks")
            if isinstance(cached, list) and cached:
                return cached

            # 兼容历史 T-1 YAML：旧数据里没有 top_volume_stocks，需要临时回拉一次
            if prev_date:
                r = self.registry.call("get_top_volume_stocks", prev_date, 10)
                if r.success and r.data:
                    logger.info(f"T-1 成交额前10缺失，已回拉 {prev_date} 的成交额排名")
                    return list(r.data)
                logger.debug(
                    "T-1 成交额前10回拉失败：%s",
                    getattr(r, "error", "unknown"),
                )
            return []

        # ── 收集三类来源，code → {name, prev_close, sources}
        candidates: dict[str, dict] = {}

        def _add(code: str, name: str, prev_close: float, source: str) -> None:
            prev_close_val = _to_float(prev_close, zero_is_none=True)
            if not code or prev_close_val is None:
                return
            if code in candidates:
                if source not in candidates[code]["sources"]:
                    candidates[code]["sources"].append(source)
            else:
                candidates[code] = {
                    "name": name,
                    "prev_close": prev_close_val,
                    "sources": [source],
                }

        # A. 龙虎榜净买
        for item in (raw.get("dragon_tiger", {}).get("data") or []):
            net = item.get("net_amount")
            try:
                net_val = float(net) if net is not None else 0.0
            except (TypeError, ValueError):
                net_val = 0.0
            if net_val > 0:
                _add(
                    str(item.get("code", "")),
                    str(item.get("name", "")),
                    item.get("prev_close") or item.get("close", 0),
                    "dragon_tiger",
                )

        # B. 连板股（limit_times >= 2），prev_close 取当时涨停收盘价
        for stock in (raw.get("limit_up", {}).get("stocks") or []):
            if (stock.get("limit_times") or 1) >= 2:
                _add(
                    str(stock.get("code", "")),
                    str(stock.get("name", "")),
                    stock.get("close", 0),
                    "consecutive",
                )

        # C. 成交额前10（从 T-1 的 top_volume_stocks 字段读取）
        for item in _load_prev_top_volume_stocks()[:10]:
            _add(
                str(item.get("code", "")),
                str(item.get("name", "")),
                item.get("close", 0),
                "volume_top10",
            )

        if not candidates:
            logger.info("人气股来源均为空，跳过 popularity_backfill")
            return []

        logger.info(f"人气股候选数量：{len(candidates)}（A/B/C 合并去重后）")

        # ── 拉取 T 日数据，计算次日表现
        results: list[dict] = []
        for code, info in candidates.items():
            r = self.registry.call("get_stock_daily", code, trade_date)
            if not r.success or not r.data:
                logger.debug(f"  {code} T日行情获取失败：{getattr(r, 'error', '')}")
                continue

            d = r.data
            t_open_raw = d.get("open")
            t_close_raw = d.get("close")
            t_open = _to_float(t_open_raw, zero_is_none=True)
            t_close = _to_float(t_close_raw, zero_is_none=True)
            prev_close = info["prev_close"]

            # prev_close=0 意味着来源数据本身缺失，无法计算相对变化
            if not prev_close or prev_close == 0:
                continue

            # t_close=0 表示当日数据获取失败（停牌/未上市等），跳过脏数据
            if t_close is None:
                logger.debug(f"  {code} T日收盘价为0或缺失，跳过")
                continue

            t_open_premium = (
                round((t_open - prev_close) / prev_close * 100, 2)
                if t_open is not None else None
            )
            t_close_change = round((t_close - prev_close) / prev_close * 100, 2)

            pct_chg_raw = d.get("pct_chg")
            if pct_chg_raw in (None, ""):
                pct_chg_raw = d.get("change_pct")
            pct_chg = _to_float(pct_chg_raw)
            limit_threshold = 9.8
            # 仅在有有效 pct_chg 时判断涨跌停；价格为0时已在上方跳过
            t_is_limit_up = pct_chg is not None and pct_chg >= limit_threshold
            t_is_limit_down = pct_chg is not None and pct_chg <= -limit_threshold

            results.append({
                "code": code,
                "name": info["name"],
                "source": info["sources"],
                "prev_close": prev_close,
                "t_open": t_open,
                "t_open_premium_pct": t_open_premium,
                "t_close": t_close,
                "t_close_change_pct": t_close_change,
                "t_is_limit_up": t_is_limit_up,
                "t_is_limit_down": t_is_limit_down,
            })

        return results

    def format_report(self, result: dict) -> str:
        """生成可推送的溢价率摘要文本"""
        if not result:
            return ""

        prev = result.get("prev_date", "")

        lines = [
            f"**{prev} 涨停板次日溢价率回填**",
            "",
        ]

        def _fmt_group(label: str, data: dict) -> list[str]:
            if not data.get("count"):
                return [f"{label}：无数据"]
            return [
                f"{label}（{data['count']} 只）",
                f"  高开率：{data.get('open_up_rate', 0):.1%}　"
                f"溢价中位：{data.get('premium_median', 0):+.2f}%　"
                f"溢价均值：{data.get('premium_mean', 0):+.2f}%",
            ]

        lines += _fmt_group("首板（合计）", result.get("first_board", {}))
        lines += _fmt_group("  ├ 10cm首板", result.get("first_board_10cm", {}))
        lines += _fmt_group("  ├ 20cm首板", result.get("first_board_20cm", {}))
        lines += _fmt_group("  └ 30cm首板", result.get("first_board_30cm", {}))
        lines += _fmt_group("首板一字", result.get("first_board_yizi", {}))
        lines += _fmt_group("二板", result.get("second_board", {}))
        lines += _fmt_group("三板+（合计）", result.get("third_board_plus", {}))
        lines += _fmt_group("  ├ 三板", result.get("third_board", {}))
        lines += _fmt_group("  ├ 四板", result.get("fourth_board", {}))
        lines += _fmt_group("  └ 五板+", result.get("fifth_board_plus", {}))
        lines += _fmt_group("一字首开（连板）", result.get("yizi_first_open", {}))
        lines += _fmt_group("容量票 Top10", result.get("capacity_top10", {}))

        return "\n".join(lines)
