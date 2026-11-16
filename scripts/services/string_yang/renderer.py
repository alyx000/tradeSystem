"""串阳首阴观察清单 Markdown 渲染。"""
from __future__ import annotations

from datetime import date as calendar_date
from pathlib import Path
from tempfile import NamedTemporaryFile

from services.string_yang import constants as C
from services.string_yang.mainline import MainlineJudgment

_REDLINE = ("> 盘后只读观察清单 · 全部为 [判断] · "
            "不构成买卖建议、不含价位、不预测点位、不写交易计划层。")
_MAINLINE_DATA_ERRORS = ("concept_flow", "ths_member", "concept_coverage")
_MAINLINE_DATA_LABEL = "- 主线数据提示："


def _fmt_ratio(v) -> str:
    return f"{float(v):.2f}x" if v is not None else "-"


def _fmt_pct(v) -> str:
    return f"{float(v):+.2f}%" if v is not None else "-"


def mainline_status_label(mainline: dict) -> str:
    """主线来源文案。llm_fallback 按 source_errors 细分调用失败与有效空裁决。"""
    if mainline.get("status") == "llm_fallback":
        errors = set(mainline.get("source_errors") or [])
        if "llm_call_failed" in errors:
            return "LLM调用失败，降级成交额集中度"
        if "llm_error" in errors:
            return "LLM调用异常，降级成交额集中度"
        if "llm_mainline_empty" in errors:
            return "LLM判无有效主线，降级成交额集中度"
        return "LLM不可用/无有效主线，降级成交额集中度"
    return {
        "llm": "LLM融合判断",
        "fallback": "成交额集中度兜底",
        "disabled": "未启用LLM，使用成交额集中度",
    }.get(mainline.get("status"), mainline.get("status") or "成交额集中度")


def render_daily(result: dict) -> str:
    date = result.get("date", "")
    lines = [f"# 串阳首阴股票池 · {date}  [判断]", "", _REDLINE, ""]
    sectors = result.get("main_sectors") or []
    mainline = result.get("mainline") or {}
    concepts = mainline.get("main_concepts") or []
    degraded = "（当日主线缺失，已回退最近一日）" if result.get("main_sector_degraded") else ""
    candidates = result.get("candidates") or []
    status_label = mainline_status_label(mainline)
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
    ]
    data_errors = mainline.get("source_errors") or []
    mainline_gaps = [error for error in _MAINLINE_DATA_ERRORS if error in data_errors]
    if mainline_gaps:
        lines.append(f"{_MAINLINE_DATA_LABEL}{'、'.join(mainline_gaps)}")
    if result.get("status") == "source_failed":
        lines += ["- 扫描状态：数据源失败，未完成扫描", ""]
    else:
        lines += [f"- 命中数量：{len(candidates)}", ""]
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
        lines += [
            "## 数据源异常",
            "- 数据源失败，未完成扫描；不代表已完成筛选后的空池。",
            f"- 失败源：{'、'.join(result.get('source_errors') or [])}",
            "",
        ]
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


def render_dingtalk(result: dict) -> str:
    """手机端摘要，沿用扫描排序，完整明细留在本地报告。"""
    date = result["date"]
    if result.get("status") == "source_failed":
        errors = "、".join(result.get("source_errors") or [])
        detail = f" · {errors}" if errors else ""
        return f"# 串阳首阴数据源失败 · {date}\n\n**未完成扫描**{detail}\n"

    mainline = result.get("mainline") or {}
    main_groups = (result.get("main_sectors") or []) + (mainline.get("main_concepts") or [])
    candidates = result.get("candidates") or []
    missing = len(result.get("data_errors") or [])
    concept_gap = bool(set(_MAINLINE_DATA_ERRORS).intersection(mainline.get("source_errors") or []))
    if main_groups:
        coverage = "（已核验样本）" if missing or concept_gap else ""
        outcome = f"命中 {len(candidates)} 只{coverage}"
    else:
        outcome = "暂无可扫描主线"
    lines = [f"# 串阳首阴股票池 · {date} [判断]", "", f"**{outcome}**", ""]
    if main_groups:
        lines.append(f"- 主线 · {'、'.join(main_groups)}")
    if mainline.get("status") != "llm":
        lines.append(f"- {mainline_status_label(mainline)}")
    if result.get("main_sector_degraded"):
        lines.append("- 板块成交额沿用最近一日" if mainline.get("volume_sectors") else "- 当日板块成交额数据缺失")
    if concept_gap:
        lines.append("- 概念数据覆盖不全")
    if missing:
        lines.append(f"- {missing} 只行情缺失")
    if candidates:
        lines += ["", "连阳天数 · 当日涨跌 · 成交额/前5日最高", ""]
        preview = candidates[:5]
        for c in preview:
            lines.append(
                f"- **{c.get('name', '')}**（{c.get('code', '')}）"
                f" · {c.get('yang_count', '—')} 阳 · {_fmt_pct(c.get('today_pct_chg'))}"
                f" · {_fmt_ratio(c.get('amount_ratio_vs_prev5_max'))}"
            )
        remaining = len(candidates) - len(preview)
        if remaining:
            lines += ["", f"另有 {remaining} 只见本地报告。"]
    lines += ["", f"本地报告 `{C.REPORT_DIR}/{date}.md`"]
    return "\n".join(lines) + "\n"


