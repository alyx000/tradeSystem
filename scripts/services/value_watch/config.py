"""value-watch 口径常量（spec v8 定稿，用户已确认；改动=口径变更须 bump LOGIC_VERSION）。"""
from __future__ import annotations

# 事件键版本前缀：口径升级 bump = 有意让同一自然事件在新口径下重新评价（旧账本不压制新键）。
# bump 属显式决策，须在 PR 说明重推影响面。
LOGIC_VERSION = 1

# ① 红利买入触发（回撤 episode）
BASIS_WINDOW = 120                      # 波段高点滚动窗口（交易日）
BANK_INDEX = "801780.SI"                # 申万一级银行指数
DRAWDOWN_TARGETS: dict[str, list[int]] = {
    "801780.SI": [10, 15],              # 银行板块指数：10%/15% 两档
    "600900.SH": [10],                  # 长江电力：仅 10% 档（老师未给 15% 档）
}

# ② 卖出阶梯（读持仓池）
LADDER_CODES: dict[str, str] = {
    "601398.SH": "工商银行", "601939.SH": "建设银行",
    "601288.SH": "农业银行", "601988.SH": "中国银行",
    "600900.SH": "长江电力",
}
LADDER_RUNGS = [10, 15, 20]             # 首触各档提示；20 档后回落全清提示

# ③ 稀缺价值（周线）
SCARCITY_CODES: dict[str, str] = {"600436.SH": "片仔癀"}
MA_SPREAD_MAX = 0.03                    # 周 MA5/10/20 粘合阈值 (max-min)/min ≤ 3%
WARMUP_WEEKS = 35                       # 周 MACD 最少完成周数，不足 → insufficient_history
INVALIDATE_WEEKS = 2                    # signaled 后连续 N 完成周不满足才失效（去抖）

# 认知出处（推送/报告引用区标注）
TEACHER_NOTE_REF = "teacher_notes#391"
