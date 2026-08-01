"""形态篇扫描阈值。

出处：`teacher_notes#444` 鞠磊《形态篇（第一节技术课程）》，认知 `cog_3b32e660`。
课程只给定性口径（「大部分阳线」「好几组」），下面的数值是把它落成可判定门槛的选择，
各自注明依据；调阈值只改这里，scanner / detectors 不得内联魔法数。
"""

# 主线口径与 string-yang 对齐（复用同一 `judge_mainline`），故默认值也保持一致，
# 避免两个扫描器对「什么是主线」给出不同答案。
DEFAULT_TOP_K_SECTORS = 5
DEFAULT_TOP_CONCEPTS = 8

# MACD 需 120 根交易日（`utils.pattern.MIN_BARS_MACD`）。实测交易日/自然日 ≈ 0.66
# （`utils.ma_position` 的 400 自然日→269 根校准），120/0.66 ≈ 182 自然日；
# 取 300 自然日（≈198 根）留足春节/国庆长假余量。
RANGE_LOOKBACK_DAYS = 300

# 阳放阴缩节奏的统计窗口：20 个交易日≈1 个月，够容纳课程说的「做了好几组」，
# 又不会把两个月前的旧节奏算进当下。
RHYTHM_LOOKBACK = 20

# 「上涨的大部分的阳线都超过了均量线」——课程原话是「大部分」不是「每根」，取半数。
MIN_YANG_ABOVE_RATIO = 0.5
# 「做了好几组」的下限：至少完整出现过 1 组「放量阳→缩量阴」，否则只是单根放量。
MIN_RHYTHM_GROUPS = 1

# 未加速窗口：近 20 个交易日内不得出现加速日（涨停 / 双创 15%+）。
# 与 RHYTHM_LOOKBACK 同为 20 是巧合而非耦合——前者问「节奏成型了吗」，
# 后者问「空间透支了吗」，改其中一个不必动另一个。
ACCEL_LOOKBACK_BARS = 20

REPORT_DIR = "data/reports/pattern-scan"
