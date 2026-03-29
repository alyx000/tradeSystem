"""
报告生成器
将采集到的原始数据转化为可推送的报告（Markdown格式 + YAML存档）
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ReportGenerator:
    """报告生成器"""

    def __init__(self):
        self.daily_dir = BASE_DIR / "daily"

    def _ensure_dir(self, date: str) -> Path:
        d = self.daily_dir / date
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ==========================================
    # 盘前简报
    # ==========================================

    def generate_pre_market(
        self,
        date: str,
        market_data: dict,
        holdings_announcements: dict,
        news: list[dict] | None = None,
        calendar_events: list[dict] | None = None,
    ) -> tuple[str, str]:
        """
        生成盘前简报。
        返回: (markdown报告文本, yaml存档路径)
        """
        lines = [f"# 盘前简报 {date}\n", f"生成时间: {datetime.now().strftime('%H:%M')}\n"]

        # 外盘
        lines.append("## 一、隔夜外盘\n")
        gi = market_data.get("global_indices", {})
        for name, label in [("dow_jones", "道琼斯"), ("nasdaq", "纳斯达克"), ("sp500", "标普500"), ("a50", "A50期货")]:
            info = gi.get(name, {})
            if "error" in info:
                lines.append(f"- {label}: 数据获取失败")
            else:
                pct = info.get("change_pct", 0)
                emoji_dir = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] {label}: {info.get('close', 'N/A')} ({emoji_dir}{pct}%) [★★★]"
                )

        # 商品 & 汇率
        lines.append("\n## 二、大宗商品 & 汇率\n")
        for section, label in [("commodities", "商品"), ("forex", "汇率")]:
            for key, info in market_data.get(section, {}).items():
                if "error" in info:
                    continue
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] {info.get('name', key)}: {info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
                )

        # 持仓公告
        if holdings_announcements:
            lines.append("\n## 三、持仓股公告\n")
            for code, info in holdings_announcements.items():
                name = info.get("name", code)
                anns = info.get("announcements", [])
                if anns:
                    lines.append(f"### {name} ({code})")
                    for a in anns[:5]:  # 最多5条
                        lines.append(f"- [事实] ★★★ {a.get('title', '')} ({a.get('ann_date', '')})")
                else:
                    lines.append(f"- {name}: 无新公告")

        # 新闻
        if news:
            lines.append("\n## 四、重要新闻\n")
            for n in news[:10]:
                lines.append(
                    f"- [{n.get('type', '事实')}] {n.get('confidence', '★★☆')} "
                    f"{n.get('timeliness', '[近期]')} {n.get('content', '')}"
                )
                if n.get("source"):
                    lines.append(f"  来源: {n['source']}")

        # 日历
        if calendar_events:
            lines.append("\n## 五、日历提醒\n")
            for e in calendar_events:
                lines.append(f"- {e.get('event', '')} | {e.get('time', '')} | {e.get('impact', '')}")

        # 底部提示
        lines.append("\n---")
        lines.append("*以上数据自动采集，事实类信息已标注来源。")
        lines.append("情绪判断、节点分析等主观定性请在复盘时手动补充。*")

        md_text = "\n".join(lines)

        # 保存 YAML
        out_dir = self._ensure_dir(date)
        yaml_path = out_dir / "pre-market.yaml"
        yaml_data = {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "market_data": market_data,
            "holdings_announcements": holdings_announcements,
            "news": news or [],
            "calendar_events": calendar_events or [],
        }
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, allow_unicode=True, default_flow_style=False)

        return md_text, str(yaml_path)

    # ==========================================
    # 盘后报告
    # ==========================================

    def generate_post_market(
        self,
        date: str,
        raw_data: dict,
        holdings_data: list[dict] | None = None,
    ) -> tuple[str, str]:
        """
        生成盘后数据报告。
        返回: (markdown报告文本, yaml存档路径)
        """
        lines = [f"# 盘后数据报告 {date}\n", f"生成时间: {datetime.now().strftime('%H:%M')}\n"]

        # 指数
        lines.append("## 一、指数表现\n")
        lines.append("| 指数 | 收盘 | 涨跌幅 | 成交额(亿) |")
        lines.append("|------|------|--------|-----------|")
        idx_names = {"shanghai": "上证指数", "shenzhen": "深证成指", "chinext": "创业板指", "star50": "科创50"}
        for key, label in idx_names.items():
            info = raw_data.get("indices", {}).get(key, {})
            if "error" in info:
                lines.append(f"| {label} | - | - | - |")
            else:
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"| {label} | {info.get('close', '-')} | {sign}{pct}% | "
                    f"{info.get('amount_billion', '-')} |"
                )

        # 成交额
        vol = raw_data.get("total_volume", {})
        if "total_billion" in vol:
            lines.append(f"\n两市总成交额: **{vol['total_billion']}亿**")
            lines.append(f"(沪: {vol.get('shanghai_billion', '-')}亿 / 深: {vol.get('shenzhen_billion', '-')}亿)")

        # 涨跌停
        lines.append("\n## 二、涨跌停数据\n")
        lu = raw_data.get("limit_up", {})
        ld = raw_data.get("limit_down", {})
        if "count" in lu:
            lines.append(f"- 涨停: **{lu['count']}家** (首板: {lu.get('first_board_count', '-')}, 连板: {lu.get('consecutive_board_count', '-')})")
            lines.append(f"- 最高连板: **{lu.get('highest_board', '-')}板**")
            # 连板梯队
            ladder = lu.get("board_ladder", {})
            if ladder:
                lines.append("- 连板梯队:")
                for boards, names in sorted(ladder.items(), key=lambda x: -int(x[0])):
                    lines.append(f"  - {boards}板: {', '.join(names[:8])}")
        if "count" in ld:
            lines.append(f"- 跌停: **{ld['count']}家**")

        # 板块排名
        lines.append("\n## 三、板块涨幅前10\n")
        lines.append("| 板块 | 涨跌幅 | 领涨股 |")
        lines.append("|------|--------|--------|")
        for s in raw_data.get("sector_industry", {}).get("data", [])[:10]:
            lines.append(f"| {s.get('name', '')} | {s.get('change_pct', 0)}% | {s.get('top_stock', '')} |")

        # 北向资金
        nb = raw_data.get("northbound", {})
        if "net_buy_billion" in nb:
            lines.append(f"\n## 四、北向资金\n")
            val = nb["net_buy_billion"]
            direction = "净买入" if val >= 0 else "净卖出"
            lines.append(f"- {direction}: **{abs(val)}亿**")

        # 持仓表现
        if holdings_data:
            lines.append("\n## 五、持仓表现\n")
            lines.append("| 股票 | 收盘 | 涨跌幅 | 成本 | 盈亏 | 成交额(亿) |")
            lines.append("|------|------|--------|------|------|-----------|")
            for s in holdings_data:
                if "error" in s:
                    lines.append(f"| {s.get('name', s['code'])} | - | - | - | - | - |")
                    continue
                pnl_str = f"{s.get('pnl_pct', '-')}%" if "pnl_pct" in s else "-"
                pct = s.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"| {s.get('name', s['code'])} | {s.get('close', '-')} | "
                    f"{sign}{pct}% | {s.get('cost', '-')} | {pnl_str} | "
                    f"{s.get('amount_billion', '-')} |"
                )

        # 龙虎榜
        dt_data = raw_data.get("dragon_tiger", {}).get("data", [])
        if dt_data:
            lines.append("\n## 六、龙虎榜\n")
            for d in dt_data[:10]:
                net = d.get("net_amount", 0)
                direction = "净买" if net > 0 else "净卖"
                lines.append(f"- {d.get('name', '')} | {d.get('reason', '')} | {direction} {abs(net):.0f}万")

        # 板块节奏分析
        _render_sector_rhythm(lines, raw_data)

        # 底部
        lines.append("\n---")
        lines.append("*自动采集数据标注 [事实] ★★★；板块节奏判断标注 [判断] ★★☆，仅供参考。*")
        lines.append("*请在 review.yaml 中完成主观确认：情绪周期、三位一体结论等。*")

        md_text = "\n".join(lines)

        # 保存
        out_dir = self._ensure_dir(date)
        yaml_path = out_dir / "post-market.yaml"
        yaml_data = {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "raw_data": raw_data,
            "holdings_data": holdings_data or [],
        }
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, allow_unicode=True, default_flow_style=False)

        # 同时生成空白的 review.yaml 供手动填写
        review_path = out_dir / "review.yaml"
        if not review_path.exists():
            template_path = BASE_DIR / "templates" / "daily-review.yaml"
            if template_path.exists():
                import shutil
                shutil.copy(template_path, review_path)
                # 替换日期
                content = review_path.read_text(encoding="utf-8")
                content = content.replace('date: "YYYY-MM-DD"', f'date: "{date}"')
                weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                from datetime import datetime as dt
                wd = dt.strptime(date, "%Y-%m-%d").weekday()
                content = content.replace('weekday: ""', f'weekday: "{weekdays[wd]}"')
                review_path.write_text(content, encoding="utf-8")

        return md_text, str(yaml_path)


# ------------------------------------------------------------------
# 板块节奏渲染（模块级工具函数）
# ------------------------------------------------------------------

_PHASE_ICON = {
    "启动": "[启动]",
    "发酵": "[发酵]",
    "高潮": "[高潮]",
    "首次分歧": "[分歧]",
    "震荡": "[震荡]",
    "衰退": "[衰退]",
    "观察中": "[观察]",
}


def _render_sector_rhythm(lines: list, raw_data: dict) -> None:
    """在盘后报告中追加板块节奏分析章节。"""
    industry_rhythm = raw_data.get("sector_rhythm_industry", [])
    concept_rhythm = raw_data.get("sector_rhythm_concept", [])

    if not industry_rhythm and not concept_rhythm:
        return

    lines.append("\n## 七、板块节奏分析 [判断] ★★☆\n")
    lines.append("> 基于过去 20 个交易日板块涨幅排名时间序列自动推断，置信度仅供参考，请结合盘面实际判断。\n")

    for label, rhythm_list in [("行业板块", industry_rhythm), ("概念板块", concept_rhythm)]:
        if not rhythm_list:
            continue
        lines.append(f"### {label}\n")
        lines.append("| 板块 | 今日排名 | 今日涨幅 | 连续上榜 | 5日累计 | 阶段 | 置信度 | 关键信号 |")
        lines.append("|------|---------|---------|---------|---------|------|-------|---------|")
        for item in rhythm_list:
            rank = item.get("rank_today")
            rank_str = f"#{rank}" if rank else "-"
            change = item.get("change_today")
            if change is not None:
                change_str = f"+{change}%" if change >= 0 else f"{change}%"
            else:
                change_str = "-"
            consec = item.get("consecutive_in_top30", 0)
            cumul = item.get("cumulative_pct_5d", 0) or 0
            cumul_str = f"+{cumul}%" if cumul >= 0 else f"{cumul}%"
            phase = item.get("phase", "观察中")
            phase_label = _PHASE_ICON.get(phase, phase)
            conf = item.get("confidence", "-")
            evidence = item.get("evidence", [])
            signal_str = "；".join(evidence[:2]) if evidence else "-"
            lines.append(
                f"| {item.get('name', '')} | {rank_str} | {change_str} | "
                f"{consec}天 | {cumul_str} | {phase_label} | {conf} | {signal_str} |"
            )
        lines.append("")
