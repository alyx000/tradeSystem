"""月线模式观察池 Markdown 渲染测试。"""
from __future__ import annotations

from services.monthly_pattern import renderer


def _candidate(**overrides) -> dict:
    candidate = {
        "stock_code": "600000",
        "stock_name": "浦发银行",
        "strategy_type": "theme_monthly_attack",
        "pool_status": "active",
        "industry": "股份制银行Ⅱ",
        "mainline_match": True,
        "technical_evidence": {
            "ma5": 10.2,
            "ma10": 9.8,
            "ma20": 9.4,
            "macd_dif": 0.36,
            "conditions": {
                "bullish_body_crosses_three_mas": {"met": True},
                "close_near_month_high": {"met": False, "hard_gate": False},
            },
        },
        "financial_evidence": {
            "status": "verified",
            "financial_ann_date": "2026-03-28",
            "report_period": "2025-12-31",
            "roe_waa": 18.6,
            "debt_to_assets": 43.2,
        },
    }
    candidate.update(overrides)
    return candidate


def _summary(**overrides) -> dict:
    summary = {
        "scan_date": "2026-07-01",
        "signal_month": "2026-06",
        "status": "complete",
        "source_status": {
            "monthly": "success",
            "financials": "success",
            "mainline": "success",
        },
        "counts": {
            "scanned": 5200,
            "matched": 1,
            "active": 1,
            "risk": 0,
        },
        "candidates": [_candidate()],
        "transitions": [],
        "error": None,
    }
    summary.update(overrides)
    return summary


def test_render_daily_labels_observation_facts_and_judgments():
    md = renderer.render_daily(_summary())

    assert "月线模式观察池" in md
    assert "扫描日：2026-07-01" in md
    assert "信号月：2026-06" in md
    assert "模式归类：[判断] 题材月线进攻" in md
    assert "主线匹配：[判断] 命中" in md
    assert "技术数值：[事实]" in md
    assert "MA5=10.20" in md
    assert "技术规则：[判断]" in md
    assert "财务状态：[事实] 已核验" in md
    assert "公告日=2026-03-28；报告期=2025-12-31" in md
    assert "ROE（加权）=18.60" in md


def test_render_daily_adds_industry_shadow_without_changing_lifecycle_score():
    assessment = {
        "status": "verified",
        "report_period": "2025-12-31",
        "context": {
            "contract_liability": 300_000_000.0,
            "contract_liability_growth_pct": 20.0,
            "contract_liability_growth_ge_20": True,
            "contract_liability_to_revenue_pct": 12.5,
            "contract_liability_qoq_pct": 200.0,
            "contract_liability_qoq_delta": 200_000_000.0,
            "contract_liability_qoq_prior_value": 100_000_000.0,
            "contract_liability_qoq_prior_period": "2025-09-30",
            "contract_liability_qoq_low_base": False,
            "rd_exp": 80_000_000.0,
            "rd_exp_growth_pct": 25.0,
            "rd_exp_increasing": True,
            "rd_exp_to_revenue_pct": 8.0,
        },
    }
    candidate = _candidate(
        stock_code="688001",
        stock_name="研发样本",
        industry="半导体",
        financial_evidence={
            "status": "verified",
            "financial_ann_date": "2026-04-30",
            "report_period": "2026-03-31",
            "latest": {
                **assessment,
                "status": "pre_screen",
                "report_period": "2026-03-31",
            },
            "annual": assessment,
        },
    )

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "行业增强层：[判断·影子]" in md
    assert "合同负债=辅助证据" in md
    assert "研发费用=核心证据" in md
    assert "仅展示，不参与基本面硬门、池状态或生命周期观察分" in md
    assert "合同负债：[事实·影子]" in md
    assert "研发费用：[事实·影子]" in md
    assert "同比+20.00%" in md
    assert "占同期营收12.50%" in md
    assert "环比+200.00%" in md
    assert "较2025-09-30增加2.00亿元" in md
    assert "contract_liability_growth_ge_20" not in md
    assert "contract_liability_qoq_pct" not in md
    assert "contract_liability_qoq_delta" not in md
    assert "rd_exp_increasing" not in md
    assert "生命周期观察分 4" in md


