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

        # 2. 按 limit_times 分组，拉取 T 日开盘价
        groups: dict[str, list[dict]] = {"first": [], "second": [], "third_plus": []}

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
            entry = {
                "code": code,
                "name": stock.get("name", ""),
                "limit_times": limit_times,
                "prev_close": prev_close,
                "t_open": t_open,
                "premium_pct": premium_pct,
            }

            if limit_times == 1:
                groups["first"].append(entry)
            elif limit_times == 2:
                groups["second"].append(entry)
            else:
                groups["third_plus"].append(entry)

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

        result = {
            "computed_at": datetime.now().isoformat(),
            "trade_date": trade_date,
            "prev_date": prev_date,
            "first_board": _agg(groups["first"]),
            "second_board": _agg(groups["second"]),
            "third_board_plus": _agg(groups["third_plus"]),
        }

        # 4. 写回 T-1 的 post-market.yaml
        prev_data["premium_backfill"] = result
        with open(prev_yaml, "w", encoding="utf-8") as f:
            yaml.dump(prev_data, f, allow_unicode=True, default_flow_style=False)

        logger.info(
            f"溢价率回填完成 → {prev_yaml}｜"
            f"首板 {result['first_board'].get('count', 0)} 只，"
            f"高开率 {result['first_board'].get('open_up_rate', '-')}"
        )
        return result

    def format_report(self, result: dict) -> str:
        """生成可推送的溢价率摘要文本"""
        if not result:
            return ""

        prev = result.get("prev_date", "")
        fb = result.get("first_board", {})
        sb = result.get("second_board", {})
        tb = result.get("third_board_plus", {})

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

        lines += _fmt_group("首板", fb)
        lines += _fmt_group("二板", sb)
        lines += _fmt_group("三板+", tb)

        return "\n".join(lines)
