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
        holdings_info: dict | None = None,
        watchlist_info: dict | None = None,
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

        if holdings_info:
            lines.append(f"\n## {_roman(idx)}、持仓信息面\n")
            idx += 1
            _render_stock_info_section(lines, holdings_info)

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

        if watchlist_info:
            lines.append(f"\n## {_roman(idx)}、关注池信息面\n")
            idx += 1
            _render_stock_info_section(lines, watchlist_info)

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
            "holdings_info": holdings_info or {},
            "watchlist_info": watchlist_info or {},
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
        holdings_announcements: dict | None = None,
        watchlist_data: dict | None = None,
        holdings_summary: dict | None = None,
    ) -> tuple[str, str]:
        """
        生成盘后数据报告。
        返回: (markdown报告文本, yaml存档路径)
        """
        lines = [f"# 盘后数据报告 {date}\n", f"生成时间: {datetime.now().strftime('%H:%M')}\n"]

        section_idx = 1

        # ---- 指数表现 ----
        lines.append(f"## {_roman(section_idx)}、指数表现\n")
        section_idx += 1
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

        # 成交额 + 对比
        vol = raw_data.get("total_volume", {})
        if "total_billion" in vol:
            vol_line = f"\n两市总成交额: **{vol['total_billion']:.2f}亿**"
            vs_yesterday = vol.get("vs_yesterday_pct")
            if vs_yesterday is not None:
                direction = "放量" if vs_yesterday > 0 else "缩量"
                vol_line += f"，较昨日{direction} {abs(vs_yesterday):.1f}%"
            lines.append(vol_line)
            ma_parts = []
            if "ma5_billion" in vol:
                ma_parts.append(f"5日均量 {vol['ma5_billion']:.2f}亿（{vol.get('vs_ma5', '')}）")
            if "ma20_billion" in vol:
                ma_parts.append(f"20日均量 {vol['ma20_billion']:.2f}亿（{vol.get('vs_ma20', '')}）")
            if ma_parts:
                lines.append(f"{'　|　'.join(ma_parts)}")

        # 市场宽度
        breadth = raw_data.get("breadth", {})
        if breadth.get("advance"):
            ratio_val = breadth.get("advance_ratio", "-")
            ratio_str = "∞" if ratio_val == float("inf") else str(ratio_val)
            lines.append(
                f"\n涨跌家数: 涨 {breadth['advance']} / 跌 {breadth['decline']} / "
                f"平 {breadth.get('flat', 0)}　涨跌比 **{ratio_str}**"
            )

        # 均线
        ma = raw_data.get("moving_averages", {}).get("shanghai", {})
        if ma:
            ma_items = []
            for p in [5, 10, 20, 60]:
                k = f"ma{p}"
                if k in ma:
                    above = "上" if ma.get(f"above_{k}") else "下"
                    ma_items.append(f"MA{p}={ma[k]}({above})")
            if "ma5w" in ma:
                above_w = "上" if ma.get("above_ma5w") else "下"
                ma_items.append(f"5周线={ma['ma5w']}({above_w})")
            if ma_items:
                lines.append(f"\n上证均线: {'　'.join(ma_items)}")

        # ---- 涨跌停数据 ----
        lines.append(f"\n## {_roman(section_idx)}、涨跌停数据\n")
        section_idx += 1
        lu = raw_data.get("limit_up", {})
        ld = raw_data.get("limit_down", {})
        if "count" in lu:
            lines.append(
                f"- 涨停: **{lu['count']}家** "
                f"(10cm: {lu.get('first_board_10cm', '-')}, "
                f"20cm: {lu.get('first_board_20cm', '-')}, "
                f"30cm: {lu.get('first_board_30cm', '-')}, "
                f"连板: {lu.get('consecutive_board_count', '-')})"
            )
            lines.append(f"- 最高连板: **{lu.get('highest_board', '-')}板**")
            seal = lu.get("seal_rate_pct")
            broken = lu.get("broken_count", 0)
            if seal is not None:
                lines.append(
                    f"- 封板率: **{seal}%**　炸板: {broken}家 ({lu.get('broken_rate_pct', 0)}%)"
                )
            ladder = lu.get("board_ladder", {})
            if ladder:
                lines.append("- 连板梯队:")
                for boards, names in sorted(ladder.items(), key=lambda x: -int(x[0])):
                    lines.append(f"  - {boards}板: {', '.join(names[:8])}")
        if "count" in ld:
            lines.append(f"- 跌停: **{ld['count']}家**")

        # ---- 板块排名 ----
        lines.append(f"\n## {_roman(section_idx)}、板块排名\n")
        section_idx += 1
        lines.append("### 行业涨幅前10\n")
        lines.append("| 板块 | 涨跌幅 | 成交额(亿) | 领涨股 |")
        lines.append("|------|--------|-----------|--------|")
        for s in raw_data.get("sector_industry", {}).get("data", [])[:10]:
            vol_str = f"{s.get('volume_billion', 0)}" if s.get("volume_billion") else "-"
            lines.append(
                f"| {s.get('name', '')} | {s.get('change_pct', 0)}% | "
                f"{vol_str} | {s.get('top_stock', '')} |"
            )

        bottom_ind = raw_data.get("sector_industry", {}).get("bottom", [])
        if bottom_ind:
            lines.append("\n### 行业跌幅前5\n")
            lines.append("| 板块 | 涨跌幅 | 领涨股 |")
            lines.append("|------|--------|--------|")
            for s in bottom_ind[:5]:
                lines.append(f"| {s.get('name', '')} | {s.get('change_pct', 0)}% | {s.get('top_stock', '')} |")

        # 板块资金净流入
        fund_flow = raw_data.get("sector_fund_flow", {}).get("data", [])
        if fund_flow:
            lines.append("\n### 资金净流入前10\n")
            lines.append("| 板块 | 净流入(亿) | 涨跌幅 |")
            lines.append("|------|-----------|--------|")
            for s in fund_flow[:10]:
                lines.append(
                    f"| {s.get('name', '')} | {s.get('net_inflow_billion', 0):.2f} | "
                    f"{s.get('change_pct', 0)}% |"
                )

        # ---- 北向资金 ----
        nb = raw_data.get("northbound", {})
        if "net_buy_billion" in nb:
            lines.append(f"\n## {_roman(section_idx)}、北向资金\n")
            section_idx += 1
            val = nb["net_buy_billion"]
            direction = "净买入" if val >= 0 else "净卖出"
            lines.append(f"- {direction}: **{abs(val):.2f}亿**")
            top_active = nb.get("top_active_stocks", [])
            if top_active:
                active_names = ", ".join(
                    f"{s['name']}({s['amount_yi']:.1f}亿)" for s in top_active[:5]
                )
                lines.append(f"- 十大活跃股: {active_names}")

        # ---- 融资融券 ----
        margin = raw_data.get("margin_data", {})
        if margin.get("trade_date"):
            lines.append(f"\n## {_roman(section_idx)}、融资融券\n")
            section_idx += 1
            lines.append(
                f"- 融资余额: {margin.get('total_rzye_yi', '-')}亿　"
                f"融券余额: {margin.get('total_rqye_yi', '-')}亿　"
                f"合计: {margin.get('total_rzrqye_yi', '-')}亿"
            )

        # ---- 持仓表现 ----
        if holdings_data:
            lines.append(f"\n## {_roman(section_idx)}、持仓表现\n")
            section_idx += 1
            if holdings_summary:
                tv = holdings_summary.get("total_market_value", 0)
                tp = holdings_summary.get("total_pnl_pct", 0)
                sign_s = "+" if tp >= 0 else ""
                lines.append(f"> 总市值 {tv:,.0f} 元，总盈亏 {sign_s}{tp}%\n")
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

        # ---- 盘后公告 ----
        if holdings_announcements:
            has_anns = any(info.get("announcements") for info in holdings_announcements.values())
            if has_anns:
                lines.append(f"\n## {_roman(section_idx)}、持仓盘后公告\n")
                section_idx += 1
                for code, info in holdings_announcements.items():
                    anns = info.get("announcements", [])
                    if anns:
                        stock_name = info.get("name", code)
                        lines.append(f"**{stock_name}** ({code})")
                        for a in anns[:3]:
                            lines.append(f"- {a.get('title', '')} ({a.get('ann_date', '')})")

        # ---- 龙虎榜 ----
        dt_data = raw_data.get("dragon_tiger", {}).get("data", [])
        if dt_data:
            lines.append(f"\n## {_roman(section_idx)}、龙虎榜\n")
            section_idx += 1
            for d in dt_data[:10]:
                net = d.get("net_amount", 0)
                direction = "净买" if net > 0 else "净卖"
                net_yi = abs(net) / 1e8
                lines.append(f"- {d.get('name', '')} | {d.get('reason', '')} | {direction} {net_yi:.2f}亿")

        # ---- 板块节奏分析 ----
        section_idx = _render_sector_rhythm(lines, raw_data, section_idx)

        # ---- 风格化赚钱效应 ----
        section_idx = _render_style_factors(lines, raw_data, section_idx)

        # ---- AI 自动分析摘要 ----
        auto = _generate_auto_analysis(raw_data)
        if auto:
            lines.append(f"\n## {_roman(section_idx)}、数据摘要 [事实] ★★★\n")
            section_idx += 1
            for item in auto:
                lines.append(f"- {item}")

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
            "holdings_announcements": holdings_announcements or {},
            "holdings_summary": holdings_summary or {},
            "watchlist_data": watchlist_data or {},
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

