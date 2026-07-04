"""串阳首阴观察清单 Markdown 渲染。"""
from __future__ import annotations

from pathlib import Path

from services.string_yang import constants as C

_REDLINE = ("> 盘后只读观察清单 · 全部为 [判断] · "
            "不构成买卖建议、不含价位、不预测点位、不写交易计划层。")


def _fmt_ratio(v) -> str:
    return f"{float(v):.2f}x" if v is not None else "-"


def _fmt_pct(v) -> str:
    return f"{float(v):+.2f}%" if v is not None else "-"


def render_daily(result: dict) -> str:
    date = result.get("date", "")
    lines = [f"# 串阳首阴股票池 · {date}  [判断]", "", _REDLINE, ""]
    sectors = result.get("main_sectors") or []
    mainline = result.get("mainline") or {}
    concepts = mainline.get("main_concepts") or []
    degraded = "（当日主线缺失，已回退最近一日）" if result.get("main_sector_degraded") else ""
    candidates = result.get("candidates") or []
    status_label = {
        "llm": "LLM融合判断",
        "llm_fallback": "LLM不可用/无有效主线，降级成交额集中度",
        "fallback": "成交额集中度兜底",
        "disabled": "未启用LLM，使用成交额集中度",
    }.get(mainline.get("status"), mainline.get("status") or "成交额集中度")
    confidence = mainline.get("confidence")
    confidence_text = f"{float(confidence):.2f}" if confidence is not None else "-"
    lines += [
        "## 扫描口径",
        "- 主线判断：成交额集中度 + 同花顺概念分支 + 老师观点，由 LLM 只裁决板块/概念；LLM 失败时降级成交额 Top-K",
        f"- 条件：昨日以前连续 >= {C.STRING_YANG_MIN_COUNT} 根阳线，串阳段无涨停，最大单日涨幅 <= {C.MAX_YANG_PCT:.1f}%，今日出现第一根放量阴线",
        f"- 风险过滤：最近 {C.RECENT_LIMIT_LOOKBACK_DAYS} 个交易日无涨停；首阴日成交额 > 前5个交易日最大成交额；首阴收盘价 / MA{C.PRICE_MA_LOOKBACK_BARS} <= {C.MAX_PRICE_MA_RATIO:.2f}",
        "- 排序：今日成交额 / 前5日最大成交额 优先，其次今日成交额",
        "",
        "## 今日结果",
        f"- 主线来源：{status_label}；置信度：{confidence_text}",
        f"- 主线板块{degraded}：{'、'.join(sectors) or '（无）'}",
        f"- 主线概念分支：{'、'.join(concepts) or '（无）'}",
        f"- 命中数量：{len(candidates)}",
        "",
    ]
    evidence = mainline.get("evidence") or []
    watch_only = mainline.get("watch_only") or []
    if evidence or watch_only:
        lines += ["## 主线判断证据"]
        for item in evidence:
            lines.append(f"- {item}")
        if watch_only:
            lines.append(f"- 观察不纳入主线：{'、'.join(watch_only)}")
        lines.append("")

    if result.get("status") == "source_failed":
        lines += ["## 数据源异常", f"- 失败源：{'、'.join(result.get('source_errors') or [])}", ""]
        return "\n".join(lines).rstrip() + "\n"

    lines += ["## 首阴确认池（只列已出现第一根阴线）[判断]"]
    if not candidates:
        lines += ["今日无命中。", ""]
    else:
        lines += [
            "| 代码 | 名称 | 申万二级 | 连阳数 | 最大阳线 | 串阳累计 | 今日涨跌 | 今日额/前5最大 | 今日额/昨日 | 串阳区间 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for c in candidates:
            interval = f"{c.get('string_start_date', '')}~{c.get('string_end_date', '')}"
            branch = c.get("branch_concepts") or []
            sw_l2 = c.get("sw_l2", "")
            main_hit = f"{sw_l2}·分支:{'、'.join(branch)}" if branch else sw_l2
            lines.append(
                f"| {c.get('code', '')} | {c.get('name', '')} | {main_hit} | "
                f"{c.get('yang_count', '')} | {_fmt_pct(c.get('max_yang_pct'))} | "
                f"{_fmt_pct(c.get('string_total_pct'))} | {_fmt_pct(c.get('today_pct_chg'))} | "
                f"{_fmt_ratio(c.get('amount_ratio_vs_prev5_max'))} | "
                f"{_fmt_ratio(c.get('amount_ratio_vs_prev_day'))} | {interval} |"
            )
        lines.append("")

    rejects = result.get("rejects") or {}
    data_errors = result.get("data_errors") or []
    if rejects or data_errors:
        lines += ["## 过滤与数据提示"]
        if rejects:
            brief = "、".join(f"{k}:{v}" for k, v in rejects.items() if v)
            lines.append(f"- 过滤计数：{brief or '无'}")
        if data_errors:
            lines.append(f"- 个股行情缺失：{'、'.join(data_errors)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(markdown: str, date: str, *, root: Path | None = None) -> Path:
    repo_root = root or Path(__file__).resolve().parents[3]
    out_dir = repo_root / C.REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