def test_render_daily_marks_contract_qoq_low_base_without_turning_it_into_score() -> None:
    candidate = _candidate(
        stock_code="688256",
        stock_name="低基数样本",
        industry="半导体",
        financial_evidence={
            "status": "verified",
            "latest": {
                "status": "pre_screen",
                "report_period": "2026-03-31",
                "context": {
                    "contract_liability": 396_000_000.0,
                    "contract_liability_qoq_pct": 64_700.0,
                    "contract_liability_qoq_delta": 395_390_000.0,
                    "contract_liability_qoq_prior_value": 610_000.0,
                    "contract_liability_qoq_prior_period": "2025-12-31",
                    "contract_liability_qoq_low_base": True,
                },
            },
        },
    )

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "环比+64700.00%" in md
    assert "较2025-12-31增加3.95亿元" in md
    assert "环比基数敏感" in md
    assert "contract_liability_qoq_low_base" not in md
    assert "生命周期观察分 4" in md


def test_render_daily_marks_financial_industry_factors_not_applicable_not_failed():
    assessment = {
        "status": "verified",
        "report_period": "2025-12-31",
        "context": {
            "contract_liability": None,
            "contract_liability_growth_pct": None,
            "contract_liability_to_revenue_pct": None,
            "rd_exp": None,
            "rd_exp_growth_pct": None,
            "rd_exp_to_revenue_pct": None,
        },
    }
    candidate = _candidate(
        financial_evidence={
            "status": "verified",
            "financial_ann_date": "2026-03-28",
            "report_period": "2025-12-31",
            "latest": assessment,
            "annual": assessment,
        }
    )

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "合同负债=不适用" in md
    assert "研发费用=不适用" in md
    assert "行业增强层" in md
    assert "行业增强层：[判断·影子] 未通过" not in md


def test_render_daily_does_not_apply_shadow_template_to_top_level_not_as_of_industry():
    candidate = _candidate(
        industry="半导体",
        source_meta={
            "industry": "半导体",
            "industry_map": "not_as_of",
        },
        financial_evidence={
            "status": "verified",
            "latest": {
                "status": "verified",
                "report_period": "2025-12-31",
                "context": {"rd_exp": 80_000_000.0},
            },
        },
    )

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "行业：[事实] 半导体" in md
    assert "合同负债=行业未知；研发费用=行业未知" in md
    assert "研发费用=核心证据" not in md


def test_render_daily_does_not_apply_shadow_template_to_nested_not_as_of_industry():
    candidate = _candidate(
        industry=None,
        source_meta={
            "industry": "半导体",
            "industry_map": "not_as_of",
        },
        financial_evidence={
            "status": "verified",
            "latest": {
                "status": "verified",
                "report_period": "2025-12-31",
                "context": {"rd_exp": 80_000_000.0},
            },
        },
    )

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "行业：[事实] 半导体" in md
    assert "合同负债=行业未知；研发费用=行业未知" in md
    assert "研发费用=核心证据" not in md


def test_lifecycle_priority_score_uses_status_only():
    active_with_sparse_evidence = _candidate(
        pool_status="active",
        mainline_match=False,
        technical_evidence={"conditions": {}},
    )
    active_with_rich_evidence = _candidate(
        pool_status="active",
        mainline_match=True,
        technical_evidence={
            "conditions": {
                "first": {"met": True},
                "second": {"met": True, "hard_gate": False},
            }
        },
    )

    assert renderer.lifecycle_priority_score(active_with_sparse_evidence) == 4
    assert renderer.lifecycle_priority_score(active_with_rich_evidence) == 4
    assert (
        renderer.lifecycle_priority_score(
            _candidate(pool_status="fundamental_verified")
        )
        == 3
    )
    assert (
        renderer.lifecycle_priority_score(
            _candidate(pool_status="technical_candidate")
        )
        == 2
    )
    assert renderer.lifecycle_priority_score(_candidate(pool_status="risk")) == 1
    assert renderer.lifecycle_priority_score(_candidate(pool_status="exited")) == 0
    assert renderer.lifecycle_priority_score(_candidate(pool_status="reentry")) is None
    assert renderer.lifecycle_priority_score(_candidate(pool_status="unknown")) is None


