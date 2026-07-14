DEFAULT_TOP_N = 10
DEFAULT_BACKFILL_YEARS = 5
EPSILON = 1e-8
UNCLASSIFIED = "未分类"
REPORT_DIR = "data/reports/new-high"

# 由生产库 2021-07-12～2026-07-10 的 1211 个 canonical 交易日校准：
# 有效行情数最低 4477，行情/复权宇宙覆盖最低 96.2477%，正常相邻交易日
# 市场数比率为 98.8399%～100.7851%；2026-07-10 申万二级覆盖为
# 5513/5521=99.8551%。绝对地板对五年默认回补窗口留约 10% 缓冲，
# 行业覆盖留约 0.85 个百分点缓冲，其余比例各留约 1 个百分点缓冲。
MIN_MARKET_COUNT = 4000
MIN_ADJ_UNIVERSE_COVERAGE = 0.95
MIN_INDUSTRY_COVERAGE = 0.99
MIN_PREVIOUS_MARKET_RATIO = 0.98
MAX_PREVIOUS_MARKET_RATIO = 1.02
