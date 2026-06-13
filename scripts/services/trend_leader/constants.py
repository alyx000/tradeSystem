"""趋势主升检测器阈值（O1 锁定默认；可经 CLI flag / config 覆盖，上线后用真实数据校准）。"""

# 首次涨停加速：近 N 交易日内除今日外无涨停
FIRST_LIMIT_LOOKBACK_DAYS = 60

# 缓涨（角度）：近 M 交易日累计涨幅区间，且期间无涨停
GENTLE_RISE_WINDOW = 20
GENTLE_RISE_MIN_PCT = 5.0
GENTLE_RISE_MAX_PCT = 40.0

# 贴 MA5：|close - MA5| / MA5 <= 阈值
NEAR_MA5_MAX_DEVIATION = 0.03

# 缩量阴线买点：vol <= min(昨量, MA5量) * 阈值
SHRINK_VOLUME_RATIO = 0.8

# 远离 MA5 见顶：(close - MA5) / MA5 >= 阈值（仅标记，不退池）
FAR_FROM_MA5_MIN_DEVIATION = 0.08

# 历史不足兜底：bar 数 < 此值的检测返回 insufficient_history
MIN_BARS_FOR_SIGNAL = 10

# 漏斗扫描参数（scanner）
RANGE_LOOKBACK_DAYS = 90        # 拉区间日线的自然日跨度（保证 ≥60 交易日供首次涨停/缓涨判定）
DEFAULT_TOP_K_SECTORS = 5       # 主线池默认取成交额集中度 Top-K 申万二级

# 历史「首次涨停」判定：板块涨停比例直接复用 utils/price_limit.limit_pct_for（权威，含 ST/北交所/双创）。
# 用 pct_chg 回判历史涨停时乘以容差因子（涨停日 pct_chg 因舍入可能略低于名义比例，如 10% 板显 9.96）。
LIMIT_DETECT_FACTOR = 0.98  # 主板 10×0.98=9.8 / 双创 20×0.98=19.6 / 北交所 30×0.98=29.4 / ST 5×0.98=4.9