def test_render_daily_groups_by_industry_merges_strategies_and_sorts_by_score():
    candidates = [
        _candidate(
            stock_code="000001",
            stock_name="未分类股",
            industry="未分类",
            pool_status="active",
        ),
        _candidate(
            stock_code="600001",
            stock_name="双策略股",
            industry="电子",
            strategy_type="fundamental_monthly_trend",
            pool_status="fundamental_verified",
        ),
        _candidate(
            stock_code="600002",
            stock_name="高优先股",
            industry="电子",
            strategy_type="monthly_reacceleration",
            pool_status="active",
        ),
        _candidate(
            stock_code="600001.SH",
            stock_name="双策略股",
            industry="电子",
            strategy_type="theme_monthly_attack",
            pool_status="fundamental_verified",
        ),
        _candidate(
            stock_code="300001",
            stock_name="计算机股",
            industry="计算机设备",
            pool_status="technical_candidate",
        ),
    ]

    md = renderer.render_daily(_summary(candidates=list(reversed(candidates))))

    assert "## 候选观察（按申万二级板块聚合）" in md
    assert "生命周期观察分 v1" in md
    assert "不代表胜率、技术强弱或买卖建议" in md
    assert "后台技术初筛：4 只" in md
    assert "基本面核验层：3 只" in md
    assert "### 电子（2只 / 3条策略记录）" in md
    assert md.index("#### 1. 600001 双策略股｜生命周期观察分 3") < md.index(
        "#### 2. 600002 高优先股｜生命周期观察分 4"
    )
    assert md.count("600001 双策略股｜生命周期观察分") == 1
    assert "##### 基本面月线趋势｜基本面已核验｜策略分 3" in md
    assert "##### 题材月线进攻｜基本面已核验｜策略分 3" in md
    assert "### 计算机设备" not in md
    assert "### 未分类" in md


def test_render_daily_focus_uses_full_open_pool_snapshot_when_available():
    matched = _candidate(stock_code="600001", stock_name="本次命中")
    open_pool = _candidate(stock_code="600002", stock_name="完整开放池")

    md = renderer.render_daily(
        _summary(candidates=[matched], focus_candidates=[open_pool])
    )

    assert "600002 完整开放池" in md
    assert "600001 本次命中" not in md


def test_render_daily_does_not_report_empty_when_open_pool_snapshot_exists():
    open_pool = _candidate(stock_code="600002", stock_name="完整开放池")

    md = renderer.render_daily(_summary(candidates=[], focus_candidates=[open_pool]))

    assert "600002 完整开放池" in md
    assert "真实空候选" not in md


def test_render_daily_prefers_classified_success_industry_when_strategies_conflict():
    low_quality = _candidate(
        stock_code="688188",
        stock_name="688188",
        industry=None,
        strategy_type="monthly_reacceleration",
        pool_status="active",
    )
    low_quality["source_meta"] = {
        "industry": "未分类",
        "industry_map": "not_as_of",
    }
    classified = _candidate(
        stock_code="688188.SH",
        stock_name="柏楚电子",
        industry=None,
        strategy_type="fundamental_monthly_trend",
        pool_status="fundamental_verified",
    )
    classified["source_meta"] = {
        "industry": "计算机设备",
        "industry_map": "success",
    }

    md = renderer.render_daily(
        _summary(candidates=[low_quality, classified])
    )

    assert "### 计算机设备（1只 / 2条策略记录）" in md
    assert "688188 柏楚电子｜生命周期观察分 4" in md
    assert "### 未分类" not in md


def test_render_daily_tie_break_is_stable_and_independent_of_input_order():
    candidates = [
        _candidate(
            stock_code="600003",
            stock_name="较早单策略",
            industry="电子",
            strategy_type="monthly_reacceleration",
            pool_status="active",
            signal_month="2026-05",
        ),
        _candidate(
            stock_code="600002",
            stock_name="较新单策略",
            industry="电子",
            strategy_type="monthly_reacceleration",
            pool_status="active",
            signal_month="2026-06",
        ),
        _candidate(
            stock_code="600001",
            stock_name="双策略",
            industry="电子",
            strategy_type="theme_monthly_attack",
            pool_status="active",
            signal_month="2026-05",
        ),
        _candidate(
            stock_code="600001.SH",
            stock_name="双策略",
            industry="电子",
            strategy_type="monthly_reacceleration",
            pool_status="active",
            signal_month="2026-05",
        ),
    ]

    forward = renderer.render_daily(_summary(candidates=candidates))
    reversed_order = renderer.render_daily(
        _summary(candidates=list(reversed(candidates)))
    )

    assert forward == reversed_order
    assert forward.index("#### 1. 600001 双策略") < forward.index(
        "#### 2. 600002 较新单策略"
    )
    assert forward.index("#### 2. 600002 较新单策略") < forward.index(
        "#### 3. 600003 较早单策略"
    )


