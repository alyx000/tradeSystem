"""TDD test for tail-scan renderer (only-read observation list MD)."""
import pytest

from services.tail_scan import constants as C
from services.tail_scan import renderer


def _scan():
    return {"status": "ok", "quote_date": "2026-07-13", "quote_time": "14:30:00",
            "matched": 1, "scanned": 5000,
            "candidates": [{"code": "600001.SH"}]}


def _scored():
    return [{"code": "600001.SH", "name": "测试股", "pct_chg": 8.5, "amount_yi": 25.0,
             "in_main_sector": True,
             "stock_concept_names": ["算力租赁", "AI算力"],
             "stock_concept_total": 2, "stock_concept_status": "ok",
             "stock_concept_snapshot_at": "2026-07-14T14:40:00",
             "concept_names": ["AI算力"], "concept_status": "ok", "total": 4.0,
             "rank_score": 1, "gain5": 20.0, "up_days": 3, "is_limit_up": False}]


def test_render_has_disclaimer_and_judgement_tag():
    md = renderer.render_daily(_scan(), _scored(), None)
    assert "T-1" in md and "快照" in md          # 数据时效声明
    assert "[判断]" in md
    assert "600001" in md


def test_render_concept_layers_show_memberships_and_t1_hot_hits_separately():
    card = {
        **_scored()[0],
        "stock_concept_names": [
            "天然气", "航运概念", "煤化工概念", "页岩气", "智能物流", "新疆振兴",
        ],
        "stock_concept_total": 6,
        "stock_concept_status": "ok",
        "stock_concept_snapshot_at": "2026-07-14T00:00:00",
        "concept_names": ["航运概念"],
        "concept_status": "ok",
    }

    md = renderer.render_daily(_scan(), [card], None)

    assert "[事实·归属概念] 天然气 / 航运概念 / 煤化工概念 / 页岩气 / 智能物流（当前快照，共6个）" in md
    assert "新疆振兴" not in md
    assert "[事实·T-1热概念命中] 航运概念" in md
    candidate_head = next(line for line in md.splitlines() if line.startswith("- **"))
    assert "概念:" not in candidate_head


def test_render_concept_limits_t1_hot_hits_to_two():
    card = {
        **_scored()[0],
        "stock_concept_names": ["天然气"],
        "stock_concept_total": 1,
        "stock_concept_status": "ok",
        "concept_names": ["航运概念", "页岩气", "煤化工概念"],
        "concept_status": "ok",
    }

    md = "".join(renderer._render_concept_context(card))

    assert "[事实·T-1热概念命中] 航运概念 / 页岩气" in md
    assert "煤化工概念" not in md


def test_render_concept_distinguishes_no_hot_hit_from_each_failure_status():
    base = {
        "stock_concept_names": ["天然气"],
        "stock_concept_total": 1,
        "stock_concept_status": "ok",
        "concept_names": [],
    }

    no_hit = "".join(renderer._render_concept_context({**base, "concept_status": "ok"}))
    source_failed = "".join(
        renderer._render_concept_context({**base, "concept_status": "source_failed"})
    )
    member_failed = "".join(
        renderer._render_concept_context({**base, "concept_status": "member_failed"})
    )
    coverage_failed = "".join(
        renderer._render_concept_context({**base, "concept_status": "coverage_failed"})
    )

    assert "未命中上一交易日资金流前8窄概念" in no_hit
    assert "无法判断" not in no_hit
    assert "T-1概念资金流源失败" in source_failed
    assert "个股概念归属源失败" in member_failed
    assert "T-1热概念覆盖数据不完整" in coverage_failed
    assert len({source_failed, member_failed, coverage_failed}) == 3


def test_render_concept_distinguishes_membership_missing_from_source_failed():
    missing = "".join(renderer._render_concept_context({
        "stock_concept_names": [],
        "stock_concept_total": 0,
        "stock_concept_status": "missing",
        "concept_names": [],
        "concept_status": "ok",
    }))
    failed = "".join(renderer._render_concept_context({
        "stock_concept_names": [],
        "stock_concept_total": 0,
        "stock_concept_status": "source_failed",
        "concept_names": [],
        "concept_status": "member_failed",
    }))

    assert "当前快照暂无可用窄概念" in missing
    assert "概念归属源失败" not in missing
    assert "概念归属源失败" in failed


