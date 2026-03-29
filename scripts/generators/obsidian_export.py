#!/usr/bin/env python3
"""
YAML → Obsidian Markdown 导出器

将 tradeSystem 的 YAML 数据转换为 Obsidian 可读的 Markdown 格式，
同步到 obsidian-vault 知识库。

用法:
    # 导出单日复盘
    python obsidian_export.py --date 2026-03-29

    # 导出所有未同步的复盘
    python obsidian_export.py --sync-all

    # 导出盘后数据
    python obsidian_export.py --date 2026-03-29 --type post-market

    # 指定目标目录
    python obsidian_export.py --date 2026-03-29 --obsidian-dir /path/to/obsidian
"""
import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 优先读环境变量 OBSIDIAN_DIR，fallback 到 VPS 路径
_env_obsidian = os.getenv("OBSIDIAN_DIR", "")
DEFAULT_OBSIDIAN_DIR = Path(_env_obsidian) if _env_obsidian else Path("/root/.openclaw/workspace/obsidian-vault")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("obsidian_export")


class ObsidianExporter:
    """YAML → Obsidian Markdown 导出器"""

    def __init__(self, tradesystem_dir: str = None, obsidian_dir: str = None):
        self.ts_dir = Path(tradesystem_dir) if tradesystem_dir else BASE_DIR
        self.ob_dir = Path(obsidian_dir) if obsidian_dir else DEFAULT_OBSIDIAN_DIR
        logger.info(f"tradeSystem 目录：{self.ts_dir}")
        logger.info(f"Obsidian 目录：{self.ob_dir}")

    def export_daily_review(self, date: str) -> str:
        """
        导出单日复盘（review.yaml）到 Obsidian

        Args:
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            生成的 Markdown 文件路径
        """
        yaml_path = self.ts_dir / "daily" / date / "review.yaml"
        if not yaml_path.exists():
            logger.warning(f"复盘文件不存在：{yaml_path}")
            return None

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        md_lines = self._build_review_markdown(date, data)

        md_path = self.ob_dir / "daily-reviews" / date[:4] / f"{date}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        logger.info(f"已导出复盘：{md_path}")
        return str(md_path)

    def _build_review_markdown(self, date: str, data: dict) -> list:
        """构建复盘 Markdown 内容"""
        md = [
            f"# {date} 复盘笔记",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"标签：#daily-review #A 股",
            "",
        ]

        # 第一步：大盘分析
        md.append("## 一、大盘分析")
        overview = data.get("market_overview", {})
        direction = overview.get("direction", {})
        if direction.get("trend"):
            md.append(f"- **趋势**: {direction['trend']}")
        if direction.get("ma5w_position"):
            md.append(f"- **5 周均线**: {direction['ma5w_position']}")

        node = overview.get("node", {})
        if node.get("current"):
            md.append(f"- **当前节点**: {node['current']}")
        if node.get("expectation"):
            md.append(f"- **预期**: {node['expectation']}")

        # 指数数据
        indices = overview.get("indices", {})
        if indices:
            md.append("")
            md.append("### 指数表现")
            md.append("| 指数 | 收盘 | 涨跌幅 |")
            md.append("|------|------|--------|")
            idx_map = {
                "shanghai": "上证指数",
                "shenzhen": "深证成指",
                "chinext": "创业板指",
                "star50": "科创 50",
            }
            for key, label in idx_map.items():
                info = indices.get(key, {})
                if info and "error" not in info:
                    close = info.get("close", "-")
                    pct = info.get("change_pct", 0)
                    sign = "+" if pct >= 0 else ""
                    md.append(f"| {label} | {close} | {sign}{pct}% |")

        # 成交额
        total_volume = overview.get("total_volume", {})
        if total_volume.get("amount_billion"):
            md.append("")
            md.append(f"### 成交额")
            md.append(f"- **两市总成交额**: {total_volume['amount_billion']}亿")
            if total_volume.get("vs_yesterday"):
                md.append(f"- **较昨日**: {total_volume['vs_yesterday']}")

        md.append("")

        # 第二步：板块梳理
        md.append("## 二、板块梳理")
        sectors = data.get("sectors", {})
        main_theme = sectors.get("main_theme", {})
        if main_theme.get("name"):
            md.append(f"- **主线板块**: {main_theme['name']} ({main_theme.get('status', '')})")
        if main_theme.get("node"):
            md.append(f"- **主线节奏**: {main_theme['node']}")

        strongest = sectors.get("strongest_today", [])
        if strongest:
            md.append("")
            md.append("### 当日最强板块")
            for s in strongest[:5]:
                if s.get("name"):
                    reason = f" - {s['reason']}" if s.get("reason") else ""
                    md.append(f"- {s['name']}{reason}")

        md.append("")

        # 第三步：情绪周期
        md.append("## 三、情绪周期")
        emotion = data.get("emotion_cycle", {})
        if emotion.get("phase"):
            md.append(f"- **情绪阶段**: {emotion['phase']}")
        if emotion.get("sub_cycle"):
            md.append(f"- **子周期**: {emotion['sub_cycle']}")

        indicators = emotion.get("indicators", {})
        if indicators:
            md.append("")
            md.append("### 情绪指标")
            if indicators.get("limit_up_count"):
                md.append(f"- **涨停**: {indicators['limit_up_count']}家")
            if indicators.get("limit_down_count"):
                md.append(f"- **跌停**: {indicators['limit_down_count']}家")
            if indicators.get("highest_board"):
                md.append(f"- **最高连板**: {indicators['highest_board']}板")

        md.append("")

        # 第四步：风格化
        md.append("## 四、风格化赚钱效应")
        style = data.get("style_analysis", {})
        if style.get("current_preference"):
            pref = style["current_preference"]
            if pref.get("cap_size"):
                md.append(f"- **市值偏好**: {pref['cap_size']}")
            if pref.get("style"):
                md.append(f"- **交易风格**: {pref['style']}")

        md.append("")

        # 第五步：龙头/最票
        md.append("## 五、龙头/最票识别")
        leaders = data.get("leaders", {})
        top_leaders = leaders.get("top_leaders", [])
        if top_leaders:
            for l in top_leaders[:5]:
                if l.get("stock"):
                    attr = f"({l.get('attribute', '')})" if l.get("attribute") else ""
                    md.append(f"- **{l['stock']}** {attr}")

        md.append("")

        # 第六步：节点判断
        md.append("## 六、节点判断")
        nodes = data.get("nodes", {})
        if nodes.get("overall_assessment"):
            md.append(f"**综合评估**: {nodes['overall_assessment']}")

        md.append("")

        # 第七步：持仓检视
        md.append("## 七、持仓检视")
        positions = data.get("positions", [])
        if positions:
            md.append("| 股票 | 成本 | 当前价 | 仓位% | 计划 |")
            md.append("|------|------|--------|-------|------|")
            for p in positions:
                if p.get("stock"):
                    md.append(
                        f"| {p['stock']} | {p.get('cost', '-')} | "
                        f"{p.get('current_price', '-')} | {p.get('position_pct', '-')} | "
                        f"{p.get('action_plan', '-')} |"
                    )
        else:
            md.append("*无持仓*")

        md.append("")

        # 第八步：次日计划
        md.append("## 八、次日计划")
        plan = data.get("next_day_plan", {})
        if plan.get("key_factor"):
            md.append(f"**重点因子**: {plan['key_factor']}")

        watch = plan.get("watch_directions", [])
        if watch:
            md.append("")
            md.append("### 关注方向")
            for w in watch:
                if w.get("direction"):
                    reason = f" - {w['reason']}" if w.get("reason") else ""
                    md.append(f"- {w['direction']}{reason}")

        risks = plan.get("risks", [])
        if risks:
            md.append("")
            md.append("### 风险提示")
            for r in risks:
                if r.get("description"):
                    md.append(f"- {r['description']}")

        md.append("")

        # 综合结论
        md.append("---")
        md.append("## 综合结论")
        summary = data.get("summary", {})
        if summary.get("one_sentence"):
            md.append(f"> {summary['one_sentence']}")
        if summary.get("trinity_conclusion"):
            md.append(f"\n**三位一体结论**: {summary['trinity_conclusion']}")
        if summary.get("confidence"):
            md.append(f"\n**信心**: {summary['confidence']}")

        md.append("")
        md.append("---")
        md.append("*本文档由 tradeSystem 自动生成，主观判断部分需手动补充*")

        return md

    def export_post_market(self, date: str) -> str:
        """
        导出盘后数据（post-market.yaml）到 Obsidian

        Args:
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            生成的 Markdown 文件路径
        """
        yaml_path = self.ts_dir / "daily" / date / "post-market.yaml"
        if not yaml_path.exists():
            logger.warning(f"盘后数据文件不存在：{yaml_path}")
            return None

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        md_lines = self._build_post_market_markdown(date, data)

        md_path = self.ob_dir / "daily-reviews" / date[:4] / f"{date}-盘后数据.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        logger.info(f"已导出盘后数据：{md_path}")
        return str(md_path)

    def _build_post_market_markdown(self, date: str, data: dict) -> list:
        """构建盘后数据 Markdown 内容"""
        md = [
            f"# {date} 盘后数据",
            f"生成时间：{data.get('generated_at', '')}",
            f"标签：#market-data #盘后",
            "",
        ]

        raw_data = data.get("raw_data", {})

        # 指数
        md.append("## 指数表现")
        md.append("| 指数 | 收盘 | 涨跌幅 |")
        md.append("|------|------|--------|")
        indices = raw_data.get("indices", {})
        idx_map = {
            "shanghai": "上证指数",
            "shenzhen": "深证成指",
            "chinext": "创业板指",
            "star50": "科创 50",
        }
        for key, label in idx_map.items():
            info = indices.get(key, {})
            if info and "error" not in info:
                close = info.get("close", "-")
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                md.append(f"| {label} | {close} | {sign}{pct}% |")

        # 成交额
        vol = raw_data.get("total_volume", {})
        if vol.get("total_billion"):
            md.append("")
            md.append(f"## 成交额")
            md.append(f"**{vol['total_billion']}亿**")

        # 涨跌停
        md.append("")
        md.append("## 涨跌停")
        limit_up = raw_data.get("limit_up", {})
        limit_down = raw_data.get("limit_down", {})
        if limit_up.get("count"):
            md.append(f"- **涨停**: {limit_up['count']}家")
            if limit_up.get("first_board_count"):
                md.append(f"  - 首板：{limit_up['first_board_count']}家")
            if limit_up.get("consecutive_board_count"):
                md.append(f"  - 连板：{limit_up['consecutive_board_count']}家")
        if limit_down.get("count"):
            md.append(f"- **跌停**: {limit_down['count']}家")

        # 板块排名
        md.append("")
        md.append("## 板块涨幅前 10")
        sector_data = raw_data.get("sector_industry", {}).get("data", [])
        if sector_data:
            md.append("| 板块 | 涨跌幅 | 领涨股 |")
            md.append("|------|--------|--------|")
            for s in sector_data[:10]:
                md.append(f"| {s.get('name', '')} | {s.get('change_pct', 0)}% | {s.get('top_stock', '')} |")

        # 北向资金
        nb = raw_data.get("northbound", {})
        if nb.get("net_buy_billion") is not None:
            md.append("")
            md.append("## 北向资金")
            val = nb["net_buy_billion"]
            direction = "净买入" if val >= 0 else "净卖出"
            md.append(f"- **{direction}**: {abs(val)}亿")

        md.append("")
        md.append("---")
        md.append("*数据来源：tradeSystem 自动采集*")

        return md

    def export_all(self, date: str = None) -> dict:
        """
        导出指定日期的所有数据

        Args:
            date: 日期字符串，默认今天

        Returns:
            导出结果字典
        """
        from datetime import date as date_lib
        if date is None:
            date = date_lib.today().isoformat()

        results = {
            "date": date,
            "review": None,
            "post_market": None,
        }

        results["review"] = self.export_daily_review(date)
        results["post_market"] = self.export_post_market(date)

        return results