def _report_path(date: str, root: Path | None) -> Path:
    if calendar_date.fromisoformat(date).isoformat() != date:
        raise ValueError("交易日须为 YYYY-MM-DD")
    repo_root = root or Path(__file__).resolve().parents[3]
    return repo_root / C.REPORT_DIR / f"{date}.md"


def read_report_mainline(date: str, *, root: Path | None = None) -> MainlineJudgment:
    """从同日已落盘报告读取裁决，兼容既有 Markdown 归档。"""
    markdown = _report_path(date, root).read_text(encoding="utf-8")
    if not markdown.startswith(f"# 串阳首阴股票池 · {date}  [判断]\n"):
        raise ValueError("串阳首阴报告日期或标题不符")
    _, heading, results = markdown.partition("\n## 今日结果\n")
    fields = {}
    result_lines = results.split("\n## ", 1)[0].splitlines()
    for line in result_lines:
        label, separator, value = line.partition("：")
        if separator and label.startswith(("- 主线来源", "- 主线板块", "- 主线概念分支")):
            field = label.split("（", 1)[0]
            if field in fields or not value.strip():
                raise ValueError("串阳首阴报告主线字段重复或为空")
            fields[field] = (label, value.strip())
    if not heading or set(fields) != {"- 主线来源", "- 主线板块", "- 主线概念分支"}:
        raise ValueError("串阳首阴报告缺少主线字段")

    label, _, confidence = fields["- 主线来源"][1].partition("；置信度：")
    if label == "LLM融合判断":
        status, errors = "llm", []
    elif label.startswith("LLM") and label.endswith("降级成交额集中度"):
        status = "llm_fallback"
        errors = [reason for prefix, reason in (
            ("LLM调用失败", "llm_call_failed"),
            ("LLM调用异常", "llm_error"),
            ("LLM判无有效主线", "llm_mainline_empty"),
        ) if label.startswith(prefix)]
    else:
        raise ValueError("串阳首阴报告没有 LLM 裁决或降级记录")
    data_errors = {
        error for line in result_lines if line.startswith(_MAINLINE_DATA_LABEL)
        for error in line[len(_MAINLINE_DATA_LABEL):].split("、")
    }
    errors.extend(error for error in _MAINLINE_DATA_ERRORS if error in data_errors)

    sector_label, sector_text = fields["- 主线板块"]
    concept_text = fields["- 主线概念分支"][1]
    sectors = [] if sector_text == "（无）" else sector_text.split("、")
    concepts = [] if concept_text == "（无）" else concept_text.split("、")
    if status == "llm" and not (sectors or concepts):
        raise ValueError("串阳首阴报告标为 LLM 裁决，但无有效主线")
    return MainlineJudgment(
        date=date, main_sectors=sectors, main_concepts=concepts, status=status,
        degraded="已回退" in sector_label,
        confidence=float(confidence) if confidence not in {"", "-"} else None,
        source_errors=errors,
    )


def write_report(markdown: str, date: str, *, root: Path | None = None) -> Path:
    path = _report_path(date, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(markdown)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path