@pytest.mark.parametrize("names", ["天然气", (), [], [""], ["天然气", {"name": "航运概念"}]])
def test_render_concept_ok_with_invalid_membership_names_fails_closed(names):
    md = "".join(renderer._render_concept_context({
        "stock_concept_names": names,
        "stock_concept_total": 2,
        "stock_concept_status": "ok",
        "concept_names": [],
        "concept_status": "ok",
    }))

    assert "[事实·归属概念]" not in md
    assert "归属概念数据无效" in md


@pytest.mark.parametrize("names", ["航运概念", (), [""], [{"name": "航运概念"}]])
def test_render_concept_ok_with_invalid_hot_names_fails_closed(names):
    md = "".join(renderer._render_concept_context({
        "stock_concept_names": ["天然气"],
        "stock_concept_total": 1,
        "stock_concept_status": "ok",
        "concept_names": names,
        "concept_status": "ok",
    }))

    assert "[事实·T-1热概念命中]" not in md
    assert "T-1热概念命中数据无效" in md
    assert "无法判断" in md


@pytest.mark.parametrize("total", [None, "6", -1, 1, 2.5, True, 99])
def test_render_concept_normalizes_invalid_or_undersized_membership_total(total):
    md = "".join(renderer._render_concept_context({
        "stock_concept_names": ["天然气", "航运概念", "页岩气"],
        "stock_concept_total": total,
        "stock_concept_status": "ok",
        "concept_names": [],
        "concept_status": "ok",
    }))

    assert "当前快照，共3个" in md


def test_render_concept_names_are_plain_markdown_text():
    malicious = r"天然气[伪链接](https://example.com)*强调*_斜_<tag>\尾`代码`#标题"
    md = "".join(renderer._render_concept_context({
        "stock_concept_names": [malicious],
        "stock_concept_total": 1,
        "stock_concept_status": "ok",
        "concept_names": [malicious],
        "concept_status": "ok",
    }))
    escaped = (
        r"天然气\[伪链接\]\(https://example.com\)\*强调\*\_斜\_"
        r"\<tag\>\\尾\`代码\`\#标题"
    )

    assert md.count(escaped) == 2
    assert "[伪链接](https://example.com)" not in md


def test_render_concept_lines_precede_industry_logic_and_disclaimer_is_explicit():
    card = {
        **_scored()[0],
        "is_limit_up": True,
        "business_status": "missing",
        "industry_position": "",
        "catalyst_status": "none",
        "catalyst_evidence": [],
    }

    md = renderer.render_daily(_scan(), [card], None)

    candidate_head = next(line for line in md.splitlines() if line.startswith("- **"))
    assert "｜主线 已涨停" in candidate_head
    assert "概念:" not in candidate_head
    assert md.index("[事实·归属概念]") < md.index("[事实·T-1热概念命中]")
    assert md.index("[事实·T-1热概念命中]") < md.index("[事实·主营]")
    assert "归属概念为扫描时当前快照" in md
    assert "T-1热概念命中为上一交易日资金流口径" in md


@pytest.mark.parametrize("hot_status", ["source_failed", "member_failed", "coverage_failed"])
def test_degradation_note_counts_concept_failures_by_layer(hot_status):
    failed = {
        **_scored()[0],
        "stock_concept_status": "source_failed",
        "concept_status": hot_status,
    }
    note = renderer._degradation_note([failed])
    assert "归属概念" in note
    assert "T-1热概念" in note


def test_degradation_note_describes_known_missing_dimensions():
    card = {
        **_scored()[0],
        "main_sector_status": "missing",
        "index_status": "missing",
    }

    note = renderer._degradation_note([card])

    assert "主线" in note and "大势" in note
    assert "取数失败、缺失或数据无效" in note

    known_empty = {
        **_scored()[0],
        "stock_concept_names": [],
        "stock_concept_total": 0,
        "stock_concept_status": "missing",
        "concept_names": [],
        "concept_status": "ok",
    }
    note = renderer._degradation_note([known_empty])
    assert "归属概念" not in note
    assert "T-1热概念" not in note


def test_degradation_note_counts_missing_both_concept_status_keys():
    card = {
        key: value
        for key, value in _scored()[0].items()
        if key not in {"stock_concept_status", "concept_status"}
    }

    note = renderer._degradation_note([card])

    assert "归属概念" in note
    assert "T-1热概念" in note


def test_degradation_note_counts_only_missing_stock_concept_status_key():
    card = {
        key: value
        for key, value in _scored()[0].items()
        if key != "stock_concept_status"
    }

    note = renderer._degradation_note([card])

    assert "归属概念" in note
    assert "T-1热概念" not in note