def test_render_daily_places_equal_quality_industry_conflict_in_explicit_bucket():
    first = _candidate(
        stock_code="600001",
        industry="电子",
        strategy_type="theme_monthly_attack",
    )
    second = _candidate(
        stock_code="600001.SH",
        industry="计算机设备",
        strategy_type="monthly_reacceleration",
    )

    md = renderer.render_daily(_summary(candidates=[first, second]))

    assert "### 行业冲突（1只 / 2条策略记录）" in md
    assert "同质量证据冲突" in md
    assert "候选=电子、计算机设备" in md


def test_render_daily_adds_unique_stock_and_risk_record_counts():
    candidates = [
        _candidate(
            stock_code="600001",
            strategy_type="fundamental_monthly_trend",
            pool_status="active",
        ),
        _candidate(
            stock_code="600001.SH",
            strategy_type="monthly_reacceleration",
            pool_status="risk",
        ),
        _candidate(stock_code="600002", pool_status="risk"),
    ]

    md = renderer.render_daily(
        _summary(
            candidates=candidates,
            counts={"matched_candidates": 3},
        )
    )

    assert "本次初筛命中记录数：[事实] 3" in md
    assert "本次初筛去重股票数：[事实] 2" in md
    assert "本次命中·风险观察记录数：[事实] 2" in md


def test_render_push_summary_groups_and_dedupes_full_pool_by_score():
    summary = _summary(candidates=[])
    full = renderer.render_daily(summary)
    focus = [
        _candidate(
            stock_code="600002",
            stock_name="基本面股",
            pool_status="fundamental_verified",
            industry=None,
            strategy_type="fundamental_monthly_trend",
        ),
        _candidate(
            stock_code="600001",
            stock_name="双策略股",
            pool_status="active",
            industry=None,
            strategy_type="monthly_reacceleration",
        ),
        _candidate(
            stock_code="600001.SH",
            stock_name="双策略股",
            pool_status="active",
            industry=None,
            strategy_type="theme_monthly_attack",
        ),
    ]
    for item in focus:
        item["source_meta"] = {"industry": "电子", "industry_map": "success"}

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path="data/reports/monthly-pattern/2026-07-01.md",
        focus_candidates=list(reversed(focus)),
    )

    assert "### 电子（2只 / 3条策略记录）" in pushed
    assert pushed.index("600001 双策略股｜综合观察分=") < pushed.index(
        "600002 基本面股｜综合观察分="
    )
    assert pushed.count("600001 双策略股｜综合观察分=") == 1
    assert "策略=题材月线进攻、月线二次启动" in pushed
    assert "池内重点股票展示 2/2 只（来自 3 条策略记录）" in pushed
    assert "本次初筛 0 只独立股票/0 条策略记录" in pushed


def test_render_push_summary_returns_full_report_within_budget():
    full = renderer.render_daily(_summary())

    pushed = renderer.render_push_summary(
        _summary(),
        full_markdown=full,
        report_path="data/reports/monthly-pattern/2026-07-01.md",
    )

    assert pushed == full


