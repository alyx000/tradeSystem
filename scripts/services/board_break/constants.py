"""board-break 阈值/权重/关键词常量（spec D1–D13 锁定，上线后真实数据校准）。"""

# —— 筛选层（[事实]）——
MIN_LIMIT_TIMES = 2            # D1: 昨日连板 >= 2
BREAK_DAY_MAX_PCT = 6.0        # D3: 断板日涨幅 <= 6%（含 6.0）
REBOUND_REF_RATIO = 1.06       # D7: 6% 参考位 = 收盘 * 1.06
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")  # D6: 10cm 主板

# —— 打分数据窗口 ——
LOOKBACK_NATURAL_DAYS = 400    # 日线区间 T-400 自然日，≈250+ 交易日
ANNOUNCE_WINDOW_DAYS = 30      # 公告/增减持回看 30 自然日
EARNINGS_WINDOW_DAYS = 90      # 业绩回看 90 自然日
POSITION_BARS = 250            # 250 日区间分位样本
MIN_BARS_INDICATOR = 120       # MACD warm-up / 分位最低样本，低于此标缺失

# —— 打分权重（方式一）——
W_MAIN_SECTOR = 2.0
W_INCREASE = 1.5               # 增持/回购
W_PLACEMENT = 1.0              # 定增
W_REDUCE_LOW = 1.5             # D12: 低位减持加分
W_REDUCE_HIGH = -2.0           # D12: 高位减持减分（中位 0）
W_ANN_GOOD = 1.0
W_ANN_BAD = -1.0
W_EARN_GOOD = 1.5
W_EARN_BAD = -1.5
W_GAIN_HIGH = -2.0             # 近10日累计 >= 40%
W_GAIN_MID = -1.0              # 25% ~ 40%
W_MACD_UP = 1.0                # DIF > 0
GAIN10_HIGH = 40.0
GAIN10_MID = 25.0
POSITION_LOW = 0.30            # 250日分位 <= 0.30 低位
POSITION_HIGH = 0.70           # >= 0.70 高位
MAIN_SECTOR_TOP_K = 5          # 主线 = 当日成交额集中度 Top-5 申万二级

# —— 公告分类关键词（优先级：否定 > 减持 > 增持/回购 > 定增 > 利好 > 利空；业绩类让位业绩维度）——
KW_NEGATE = ("不减持", "终止减持", "提前终止减持", "解除质押", "回购注销", "取消")
KW_REDUCE = ("减持",)
KW_INCREASE = ("增持", "回购")
KW_PLACEMENT = ("定增", "非公开发行", "向特定对象发行")
KW_EARNINGS = ("业绩预告", "业绩快报", "预增", "预减")   # 公告维度跳过，由业绩维度承接
KW_GOOD = ("中标", "签订合同", "签署合同")
KW_BAD = ("质押", "违规", "警示", "立案", "问询")
KW_EXCLUDE = ("中标候选人",)   # 未定标不算利好

# —— PK（方式二）——
PK_POOL_MAX = 12
PK_BUDGET_SECONDS = 1200       # 熔断硬上限
PK_INVALID_RATIO_MAX = 0.30    # 无效场占比熔断
PK_VALID_RATIO_MIN = 0.70      # 有效场占比 >= 0.70 才渲染 PK 排名
PK_REASON_MAX_CHARS = 60
FACT_CARD_ANN_MAX = 5          # 事实卡公告标题最多 5 条
FACT_CARD_ANN_CHARS = 40       # 每条截断 40 字