def test_degradation_note_counts_only_missing_hot_concept_status_key():
    card = {
        key: value
        for key, value in _scored()[0].items()
        if key != "concept_status"
    }

    note = renderer._degradation_note([card])

    assert "归属概念" not in note
    assert "T-1热概念" in note


def test_render_no_price_advice_words():
    md = renderer.render_daily(_scan(), _scored(), None)
    for banned in ("买入价", "目标价", "仓位", "止损"):
        assert banned not in md


def test_render_source_failed():
    md = renderer.render_source_failed({"status": "source_failed",
                                        "error": "实时行情获取失败"})
    assert "失败" in md


def test_render_rounds_floats_and_handles_none_gain5():
    """真机推送暴露：长尾小数(30.3661162…)与 None gain5(近5日None%) → 修为定点/—。"""
    scored = [{"code": "600360.SH", "name": "华微电子", "pct_chg": 8.1596,
               "amount_yi": 26.86, "gain5": 30.366116295764535, "up_days": 2,
               "in_main_sector": True, "total": 3.0, "rank_score": 1},
              {"code": "301583.SZ", "name": "C托伦斯", "pct_chg": 8.168,
               "amount_yi": 22.94, "gain5": None, "up_days": 1, "total": 1.0,
               "rank_score": 2}]
    scan = {"status": "ok", "quote_date": "2026-07-13", "quote_time": "14:30:00",
            "matched": 2, "scanned": 5000, "candidates": []}
    md = renderer.render_daily(scan, scored, None)
    assert "涨8.16%" in md                    # pct_chg 定点 2 位
    assert "近5日30.4%" in md                  # gain5 定点 1 位
    assert "30.366116" not in md              # 无长尾小数
    assert "近5日—" in md                      # None gain5 → —（不再是 None%）
    assert "None%" not in md


def test_render_pk_detail_reasons():
    """PK 理由渲染进报告：status=ok 时列出每场 A vs B → 胜者 + 理由。"""
    scored = [{"code": "688072.SH", "name": "拓荆科技", "pct_chg": 7.15, "amount_yi": 117.7,
               "gain5": 19.2, "up_days": 5, "total": 4.0, "rank_score": 1},
              {"code": "688802.SH", "name": "沐曦股份", "pct_chg": 10.86, "amount_yi": 21.9,
               "gain5": 37.9, "up_days": 0, "total": 6.5, "rank_score": 2}]
    scan = {"status": "ok", "quote_date": "2026-07-13", "quote_time": "10:35",
            "matched": 2, "scanned": 5000, "candidates": []}
    pk_result = {"status": "ok", "ranks": {"688072.SH": 1, "688802.SH": 2},
                 "matches": [{"a": "688072.SH", "b": "688802.SH", "winner": "688072.SH",
                              "reason": "A百亿放量突破新高，B缩量冲高回落", "state": "valid"}]}
    md = renderer.render_daily(scan, scored, pk_result)
    assert "PK 对局明细" in md
    assert "拓荆科技 vs 沐曦股份 → 胜：**拓荆科技**" in md
    assert "百亿放量突破新高" in md          # 理由渲染进去
    # 熔断态不渲染明细
    md2 = renderer.render_daily(scan, scored, {"status": "melted", "matches": pk_result["matches"]})
    assert "PK 对局明细" not in md2


def test_render_orders_by_pk_rank_not_coarse():
    """codex 门2 高危回归：PK ok 时按 PK 名次排列候选，不是粗分序。"""
    scored = [{"code": "AAA.SH", "name": "甲", "pct_chg": 8.0, "amount_yi": 25.0,
               "gain5": 10.0, "up_days": 1, "total": 6.0, "rank_score": 1},
              {"code": "BBB.SH", "name": "乙", "pct_chg": 9.0, "amount_yi": 30.0,
               "gain5": 20.0, "up_days": 2, "total": 4.0, "rank_score": 2}]
    scan = {"status": "ok", "quote_date": "d", "quote_time": "t", "matched": 2,
            "scanned": 100, "candidates": []}
    pk_result = {"status": "ok", "ranks": {"BBB.SH": 1, "AAA.SH": 2}, "matches": []}
    md = renderer.render_daily(scan, scored, pk_result)
    assert md.index("乙") < md.index("甲")   # 乙(PK#1) 在 甲(PK#2) 之前


