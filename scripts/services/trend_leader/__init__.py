"""趋势主升自动识别 scanner（盘后只读·派生信号层）。

漏斗：当日涨停 ∩ 主线板块 → 拉区间 OHLCV → 检测器判定 → 持久化观察池 trend_leader_pool。
detectors.py 为纯函数层（输入 OHLCV 序列，输出 (matched, detail)），便于单测与复用。
"""