def _render_stock_info_section(lines: list[str], info_dict: dict) -> None:
    """渲染个股信息面（互动易/研报/新闻），持仓和关注池共用。"""
    for code, info in info_dict.items():
        stock_name = info.get("name", code)
        qa_list = info.get("investor_qa", [])
        rr_list = info.get("research_reports", [])
        news_list = info.get("news", [])
        lines.append(f"### {stock_name} ({code})\n")
        if qa_list:
            lines.append("**互动易问答**")
            for qa in qa_list[:3]:
                lines.append(f"- Q: {qa.get('question', '')}")
                ans = qa.get("answer", "")
                if ans:
                    lines.append(f"  A: {ans[:150]}")
            lines.append("")
        if rr_list:
            lines.append("**研报动态**")
            for rr in rr_list[:3]:
                inst = rr.get("institution", "")
                rating = rr.get("rating", "")
                tp = rr.get("target_price", 0)
                tp_str = f"，目标价 {tp}" if tp else ""
                lines.append(f"- [观点] ★★☆ {inst}：{rating}{tp_str} ({rr.get('date', '')})")
            lines.append("")
        if news_list:
            lines.append("**个股新闻**")
            for n in news_list[:3]:
                lines.append(f"- [事实] ★★☆ {n.get('title', '')} ({n.get('time', '')})")
            lines.append("")


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