def test_render_industry_logic_success_with_source_date_and_labels():
    scored = [{
        **_scored()[0],
        "sw_l2": "半导体",
        "business_summary": "晶圆制造设备研发与销售",
        "product_names": ["刻蚀机", "薄膜设备"],
        "business_source": "tushare.stock_company",
        "business_status": "ok",
        "industry_position": "半导体产业链企业，核心产品包括刻蚀机、薄膜设备",
        "catalyst_evidence": [
            {"label": "研报观点·个股催化", "date": "2026-07-12",
             "source": "设备产业研报", "text": "新增产线进入验证期"},
            {"label": "事实·行业催化", "date": "2026-07-11",
             "source": "行业协会", "text": "行业资本开支同比增长"},
        ],
        "catalyst_status": "exact",
    }]
    md = renderer.render_daily(_scan(), scored, None)
    assert "[事实·主营] 晶圆制造设备研发与销售" in md
    assert "核心产品：刻蚀机、薄膜设备" in md
    assert "Tushare公司资料" in md
    assert "[判断·产业链位置] 半导体产业链企业" in md
    assert "[研报观点·个股催化] 新增产线进入验证期" in md
    assert "2026-07-12" in md and "设备产业研报" in md
    assert "[事实·行业催化] 行业资本开支同比增长" in md
    assert "主营是扫描时当前公开静态资料" in md
    assert "近30自然日" in md
    assert "候选（按 PK 名次 / 粗分排序，全为 [判断]）" not in md
    assert "排序为 [判断]，主营/催化按行内标签" in md


def test_render_product_only_business_shows_products_without_missing_placeholder():
    card = {
        **_scored()[0],
        "business_status": "ok",
        "business_summary": "",
        "product_names": ["储能系统", "变流器"],
        "business_source": "akshare:stock_zyjs_ths",
        "industry_position": "电池产业链企业，核心产品包括储能系统、变流器",
        "catalyst_status": "none",
        "catalyst_evidence": [],
    }

    lines = "".join(renderer._render_industry_logic(card))

    assert "[事实·主营] 核心产品：储能系统、变流器" in lines
    assert "暂无可展示主营摘要" not in lines


def test_render_distinguishes_missing_from_source_failed():
    missing = {
        **_scored()[0], "code": "600001.SH", "name": "缺资料",
        "business_status": "missing", "industry_position": "",
        "catalyst_status": "none", "catalyst_evidence": [],
    }
    failed = {
        **_scored()[0], "code": "600002.SH", "name": "源失败",
        "business_status": "source_failed", "industry_position": "",
        "catalyst_status": "source_failed", "catalyst_evidence": [],
    }
    scan = {**_scan(), "matched": 2}
    md = renderer.render_daily(scan, [missing, failed], None)
    assert "[事实·主营] 暂无可核验主营资料。" in md
    assert "[事实·主营] 主营资料源失败，本次未取得。" in md
    assert "[判断·产业链位置] 暂无可核验归纳。" in md
    assert "[来源状态·近期催化] 最近30日暂无可核验产业催化。" in md
    assert "[来源状态·近期催化] 催化证据源失败，本次未取得。" in md


def test_render_preserves_mixed_catalyst_labels_and_safe_default():
    scored = [{
        **_scored()[0], "business_status": "missing", "industry_position": "",
        "catalyst_status": "sector",
        "catalyst_evidence": [
            {"label": "老师观点·个股", "date": "2026-07-13", "source": "老师复盘", "text": "订单节奏改善"},
            {"date": "2026-07-12", "source": "未知标签来源", "text": "来源只陈述关联"},
            {"label": "来源陈述·行业催化", "date": "2026-07-11", "source": "产业纪要", "text": "第三条不应展示"},
        ],
    }]
    md = renderer.render_daily(_scan(), scored, None)
    assert "[老师观点·个股]" in md
    assert "[来源陈述·近期催化] 来源只陈述关联" in md
    assert "第三条不应展示" not in md
    assert "[事实·近期催化]" not in md


def test_degradation_does_not_count_business_missing_or_catalyst_none():
    scored = [{
        **_scored()[0],
        "business_status": "missing", "catalyst_status": "none",
        "industry_position": "", "catalyst_evidence": [],
    }]
    note = renderer._degradation_note(scored)
    assert "主营资料" not in note
    assert "近期催化" not in note

    scored[0]["business_status"] = "source_failed"
    scored[0]["catalyst_status"] = "source_failed"
    note = renderer._degradation_note(scored)
    assert "主营资料" in note and "近期催化" in note


