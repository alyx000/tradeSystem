"""TDD test for tail-scan renderer (only-read observation list MD)."""
from services.tail_scan import renderer


def _scan():
    return {"status": "ok", "quote_date": "2026-07-13", "quote_time": "14:30:00",
            "matched": 1, "scanned": 5000,
            "candidates": [{"code": "600001.SH"}]}


def _scored():
    return [{"code": "600001.SH", "name": "测试股", "pct_chg": 8.5, "amount_yi": 25.0,
             "in_main_sector": True, "concept_names": ["AI算力"], "total": 4.0,
             "rank_score": 1, "gain5": 20.0, "up_days": 3, "is_limit_up": False}]


def test_render_has_disclaimer_and_judgement_tag():
    md = renderer.render_daily(_scan(), _scored(), None)
    assert "T-1" in md and "快照" in md          # 数据时效声明
    assert "[判断]" in md
    assert "600001" in md


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
