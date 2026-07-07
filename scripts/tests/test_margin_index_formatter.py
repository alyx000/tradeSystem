"""margin_index_correlation.formatter：钉钉 markdown 渲染（背离打头，守红线）。"""
from __future__ import annotations

from services.margin_index_correlation import formatter


def _record(stale=False, diverged=True):
    return {
        "date": "2026-06-19",
        "data_trade_date": "2026-06-18" if stale else "2026-06-19",
        "windows": [5, 20],
        "base_index": "000001.SH",
        "indices": [
            {"pair_key": "total:000001.SH", "margin_key": "total", "index_code": "000001.SH",
             "index_name": "上证指数", "group": "broad"},
            {"pair_key": "sse:000001.SH", "margin_key": "sse", "index_code": "000001.SH",
             "index_name": "上证指数", "group": "cross", "margin_label": "沪市两融"},
        ],
        "lag": {
            "total:000001.SH": {"best_lag": 2, "best_corr": 0.61, "relation": "两融滞后",
                                "by_lag": {}},
        },
        "sync_corr": {
            "total:000001.SH": {"5": {"corr": 0.72, "label": "强同向"},
                                "20": {"corr": 0.55, "label": "弱同向"}},
        },
        "divergence": {
            "total:000001.SH": {
                "5": {"index_cum": 3.1, "margin_cum": -1.2, "diverged": diverged,
                      "type": "涨指两融降" if diverged else "无背离", "magnitude": 4.3},
            },
        },
        "balance": {
            "total": {"latest_yi": 18000.0, "dod_pct": 0.5, "pctile_20d": 0.9,
                      "up_streak": 3, "down_streak": 0, "ma20": 17500.0, "vs_ma20": 2.8},
            "sse": {"latest_yi": 9100.0, "dod_pct": 0.4, "pctile_20d": 0.8,
                    "up_streak": 3, "down_streak": 0, "ma20": 8900.0, "vs_ma20": 2.2},
            "szse": {"latest_yi": 8900.0, "dod_pct": 0.6, "pctile_20d": 0.85,
                     "up_streak": 2, "down_streak": 0, "ma20": 8700.0, "vs_ma20": 2.3},
        },
        "meta": {"source": "tushare:margin", "market_scope": "BSE+SSE+SZSE",
                 "stale": stale, "divergence_windows": [5]},
    }


def test_headline_is_divergence():
    md = formatter.format_daily_report(_record(diverged=True))
    # 背离段在水位段之前（头条）
    assert md.index("背离") < md.index("余额水位")
    assert "涨指两融降" in md


def test_risk_alert_summary_renders_before_divergence_details():
    rec = _record(diverged=True)
    rec["risk_alert"] = {
        "level": "medium",
        "score": 4,
        "headline": "中风险：多口径两融与指数反向",
        "reasons": ["两融合计×上证指数 5日 涨指两融降"],
        "hit_count": 1,
        "unevaluated_count": 0,
    }
    md = formatter.format_daily_report(rec)
    assert "风险预警" in md
    assert "中风险" in md
    assert md.index("风险预警") < md.index("背离明细")


def test_all_sections_present():
    md = formatter.format_daily_report(_record())
    for kw in ["背离", "余额水位", "领先", "同步相关"]:
        assert kw in md


def test_redline_footnote_and_judgement_marker():
    md = formatter.format_daily_report(_record())
    assert "[判断]" in md
    # 守红线脚注：不构成买卖建议
    assert "不构成" in md or "仅供" in md


def test_stale_hint_when_t_minus_1():
    md = formatter.format_daily_report(_record(stale=True))
    assert "2026-06-18" in md  # 两融真实日
    assert "两融数据" in md and ("T-1" in md or "非当日" in md or "陈旧" in md or "滞后" in md)


def test_no_divergence_shows_calm_line():
    md = formatter.format_daily_report(_record(diverged=False))
    assert "无背离" in md or "未见背离" in md


def test_unevaluated_not_rendered_as_no_divergence():
    """日期缺口/样本不足/无法评估 不能渲染成「未见背离」（codex round2 #2）。"""
    rec = _record(diverged=False)
    rec["divergence"]["total:000001.SH"]["5"] = {
        "index_cum": None, "margin_cum": None, "diverged": False,
        "type": "日期缺口", "magnitude": None}
    md = formatter.format_daily_report(rec)
    assert "数据质量提示" in md and "未评估" in md and "日期缺口" in md
    # 这条 pairing 只有一个窗口且为日期缺口 → 不得出现「方向一致」误导


def test_signals_unevaluated_not_masked_as_no_divergence():
    """signals 只读路径同样不得把 日期缺口/样本不足 渲染成「无背离」（codex round3）。"""
    rec = _record(diverged=False)
    rec["divergence"] = {"total:000001.SH": {"5": {
        "index_cum": None, "margin_cum": None, "diverged": False,
        "type": "日期缺口", "magnitude": None}}}
    md = formatter.format_signals([rec])
    assert "未评估" in md and "日期缺口" in md
    # 该行不得以「无背离」收尾误导
    line = [ln for ln in md.splitlines() if rec["date"] in ln][0]
    assert "未评估" in line


def test_signals_show_risk_alert_level_when_available():
    rec = _record(diverged=True)
    rec["risk_alert"] = {
        "level": "high",
        "score": 8,
        "headline": "高风险：两融与指数反向，5日+20日共振",
        "reasons": [],
        "hit_count": 3,
        "unevaluated_count": 0,
    }
    md = formatter.format_signals([rec])
    line = [ln for ln in md.splitlines() if rec["date"] in ln][0]
    assert "高风险" in line


def test_all_unevaluated_no_false_calm():
    """全部窗口未评估时，不得输出「未见背离」。"""
    rec = _record(diverged=False)
    rec["divergence"] = {"total:000001.SH": {"5": {
        "index_cum": None, "margin_cum": None, "diverged": False,
        "type": "样本不足", "magnitude": None}}}
    md = formatter.format_daily_report(rec)
    assert "未见背离" not in md
    assert "未评估" in md
