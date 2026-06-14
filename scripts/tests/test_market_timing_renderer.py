"""market-timing 渲染单测：内容正确 + 守红线（无买卖建议/方向/价位目标）。"""
from __future__ import annotations

from services.market_timing import formatter


_RESULT = {
    "date": "2026-06-13",
    "signals": [
        {"index_name": "上证综指", "swing_pivot_date": "2026-05-15", "swing_pivot_type": "high",
         "swing_pivot_price": 4258.0, "fib_day_count": 21, "fib_hit": 21, "fib_near": None,
         "fractal_status": "none", "fractal_low_date": None, "fractal_low_price": None,
         "fractal_confirm_date": None},
        {"index_name": "中证2000", "swing_pivot_date": "2026-05-20", "swing_pivot_type": "low",
         "swing_pivot_price": 3300.0, "fib_day_count": 7, "fib_hit": None, "fib_near": 8,
         "fractal_status": "confirmed", "fractal_low_date": "2026-06-10", "fractal_low_price": 3280.0,
         "fractal_confirm_date": "2026-06-13"},
    ],
    "context": {"market_amount_yi": 25000.0, "amount_pctile_20d": 0.1,
                "advance": 4500, "decline": 900, "limit_down_count": 12},
    "resonance_count": 1,
    "skipped": [],
}


def test_render_daily_content():
    md = formatter.render_daily(_RESULT)
    assert "大盘择时观察 · 2026-06-13" in md
    assert "变盘窗口·第21交易日" in md       # 精确命中
    assert "临近变盘窗口（斐波那契8" in md     # 临近
    assert "不预判涨跌" in md                 # 表下释义
    assert "底分型确认" in md                # confirmed 状态
    assert "高点 2026-05-15" in md           # 起算拐点(事实)
    assert "地量" in md                      # pctile 0.1 ≤ 0.2 → 地量标注


def test_render_daily_redline_no_buy_sell_or_targets():
    md = formatter.render_daily(_RESULT)
    assert "不构成买卖建议" in md
    assert "不预测方向" in md
    for forbidden in ("目标价", "买入价", "止损位", "建议买", "建议卖"):
        assert forbidden not in md


def test_render_daily_empty():
    md = formatter.render_daily({"date": "2026-06-13", "signals": [], "context": {},
                                 "resonance_count": 0, "skipped": [{"code": "x", "name": "上证"}]})
    assert "无可用指数数据" in md


def test_render_signals_table():
    rows = [
        {"trade_date": "2026-06-13", "index_name": "中证2000", "fib_day_count": 8, "fib_hit": 8,
         "fib_near": None, "fractal_status": "forming"},
    ]
    out = formatter.render_signals(rows)
    assert "中证2000" in out and "🎯8" in out and "底分型成型" in out


def test_render_signals_empty():
    assert "无 market-timing 信号记录" in formatter.render_signals([])
