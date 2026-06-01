"""formatter 单测（阶段3）：markdown 段落 + 双窗反向对照 + 红线脚注。"""
from __future__ import annotations

from services.sector_correlation import formatter


def _rec() -> dict:
    return {
        "date": "2026-05-29", "windows": [20, 60], "top_n": 3,
        "base_index": "000001.SH", "sample_days": {"20": 20, "60": 58},
        "indices": ["000001.SH", "000688.SH"],
        "sectors": [],
        "sector_index": {
            "60": {
                "半导体": {"000001.SH": {"raw_corr": 0.81, "beta": 1.42, "label": "强同向"}},
                "黄金": {"000001.SH": {"raw_corr": -0.50, "beta": -0.30, "label": "弱逆向"}},
            },
            "20": {"半导体": {"000001.SH": {"raw_corr": 0.79, "beta": 1.38, "label": "强同向"}}},
        },
        "pair_raw": {"60": [{"a": "半导体", "b": "算力", "corr": 0.86, "label": "强同向"}], "20": []},
        "pair_excess": {
            "60": [{"a": "算力", "b": "黄金", "corr": -0.45, "label": "强逆向"}],
            "20": [{"a": "算力", "b": "黄金", "corr": -0.47, "label": "强逆向"}],
        },
        "meta": {},
    }


def test_daily_report_sections_and_footnote():
    md = formatter.format_daily_report(_rec())
    assert "板块相关性 · 2026-05-29" in md
    assert "黄金" in md and "逆向板块" in md            # 黄金进逆向行
    assert "高弹性同向" in md and "半导体" in md          # β1.42 进高弹性
    assert "🤝 联动榜" in md and "半导体 ⟷ 算力" in md
    assert "⚖️ 反向榜" in md
    assert "20日 -0.47 / 60日 -0.45  [稳定]" in md        # 双窗都≤-0.4 → 稳定
    assert "非因果" in md                                 # 红线脚注


def test_daily_report_today_comovement_section():
    rec = _rec()
    rec["sectors"] = [
        {"name": "半导体", "type": "industry", "latest_change_pct": 2.1},
        {"name": "存储芯片", "type": "concept", "latest_change_pct": 3.4},
        {"name": "电力", "type": "industry", "latest_change_pct": -1.2},
    ]
    md = formatter.format_daily_report(rec)
    assert "📅 今日联动" in md
    assert "🔴 齐涨：" in md and "存储芯片 +3.40%" in md and "半导体 +2.10%" in md
    assert "🟢 齐跌：" in md and "电力 -1.20%" in md


def test_daily_report_today_comovement_missing_data():
    rec = _rec()
    rec["sectors"] = []  # 无 latest_change_pct
    md = formatter.format_daily_report(rec)
    assert "今日板块涨跌数据缺失" in md


def test_daily_report_no_inverse_fallback():
    rec = _rec()
    rec["sector_index"]["60"]["黄金"]["000001.SH"]["label"] = "独立"
    md = formatter.format_daily_report(rec)
    assert "本期无明显逆向" in md


def test_daily_report_near_term_tag():
    rec = _rec()
    rec["pair_excess"]["20"] = [{"a": "算力", "b": "黄金", "corr": -0.10, "label": "独立"}]  # 短窗不显著
    md = formatter.format_daily_report(rec)
    assert "[近期]" in md


def test_daily_report_single_window_no_dual():
    """单窗：反向榜退单值渲染，不出现"双窗对照"与 短/长 对照（review L1）。"""
    rec = _rec()
    rec["windows"] = [60]
    rec["sample_days"] = {"60": 58}  # 与单窗一致
    rec["pair_excess"] = {"60": [{"a": "算力", "b": "黄金", "corr": -0.45, "label": "强逆向"}]}
    md = formatter.format_daily_report(rec)
    assert "双窗对照" not in md
    assert "算力 ⟷ 黄金  60日 -0.45" in md  # 单值渲染,无 短/长 对照
    assert "[稳定]" not in md and "[近期]" not in md


def test_format_matrix_and_trend():
    rec = _rec()
    assert "矩阵" in formatter.format_matrix(rec)
    assert "60日窗" in formatter.format_matrix(rec)
    trend = formatter.format_trend([rec])
    assert "2026-05-29" in trend and "强逆向对" in trend
    assert formatter.format_trend([]) == "（无历史快照）"
