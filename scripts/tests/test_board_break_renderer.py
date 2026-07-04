"""scripts/tests/test_board_break_renderer.py"""
from services.board_break import renderer


def _result(candidates):
    return {"status": "ok", "date": "2026-07-04", "prev_trade_date": "2026-07-03",
            "candidates": candidates, "rejects": {"st": 1},
            "sources": {"prev_limit_up": "ok", "today_limit_up": "ok", "today_limit_down": "ok"}}


def _scored_one():
    return [{"code": "600002", "name": "某票", "total": 3.5, "rank_score": 1,
             "evidences": [{"dimension": "main_sector", "score": 2.0, "status": "ok",
                            "detail": "申万二级「计算机」∈ 当日主线 Top-5"},
                           {"dimension": "macd", "score": 0.0, "status": "missing",
                            "detail": "维度缺失：日线不足120根"}],
             "close": 10.0, "ref_price": 10.6, "limit_times": 3, "pct_chg": 2.1, "industry": "计算机"}]


def test_report_contains_rule_header_and_redline():
    md = renderer.render_daily(_result(_scored_one()), _scored_one(), None)
    assert "断板反包观察清单" in md and "非买卖建议" in md and "[判断]" in md


def test_evidence_block_rendered_including_zero_score():
    md = renderer.render_daily(_result(_scored_one()), _scored_one(), None)
    assert "打分依据明细" in md and "计算机」∈ 当日主线" in md and "维度缺失：日线不足120根" in md


def test_ref_price_and_fact_marks():
    md = renderer.render_daily(_result(_scored_one()), _scored_one(), None)
    assert "10.6" in md and "机械换算" in md


def test_pk_none_renders_dash():
    md = renderer.render_daily(_result(_scored_one()), _scored_one(), None)
    assert "PK: —" in md or "— " in md.split("|")[0] or "—" in md


def test_empty_candidates_normal_report():
    md = renderer.render_daily(_result([]), [], None)
    assert "今日无断板反包候选" in md and "核心数据源全部成功" in md


def test_source_failed_report():
    md = renderer.render_source_failed({"status": "source_failed", "date": "2026-07-04",
                                        "failed_sources": {"today_limit_up": "接口超时"}})
    assert "数据失败" in md and "不产出候选清单" in md


def test_save_report_out_root_injectable(tmp_path):
    p = renderer.save_report("# x", "2026-07-04", out_root=str(tmp_path))
    assert p.exists() and p.name == "2026-07-04.md"