def test_render_push_summary_prioritizes_focus_statuses_and_keeps_full_path():
    candidates = [
        _candidate(
            stock_code="000001",
            stock_name="技术候选" + "长" * 300,
            pool_status="technical_candidate",
        ),
        _candidate(
            stock_code="600001",
            stock_name="在池观察" + "长" * 300,
            pool_status="active",
        ),
        _candidate(
            stock_code="600002",
            stock_name="基本面核验" + "长" * 300,
            pool_status="fundamental_verified",
        ),
    ]
    for index in range(40):
        candidates.append(
            _candidate(
                stock_code=f"{300000 + index:06d}",
                stock_name=f"批量候选{index:02d}" + "长" * 300,
                pool_status="technical_candidate",
            )
        )
    transitions = [
        {
            "stock_code": "600001",
            "strategy_type": "theme_monthly_attack",
            "from_status": "technical_candidate",
            "to_status": "active",
        },
        {
            "stock_code": "600002",
            "strategy_type": "fundamental_monthly_trend",
            "from_status": "technical_candidate",
            "to_status": "fundamental_verified",
        },
    ]
    summary = _summary(candidates=candidates, transitions=transitions)
    full = renderer.render_daily(summary)
    path = "data/reports/monthly-pattern/2026-07-01.md"

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path=path,
    )

    assert len(full.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert pushed == full
    assert "技术候选 → 在池观察" in pushed
    assert "技术候选 → 基本面已核验" in pushed
    assert "### 600001  · 题材月线进攻" in pushed
    assert "### 600002  · 基本面月线趋势" in pushed
    assert "600001" in pushed
    assert "600002" in pushed
    assert "000001" not in pushed
    assert "### 股份制银行Ⅱ（2只 / 2条策略记录）" in pushed
    assert pushed.count("综合观察分") >= 4
    assert f"后台技术初筛：{len(candidates)} 只" in pushed
    assert "基本面核验层：2 只" in pushed


def test_render_push_summary_uses_full_pool_projection_even_when_report_is_short():
    summary = _summary(candidates=[])
    full = renderer.render_daily(summary)
    focus = [
        _candidate(
            stock_code="600001",
            stock_name="专池存量",
            pool_status="active",
            industry=None,
        )
    ]
    focus[0]["source_meta"] = {"industry": "电力"}

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path="data/reports/monthly-pattern/2026-07-01.md",
        focus_candidates=focus,
    )

    assert len(full.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert pushed != full
    assert "600001 专池存量" in pushed
    assert "### 电力（1只 / 1条策略记录）" in pushed
    assert "池内重点股票展示 1/1 只（来自 1 条策略记录）" in pushed
    assert "本次初筛 0 只独立股票/0 条策略记录" in pushed
    assert "真实空候选" not in pushed


def test_render_push_summary_caps_utf8_without_partial_compact_lines():
    candidates = [
        _candidate(
            stock_code=f"{100000 + index:06d}",
            stock_name=f"重点股票{index:03d}" + "长" * 100,
            pool_status="active",
            industry="申万二级行业" + "业" * 100,
        )
        for index in range(500)
    ]
    summary = _summary(candidates=candidates)
    full = renderer.render_daily(summary)

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path="data/reports/monthly-pattern/2026-07-01.md",
        focus_candidates=candidates,
    )

    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    shown = pushed.count("｜综合观察分=")
    assert 0 < shown < len(candidates)
    assert shown == 13  # Top3 三行 + Top10 十行
    assert "池内重点股票展示 10/10 只" in pushed
    assert "另有 490 只基本面已核验股票未进入 Top10" in pushed
    assert "python3 scripts/main.py monthly-pattern pool" in pushed
    for line in pushed.splitlines():
        if "｜综合观察分=" in line and line.startswith("- "):
            assert line.startswith("- ")
            assert "｜策略=" in line
    for index, line in enumerate(pushed.splitlines()):
        if line.startswith("### 申万二级行业"):
            assert index + 2 < len(pushed.splitlines())
            assert pushed.splitlines()[index + 2].startswith("- ")


def test_render_push_summary_aggregates_many_transitions_without_losing_exit():
    transitions = [
        {
            "stock_code": f"{100000 + index:06d}",
            "strategy_type": "fundamental_monthly_trend",
            "from_status": "technical_candidate",
            "to_status": "fundamental_verified",
        }
        for index in range(1000)
    ]
    transitions.append(
        {
            "stock_code": "600999",
            "stock_name": "关键退出股",
            "strategy_type": "theme_monthly_attack",
            "from_status": "risk",
            "to_status": "exited",
            "reason": "完成月趋势资格失效",
        }
    )
    summary = _summary(
        candidates=[_candidate(stock_code="600001", pool_status="active")],
        transitions=transitions,
    )
    full = renderer.render_daily(summary)

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path="/Users/alyx/tradeSystem/data/reports/monthly-pattern/2026-07-01.md",
    )

    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert "题材月线进攻｜风险观察 → 已移出观察：1 只" in pushed
    assert "基本面月线趋势｜技术候选 → 基本面已核验：1000 只" in pushed
    assert "状态变化覆盖 1001/1001 条" in pushed
    assert "600999 关键退出股｜题材月线进攻｜风险观察 → 已移出观察" in pushed
    assert "原因=完成月趋势资格失效" in pushed
    assert "关键风险/退出明细 1/1 条" in pushed
    assert "600001" in pushed


