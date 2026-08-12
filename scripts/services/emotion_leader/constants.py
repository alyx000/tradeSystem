"""情绪核心生命周期监控常量。"""

DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_MAX_ROWS = 20
FETCH_WORKERS = 2

# 「打开高度」不与普通晋级混用：目标日非 ST 连板最高高度
# 必须严格超过此前 20 个开放日的最高高度。
HEIGHT_BREAKTHROUGH_LOOKBACK_OPEN_DAYS = 20

# 自动归档仅影响日报展示，不删除历史事实：最后一次涨停已久且距峰值回撤较深。
ARCHIVE_AFTER_TRADE_DAYS = 20
ARCHIVE_DRAWDOWN_PCT = -30.0

# 波段标签是 [判断]。一次不少于 10% 的收盘回撤后，再收复前高才确认新一波；
# 尚未收复前高但自回撤低点反弹不少于 10%，只标“候选”。
WAVE_PULLBACK_PCT = 10.0
WAVE_RECOVERY_PCT = 10.0

REPORT_DIR = "data/reports/emotion-leader"

MANUAL_CORE_ATTRIBUTES = frozenset({"连板核心", "前排活跃", "弹性前排"})
MANUAL_WAVE_LABELS = frozenset({"单波", "二波", "多波", "二波候选", "多波候选"})