def _generate_auto_analysis(raw_data: dict) -> list[str]:
    """基于采集数据生成客观描述摘要"""
    items: list[str] = []

    def _fmt_pct(v):
        return f"+{v}%" if v >= 0 else f"{v}%"

    # 成交额评估
    vol = raw_data.get("total_volume", {})
    tb = vol.get("total_billion", 0)
    if tb:
        desc = f"两市成交额 {tb:.0f}亿"
        vs = vol.get("vs_yesterday_pct")
        if vs is not None:
            desc += f"，较昨日{'放量' if vs > 0 else '缩量'} {abs(vs):.1f}%"
        vs_ma5 = vol.get("vs_ma5")
        if vs_ma5:
            desc += f"，{vs_ma5}5日均量"
        items.append(desc)

    # 涨跌比评估
    breadth = raw_data.get("breadth", {})
    adv = breadth.get("advance", 0)
    dec = breadth.get("decline", 0)
    if adv or dec:
        ratio = breadth.get("advance_ratio", 0)
        if ratio > 2:
            mood = "赚钱效应强"
        elif ratio > 1:
            mood = "赚钱效应偏强"
        elif ratio > 0.5:
            mood = "赚钱效应偏弱"
        else:
            mood = "亏钱效应显著"
        ratio_str = "∞" if ratio == float("inf") else str(ratio)
        items.append(f"涨跌比 {ratio_str}（涨{adv}/跌{dec}），{mood}")

    # 涨跌停评估
    lu = raw_data.get("limit_up", {})
    ld = raw_data.get("limit_down", {})
    lu_count = lu.get("count", 0)
    ld_count = ld.get("count", 0)
    if lu_count:
        desc = f"涨停 {lu_count}家，跌停 {ld_count}家"
        seal = lu.get("seal_rate_pct")
        if seal is not None:
            desc += f"，封板率 {seal}%"
            broken = lu.get("broken_count", 0)
            if broken:
                desc += f"（炸板 {broken}家）"
        hb = lu.get("highest_board", 0)
        if hb >= 2:
            desc += f"，最高 {hb}板"
        items.append(desc)

    # 北向资金评估
    nb = raw_data.get("northbound", {})
    nb_val = nb.get("net_buy_billion", 0)
    if nb_val:
        direction = "净买入" if nb_val >= 0 else "净卖出"
        items.append(f"北向资金{direction} {abs(nb_val):.2f}亿")

    # 板块评估
    top_ind = raw_data.get("sector_industry", {}).get("data", [])
    if top_ind:
        top3 = [f"{s['name']}({_fmt_pct(s['change_pct'])})" for s in top_ind[:3]]
        items.append(f"行业领涨: {', '.join(top3)}")

    # 均线位置
    ma = raw_data.get("moving_averages", {}).get("shanghai", {})
    if ma:
        above_5w = ma.get("above_ma5w")
        above_20 = ma.get("above_ma20")
        if above_5w is not None:
            pos = "5周线上方" if above_5w else "5周线下方"
            if above_20 is not None:
                pos += f"，MA20{'上' if above_20 else '下'}"
            items.append(f"上证运行于{pos}")

    # 风格化摘要
    sf = raw_data.get("style_factors", {})
    snap = sf.get("premium_snapshot", {})
    fb = snap.get("first_board", {})
    fb_med = fb.get("premium_median")
    fb_rate = fb.get("open_up_rate")
    if fb_med is not None and fb_rate is not None:
        items.append(f"首板溢价中位 {fb_med:+.2f}%，高开率 {fb_rate:.0%}")

    cap = sf.get("cap_preference", {})
    rel = cap.get("relative")
    if rel:
        items.append(
            f"大小盘偏向：{rel}"
            f"（沪深300 {_fmt_pct(cap.get('csi300_chg', 0))}"
            f" vs 中证1000 {_fmt_pct(cap.get('csi1000_chg', 0))}）"
        )

    return items