def test_render_push_summary_preserves_failed_status_when_error_is_huge():
    summary = _summary(
        status="failed",
        candidates=[],
        transitions=[],
        error="来源故障" * 6000,
    )
    full = renderer.render_daily(summary)
    path = "/Users/alyx/tradeSystem/data/reports/monthly-pattern/2026-07-01.md"

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path=path,
    )

    assert len(full.encode("utf-8")) > renderer.PUSH_BODY_MAX_BYTES
    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert "运行状态：[事实] 失败" in pushed
    assert "信号月：2026-06" in pushed
    assert path in pushed


def test_render_push_summary_can_use_full_open_pool_focus_projection():
    summary = _summary(
        candidates=[
            _candidate(
                stock_code=f"{300000 + index:06d}",
                stock_name="本月技术候选" + "长" * 300,
                pool_status="technical_candidate",
            )
            for index in range(40)
        ]
    )
    focus = [
        _candidate(stock_code="600001", pool_status="active"),
        _candidate(stock_code="600002", pool_status="fundamental_verified"),
        _candidate(stock_code="600003", pool_status="risk"),
    ]
    full = renderer.render_daily(summary)

    pushed = renderer.render_push_summary(
        summary,
        full_markdown=full,
        report_path="/Users/alyx/tradeSystem/data/reports/monthly-pattern/2026-07-01.md",
        focus_candidates=focus,
    )

    assert "600001" in pushed
    assert "600002" in pushed
    assert "600003" not in pushed
    assert "池内重点股票展示 2/2 只（来自 2 条策略记录）" in pushed
    assert "本次初筛 40 只独立股票/40 条策略记录" in pushed


def test_render_daily_failure_is_not_assumed_to_be_only_a_source_failure():
    summary = _summary(
        status="failed",
        source_status={"monthly": "source_failed", "financials": "not_run"},
        candidates=[_candidate(stock_code="000001")],
        error="monthly_source_timeout",
    )

    md = renderer.render_daily(summary)

    assert "运行失败（来源或处理链路）" in md
    assert "不等于真实空候选" in md
    assert "monthly_source_timeout" in md
    assert "000001" not in md
    assert "真实空候选（非采集故障）" not in md


def test_render_daily_partial_empty_does_not_claim_true_empty():
    md = renderer.render_daily(
        _summary(
            status="partial",
            source_status={"monthly": "success", "financials": "partial"},
            candidates=[],
            error="部分财务快照缺失",
        )
    )

    assert "部分来源可用" in md
    assert "不能判为真实空候选" in md
    assert "部分财务快照缺失" in md
    assert "真实空候选（非采集故障）" not in md


def test_render_daily_complete_empty_is_explicit_true_vacuum():
    md = renderer.render_daily(_summary(candidates=[], counts={"matched": 0}))

    assert "真实空候选（非采集故障）" in md
    assert "数据源失败" not in md
    assert "部分来源可用" not in md


def test_explicit_complete_is_not_downgraded_by_source_counts_or_optional_states():
    md = renderer.render_daily(
        _summary(
            status="complete",
            source_status={
                "monthly_bars_fetched": 5200,
                "monthly_bars_cached": 156000,
                "mainline": "limited_history",
                "financials": "source_ok_empty",
            },
            candidates=[],
            counts={"matched": 0},
        )
    )

    assert "运行状态：[事实] 完成" in md
    assert "真实空候选（非采集故障）" in md
    assert "部分来源可用" not in md
    assert "月线行情拉取数：5200" in md
    assert "月线行情缓存数：156000" in md
    assert "主线证据：历史样本有限" in md
    assert "财务快照：来源成功但无记录" in md


def test_source_lines_explain_disabled_and_unknown_without_forging_failure():
    md = renderer.render_daily(
        _summary(
            status="complete",
            source_status={
                "monthly": "success",
                "financials": "disabled",
                "optional_extension": "new_state",
            },
        )
    )

    assert "运行状态：[事实] 完成" in md
    assert "财务快照：已禁用" in md
    assert "optional_extension：未识别状态（new_state）" in md
    assert "数据源失败" not in md
    assert "部分来源可用" not in md


def test_source_lines_explain_historical_as_of_coverage_failure():
    md = renderer.render_daily(
        _summary(
            status="partial",
            source_status={"financials": "as_of_coverage_failed"},
            candidates=[],
        )
    )

    assert "财务快照：历史时点可见覆盖不足" in md
    assert "未识别状态" not in md


