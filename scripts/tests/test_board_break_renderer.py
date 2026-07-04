"""scripts/tests/test_board_break_renderer.py"""
from services.board_break import renderer, scorer


def _result(candidates, empty_kind=None):
    return {"status": "ok", "date": "2026-07-04", "prev_trade_date": "2026-07-03",
            "candidates": candidates, "rejects": {"st": 1}, "empty_kind": empty_kind,
            "sources": {"prev_limit_up": {"ok": True, "source": "akshare"},
                        "today_limit_up": {"ok": True, "source": "akshare"},
                        "today_limit_down": {"ok": True, "source": "akshare"}}}


def _scored_one():
    """经真实 `scorer.build_fact_card` + `score_all` 生成（不走手写假 fixture）：
    只 mock 掉外部取数入参（ann_result/holder_result=None、earnings_rows=[]、
    adj_factors=None），揭露 ref_price 透传链路真实断裂点——`build_fact_card` 若不
    回传 `ref_price` 字段，`ref_price_cell` 会渲染成 "—" 而非 "10.6"。
    """
    cand = {
        "code": "600002", "name": "某票", "limit_times": 3, "pct_chg": 2.1,
        "close": 10.0, "ref_price": 10.6, "industry": "计算机", "bars": [],
    }
    card = scorer.build_fact_card(
        cand, main_sectors={"计算机"}, ann_result=None, holder_result=None,
        earnings_rows=[], adj_factors=None,
    )
    return scorer.score_all([card])


def test_report_contains_rule_header_and_redline():
    scored = _scored_one()
    md = renderer.render_daily(_result(scored), scored, None)
    assert "断板反包观察清单" in md and "非买卖建议" in md and "[判断]" in md


def test_evidence_block_rendered_including_zero_score():
    scored = _scored_one()
    md = renderer.render_daily(_result(scored), scored, None)
    assert "打分依据明细" in md and "计算机」∈ 当日主线" in md
    # ann/holder/earnings 全 mock 为不可用 → macd 维度因复权因子缺失整体降级 missing
    assert "维度缺失：前复权失败/样本不足120根/末根非T日" in md


def test_ref_price_and_fact_marks():
    """揭露性测试：ref_price 若在 scorer.build_fact_card 层丢失透传，本用例在修复前必须失败。"""
    scored = _scored_one()
    md = renderer.render_daily(_result(scored), scored, None)
    assert "10.6" in md and "机械换算" in md


def test_pk_none_renders_dash():
    scored = _scored_one()
    md = renderer.render_daily(_result(scored), scored, None)
    # 定位候选表数据行：pk_result=None 时 PK 列须显式渲染 "—"（不接受恒真的宽松断言）
    data_lines = [line for line in md.splitlines() if line.startswith("| 600002")]
    assert data_lines and "| — |" in data_lines[0]


def test_empty_candidates_source_ok_empty():
    md = renderer.render_daily(_result([], empty_kind="source_ok_empty"), [], None)
    assert "今日无断板反包候选（昨日无连板≥2 的入口票）" in md
    assert "核心数据源全部成功" in md


def test_empty_candidates_rule_filtered_empty():
    md = renderer.render_daily(_result([], empty_kind="rule_filtered_empty"), [], None)
    assert "今日无断板反包候选（有入口票但全部被规则剔除，明细见脚注）" in md
    assert "核心数据源全部成功" in md


def test_evidence_detail_pipe_and_newline_escaped_in_table_cell():
    scored = _scored_one()
    scored[0]["evidences"][0]["detail"] = "A|B\nC"
    md = renderer.render_daily(_result(scored), scored, None)
    row = next(line for line in md.splitlines() if "A｜B C" in line)
    # 三列表格：转义后行内只剩 4 个竖线分隔符（表格结构未被自由文本中的 "|" 破坏）
    assert row.count("|") == 4


def test_cell_none_value_renders_blank_not_literal_none():
    """门1 correctness 复查：`c.get("name", "")` 的 default 只在键缺失时生效，
    若上游给出显式 None（如行业分类为空的个股），`_cell` 须兜底为空串，不得把
    Python 的 `str(None)` 泄漏成表格里的字面量 "None"。"""
    scored = _scored_one()
    scored[0]["industry"] = None
    md = renderer.render_daily(_result(scored), scored, None)
    row = next(line for line in md.splitlines() if line.startswith("| 600002"))
    assert "None" not in row


def test_empty_candidates_message_lines_adjacent_no_stray_blank():
    """门1 correctness 复查：空候选提示与「核心数据源全部成功」须相邻展示，
    `_blank_wrapped` 复用不得在两句话之间插入多余空行。"""
    md = renderer.render_daily(_result([], empty_kind="source_ok_empty"), [], None)
    lines = md.splitlines()
    idx = next(i for i, line in enumerate(lines) if "今日无断板反包候选" in line)
    assert lines[idx + 1] == "核心数据源全部成功，本次为真实空候选（非采集故障）。"


def test_source_failed_report():
    md = renderer.render_source_failed({"status": "source_failed", "date": "2026-07-04",
                                        "failed_sources": {"today_limit_up": "接口超时"}})
    assert "数据失败" in md and "不产出候选清单" in md


def test_save_report_out_root_injectable(tmp_path):
    p = renderer.save_report("# x", "2026-07-04", out_root=str(tmp_path))
    assert p.exists() and p.name == "2026-07-04.md"
