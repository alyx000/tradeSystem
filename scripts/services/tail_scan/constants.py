"""tail-scan 常量：筛选阈值 / 四维粗权重 / 窗口 / PK 池。"""
from __future__ import annotations

# —— 筛选阈值（默认，可被 CLI --min-pct/--min-amount 覆盖）——
DEFAULT_MIN_PCT = 7.0          # 涨幅 > 7%
DEFAULT_MIN_AMOUNT_YI = 20.0   # 成交额 > 20 亿

# 已涨停判定容差（浮点比较，price 触及涨停价的相对容差）
LIMIT_UP_EPSILON = 0.003

# —— 历史窗口 ——
LOOKBACK_NATURAL_DAYS = 40     # 取历史日线的自然日窗口（够算 MA20 + 近5日涨幅 + 前高）
GAIN_WINDOW = 5                # 近 N 日涨幅
HIGH_WINDOW = 20               # 距前高回看交易日数
TEACHER_LOOKBACK_DAYS = 7      # 老师观点回看自然日
CONCEPT_TOP_M = 8              # 概念资金流 Top-M
MAIN_SECTOR_TOP_K = 8          # 主线申万二级 Top-K（对齐 trend_leader 默认）

# —— 四维粗权重（仅供强池截断排序，不进 PK prompt）——
W_LOGIC_MAIN = 2.0             # ∈主线
W_LOGIC_CONCEPT = 1.0          # ∈强概念
W_LOGIC_TEACHER = 1.0          # 老师观点命中
W_TRINITY_TOP = 1.5           # 候选集内相对强（涨幅/成交额排名靠前）
W_RHYTHM_FIRST = 1.5          # 首次放量加速
W_RHYTHM_MA = 1.0             # 现价 > MA5/10/20
W_NODE_BREAK = 1.0            # 今日突破前高
W_TAIL_STRONG = 1.0          # 收在日内高位 + 已涨停

# —— PK 强池 ——
PK_POOL_MAX = 12
PK_REASON_MAX_CHARS = 80
PK_BUDGET_SECONDS = 180.0
PK_INVALID_RATIO_MAX = 0.5

# —— 打分/事实卡阈值 ——
FIRST_SURGE_VOL_RATIO_MIN = 0.7   # 首次放量加速：半日额/昨日全日额 下限
FIRST_SURGE_UP_DAYS_MAX = 1       # 首次放量加速：连涨天数上限（非高位连涨）
TRINITY_TOP_RANK = 3              # 候选集内相对强弱：涨幅排名前 N 视为强
TAIL_STRONG_CLOSE_POS_MIN = 0.9   # 收在日内高位：close_pos 下限

# —— 日历标记 ——
MONTH_END_START_DAY = 25          # 月末窗口起始日
MONTH_START_END_DAY = 3           # 月初窗口结束日
EARNINGS_SEASON_MONTHS = (1, 4, 7, 10)  # 财报季窗口月份

# —— 大势信号读取 ——
MARKET_TIMING_FETCH_LIMIT = 20    # market_timing_signal 最近一批读取上限（余量覆盖~6指数，防截断）