def test_source_lines_support_nested_monthly_bar_counts():
    md = renderer.render_daily(
        _summary(
            source_status={
                "monthly_bars": {
                    "status": "success",
                    "fetched": 5200,
                    "cached": 156000,
                },
                "financials": "success",
            }
        )
    )

    assert "月线行情：成功" in md
    assert "月线行情·拉取数：5200" in md
    assert "月线行情·缓存数：156000" in md


def test_service_count_keys_render_with_chinese_labels():
    md = renderer.render_daily(
        _summary(
            counts={
                "market_stocks": 5200,
                "matched_candidates": 18,
                "matched_active": 5,
                "pool_active": 9,
                "pool_risk": 2,
            }
        )
    )

    assert "扫描股票数：[事实] 5200" in md
    assert "本次初筛命中记录数：[事实] 18" in md
    assert "本次命中·在池观察记录数：[事实] 5" in md
    assert "池内·在池观察记录数：[事实] 9" in md
    assert "池内·风险观察记录数：[事实] 2" in md
    assert "market_stocks" not in md
    assert "matched_candidates" not in md


def test_render_daily_renders_source_counts_and_transitions_with_layers():
    md = renderer.render_daily(
        _summary(
            transitions=[
                {
                    "stock_code": "600000",
                    "stock_name": "浦发银行",
                    "strategy_type": "fundamental_monthly_trend",
                    "from_status": "active",
                    "to_status": "risk",
                    "reason": "完成月收盘跌破 MA5",
                }
            ]
        )
    )

    assert "月线行情：成功" in md
    assert "财务快照：成功" in md
    assert "扫描股票数：[事实] 5200" in md
    assert "命中记录数：[事实] 1" in md
    assert "状态变化：[判断] 在池观察 → 风险观察" in md
    assert "完成月收盘跌破 MA5" in md


def test_financial_timepoint_is_visible_even_when_missing():
    candidate = _candidate(financial_evidence={"status": "missing"})

    md = renderer.render_pool([candidate])

    assert "公告日=缺失；报告期=缺失" in md
    assert "财务状态：[事实] 缺失" in md


def test_financial_hard_gate_status_is_distinct_from_source_failure():
    verified = _candidate(
        financial_evidence={
            "status": "verified",
            "financial_ann_date": "2026-03-28",
            "report_period": "2025-12-31",
        }
    )
    rejected = _candidate(
        stock_code="000001",
        financial_evidence={
            "status": "failed",
            "financial_ann_date": "2026-03-29",
            "report_period": "2025-12-31",
        },
    )

    md = renderer.render_pool([verified, rejected])

    assert "财务状态：[事实] 已核验" in md
    assert "财务状态：[事实] 未通过硬门" in md
    assert "财务状态：[事实] 来源失败" not in md


def test_render_pool_has_observation_title_and_candidate_evidence():
    row = {
        **_candidate(),
        "status": "active",
        "signal_month": "2026-06",
        "last_seen_date": "2026-07-01",
    }

    md = renderer.render_pool([row])

    assert md.startswith("# 月线模式观察池")
    assert "600000 浦发银行" in md
    assert "池状态：[判断] 在池观察" in md
    assert "技术数值：[事实]" in md
    assert "公告日=2026-03-28；报告期=2025-12-31" in md


def test_render_pool_empty_state_is_clear():
    md = renderer.render_pool([])

    assert "月线模式观察池" in md
    assert "观察池为空" in md


def test_renderer_does_not_leak_action_position_or_price_target_fields():
    candidate = _candidate(
        technical_evidence={
            "ma5": 10.2,
            "target_price": 88,
            "position": "五成",
            "note": "建议买入",
        }
    )
    summary = _summary(
        candidates=[candidate],
        transitions=[
            {
                "stock_code": "600000",
                "from_status": "risk",
                "to_status": "active",
                "reason": "建议卖出后再买入",
            }
        ],
    )

    md = renderer.render_daily(summary)

    assert "买入" not in md
    assert "卖出" not in md
    assert "仓位" not in md
    assert "目标价" not in md
    assert "88" not in md
    assert "五成" not in md
    assert "已省略非观察性描述" in md


def test_renderer_escapes_free_text_in_markdown():
    candidate = _candidate(stock_name="浦发|银行\n测试")

    md = renderer.render_daily(_summary(candidates=[candidate]))

    assert "浦发｜银行 测试" in md
    assert "浦发|银行" not in md
