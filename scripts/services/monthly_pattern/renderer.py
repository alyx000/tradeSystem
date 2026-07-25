"""月线模式观察池 Markdown 渲染。

输入是扫描器/仓库整理好的 summary 或 pool rows；本模块不保存文件、不访问外部来源。
技术数值与财务时点标为 [事实]，模式归类、主线匹配、规则命中与状态变化标为 [判断]。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any


_NOTICE = "> 仅用于事实核验与模式观察，不构成交易指令。"

_STRATEGY_LABELS = {
    "fundamental_monthly_trend": "基本面月线趋势",
    "theme_monthly_attack": "题材月线进攻",
    "monthly_reacceleration": "月线二次启动",
}

_POOL_STATUS_LABELS = {
    "technical_candidate": "技术候选",
    "fundamental_verified": "基本面已核验",
    "active": "在池观察",
    "risk": "风险观察",
    "reentry": "重新进入观察",
    "exited": "已移出观察",
}

_SOURCE_LABELS = {
    "monthly": "月线行情",
    "monthly_bars": "月线行情",
    "monthly_bars_fetched": "月线行情拉取数",
    "monthly_bars_cached": "月线行情缓存数",
    "bars": "月线行情",
    "market": "市场行情",
    "financial": "财务快照",
    "financials": "财务快照",
    "mainline": "主线证据",
    "names": "证券名称",
}

_SOURCE_STATE_LABELS = {
    "success": "成功",
    "ok": "成功",
    "complete": "成功",
    "partial": "部分可用",
    "degraded": "降级可用",
    "limited_history": "历史样本有限",
    "source_ok_empty": "来源成功但无记录",
    "coverage_failed": "覆盖不足",
    "as_of_coverage_failed": "历史时点可见覆盖不足",
    "not_as_of": "无历史时点口径",
    "disabled": "已禁用",
    "missing": "缺失",
    "source_failed": "来源失败",
    "failed": "来源失败",
    "error": "来源失败",
    "blocked": "阻断",
    "not_run": "未运行",
    "skipped": "未运行",
}

_SOURCE_COUNT_LABELS = {
    "fetched": "拉取数",
    "cached": "缓存数",
    "count": "数量",
}

_COUNT_LABELS = {
    "scanned": "扫描股票数",
    "total": "扫描股票数",
    "market_stocks": "扫描股票数",
    "matched": "命中数",
    "matched_candidates": "本次模式命中数",
    "matched_technical_candidate": "本次命中·技术候选数",
    "matched_fundamental_verified": "本次命中·基本面已核验数",
    "matched_active": "本次命中·在池观察数",
    "pool_technical_candidate": "池内·技术候选数",
    "pool_fundamental_verified": "池内·基本面已核验数",
    "pool_active": "池内·在池观察数",
    "pool_risk": "池内·风险观察数",
    # 兼容旧 run：这些字段历史上统计的也是“本次命中”，不是池内总数。
    "technical_candidate": "本次命中·技术候选数",
    "technical_candidates": "本次模式命中数",
    "fundamental_verified": "本次命中·基本面已核验数",
    "financial_verified": "基本面核验数",
    "active": "本次命中·在池观察数",
    "risk": "本次命中·风险观察数",
    "reentry": "重新进入观察数",
    "exited": "移出观察数",
}

_EVIDENCE_LABELS = {
    "open": "月开盘",
    "high": "月最高",
    "low": "月最低",
    "close": "月收盘",
    "volume": "月成交量",
    "amount": "月成交额",
    "ma5": "MA5",
    "ma10": "MA10",
    "ma20": "MA20",
    "volume_ma5": "成交量MA5",
    "volume_ma10": "成交量MA10",
    "prior_volume_ma5": "前5月均量",
    "macd_dif": "MACD DIF",
    "macd_dea": "MACD DEA",
    "macd_histogram": "MACD柱",
    "distance_ratio": "距月高比例",
    "spread_ratio": "均线离散率",
    "roe_waa": "ROE（加权）",
    "roe": "ROE",
    "debt_to_assets": "资产负债率",
    "netprofit_yoy": "净利润同比",
    "deductedprofit_yoy": "扣非净利润同比",
    "bullish_body_crosses_three_mas": "阳线实体穿三线",
    "zero_axis_golden_cross": "MACD零轴上金叉",
    "volume_above_ma5_or_ma10": "成交量超过月均量",
    "close_near_month_high": "收盘靠近月高",
    "ma_bullish_alignment": "月均线多头排列",
    "close_at_or_above_ma5": "月收盘不低于MA5",
    "prior_setup": "既往启动结构",
    "high_volume_bullish_month": "当前放量阳线",
}

_UNSAFE_FIELD_TOKENS = (
    "target_price",
    "price_target",
    "position",
    "buy",
    "sell",
    "entry_price",
    "exit_price",
    "目标价",
    "仓位",
    "买入",
    "卖出",
)

_UNSAFE_TEXT_TOKENS = ("目标价", "仓位", "买入", "卖出")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("|", "｜").replace("\r", " ").replace("\n", " ")
    if any(token in text for token in _UNSAFE_TEXT_TOKENS):
        return "（已省略非观察性描述）"
    return text


def _is_unsafe_field(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return any(token in normalized for token in _UNSAFE_FIELD_TOKENS)


def _format_number(value: Real) -> str:
    if isinstance(value, bool):
        return "命中" if value else "未命中"
    return f"{float(value):.2f}"


def _label(key: str) -> str:
    return _EVIDENCE_LABELS.get(key, key)


def _collect_evidence(
    payload: Any,
    *,
    facts: list[tuple[str, str]],
    judgments: list[tuple[str, str]],
    parent_key: str = "",
    depth: int = 0,
) -> None:
    """提取受控的数值事实和布尔规则，不回显任意自由文本。"""
    if depth > 4 or not isinstance(payload, Mapping):
        return
    for raw_key, value in payload.items():
        key = str(raw_key)
        if _is_unsafe_field(key) or key in {
            "financial_ann_date",
            "report_period",
            "status",
            "hard_gate",
            "operator",
            "reason",
            "note",
            "text",
        }:
            continue
        if isinstance(value, Mapping):
            condition_label = _label(key)
            met = value.get("met")
            if isinstance(met, bool):
                judgments.append((condition_label, "命中" if met else "未命中"))
            _collect_evidence(
                value,
                facts=facts,
                judgments=judgments,
                parent_key=key,
                depth=depth + 1,
            )
        elif isinstance(value, bool):
            if key != "met":
                judgments.append((_label(key), "命中" if value else "未命中"))
        elif isinstance(value, Real):
            facts.append((_label(key), _format_number(value)))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                _collect_evidence(
                    item,
                    facts=facts,
                    judgments=judgments,
                    parent_key=key,
                    depth=depth + 1,
                )


def _dedupe(items: Sequence[tuple[str, str]], *, limit: int) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _pairs_text(items: Sequence[tuple[str, str]]) -> str:
    return "；".join(f"{label}={value}" for label, value in items) or "缺失"


def _strategy_text(value: Any) -> str:
    text = str(value or "").strip()
    return _STRATEGY_LABELS.get(text, _safe_text(text) or "未提供")


def _pool_status_text(value: Any) -> str:
    text = str(value or "").strip()
    return _POOL_STATUS_LABELS.get(text, _safe_text(text) or "未提供")


def _mainline_text(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("matched", value.get("met", value.get("value")))
    if value is True:
        return "命中"
    if value is False:
        return "未命中"
    if value is None or value == "":
        return "未提供"
    return _safe_text(value)


def _financial_status_text(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    labels = {
        "verified": "已核验",
        "fundamental_verified": "已核验",
        "passed": "已核验",
        "ok": "已核验",
        "success": "已核验",
        "pending": "待核验",
        "pre_screen": "中报预筛",
        "insufficient": "资料不足",
        "stale": "报告期滞后",
        "missing": "缺失",
        "source_failed": "来源失败",
        "failed": "未通过硬门",
        "not_applicable": "不适用",
    }
    return labels.get(normalized, _safe_text(value) or "未提供")


def _render_candidate(candidate: Mapping[str, Any]) -> list[str]:
    code = _safe_text(candidate.get("stock_code") or candidate.get("code"))
    name = _safe_text(candidate.get("stock_name") or candidate.get("name"))
    strategy = _strategy_text(candidate.get("strategy_type"))
    pool_status = _pool_status_text(
        candidate.get("pool_status")
        if candidate.get("pool_status") is not None
        else candidate.get("status")
    )
    industry = _safe_text(candidate.get("industry")) or "未提供"
    mainline = _mainline_text(candidate.get("mainline_match"))

    lines = [
        f"### {code} {name}".rstrip(),
        f"- 模式归类：[判断] {strategy}",
        f"- 池状态：[判断] {pool_status}",
        f"- 行业：[事实] {industry}",
        f"- 主线匹配：[判断] {mainline}",
    ]
    if candidate.get("signal_month") or candidate.get("last_seen_date"):
        lines.append(
            "- 观察时点：[事实] "
            f"信号月={_safe_text(candidate.get('signal_month')) or '缺失'}；"
            f"最后观察日={_safe_text(candidate.get('last_seen_date')) or '缺失'}"
        )

    technical = candidate.get("technical_evidence")
    technical_facts: list[tuple[str, str]] = []
    technical_judgments: list[tuple[str, str]] = []
    _collect_evidence(
        technical,
        facts=technical_facts,
        judgments=technical_judgments,
    )
    lines.append(
        f"- 技术数值：[事实] "
        f"{_pairs_text(_dedupe(technical_facts, limit=14))}"
    )
    if technical_judgments:
        lines.append(
            f"- 技术规则：[判断] "
            f"{_pairs_text(_dedupe(technical_judgments, limit=10))}"
        )

    financial = candidate.get("financial_evidence")
    financial_map = financial if isinstance(financial, Mapping) else {}
    financial_status = _financial_status_text(financial_map.get("status"))
    announcement_date = (
        candidate.get("financial_ann_date")
        or financial_map.get("financial_ann_date")
        or financial_map.get("announcement_date")
    )
    report_period = candidate.get("report_period") or financial_map.get("report_period")
    lines += [
        f"- 财务状态：[事实] {financial_status}",
        "- 财务时点：[事实] "
        f"公告日={_safe_text(announcement_date) or '缺失'}；"
        f"报告期={_safe_text(report_period) or '缺失'}",
    ]
    financial_facts: list[tuple[str, str]] = []
    financial_judgments: list[tuple[str, str]] = []
    _collect_evidence(
        financial_map,
        facts=financial_facts,
        judgments=financial_judgments,
    )
    if financial_facts:
        lines.append(
            f"- 财务数值：[事实] "
            f"{_pairs_text(_dedupe(financial_facts, limit=12))}"
        )
    if financial_judgments:
        lines.append(
            f"- 财务规则：[判断] "
            f"{_pairs_text(_dedupe(financial_judgments, limit=8))}"
        )
    lines.append("")
    return lines


def _source_status_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    known = _SOURCE_STATE_LABELS.get(normalized)
    if known is not None:
        return known
    raw = _safe_text(value)
    return f"未识别状态（{raw}）" if raw else "未提供"


def _source_count_text(value: Real) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _source_lines(source_status: Any) -> list[str]:
    lines = ["## 来源状态 [事实]", ""]
    if not isinstance(source_status, Mapping) or not source_status:
        state = _SOURCE_STATE_LABELS.get(str(source_status or "").lower(), "未提供")
        lines += [f"- 综合：{state}", ""]
        return lines
    for key, value in source_status.items():
        label = _SOURCE_LABELS.get(str(key), _safe_text(key))
        if isinstance(value, Real) and not isinstance(value, bool):
            lines.append(f"- {label}：{_source_count_text(value)}")
            continue
        if isinstance(value, Mapping):
            raw_state = value.get("status", value.get("state"))
            if raw_state is not None:
                lines.append(f"- {label}：{_source_status_label(raw_state)}")
            for count_key, count_label in _SOURCE_COUNT_LABELS.items():
                count = value.get(count_key)
                if isinstance(count, Real) and not isinstance(count, bool):
                    lines.append(
                        f"- {label}·{count_label}：{_source_count_text(count)}"
                    )
            if raw_state is None and not any(
                isinstance(value.get(count_key), Real)
                and not isinstance(value.get(count_key), bool)
                for count_key in _SOURCE_COUNT_LABELS
            ):
                lines.append(f"- {label}：未提供")
            continue
        lines.append(f"- {label}：{_source_status_label(value)}")
    lines.append("")
    return lines


def _effective_run_status(summary: Mapping[str, Any]) -> str:
    """运行表的显式状态是唯一主判定；来源明细只用于解释，不反向改写运行态。"""
    status = str(summary.get("status") or "").strip().lower()
    if status in {"complete", "partial", "failed"}:
        return status
    # 旧/畸形 summary 没有合法运行态时保持保守 partial；未知来源枚举不得伪造成失败。
    return "partial"


def _render_counts(counts: Any) -> list[str]:
    lines = ["## 扫描计数 [事实]", ""]
    if not isinstance(counts, Mapping) or not counts:
        return lines + ["- 未提供", ""]
    preferred = list(_COUNT_LABELS)
    keys = [key for key in preferred if key in counts]
    keys.extend(key for key in counts if key not in keys)
    for key in keys:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, Real):
            continue
        label = _COUNT_LABELS.get(str(key), _safe_text(key))
        number = int(value) if float(value).is_integer() else float(value)
        lines.append(f"- {label}：[事实] {number}")
    lines.append("")
    return lines


def _render_error(error: Any) -> list[str]:
    if not error:
        return []
    if isinstance(error, Mapping):
        parts = [
            f"{_safe_text(key)}={_safe_text(value)}"
            for key, value in error.items()
        ]
        text = "；".join(parts)
    elif isinstance(error, Sequence) and not isinstance(error, (str, bytes)):
        text = "；".join(_safe_text(value) for value in error)
    else:
        text = _safe_text(error)
    return ["## 异常说明 [事实]", "", text or "未提供", ""]


def _render_transitions(transitions: Any) -> list[str]:
    lines = ["## 状态变化 [判断]", ""]
    if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes)):
        return lines + ["本次无状态变化。", ""]
    if not transitions:
        return lines + ["本次无状态变化。", ""]
    for transition in transitions:
        if not isinstance(transition, Mapping):
            continue
        code = _safe_text(transition.get("stock_code") or transition.get("code"))
        name = _safe_text(transition.get("stock_name") or transition.get("name"))
        strategy = transition.get("strategy_type")
        if strategy:
            lines.append(f"### {code} {name} · {_strategy_text(strategy)}".rstrip())
        else:
            lines.append(f"### {code} {name}".rstrip())
        lines.append(
            "- 状态变化：[判断] "
            f"{_pool_status_text(transition.get('from_status'))} → "
            f"{_pool_status_text(transition.get('to_status'))}"
        )
        if transition.get("reason"):
            lines.append(f"- 变化依据：[判断] {_safe_text(transition.get('reason'))}")
        lines.append("")
    return lines


def render_daily(summary: Mapping[str, Any]) -> str:
    """渲染一次月线扫描摘要，并严格区分失败、partial 与真实空候选。"""
    scan_date = _safe_text(summary.get("scan_date")) or "未提供"
    signal_month = _safe_text(summary.get("signal_month")) or "未提供"
    run_status = _effective_run_status(summary)
    status_label = {
        "complete": "完成",
        "partial": "部分完成",
        "failed": "失败",
    }[run_status]
    lines = [
        f"# 月线模式观察池日报 · {scan_date}",
        "",
        _NOTICE,
        "",
        f"- 扫描日：{scan_date}",
        f"- 信号月：{signal_month}",
        f"- 运行状态：[事实] {status_label}",
        "",
    ]
    lines += _source_lines(summary.get("source_status"))

    if run_status == "failed":
        lines += [
            "## 运行结果",
            "",
            "运行失败（来源或处理链路）：本次不产出正常候选，该状态不等于真实空候选。",
            "",
        ]
        lines += _render_error(summary.get("error"))
        return "\n".join(lines).rstrip() + "\n"

    if run_status == "partial":
        lines += [
            "> 部分来源可用：以下内容仅基于已取得证据；缺失证据不视为未命中。",
            "",
        ]

    lines += _render_counts(summary.get("counts"))
    raw_candidates = summary.get("candidates")
    candidates = (
        [item for item in raw_candidates if isinstance(item, Mapping)]
        if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes))
        else []
    )
    lines += ["## 候选观察", ""]
    if not candidates:
        if run_status == "complete":
            lines += [
                "真实空候选（非采集故障）：来源均成功，完成月规则筛选后数量为 0。",
                "",
            ]
        else:
            lines += [
                "当前无可展示候选，但来源不完整，不能判为真实空候选。",
                "",
            ]
    else:
        for candidate in candidates:
            lines += _render_candidate(candidate)

    lines += _render_transitions(summary.get("transitions"))
    lines += _render_error(summary.get("error"))
    return "\n".join(lines).rstrip() + "\n"


def render_pool(rows: Sequence[Mapping[str, Any]]) -> str:
    """渲染当前月线模式观察池，只展示状态和分层证据。"""
    lines = ["# 月线模式观察池", "", _NOTICE, ""]
    valid_rows = [row for row in rows if isinstance(row, Mapping)]
    if not valid_rows:
        lines += ["观察池为空。", ""]
    else:
        lines += ["## 池内观察", ""]
        for row in valid_rows:
            lines += _render_candidate(row)
    return "\n".join(lines).rstrip() + "\n"
