"""月线模式观察池 Markdown 渲染。

输入是扫描器/仓库整理好的 summary 或 pool rows；本模块不保存文件、不访问外部来源。
技术数值与财务时点标为 [事实]，模式归类、主线匹配、规则命中与状态变化标为 [判断]。
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from services.monthly_pattern import financials, focus


_NOTICE = "> 仅用于事实核验与模式观察，不构成交易指令。"
PUSH_BODY_MAX_BYTES = 18_000
PUSH_CRITICAL_TRANSITION_MAX_ITEMS = 12
LIFECYCLE_SCORE_VERSION = "monthly_pool_lifecycle_v1"

_STRATEGY_LABELS = {
    "fundamental_monthly_trend": "基本面月线趋势",
    "theme_monthly_attack": "题材月线进攻",
    "monthly_reacceleration": "月线二次启动",
}
_STRATEGY_ORDER = {
    strategy: index for index, strategy in enumerate(_STRATEGY_LABELS)
}

_POOL_STATUS_LABELS = {
    "technical_candidate": "技术候选",
    "fundamental_verified": "基本面已核验",
    "active": "在池观察",
    "risk": "风险观察",
    "reentry": "重新进入观察",
    "exited": "已移出观察",
}
_LIFECYCLE_PRIORITY_SCORES = {
    "active": 4,
    "fundamental_verified": 3,
    "technical_candidate": 2,
    "risk": 1,
    "exited": 0,
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
    "valuation": "行业估值",
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
    "insufficient": "资料不足",
    "stale": "数据陈旧",
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
    "matched": "命中记录数",
    "matched_candidates": "本次初筛命中记录数",
    "matched_stocks": "本次初筛去重股票数",
    "matched_technical_candidate": "本次命中·技术候选记录数",
    "matched_fundamental_verified": "本次命中·基本面已核验记录数",
    "matched_active": "本次命中·在池观察记录数",
    "matched_risk": "本次命中·风险观察记录数",
    "pool_technical_candidate": "池内·技术候选记录数",
    "pool_fundamental_verified": "池内·基本面已核验记录数",
    "pool_active": "池内·在池观察记录数",
    "pool_risk": "池内·风险观察记录数",
    # 兼容旧 run：这些字段历史上统计的也是“本次命中”，不是池内总数。
    "technical_candidate": "本次命中·技术候选记录数",
    "technical_candidates": "本次模式命中记录数",
    "fundamental_verified": "本次命中·基本面已核验记录数",
    "financial_verified": "基本面核验记录数",
    "active": "本次命中·在池观察记录数",
    "risk": "本次命中·风险观察记录数",
    "reentry": "重新进入观察记录数",
    "exited": "移出观察记录数",
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
    "contract_liability": "合同负债",
    "contract_liability_growth_pct": "合同负债同比",
    "contract_liability_to_revenue_pct": "合同负债/同期营收",
    "revenue": "同期营业收入",
    "rd_exp": "研发费用",
    "rd_exp_growth_pct": "研发费用同比",
    "rd_exp_to_revenue_pct": "研发费用/同期营收",
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
            # 这两个行业差异项由显式影子层解释，不能在通用财务规则中写成“命中”。
            "contract_liability_growth_ge_20",
            "contract_liability_qoq_pct",
            "contract_liability_qoq_delta",
            "contract_liability_qoq_prior_value",
            "contract_liability_qoq_prior_period",
            "contract_liability_qoq_low_base",
            "rd_exp_increasing",
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


def _candidate_status(candidate: Mapping[str, Any]) -> str:
    value = (
        candidate.get("pool_status")
        if candidate.get("pool_status") is not None
        else candidate.get("status")
    )
    return str(value or "").strip().lower()


def lifecycle_priority_score(candidate: Mapping[str, Any]) -> int | None:
    """返回生命周期观察分；只编码池状态层级，不评价技术强弱。"""
    return _LIFECYCLE_PRIORITY_SCORES.get(_candidate_status(candidate))


def _source_meta(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("source_meta")
    return value if isinstance(value, Mapping) else {}


def _canonical_stock_code(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("stock_code") or candidate.get("code")
    return str(value or "").strip().upper().split(".", 1)[0]


def _normalized_industry(value: Any) -> str:
    text = "".join(str(value or "").split())
    if not text or text in {"未提供", "未分类", "unknown", "None", "null"}:
        return "未分类"
    return _safe_text(text)


def _source_state(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("status", value.get("state"))
    return str(value or "").strip().lower()


def _record_industry_signal(
    candidate: Mapping[str, Any],
) -> tuple[str, int, str]:
    """返回行业、来源质量与来源说明；高质量分类不被旧的未分类覆盖。"""
    top_level = _normalized_industry(candidate.get("industry"))
    nested = _normalized_industry(_source_meta(candidate).get("industry"))
    industry_map_state = _source_state(_source_meta(candidate).get("industry_map"))
    if top_level != "未分类":
        return top_level, 3, "本次候选"
    if nested != "未分类" and industry_map_state == "success":
        return nested, 3, "申万映射成功快照"
    if nested != "未分类":
        return nested, 2, f"持久池快照（{industry_map_state or '状态未提供'}）"
    return "未分类", 0, "行业证据缺失"


def _record_industry(candidate: Mapping[str, Any]) -> str:
    return _record_industry_signal(candidate)[0]


def _industry_for_shadow(
    candidate: Mapping[str, Any],
    rendered_industry: str,
) -> str:
    """只把目标时点可信的行业证据交给影子解释模板。"""
    source_meta = _source_meta(candidate)
    industry_map_state = _source_state(source_meta.get("industry_map"))
    if industry_map_state:
        return rendered_industry if industry_map_state == "success" else ""
    # 当次扫描候选会把可靠行业放在顶层；旧持久池只在 source_meta 留存。
    top_level = _normalized_industry(candidate.get("industry"))
    return rendered_industry if top_level != "未分类" else ""


def _record_mainline(candidate: Mapping[str, Any]) -> Any:
    value = candidate.get("mainline_match")
    if value is None:
        value = _source_meta(candidate).get("mainline_match")
    return value


def _stock_name(records: Sequence[Mapping[str, Any]], code: str) -> str:
    ranked: list[tuple[int, str, str]] = []
    for record in records:
        name = _safe_text(record.get("stock_name") or record.get("name"))
        if not name:
            continue
        normalized_name = name.strip().upper().split(".", 1)[0]
        quality = 1 if normalized_name == code else 2
        recency = _safe_text(
            record.get("last_seen_date")
            or record.get("signal_month")
            or ""
        )
        ranked.append((quality, recency, name))
    if not ranked:
        return code or "未提供"
    return max(ranked)[2]


def _stock_sector(
    records: Sequence[Mapping[str, Any]],
) -> tuple[str, str, str, tuple[str, ...]]:
    signals = [_record_industry_signal(record) for record in records]
    best_quality = max((quality for _sector, quality, _source in signals), default=0)
    best = sorted(
        {
            (sector, source)
            for sector, quality, source in signals
            if quality == best_quality and sector != "未分类"
        }
    )
    sectors = tuple(sorted({sector for sector, _source in best}))
    if len(sectors) > 1:
        return "行业冲突", "conflict", "同质量行业证据冲突", sectors
    if sectors:
        sector = sectors[0]
        sources = sorted(source for value, source in best if value == sector)
        return sector, "classified", "、".join(sources), sectors
    return "未分类", "missing", "行业证据缺失", ()


def _strategy_sort_key(candidate: Mapping[str, Any]) -> tuple[int, int, str]:
    strategy = str(candidate.get("strategy_type") or "").strip()
    score = lifecycle_priority_score(candidate)
    return (
        _STRATEGY_ORDER.get(strategy, len(_STRATEGY_ORDER)),
        -(score if score is not None else -1),
        _safe_text(candidate.get("signal_month")),
    )


def _month_rank(value: Any) -> int:
    text = str(value or "").strip()
    digits = text.replace("-", "")[:6]
    return int(digits) if len(digits) == 6 and digits.isdigit() else -1


def _stock_projections(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        code = _canonical_stock_code(candidate)
        if code:
            key = code
        else:
            key = (
                "missing:"
                f"{_safe_text(candidate.get('stock_name') or candidate.get('name'))}:"
                f"{_safe_text(candidate.get('strategy_type'))}:"
                f"{_safe_text(candidate.get('signal_month'))}"
            )
        buckets.setdefault(key, []).append(candidate)

    stocks: list[dict[str, Any]] = []
    for key, raw_records in buckets.items():
        records = sorted(raw_records, key=_strategy_sort_key)
        code = _canonical_stock_code(records[0])
        scores = [
            score
            for score in (lifecycle_priority_score(record) for record in records)
            if score is not None
        ]
        score = max(scores) if scores else None
        primary_records = [
            record
            for record in records
            if lifecycle_priority_score(record) == score
        ]
        primary = (
            min(primary_records, key=_strategy_sort_key)
            if primary_records
            else records[0]
        )
        sector, sector_status, sector_source, sector_options = _stock_sector(records)
        strategy_labels: list[str] = []
        status_labels: list[str] = []
        for record in records:
            strategy_label = _strategy_text(record.get("strategy_type"))
            if strategy_label not in strategy_labels:
                strategy_labels.append(strategy_label)
        for record in sorted(
            records,
            key=lambda item: (
                -(
                    lifecycle_priority_score(item)
                    if lifecycle_priority_score(item) is not None
                    else -1
                ),
                _candidate_status(item),
            ),
        ):
            status_label = _pool_status_text(_candidate_status(record))
            if status_label not in status_labels:
                status_labels.append(status_label)
        latest_month = max(
            (
                _safe_text(record.get("signal_month"))
                for record in records
                if record.get("signal_month")
            ),
            default="",
        )
        stocks.append(
            {
                "stock_code": code or "未提供",
                "stock_name": _stock_name(records, code),
                "sector": sector,
                "sector_status": sector_status,
                "sector_source": sector_source,
                "sector_options": sector_options,
                "score": score,
                "score_kind": "lifecycle_priority",
                "score_version": LIFECYCLE_SCORE_VERSION,
                "primary_strategy": _strategy_text(primary.get("strategy_type")),
                "primary_status": _pool_status_text(_candidate_status(primary)),
                "strategies": strategy_labels,
                "statuses": status_labels,
                "strategy_count": len(records),
                "latest_signal_month": latest_month,
                "records": records,
                "_identity_key": key,
            }
        )
    return stocks


def _stock_sort_key(stock: Mapping[str, Any]) -> tuple[Any, ...]:
    score = stock.get("score")
    valid_score = isinstance(score, Real) and not isinstance(score, bool)
    return (
        0 if valid_score else 1,
        -float(score) if valid_score else 0.0,
        -int(stock.get("strategy_count") or 0),
        -_month_rank(stock.get("latest_signal_month")),
        _safe_text(stock.get("stock_code")),
    )


def _sector_sort_key(sector: str) -> tuple[int, str]:
    if sector == "行业冲突":
        return 1, sector
    if sector == "未分类":
        return 2, sector
    return 0, sector


def _sector_groups(
    candidates: Sequence[Mapping[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for stock in _stock_projections(candidates):
        grouped.setdefault(str(stock["sector"]), []).append(stock)
    return [
        (sector, sorted(stocks, key=_stock_sort_key))
        for sector, stocks in sorted(
            grouped.items(),
            key=lambda item: _sector_sort_key(item[0]),
        )
    ]


def _score_text(value: Any) -> str:
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:.1f}"
    return "—"


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


_SHADOW_APPLICABILITY_LABELS = {
    "core": "核心证据",
    "secondary": "辅助证据",
    "special_context": "特殊风险口径",
    "not_applicable": "不适用",
    "unknown": "行业未知",
}


def _shadow_money_text(value: Any) -> str:
    if not isinstance(value, Real) or isinstance(value, bool):
        return "缺失"
    number = float(value)
    magnitude = abs(number)
    if magnitude >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿元"
    if magnitude >= 10_000:
        return f"{number / 10_000:.2f}万元"
    return f"{number:.2f}元"


def _shadow_pct_text(value: Any) -> str:
    if not isinstance(value, Real) or isinstance(value, bool):
        return "缺失"
    return f"{float(value):+.2f}%"


def _shadow_delta_text(value: Any) -> str:
    if not isinstance(value, Real) or isinstance(value, bool):
        return "增减额缺失"
    number = float(value)
    if number > 0:
        return f"增加{_shadow_money_text(number)}"
    if number < 0:
        return f"减少{_shadow_money_text(abs(number))}"
    return "持平"


def _shadow_point_text(point: Any) -> str:
    if not isinstance(point, Mapping):
        return ""
    period = _safe_text(point.get("report_period")) or "报告期缺失"
    values: list[str] = []
    if point.get("value") is not None:
        values.append(_shadow_money_text(point.get("value")))
    if point.get("growth_pct") is not None:
        values.append(f"同比{_shadow_pct_text(point.get('growth_pct'))}")
    if point.get("to_revenue_pct") is not None:
        values.append(
            f"占同期营收{float(point.get('to_revenue_pct')):.2f}%"
        )
    if point.get("qoq_pct") is not None:
        values.append(f"环比{_shadow_pct_text(point.get('qoq_pct'))}")
    if point.get("qoq_delta") is not None:
        prior_period = _safe_text(point.get("qoq_prior_period")) or "上一报告期"
        values.append(
            f"较{prior_period}{_shadow_delta_text(point.get('qoq_delta'))}"
        )
    if point.get("qoq_low_base") is True:
        values.append("环比基数敏感")
    return f"{period}：{'、'.join(values) if values else '数据缺失'}"


def _shadow_factor_facts(factor: Mapping[str, Any]) -> str:
    points = [
        _shadow_point_text(factor.get("latest")),
        _shadow_point_text(factor.get("annual")),
    ]
    unique: list[str] = []
    for point in points:
        if point and point not in unique:
            unique.append(point)
    return "；".join(unique) or "无可见报告期证据"


def _render_industry_financial_shadow(
    financial_map: Mapping[str, Any],
    industry: str,
) -> list[str]:
    shadow = financials.build_industry_shadow(financial_map, industry)
    if not shadow.get("has_assessment"):
        return []
    contract = shadow["contract_liability"]
    rd_exp = shadow["rd_exp"]
    contract_label = _SHADOW_APPLICABILITY_LABELS.get(
        str(contract.get("applicability") or "unknown"),
        "行业未知",
    )
    rd_label = _SHADOW_APPLICABILITY_LABELS.get(
        str(rd_exp.get("applicability") or "unknown"),
        "行业未知",
    )
    lines = [
        "- 行业增强层：[判断·影子] "
        f"合同负债={contract_label}；研发费用={rd_label}；"
        "仅展示，不参与基本面硬门、池状态或生命周期观察分"
        f"（版本={shadow['version']}）。",
        "- 行业解释：[判断·影子] "
        f"合同负债：{_safe_text(contract.get('reason')) or '缺失'}；"
        f"研发费用：{_safe_text(rd_exp.get('reason')) or '缺失'}。",
    ]
    if contract.get("status") != "not_applicable":
        lines.append(
            "- 合同负债：[事实·影子] "
            f"{_shadow_factor_facts(contract)}。"
        )
    if rd_exp.get("status") != "not_applicable":
        lines.append(
            "- 研发费用：[事实·影子] "
            f"{_shadow_factor_facts(rd_exp)}；字段为 rd_exp，"
            "不代表资本化研发的完整口径。"
        )
    return lines


def _render_candidate(
    candidate: Mapping[str, Any],
    *,
    heading: str | None = None,
) -> list[str]:
    code = _safe_text(candidate.get("stock_code") or candidate.get("code"))
    name = _safe_text(candidate.get("stock_name") or candidate.get("name"))
    strategy = _strategy_text(candidate.get("strategy_type"))
    pool_status = _pool_status_text(
        candidate.get("pool_status")
        if candidate.get("pool_status") is not None
        else candidate.get("status")
    )
    industry = _record_industry(candidate)
    mainline = _mainline_text(_record_mainline(candidate))

    lines = [
        heading or f"### {code} {name}".rstrip(),
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
    lines += _render_industry_financial_shadow(
        financial_map,
        _industry_for_shadow(candidate, industry),
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


def _candidate_records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _counts_with_candidate_semantics(
    counts: Any,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result = dict(counts) if isinstance(counts, Mapping) else {}
    result["matched_stocks"] = len(_stock_projections(candidates))
    result["matched_risk"] = sum(
        _candidate_status(candidate) == "risk" for candidate in candidates
    )
    return result


def _render_grouped_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[str]:
    stocks = _stock_projections(candidates)
    lines = [
        "## 候选观察（按申万二级板块聚合）",
        "",
        f"> 生命周期观察分 v1（{LIFECYCLE_SCORE_VERSION}）：[判断] "
        "在池观察=4、基本面已核验=3、"
        "技术候选=2、风险观察=1、已移出观察=0。"
        "该分数只反映池状态层级，不代表胜率、技术强弱或买卖建议；"
        "触发条件与主线证据只展示、不加分。",
        "",
        "> 同股多策略取最高分、不累加；同分依次按策略记录数降序、"
        "最近信号月降序、股票代码升序破平。",
        "",
        f"- 本次共 {len(stocks)} 只独立股票，来自 {len(candidates)} 条策略记录。",
        "",
    ]
    for sector, sector_stocks in _sector_groups(candidates):
        record_count = sum(
            int(stock.get("strategy_count") or 0) for stock in sector_stocks
        )
        lines += [
            f"### {sector}（{len(sector_stocks)}只 / {record_count}条策略记录）",
            "",
        ]
        for rank, stock in enumerate(sector_stocks, 1):
            score = _score_text(stock.get("score"))
            lines += [
                f"#### {rank}. {stock['stock_code']} {stock['stock_name']}"
                f"｜生命周期观察分 {score}",
                f"- 评分依据：[判断] 主策略={stock['primary_strategy']}；"
                f"生命周期状态={stock['primary_status']}；"
                f"最高池状态分={score}；同股多策略不累加",
                f"- 板块归属：[事实] {stock['sector']}"
                f"（来源={stock['sector_source']}）",
                f"- 命中策略：[判断] {'、'.join(stock['strategies'])}"
                f"（{stock['strategy_count']} 条策略记录）",
            ]
            if stock.get("sector_status") == "conflict":
                lines.append(
                    "- 行业归属：[事实] 同质量证据冲突，已归入“行业冲突”；"
                    f"候选={'、'.join(stock.get('sector_options') or ())}"
                )
            lines.append("")
            for record in stock["records"]:
                lines += _render_candidate(
                    record,
                    heading=(
                        f"##### {_strategy_text(record.get('strategy_type'))}"
                        f"｜{_pool_status_text(_candidate_status(record))}"
                        f"｜策略分 {_score_text(lifecycle_priority_score(record))}"
                    ),
                )
    return lines


def _focus_breakdown_text(item: Mapping[str, Any]) -> str:
    breakdown = item.get("breakdown")
    values = breakdown if isinstance(breakdown, Mapping) else {}
    return "；".join(
        (
            f"技术{_score_text(values.get('technical'))}/30",
            f"主线{_score_text(values.get('mainline'))}/25",
            f"基本面{_score_text(values.get('fundamental'))}/20",
            f"估值{_score_text(values.get('valuation'))}/10",
            f"行业增强{_score_text(values.get('industry_factors'))}/10",
            f"数据完整度{_score_text(values.get('data_quality'))}/5",
        )
    )


def _focus_valuation_text(item: Mapping[str, Any]) -> str:
    value = item.get("valuation")
    if not isinstance(value, Mapping) or value.get("status") != "success":
        return "资料不足，不跨行业使用绝对估值补分"
    metric_labels = {"pe_ttm": "PE(TTM)", "pb": "PB", "ps_ttm": "PS(TTM)"}
    metric = str(value.get("metric") or "")
    return (
        f"{metric_labels.get(metric, _safe_text(metric) or '指标')}="
        f"{_score_text(value.get('value'))}；行业低值分位="
        f"{_score_text(value.get('industry_percentile'))}%；"
        f"样本={_score_text(value.get('industry_sample_size'))}；"
        f"时点={_safe_text(value.get('as_of_date')) or '缺失'}"
    )


def _render_focus_funnel(
    candidates: Sequence[Mapping[str, Any]],
) -> list[str]:
    funnel = focus.build_focus_funnel(candidates)
    focus_items = list(funnel["focus"])
    priority_items = list(funnel["priority"])
    lines = [
        "## 候选漏斗 [判断]",
        "",
        f"- 后台技术初筛：{funnel['input_stocks']} 只；保留完整事实，不在日报逐只展开。",
        f"- 基本面核验层：{funnel['verified_stocks']} 只；只有该层进入综合观察评分。",
        f"- 重点观察层：展示 {len(focus_items)}/{funnel['verified_stocks']} 只。",
        f"- 专池重点层：展示 {len(priority_items)}/"
        f"{min(focus.PRIORITY_LIMIT, funnel['priority_eligible_stocks'])} 只。",
        "",
        f"> 综合观察分 100 分（{funnel['version']}）：技术30、主线25、"
        "基本面20、行业内估值10、合同负债/研发投入行业增强10、"
        "数据完整度5。只用于观察排序，不代表胜率、价格预测或操作建议。",
        "",
        "> 基本面硬门未核验的股票不参与 Top10；缺失证据记资料不足，不按失败处理。"
        "合同负债和研发投入只按行业适用性加分，不适用时不作负面判断。",
        "",
        f"> 生命周期观察分 v1（{LIFECYCLE_SCORE_VERSION}）仍保留在策略明细中，"
        "只表达池状态，不参与综合观察分，也不代表胜率、技术强弱或买卖建议。",
        "",
        "## 专池重点层（全市场前 3）",
        "",
    ]
    if not priority_items:
        lines += [
            "当前没有同时满足基本面核验、行业可归类、无风险状态与数据完整度门槛的股票。",
            "",
        ]
    else:
        for rank, item in enumerate(priority_items, 1):
            lines += [
                f"{rank}. {_safe_text(item['stock_code'])} "
                f"{_safe_text(item['stock_name'])}｜"
                f"{_safe_text(item['industry'])}｜综合观察分 "
                f"{_score_text(item['score'])}/100",
                f"   - [判断] {_focus_breakdown_text(item)}",
            ]
        lines.append("")

    lines += ["## 候选观察（按申万二级板块聚合）", ""]
    if not focus_items:
        lines += [
            "当前没有可评分的基本面已核验股票；技术初筛结果仍保留在后台池。",
            "",
        ]
        return lines

    global_rank = {
        str(item["stock_code"]): rank for rank, item in enumerate(focus_items, 1)
    }
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in focus_items:
        grouped.setdefault(str(item["industry"]), []).append(item)
    sectors = sorted(
        grouped,
        key=lambda sector: (
            -max(float(item["score"]) for item in grouped[sector]),
            _sector_sort_key(sector),
        ),
    )
    for sector in sectors:
        items = sorted(
            grouped[sector],
            key=lambda item: (
                -float(item["score"]),
                global_rank[str(item["stock_code"])],
            ),
        )
        record_count = sum(len(item["records"]) for item in items)
        lines += [
            f"### {sector}（{len(items)}只 / {record_count}条策略记录）",
            "",
        ]
        for sector_rank, item in enumerate(items, 1):
            projection = _stock_projections(item["records"])[0]
            lifecycle_score = _score_text(projection.get("score"))
            lines += [
                f"#### {sector_rank}. {_safe_text(item['stock_code'])} "
                f"{_safe_text(item['stock_name'])}"
                f"｜生命周期观察分 {lifecycle_score}｜综合观察分 "
                f"{_score_text(item['score'])}/100（全市场第"
                f"{global_rank[str(item['stock_code'])]}）",
                f"- 评分依据：[判断] {_focus_breakdown_text(item)}；"
                f"最高池状态分={lifecycle_score}；同股多策略不累加",
                f"- 主线依据：[判断] {item['mainline_reason']}",
                f"- 估值依据：[事实] {_focus_valuation_text(item)}",
                f"- 基本面依据：[事实] {'；'.join(item['fundamental_reasons'])}",
                f"- 板块归属：[事实] {_safe_text(item['industry'])}",
                f"- 命中策略：[判断] "
                f"{'、'.join(_strategy_text(value) for value in item['strategies'])}"
                f"（{len(item['records'])} 条策略记录）",
                "",
            ]
            if item["industry"] == "行业冲突":
                options = sorted(
                    {
                        _record_industry(record)
                        for record in item["records"]
                        if _record_industry(record) != "未分类"
                    }
                )
                lines.insert(
                    len(lines) - 1,
                    "- 行业归属：[事实] 同质量证据冲突，已归入“行业冲突”；"
                    f"候选={'、'.join(options) or '缺失'}",
                )
            for record in item["records"]:
                lines += _render_candidate(
                    record,
                    heading=(
                        f"##### {_strategy_text(record.get('strategy_type'))}"
                        f"｜{_pool_status_text(_candidate_status(record))}"
                        f"｜策略分 {_score_text(lifecycle_priority_score(record))}"
                    ),
                )
    if funnel["omitted_verified"]:
        lines += [
            f"> 另有 {funnel['omitted_verified']} 只基本面已核验股票未进入 Top10；"
            "完整池可通过只读命令 `python3 scripts/main.py monthly-pattern pool` 核对。",
            "",
        ]
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

    candidates = _candidate_records(summary.get("candidates"))
    focus_candidates = _candidate_records(summary.get("focus_candidates"))
    funnel_candidates = focus_candidates or candidates
    lines += _render_counts(
        _counts_with_candidate_semantics(summary.get("counts"), candidates)
    )
    if not funnel_candidates:
        lines += ["## 候选观察（按申万二级板块聚合）", ""]
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
        lines += _render_focus_funnel(funnel_candidates)

    lines += _render_transitions(summary.get("transitions"))
    lines += _render_error(summary.get("error"))
    return "\n".join(lines).rstrip() + "\n"


def _push_plain(value: Any, limit: int) -> str:
    text = _safe_text(value)[:limit]
    for char in ("\\", "`", "*", "_", "[", "]", "(", ")", "<", ">", "#", "!"):
        text = text.replace(char, f"\\{char}")
    return text


def _push_report_path(report_path: str) -> str:
    raw = str(report_path)
    if any(char in raw for char in ("\r", "\n", "`")):
        raise ValueError("report_path 包含不安全字符")
    if len(raw.encode("utf-8")) > 2_048:
        raise ValueError("report_path 超过推送安全长度")
    return f"`{raw}`"


def _push_transition_groups(transitions: Any) -> tuple[list[tuple[str, int]], int]:
    if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes)):
        return [], 0
    counts: Counter[tuple[str, str, str]] = Counter()
    for transition in transitions:
        if not isinstance(transition, Mapping):
            continue
        strategy = str(transition.get("strategy_type") or "").strip()
        from_status = str(transition.get("from_status") or "").strip().lower()
        to_status = str(transition.get("to_status") or "").strip().lower()
        if not from_status and not to_status:
            continue
        counts[(strategy, from_status, to_status)] += 1
    to_priority = {
        "exited": 0,
        "risk": 1,
        "active": 2,
        "fundamental_verified": 3,
        "technical_candidate": 4,
    }
    groups: list[tuple[str, int]] = []
    for (strategy, from_status, to_status), count in sorted(
        counts.items(),
        key=lambda item: (
            to_priority.get(item[0][2], 9),
            item[0][0],
            item[0][1],
            item[0][2],
        ),
    ):
        groups.append(
            (
                f"- {_strategy_text(strategy)}｜"
                f"{_pool_status_text(from_status)} → {_pool_status_text(to_status)}："
                f"{count} 只",
                count,
            )
        )
    return groups, sum(counts.values())


def _push_critical_transition_lines(transitions: Any) -> list[str]:
    """保留少量可定位的风险/退出明细，完整覆盖仍由聚合计数表达。"""
    if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes)):
        return []
    prioritized: list[tuple[int, int, str]] = []
    for index, transition in enumerate(transitions):
        if not isinstance(transition, Mapping):
            continue
        to_status = str(transition.get("to_status") or "").strip().lower()
        if to_status not in {"risk", "exited"}:
            continue
        code = _push_plain(
            transition.get("stock_code") or transition.get("code"),
            20,
        )
        name = _push_plain(
            transition.get("stock_name") or transition.get("name"),
            32,
        )
        identity = f"{code} {name}".strip() or "未提供股票"
        from_status = str(transition.get("from_status") or "").strip().lower()
        line = (
            f"- {identity}｜{_strategy_text(transition.get('strategy_type'))}｜"
            f"{_pool_status_text(from_status)} → {_pool_status_text(to_status)}"
        )
        reason = _push_plain(transition.get("reason"), 60)
        if reason:
            line += f"｜原因={reason}"
        prioritized.append((0 if to_status == "exited" else 1, index, line))
    prioritized.sort(key=lambda item: (item[0], item[1]))
    return [line for _priority, _index, line in prioritized]


def _push_stock_line(stock: Mapping[str, Any]) -> str:
    code = _push_plain(stock.get("stock_code"), 20)
    name = _push_plain(stock.get("stock_name"), 40)
    statuses = "、".join(str(value) for value in stock.get("statuses") or ())
    strategies = "、".join(str(value) for value in stock.get("strategies") or ())
    return (
        f"- {code} {name}｜评分={_score_text(stock.get('score'))}｜"
        f"状态={_push_plain(statuses, 40) or '未提供'}｜"
        f"策略={_push_plain(strategies, 80) or '未提供'}"
    ).rstrip()


def _push_focus_line(item: Mapping[str, Any]) -> str:
    strategies = sorted(
        (str(value) for value in item.get("strategies") or ()),
        key=lambda value: _STRATEGY_ORDER.get(value, len(_STRATEGY_ORDER)),
    )
    return (
        f"- {_push_plain(item.get('stock_code'), 20)} "
        f"{_push_plain(item.get('stock_name'), 40)}｜"
        f"综合观察分={_score_text(item.get('score'))}/100｜"
        f"{_push_plain(_focus_breakdown_text(item), 180)}｜"
        f"策略={_push_plain('、'.join(_strategy_text(value) for value in strategies), 80)}"
    ).rstrip()


def _push_header(
    summary: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    scan_date = _push_plain(summary.get("scan_date"), 32) or "未提供"
    signal_month = _push_plain(summary.get("signal_month"), 16) or "未提供"
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
    lines.extend(line[:240] for line in _source_lines(summary.get("source_status")))
    if run_status == "partial":
        lines.extend(
            [
                "> 部分来源可用：以下内容仅基于已取得证据；缺失证据不视为未命中。",
                "",
            ]
        )
    lines.extend(
        _render_counts(
            _counts_with_candidate_semantics(summary.get("counts"), candidates)
        )
    )
    if summary.get("error"):
        lines.extend(
            [
                "## 异常摘要 [事实]",
                "",
                _push_plain(summary.get("error"), 240) or "未提供",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def render_push_summary(
    summary: Mapping[str, Any],
    *,
    full_markdown: str,
    report_path: str,
    focus_candidates: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """生成钉钉预算内摘要；本地完整报告不受影响，候选只按完整块追加。"""
    # CLI 传入专池全量投影时，即使本次命中报告很短也要生成池监控摘要；
    # 未传投影的独立调用才保留“小报告原样推送”的兼容行为。
    if (
        focus_candidates is None
        and len(full_markdown.encode("utf-8")) <= PUSH_BODY_MAX_BYTES
    ):
        return full_markdown

    all_candidates = _candidate_records(summary.get("candidates"))
    header = _push_header(summary, all_candidates)
    focus_source = focus_candidates if focus_candidates is not None else all_candidates
    focus_funnel = focus.build_focus_funnel(focus_source)
    focus_stocks = list(focus_funnel["focus"])
    priority_stocks = list(focus_funnel["priority"])
    grouped_focus: dict[str, list[Mapping[str, Any]]] = {}
    for item in focus_stocks:
        grouped_focus.setdefault(str(item["industry"]), []).append(item)
    focus_groups = [
        (
            sector,
            sorted(stocks, key=lambda item: (-float(item["score"]), item["stock_code"])),
        )
        for sector, stocks in sorted(
            grouped_focus.items(),
            key=lambda pair: (
                -max(float(item["score"]) for item in pair[1]),
                _sector_sort_key(pair[0]),
            ),
        )
    ]
    all_stocks = _stock_projections(all_candidates)
    path = _push_report_path(report_path)
    transition_groups, transition_total = _push_transition_groups(
        summary.get("transitions")
    )
    critical_transitions = _push_critical_transition_lines(
        summary.get("transitions")
    )
    shown_transition_groups: list[tuple[str, int]] = []
    shown_critical_transitions: list[str] = []
    shown_sections: list[dict[str, Any]] = []
    focus_scope_label = (
        "池内重点" if focus_candidates is not None else "本次重点"
    )

    def _shown_stock_count() -> int:
        return sum(len(section["stocks"]) for section in shown_sections)

    def _shown_record_count() -> int:
        return sum(
            len(stock.get("records") or ())
            for section in shown_sections
            for stock in section["stocks"]
        )

    def _note() -> str:
        shown_transition_total = sum(
            count for _line, count in shown_transition_groups
        )
        recovery = ""
        if (
            focus_candidates is not None
            and _shown_stock_count() < len(focus_stocks)
        ):
            recovery = (
                "；专池展示发生截断；当前完整专池只读入口："
                "`python3 scripts/main.py monthly-pattern pool`。"
            )
        elif focus_funnel["omitted_verified"]:
            recovery = (
                f"；另有 {focus_funnel['omitted_verified']} 只基本面已核验股票"
                "未进入 Top10；完整池只读入口："
                "`python3 scripts/main.py monthly-pattern pool`。"
            )
        return (
            "\n> [来源状态·推送摘要] "
            f"状态变化覆盖 {shown_transition_total}/{transition_total} 条"
            f"（{len(shown_transition_groups)}/{len(transition_groups)} 组）；"
            f"关键风险/退出明细 {len(shown_critical_transitions)}/"
            f"{len(critical_transitions)} 条；"
            f"{focus_scope_label}股票展示 {_shown_stock_count()}/"
            f"{len(focus_stocks)} 只"
            f"（来自 {_shown_record_count()} 条策略记录）；"
            f"本次初筛 {len(all_stocks)} 只独立股票/"
            f"{len(all_candidates)} 条策略记录。"
            f"本次扫描完整报告：{path}"
            f"{recovery}\n"
        )

    def _candidate_body() -> str:
        if not shown_sections:
            return "本次无在池观察或基本面已核验股票。"
        rendered: list[str] = []
        for section in shown_sections:
            shown_stocks = section["stocks"]
            shown_records = sum(
                len(stock.get("records") or ()) for stock in shown_stocks
            )
            if (
                len(shown_stocks) == section["total_stocks"]
                and shown_records == section["total_records"]
            ):
                heading = (
                    f"### {section['sector']}（{len(shown_stocks)}只 / "
                    f"{shown_records}条策略记录）"
                )
            else:
                heading = (
                    f"### {section['sector']}（展示{len(shown_stocks)}/"
                    f"{section['total_stocks']}只 / {shown_records}/"
                    f"{section['total_records']}条策略记录）"
                )
            rendered.append(
                f"{heading}\n\n"
                + "\n".join(_push_focus_line(stock) for stock in shown_stocks)
            )
        return "\n\n".join(rendered)

    def _priority_body() -> str:
        if not priority_stocks:
            return "当前无满足专池重点完整性门槛的股票。"
        return "\n".join(
            f"{rank}. {_push_plain(item.get('stock_code'), 20)} "
            f"{_push_plain(item.get('stock_name'), 40)}｜"
            f"{_push_plain(item.get('industry'), 40)}｜"
            f"综合观察分={_score_text(item.get('score'))}/100"
            for rank, item in enumerate(priority_stocks, 1)
        )

    def _body() -> str:
        transition_body = (
            "\n".join(line for line, _count in shown_transition_groups)
            if shown_transition_groups
            else "本次无状态变化。"
        )
        critical_body = (
            "\n".join(shown_critical_transitions)
            if shown_critical_transitions
            else "本次无风险/退出状态变化。"
        )
        candidate_title = (
            "## 重点观察层 Top10（按申万二级板块聚合）"
            if focus_candidates is not None
            else "## 本次重点观察层（按申万二级板块聚合）"
        )
        return (
            f"{header}\n\n## 状态变化汇总 [判断]\n\n{transition_body}\n\n"
            f"## 关键风险/退出明细 [判断]\n\n{critical_body}\n\n"
            f"## 专池重点层 Top3 [判断]\n\n{_priority_body()}\n\n"
            f"{candidate_title}\n\n"
            "> 综合观察分只用于证据排序；基本面未核验者不入榜，"
            "不代表胜率或操作建议。\n\n"
            f"{_candidate_body()}\n{_note()}"
        )

    for group in transition_groups:
        shown_transition_groups.append(group)
        if len(_body().encode("utf-8")) > PUSH_BODY_MAX_BYTES:
            shown_transition_groups.pop()
            break

    for line in critical_transitions[:PUSH_CRITICAL_TRANSITION_MAX_ITEMS]:
        shown_critical_transitions.append(line)
        if len(_body().encode("utf-8")) > PUSH_BODY_MAX_BYTES:
            shown_critical_transitions.pop()
            break

    stop_candidates = False
    for sector, stocks in focus_groups:
        section = {
            "sector": _push_plain(sector, 60) or "未分类",
            "total_stocks": len(stocks),
            "total_records": sum(
                len(stock.get("records") or ()) for stock in stocks
            ),
            "stocks": [],
        }
        for stock in stocks:
            if not section["stocks"]:
                shown_sections.append(section)
            section["stocks"].append(stock)
            if len(_body().encode("utf-8")) > PUSH_BODY_MAX_BYTES:
                section["stocks"].pop()
                if not section["stocks"]:
                    shown_sections.pop()
                stop_candidates = True
                break
        if stop_candidates:
            break

    result = _body()
    if len(result.encode("utf-8")) <= PUSH_BODY_MAX_BYTES:
        return result

    run_status = {
        "complete": "完成",
        "partial": "部分完成",
        "failed": "失败",
    }[_effective_run_status(summary)]
    fallback = (
        f"# 月线模式观察池日报 · "
        f"{_push_plain(summary.get('scan_date'), 32) or '未提供'}\n\n"
        f"{_NOTICE}\n\n"
        f"- 信号月：{_push_plain(summary.get('signal_month'), 16) or '未提供'}\n"
        f"- 运行状态：[事实] {run_status}\n\n"
        "> [来源状态·推送摘要] 结构化摘要超出推送预算，"
        f"请查看本次扫描完整报告：{path}\n"
    )
    if len(fallback.encode("utf-8")) > PUSH_BODY_MAX_BYTES:
        raise ValueError("月线模式推送摘要无法在预算内保留运行状态和报告路径")
    return fallback


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
