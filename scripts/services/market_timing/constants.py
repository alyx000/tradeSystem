"""market-timing 检测器阈值（默认套；上线后用真实数据校准）。

所有默认值都是「先给默认、上线后校准」(同 trend-leader O1 模式)。校准时改这里，
并在被改常量上加一行注释记录样本与判断依据，便于后续多日复核。
"""

# ── 斐波那契时间周期 ──
# 变盘点序列：从最近 swing 拐点(高/低双向)起算的交易日数命中即为变盘点 [判断]
FIB_SEQUENCE = (5, 8, 13, 21, 34, 55, 89)
# 临近容差：|day_count - fib| <= 此值 视为「临近变盘点」(非精确命中)
FIB_NEAR_TOLERANCE = 1

# ── swing 拐点检测（双向：高点或低点都算起算点）──
# 极值窗口：拐点须是 [i-window, i+window] 内的最高/最低（两侧各 window 根确认）
SWING_WINDOW = 10
# 反转幅度：拐点之后须反向运行 ≥ 此比例才算有效 swing（过滤毛刺）
SWING_MIN_REVERSAL_PCT = 0.05

# ── 底分型 + 放量中阳确认 ──
# 放量：当日量 ≥ MA5 量 × 此倍数（>1 为放量；区别于缩量）
FRACTAL_VOLUME_RATIO = 1.5
# 中阳线最小实体：(close - open) / open ≥ 此比例
MID_YANG_MIN_PCT = 0.01

# ── 通用兜底 ──
# bar 数 < 此值的检测返回 insufficient_history（历史不足不硬算）
MIN_BARS_FOR_SIGNAL = 10
# 地量分位窗口：当日两市成交额在近 N 日中的分位
AMOUNT_PCTILE_WINDOW = 20