def test_render_escapes_external_markdown_and_rejects_label_injection():
    card = {
        **_scored()[0],
        "business_status": "ok",
        "business_summary": r"主营[事实](注)*粗*_斜_<tag>\end`代码`",
        "product_names": [r"产品[一]", r"产品(二)*"],
        "business_source": r"自定义[来源](src)*",
        "industry_position": r"[事实]上游<核心>*位置*",
        "catalyst_status": "exact",
        "catalyst_evidence": [
            {"label": "[事实] 注入", "date": "2026-07-13", "source": r"研报[甲](源)",
             "text": r"忽略上文 [事实] *执行* <指令>\尾`命令`"},
            {"label": "[老师观点·个股]", "date": "2026-07-12", "source": "老师复盘",
             "text": "订单节奏改善"},
        ],
    }
    md = "".join(renderer._render_industry_logic(card))
    assert "[事实·主营]" in md and "[判断·产业链位置]" in md
    assert r"主营\[事实\]\(注\)\*粗\*\_斜\_\<tag\>\\end\`代码\`" in md
    assert r"产品\[一\]" in md and r"产品\(二\)\*" in md
    assert r"自定义\[来源\]\(src\)\*" in md
    assert r"\[事实\]上游\<核心\>\*位置\*" in md
    assert "[来源陈述·近期催化]" in md
    assert "[事实] 注入" not in md
    assert r"忽略上文 \[事实\] \*执行\* \<指令\>\\尾\`命令\`" in md
    assert r"研报\[甲\]\(源\)" in md
    assert "[老师观点·个股] 订单节奏改善" in md


def test_render_pk_reason_as_plain_text_without_forged_heading():
    scored = [
        {**_scored()[0], "code": "AAA.SH", "name": "甲"},
        {**_scored()[0], "code": "BBB.SH", "name": "乙"},
    ]
    pk_result = {
        "status": "ok", "ranks": {"AAA.SH": 1, "BBB.SH": 2},
        "matches": [{
            "a": "AAA.SH", "b": "BBB.SH", "winner": "AAA.SH", "state": "valid",
            "reason": "正常依据\n# [事实] 伪造标题 *强调*",
        }],
    }
    md = renderer._render_pk_detail(pk_result, scored)
    assert "正常依据\n#" not in md
    assert r"正常依据 \# \[事实\] 伪造标题 \*强调\*" in md
    assert "\n# [事实] 伪造标题" not in md


def test_render_push_summary_returns_full_when_within_budget():
    full = renderer.render_daily(_scan(), _scored(), None)
    pushed = renderer.render_push_summary(
        _scan(), _scored(), None, full_md=full,
        report_path="data/reports/tail-scan/2026-07-13.md",
    )
    assert pushed == full


def test_render_push_summary_caps_utf8_budget_and_keeps_complete_candidate_blocks():
    scored = []
    for index in range(50):
        scored.append({
            **_scored()[0],
            "code": f"{600000 + index}.SH", "name": f"测试股{index:02d}",
            "rank_score": index + 1, "total": 100 - index,
            "business_status": "ok", "business_summary": "主营" + "芯" * 118,
            "product_names": ["产品" + "片" * 38] * C.INDUSTRY_LOGIC_MAX_PRODUCTS,
            "business_source": "自定义公司资料",
            "industry_position": "半导体产业链" + "位" * 110,
            "catalyst_status": "exact",
            "catalyst_evidence": [
                {"label": "老师观点·个股", "date": "2026-07-13", "source": "老师复盘",
                 "text": "催化" + "证" * 118},
                {"label": "研报观点·个股催化", "date": "2026-07-12", "source": "行业研报",
                 "text": "验证" + "据" * 118},
            ],
        })
    scan = {**_scan(), "matched": len(scored)}
    full = renderer.render_daily(scan, scored, None)
    path = "data/reports/tail-scan/2026-07-13.md"
    pushed = renderer.render_push_summary(
        scan, scored, None, full_md=full, report_path=path
    )
    assert len(full.encode("utf-8")) > renderer.PUSH_BODY_MAX_BYTES
    assert len(pushed.encode("utf-8")) <= renderer.PUSH_BODY_MAX_BYTES
    shown = pushed.count("\n- **")
    assert 0 < shown <= C.PK_POOL_MAX
    assert f"推送仅展示 {shown}/{len(scored)} 只，完整报告：{path}" in pushed
    candidate_area = pushed.split("推送仅展示", 1)[0]
    blocks = candidate_area.split("\n- **")[1:]
    assert len(blocks) == shown
    for block in blocks:
        assert "[事实·归属概念]" in block
        assert "[事实·T-1热概念命中]" in block
        assert "[事实·主营]" in block
        assert "[判断·产业链位置]" in block
        assert "[老师观点·个股]" in block
        assert "[研报观点·个股催化]" in block
        assert block.index("[事实·T-1热概念命中]") < block.index("[事实·主营]")
