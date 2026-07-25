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

    assert len(full.encode("utf-8")) > renderer.PUSH_BODY_MAX_BYTES
    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    assert f"完整报告：`{path}`" in pushed
    assert "技术候选 → 在池观察" in pushed
    assert "技术候选 → 基本面已核验" in pushed
    assert "题材月线进攻｜技术候选 → 在池观察" in pushed
    assert "基本面月线趋势｜技术候选 → 基本面已核验" in pushed
    assert "600001" in pushed
    assert "600002" in pushed
    assert "000001" not in pushed
    assert pushed.count("｜行业=") == 2
    assert "重点候选展示 2/2 只" in pushed
    assert f"本次命中候选 {len(candidates)} 只" in pushed


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
    assert "行业=电力" in pushed
    assert "池内重点候选展示 1/1 只" in pushed
    assert "本次命中候选 0 只" in pushed
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
    )

    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    shown = pushed.count("｜行业=")
    assert 0 < shown < len(candidates)
    assert f"重点候选展示 {shown}/{len(candidates)} 只" in pushed
    for line in pushed.splitlines():
        if "｜行业=" in line:
            assert line.startswith("- ")
            assert line.count("｜") == 3


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
    assert "池内重点候选展示 2/2 只" in pushed
    assert "本次命中候选 40 只" in pushed


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
    assert "本次模式命中数：[事实] 18" in md
    assert "本次命中·在池观察数：[事实] 5" in md
    assert "池内·在池观察数：[事实] 9" in md
    assert "池内·风险观察数：[事实] 2" in md
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
    assert "命中数：[事实] 1" in md
    assert "状态变化：[判断] 在池观察 → 风险观察" in md
    assert "完成月收盘跌破 MA5" in md


def test_financial_timepoint_is_visible_even_when_missing():
    candidate = _candidate(financial_evidence={"status": "missing"})

    md = renderer.render_daily(_summary(candidates=[candidate]))

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

    md = renderer.render_daily(_summary(candidates=[verified, rejected]))

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
