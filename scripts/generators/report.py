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
        watchlist_announcements: dict | None = None,
        news: list[dict] | None = None,
        calendar_events: list[dict] | None = None,
    ) -> tuple[str, str]:
        """
        生成盘前简报。
        返回: (markdown报告文本, yaml存档路径)
        """
        watchlist_announcements = watchlist_announcements or {}
        lines = [f"# 盘前简报 {date}\n", f"生成时间: {datetime.now().strftime('%H:%M')}\n"]

        # 一、隔夜外盘：美股/A50 + 亚太 + 风险指标
        lines.append("## 一、隔夜外盘\n")
        lines.append("### 美股与A50期货\n")
        gi = market_data.get("global_indices", {})
        for name, label in [("dow_jones", "道琼斯"), ("nasdaq", "纳斯达克"), ("sp500", "标普500"), ("a50", "A50期货")]:
            info = gi.get(name, {})
            if "error" in info:
                lines.append(f"- {label}: 数据获取失败")
            else:
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] {label}: {info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
                )

        lines.append("\n### 亚太股指\n")
        apac = market_data.get("global_indices_apac", {})
        for name, label in [("hsi", "恒生指数"), ("hstech", "恒生科技"), ("nikkei", "日经225")]:
            info = apac.get(name, {})
            if "error" in info:
                lines.append(f"- {label}: 数据获取失败")
            else:
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] {label}: {info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
                )

        lines.append("\n### 风险指标\n")
        ri = market_data.get("risk_indicators", {})
        vix_info = ri.get("vix", {})
        if vix_info and "error" not in vix_info:
            pct = vix_info.get("change_pct", 0)
            sign = "+" if pct >= 0 else ""
            lines.append(
                f"- [事实] VIX恐慌指数: {vix_info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
            )
        us10y_info = ri.get("us10y", {})
        if us10y_info and "error" not in us10y_info:
            close_val = us10y_info.get("close", "N/A")
            if "change_bps" in us10y_info:
                bps = us10y_info["change_bps"]
                bps_str = f"+{bps}bp" if bps >= 0 else f"{bps}bp"
                lines.append(
                    f"- [事实] 美债10年期收益率: {close_val}% ({bps_str}) [★★★]"
                )
            else:
                pct = us10y_info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] 美债10年期收益率: {close_val}% ({sign}{pct}%) [★★★]"
                )

        # 二、美股中国资产 ETF
        lines.append("\n## 二、美股中国资产相关\n")
        us_cn = market_data.get("us_china_assets", {})
        if "_error" in us_cn:
            lines.append(f"- 数据获取失败: {us_cn['_error']}")
        else:
            for sym in ("KWEB", "FXI"):
                info = us_cn.get(sym, {})
                if "error" in info:
                    lines.append(f"- {sym}: {info.get('error', '数据获取失败')}")
                    continue
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                nm = info.get("name", sym)
                lines.append(
                    f"- [事实] {nm}: {info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
                )

        # 三、商品 & 汇率
        lines.append("\n## 三、大宗商品 & 汇率\n")
        for section in ["commodities", "forex"]:
            for key, info in market_data.get(section, {}).items():
                if "error" in info:
                    continue
                pct = info.get("change_pct", 0)
                sign = "+" if pct >= 0 else ""
                lines.append(
                    f"- [事实] {info.get('name', key)}: {info.get('close', 'N/A')} ({sign}{pct}%) [★★★]"
                )

        # 四、融资融券（上一交易日）
        lines.append("\n## 四、融资融券（上一交易日）\n")
        md = market_data.get("margin_data") or {}
        if md.get("error"):
            lines.append(f"- 融资融券汇总: {md['error']}")
        elif md.get("trade_date"):
            lines.append(f"> 统计日期: {md.get('trade_date')}\n")
            lines.append(
                f"- [事实] 两市融资余额合计: {md.get('total_rzye_yi', 'N/A')} 亿元 [★★★]"
            )
            lines.append(
                f"- [事实] 两市融券余额合计: {md.get('total_rqye_yi', 'N/A')} 亿元 [★★★]"
            )
            lines.append(
                f"- [事实] 融资融券余额合计: {md.get('total_rzrqye_yi', 'N/A')} 亿元 [★★★]"
            )
            for ex in md.get("exchanges", []):
                eid = ex.get("exchange_id", "")
                lines.append(
                    f"  - {eid}: 融资 {ex.get('rzye_yi')} 亿 / 融券 {ex.get('rqye_yi')} 亿 / 合计 {ex.get('rzrqye_yi')} 亿"
                )
        else:
            lines.append("- （无融资融券汇总数据）")

        # 今日日历：先算展示列表，再与持仓/新闻一起按顺序编号（三、四、五…）
        cal_shown: list[dict] = []
        if calendar_events:
            high = [e for e in calendar_events if e.get("importance") in ("高", "3")]
            mid = [e for e in calendar_events if e.get("importance") in ("中", "2")]
            low = [e for e in calendar_events if e.get("importance") not in ("高", "3", "中", "2")]
            shown = high + mid
            if len(shown) < 30:
                shown += low[: 30 - len(shown)]
            cal_shown = shown[:30]

        # 动态章节：从「五」起（持仓 / 关注池 / 新闻 / 日历）
        idx = 5
        if holdings_announcements:
            lines.append(f"\n## {_roman(idx)}、持仓股公告\n")
            idx += 1
            for code, info in holdings_announcements.items():
                stock_name = info.get("name", code)
                anns = info.get("announcements", [])
                if anns:
                    lines.append(f"### {stock_name} ({code})")
                    for a in anns[:5]:
                        lines.append(f"- [事实] ★★★ {a.get('title', '')} ({a.get('ann_date', '')})")
                else:
                    lines.append(f"- {stock_name}: 无新公告")

        if watchlist_announcements:
            lines.append(f"\n## {_roman(idx)}、关注池公告\n")
            idx += 1
            for code, info in watchlist_announcements.items():
                stock_name = info.get("name", code)
                anns = info.get("announcements", [])
                if anns:
                    lines.append(f"### {stock_name} ({code})")
                    for a in anns[:5]:
                        lines.append(f"- [事实] ★★★ {a.get('title', '')} ({a.get('ann_date', '')})")
                else:
                    lines.append(f"- {stock_name}: 无新公告")

        if news:
            lines.append(f"\n## {_roman(idx)}、财经新闻\n")
            idx += 1
            for n in news[:15]:
                time_str = n.get("time", "")
                src = n.get("source", "")
                title = n.get("title", n.get("content", ""))
                meta = " | ".join(filter(None, [time_str, src]))
                lines.append(f"- [事实] ★★☆ {title}")
                if meta:
                    lines.append(f"  `{meta}`")

        if cal_shown:
            lines.append(f"\n## {_roman(idx)}、今日日历\n")
            idx += 1
            if len(cal_shown) < len(calendar_events or []):
                lines.append(
                    f"> 共 {len(calendar_events)} 条，优先展示高/中重要性事件（最多30条）\n"
                )
            lines.append("| 事件 | 地区 | 时间 | 重要性 | 预期 | 前值 |")
            lines.append("|------|------|------|--------|------|------|")
            for e in cal_shown:
                importance = e.get("importance", e.get("impact", ""))
                region = e.get("region", e.get("category", ""))
                lines.append(
                    f"| {e.get('event', '')} | {region} | {e.get('time', '')} | "
                    f"{importance} | {e.get('expected', '')} | {e.get('prior', '')} |"
                )

        # 底部提示
        lines.append("\n---")
        lines.append("*以上数据自动采集，[事实] 类信息已标注来源。*")
        lines.append("*情绪判断、节点分析等主观定性请在复盘时手动补充。*")

        md_text = "\n".join(lines)

        # 保存 YAML
        out_dir = self._ensure_dir(date)
        yaml_path = out_dir / "pre-market.yaml"
        yaml_data = {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "market_data": market_data,
            "holdings_announcements": holdings_announcements,
            "watchlist_announcements": watchlist_announcements,
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

def _roman(n: int) -> str:
    """将整数转为中文序号（三、四、五…）"""
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    if 1 <= n <= len(numerals):
        return numerals[n - 1]
    return str(n)


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