def main():
    parser = argparse.ArgumentParser(description="YAML → Obsidian Markdown 导出器")
    parser.add_argument("--date", help="日期 (YYYY-MM-DD)")
    parser.add_argument(
        "--type",
        choices=["review", "post-market", "all"],
        default="all",
        help="导出类型",
    )
    parser.add_argument("--obsidian-dir", help="Obsidian 目录路径")
    parser.add_argument("--tradesystem-dir", help="tradeSystem 目录路径")
    parser.add_argument("--sync-all", action="store_true", help="同步所有未导出的日期")

    args = parser.parse_args()

    exporter = ObsidianExporter(
        tradesystem_dir=args.tradesystem_dir,
        obsidian_dir=args.obsidian_dir,
    )

    if args.sync_all:
        # 扫描所有未导出的日期
        daily_dir = exporter.ts_dir / "daily"
        if not daily_dir.exists():
            logger.error(f"目录不存在：{daily_dir}")
            return

        dates = sorted([d.name for d in daily_dir.iterdir() if d.is_dir()])
        logger.info(f"发现 {len(dates)} 个日期目录：{dates}")

        for date in dates:
            logger.info(f"处理 {date}...")
            exporter.export_all(date)
    else:
        if not args.date:
            parser.error("--date 是必需的（或使用 --sync-all）")

        if args.type == "review":
            result = exporter.export_daily_review(args.date)
        elif args.type == "post-market":
            result = exporter.export_post_market(args.date)
        else:
            result = exporter.export_all(args.date)

        if result:
            if isinstance(result, dict):
                logger.info(f"导出完成：{result}")
            else:
                logger.info(f"导出完成：{result}")
        else:
            logger.warning("导出未完成，可能文件不存在")


if __name__ == "__main__":
    main()
