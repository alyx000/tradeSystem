"""形态篇观察清单 Markdown 渲染。"""
from __future__ import annotations

from pathlib import Path

from services.pattern_scan import constants as C
from utils.pattern import MA_ALIGNMENT_WINDOWS, VOLUME_MA_WINDOWS

_REDLINE = ("> 盘后只读观察清单 · 全部为 [判断] · "
            "不构成买卖建议、不含价位、不预测点位、不写交易计划层、不入关注池。")

_SOURCE = ("> 口径出处：老师《形态篇（第一节技术课程）》（teacher_notes#444，认知 cog_3b32e660）。"
           "形态成立只说明「资金进场且空间未透支」，不等于应当买入；是否参与由用户判断。")


def _fmt_pct(v) -> str:
    return f"{float(v):+.2f}%" if v is not None else "-"


def _fmt_yi(v) -> str:
    """成交额 → 亿元。

    Tushare `daily.amount` 单位是**千元**（不是元），故 1 亿元 = 1e5。
    与 `ma_breakout/renderer.py` 同一换算。写成 /1e8 会小 1000 倍——
    2026-07-24 真跑实测：全志科技 amount=5000999.24（千元）=50.01 亿，
    误算成 0.05 亿，由 `test_amount_unit_is_thousand_yuan` 锁死。
    """
    return f"{float(v) / 1e5:.2f}亿" if v is not None else "-"


def _fmt_num(v, digits: int = 3) -> str:
    return f"{float(v):.{digits}f}" if v is not None else "-"


def _ma_cell(values: dict) -> str:
    """均线值按窗口顺序拼成一列，避免每根均线单开一列把表撑爆。"""
    parts = []
    for n in MA_ALIGNMENT_WINDOWS:
        v = (values or {}).get(f"ma{n}")
        parts.append(f"{n}:{_fmt_num(v, 2)}" if v is not None else f"{n}:·")
    return " ".join(parts)


def _rhythm_cell(c: dict) -> str:
    yang = f"{c.get('yang_above_count', '-')}/{c.get('yang_total', '-')}"
    yin = f"{c.get('yin_shrink_count', '-')}/{c.get('yin_total', '-')}"
    return f"{c.get('rhythm_groups', '-')}组 阳{yang} 阴{yin}"


def _mainline_label(mainline: dict) -> str:
    return {
        "fallback": "成交额集中度兜底",
        "disabled": "未启用LLM，使用成交额集中度",
    }.get(mainline.get("status"), mainline.get("status") or "成交额集中度")


def render_daily(result: dict) -> str:
    date = result.get("date", "")
    mainline = result.get("mainline") or {}
    sectors = result.get("main_sectors") or []
    concepts = mainline.get("main_concepts") or []
    candidates = result.get("candidates") or []
    degraded = "（当日主线缺失，已回退最近一日）" if result.get("main_sector_degraded") else ""
    win = "/".join(str(n) for n in MA_ALIGNMENT_WINDOWS)
    vwin = "/".join(str(n) for n in VOLUME_MA_WINDOWS)

    lines = [f"# 形态篇选股观察清单 · {date}  [判断]", "", _REDLINE, "", _SOURCE, ""]
    lines += [
        "## 扫描口径",
        "- 板块优先：先取主线板块（成交额集中度 Top-K ∪ 同花顺概念分支，不接 LLM），再在板块内翻个股",
        f"- 条件① 均线多头排列：MA{win} 严格递减",
        "- 条件② MACD：零轴上方金叉或零上运行（DIF 与 DEA 同时 > 0 且 DIF >= DEA）",
        f"- 条件③ 量能节奏：近 {C.RHYTHM_LOOKBACK} 个交易日内，放量阳线（量站上 MA{vwin} 量）占阳线"
        f" >= {C.MIN_YANG_ABOVE_RATIO:.0%}，且完整「放量阳→缩量阴」>= {C.MIN_RHYTHM_GROUPS} 组",
        f"- 条件④ 尚未加速：近 {C.ACCEL_LOOKBACK_BARS} 个交易日无涨停 / 双创 15%+",
        f"- 价格口径：**前复权**（{C.RANGE_LOOKBACK_DAYS} 自然日窗口，含 open，因子取不到即整票剔除不硬算）",
        "- 排序：今日成交额降序（[事实]），非形态强弱排名",
        "",
        "## 今日结果",
        f"- 主线来源：{_mainline_label(mainline)}",
        f"- 主线板块{degraded}：{'、'.join(sectors) or '（无）'}",
        f"- 主线概念分支：{'、'.join(concepts) or '（无）'}",
        f"- 候选宇宙：{result.get('universe_count', 0)} 只",
    ]
    if result.get("status") == "source_failed":
        lines += ["- 扫描状态：数据源失败，未完成扫描", ""]
        lines += [
            "## 数据源异常",
            "- 数据源失败，未完成扫描；不代表已完成筛选后的空池。",
            f"- 失败源：{'、'.join(result.get('source_errors') or [])}",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    lines += [f"- 命中数量：{len(candidates)}", ""]

    lines += ["## 四条件共振池 [判断]"]
    if not candidates:
        lines += ["今日无命中。", ""]
    else:
        lines += [
            "| 代码 | 名称 | 主线归属 | 今日涨跌 | 今日成交额 | 均线（前复权） | MACD DIF/DEA | 量能节奏 |",
            "| --- | --- | --- | ---: | ---: | --- | ---: | --- |",
        ]
        for c in candidates:
            branch = c.get("branch_concepts") or []
            sw_l2 = c.get("sw_l2", "")
            main_hit = f"{sw_l2}·分支:{'、'.join(branch)}" if branch else sw_l2
            cross = " 金叉" if c.get("macd_golden_cross") else ""
            lines.append(
                f"| {c.get('code', '')} | {c.get('name', '')} | {main_hit} | "
                f"{_fmt_pct(c.get('pct_chg'))} | {_fmt_yi(c.get('today_amount'))} | "
                f"{_ma_cell(c.get('ma_values'))} | "
                f"{_fmt_num(c.get('macd_dif'))}/{_fmt_num(c.get('macd_dea'))}{cross} | "
                f"{_rhythm_cell(c)} |"
            )
        lines.append("")

    rejects = result.get("rejects") or {}
    data_errors = result.get("data_errors") or []
    if rejects or data_errors:
        lines += ["## 过滤与数据提示"]
        brief = "、".join(f"{k}:{v}" for k, v in rejects.items() if v)
        lines.append(f"- 过滤计数：{brief or '无'}")
        breaks = result.get("alignment_breaks") or {}
        if breaks:
            # 五线严格递减是最强的门（2026-07-24 实测 883 只里 842 只栽在这里）。
            # 列出断点分布供口径调参参考——纯观测，不参与筛选。
            ordered = sorted(breaks.items(), key=lambda kv: (-kv[1], kv[0]))
            detail = "、".join(f"{k}:{v}" for k, v in ordered)
            lines.append(f"- 多头排列断点分布（诊断，不参与筛选）：{detail}")
        if data_errors:
            shown = data_errors[:20]
            more = f"（另有 {len(data_errors) - len(shown)} 只未列出）" if len(data_errors) > len(shown) else ""
            lines.append(f"- 行情/复权因子缺失：{'、'.join(shown)}{more}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(markdown: str, date: str, *, root: Path | None = None) -> Path:
    repo_root = root or Path(__file__).resolve().parents[3]
    out_dir = repo_root / C.REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
