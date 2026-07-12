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