def _render_sector_rhythm(lines: list, raw_data: dict, section_idx: int) -> int:
    """在盘后报告中追加板块节奏分析章节，返回更新后的 section_idx。"""
    industry_rhythm = raw_data.get("sector_rhythm_industry", [])
    concept_rhythm = raw_data.get("sector_rhythm_concept", [])

    if not industry_rhythm and not concept_rhythm:
        return section_idx

    lines.append(f"\n## {_roman(section_idx)}、板块节奏分析 [判断] ★★☆\n")
    section_idx += 1
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

    return section_idx


# ------------------------------------------------------------------
# 风格化赚钱效应渲染
# ------------------------------------------------------------------

def _render_style_factors(lines: list, raw_data: dict, section_idx: int) -> int:
    """在盘后报告中追加风格化赚钱效应章节，返回更新后的 section_idx。"""
    sf = raw_data.get("style_factors", {})
    snap = sf.get("premium_snapshot", {})
    board_pref = sf.get("board_preference", {})
    cap_pref = sf.get("cap_preference", {})
    trend = sf.get("premium_trend", {})
    signals = sf.get("switch_signals", [])

    if not snap and not board_pref and not cap_pref:
        return section_idx

    lines.append(f"\n## {_roman(section_idx)}、风格化赚钱效应 [事实] ★★★\n")
    section_idx += 1

    # 溢价率概览表
    if snap:
        lines.append("### 溢价率概览（T-1 涨停 → T 开盘）\n")
        lines.append("| 类型 | 数量 | 高开率 | 溢价中位 | 溢价均值 |")
        lines.append("|------|------|--------|---------|---------|")
        _DISPLAY_ORDER = [
            ("first_board", "首板合计"),
            ("first_board_10cm", "├ 10cm首板"),
            ("first_board_20cm", "├ 20cm首板"),
            ("first_board_30cm", "└ 30cm首板"),
            ("first_board_yizi", "首板一字"),
            ("second_board", "二板"),
            ("third_board_plus", "三板+合计"),
            ("third_board", "├ 三板"),
            ("fourth_board", "├ 四板"),
            ("fifth_board_plus", "└ 五板+"),
            ("yizi_first_open", "一字首开（连板）"),
            ("capacity_top10", "容量票 Top10"),
        ]
        for key, label in _DISPLAY_ORDER:
            grp = snap.get(key)
            if not grp:
                continue
            cnt = grp.get("count", 0)
            rate = grp.get("open_up_rate")
            rate_s = f"{rate:.0%}" if rate is not None else "-"
            med = grp.get("premium_median")
            med_s = f"{med:+.2f}%" if med is not None else "-"
            mean = grp.get("premium_mean")
            mean_s = f"{mean:+.2f}%" if mean is not None else "-"
            lines.append(f"| {label} | {cnt} | {rate_s} | {med_s} | {mean_s} |")
        lines.append("")

    # 溢价趋势
    if trend:
        direction = trend.get("direction", "")
        medians = trend.get("first_board_median_5d", [])
        if medians:
            vals = " → ".join(f"{v:+.2f}%" for v in reversed(medians))
            lines.append(f"**溢价趋势**：{direction}（近5日首板溢价中位 {vals}）\n")

    # 涨停板偏好
    if board_pref:
        lines.append(
            f"**涨停板偏好**：{board_pref.get('dominant_type', '-')} 为主"
            f"（10cm {board_pref.get('pct_10cm', 0)}%"
            f" / 20cm {board_pref.get('pct_20cm', 0)}%"
            f" / 30cm {board_pref.get('pct_30cm', 0)}%）\n"
        )

    # 大小盘偏好
    if cap_pref:
        def _fmt(v):
            return f"+{v}%" if v >= 0 else f"{v}%"
        lines.append(
            f"**大小盘偏好**：{cap_pref.get('relative', '-')}"
            f"（沪深300 {_fmt(cap_pref.get('csi300_chg', 0))}"
            f" / 中证1000 {_fmt(cap_pref.get('csi1000_chg', 0))}"
            f"，价差 {cap_pref.get('spread', 0):+.2f}%）\n"
        )

    # 风格切换信号
    if signals:
        lines.append("### 风格切换信号\n")
        for sig in signals:
            lines.append(f"- ⚠ {sig}")
        lines.append("")

    return section_idx
