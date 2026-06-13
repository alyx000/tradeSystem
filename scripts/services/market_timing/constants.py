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

# 在最近 N 根内寻找最新底分型（无状态生命周期判定的回看窗口）
FRACTAL_LOOKBACK = 20

# ── 通用兜底 ──
# bar 数 < 此值的检测返回 insufficient_history（历史不足不硬算）
MIN_BARS_FOR_SIGNAL = 10
# 地量分位窗口：当日两市成交额在近 N 日中的分位
AMOUNT_PCTILE_WINDOW = 20

# ── 扫描指数清单（Stage 0 本机实测可达性定稿）──
# 微盘股万得 8841431 免费源取不到 → 用中证2000(932000.CSI)代理（老师本人即用中证2000 ETF 代理）；
# 平均股价 880003 经 pytdx 日线可达（哨兵 code "avg_price" 走 fetch.py 专路直连 tdx）。
INDEX_LIST = (
    {"code": "000001.SH", "name": "上证综指"},
    {"code": "399001.SZ", "name": "深证成指"},
    {"code": "399006.SZ", "name": "创业板指"},
    {"code": "000688.SH", "name": "科创50"},
    {"code": "932000.CSI", "name": "中证2000", "note": "微盘股代理"},
    {"code": "avg_price", "name": "平均股价", "note": "通达信880003"},
)

# 拉区间日线的自然日跨度（保证 ≥60 交易日供 swing/斐波那契/底分型窗口）
RANGE_LOOKBACK_DAYS = 180

# 两市成交额取数指数（tushare index_daily amount 单位=千元，亿 = 千元 / 1e5）
MARKET_AMOUNT_INDICES = ("000001.SH", "399001.SZ")
QIANYUAN_PER_YI = 1e5
