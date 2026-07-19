"""拥挤度纯函数：占比/区间涨幅/滚动分位/双高信号。全部无 IO，历史序列由调用方传入。"""
from __future__ import annotations

import math

SHARE_WARN_PCT = 30.0      # 交易拥挤提示线（formatter 参考线）
SHARE_EXTREME_PCT = 40.0   # 历史极值区（2020-21 白酒 ~42 / 本轮电子 47）
GAIN_WINDOWS = (5, 20, 60)
# 双高评分用的斜率窗口。可单独校准(独立走 interval_gain,不依赖 GAIN_WINDOWS);
# 但注意 view 输出键名 gain_pctile_{N}d 随之改变,消费方(formatter/测试)须同步
SLOPE_PCTILE_WINDOW = 20
HIGH_PCTILE = 90.0
MIN_PCTILE_SAMPLES = 60    # 历史样本(含当日)不足 60 个交易日不出分位
# ETF 单次份额变动超存量 30% → 疑拆分/异常,标注勿直读(spec 事故级用例 6;
# 数据质量阈值与其他阈值同住本模块,formatter 只渲染)
ETF_JUMP_RATIO = 0.3


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def compute_share_pct(amount_billion, market_total_billion) -> float | None:
    if not (_finite(amount_billion) and _finite(market_total_billion)) or market_total_billion <= 0:
        return None
    return round(amount_billion / market_total_billion * 100, 2)


def _gain_at(bars: list, i: int, n: int) -> float | None:
    """bars[i] 相对 bars[i-n] 的涨幅%。涨幅公式与守卫的单点真源。"""
    if i < n:
        return None
    base, last = bars[i - n][1], bars[i][1]
    if not (_finite(base) and _finite(last)) or base <= 0:
        return None
    gain = round((last / base - 1) * 100, 2)
    return gain if math.isfinite(gain) else None


def interval_gain(bars: list, n: int, end_date: str) -> float | None:
    """bars 升序 (date, close)。末根日期必须等于 end_date（防节假日/陈旧数据冒充当日）。

    窗口按 bar 索引回数（非交易日历距离）：假设行业指数 close 无缺失日；若历史存在
    缺 close 被跳过的日子，窗口会向前偏移（指数极少缺 close，接受该假设）。"""
    if not bars or bars[-1][0] != end_date:
        return None
    return _gain_at(bars, len(bars) - 1, n)


def rolling_percentile(history: list, current) -> float | None:
    """current 在 history+current 中的分位(0-100,最大值=100)。样本不足 MIN_PCTILE_SAMPLES → None。

    history 不含 current;非有限元素在单遍计数中剔除(公开 API,不能只靠调用方契约——
    NaN 混入会静默稀释分位)。零方差序列(全体等值)返 None:恒定死板块的"分位"无区分度,
    按 100 处理会把零波动板块误判双高(门1 review 高优先级)。"""
    if not _finite(current):
        return None
    n_valid, below, vmin, vmax = 1, 1, current, current
    for v in history:
        if not _finite(v):
            continue
        n_valid += 1
        if v <= current:
            below += 1
        vmin, vmax = min(vmin, v), max(vmax, v)
    if n_valid < MIN_PCTILE_SAMPLES or vmin == vmax:
        return None
    return round(below / n_valid * 100, 1)


def pctile_of_last(series: list) -> float | None:
    """序列末元素在整段序列中的分位——「分位剔除当日」机制的单一入口。"""
    if not series:
        return None
    return rolling_percentile(series[:-1], series[-1])


def _dedup_sectors(sectors: list) -> list:
    """同快照内 (level, code) 去重,保留末条(数据源重复行时末条通常为修正值)。

    不去重会张冠李戴:重复键的前条行会拿末条的值当"当日值"算分位,且双高重复计入。"""
    per_key: dict = {}
    for s in sectors or []:
        per_key[(s.get("level"), s.get("code"))] = s
    return list(per_key.values())


def _series_by_code(records: list[dict]) -> dict:
    """{(level, code): {"bars": [(date, close)], "shares": [float], "name": str}}。
    按 (level, code) 键隔离，L1/L2 永不掺混（spec 事故级用例）。"""
    out: dict = {}
    for rec in records:
        for s in _dedup_sectors(rec.get("sectors")):
            key = (s.get("level"), s.get("code"))
            ent = out.setdefault(key, {"bars": [], "shares": [], "name": s.get("name")})
            if _finite(s.get("close")):
                ent["bars"].append((rec["date"], s["close"]))
            if _finite(s.get("share_pct")):
                ent["shares"].append(s["share_pct"])
    return out


def _gain_history(bars: list, n: int) -> list:
    """整段历史上每个可计算日的 n 日涨幅序列（含末日），供涨幅分位。"""
    out = []
    for i in range(n, len(bars)):
        g = _gain_at(bars, i, n)
        if g is not None:
            out.append(g)
    return out


def build_view(records: list[dict], date: str) -> dict | None:
    """从升序历史快照现算当日视图（分位/涨幅/双高）。末行必须是目标日。"""
    if not records or records[-1]["date"] != date:
        return None
    today = records[-1]
    series = _series_by_code(records)
    sectors, double_high = [], []
    slope_key = f"gain_pctile_{SLOPE_PCTILE_WINDOW}d"
    for s in _dedup_sectors(today.get("sectors")):
        ent = series[(s.get("level"), s.get("code"))]
        row = dict(s)
        # 当日值有限时恒为序列末元素 → pctile_of_last;无限值则无当日样本,分位无意义
        row["share_pctile"] = pctile_of_last(ent["shares"]) if _finite(s.get("share_pct")) else None
        for n in GAIN_WINDOWS:
            row[f"gain_{n}d"] = interval_gain(ent["bars"], n, date)
        # slope 窗口独立计算,不依赖 GAIN_WINDOWS 键(否则改 SLOPE_PCTILE_WINDOW 即 KeyError);
        # 当日涨幅缺席时跳过整段涨幅史计算(缺 close 日免 O(N) 白算)
        slope_gain = interval_gain(ent["bars"], SLOPE_PCTILE_WINDOW, date)
        row[slope_key] = (pctile_of_last(_gain_history(ent["bars"], SLOPE_PCTILE_WINDOW))
                          if slope_gain is not None else None)
        sectors.append(row)
        if (row["share_pctile"] is not None and row["share_pctile"] >= HIGH_PCTILE
                and row[slope_key] is not None and row[slope_key] >= HIGH_PCTILE):
            double_high.append(row)  # 与 sectors 共享同一 dict 对象,消费方只读、勿就地改写
    return {
        "date": date,
        "market_total_billion": today.get("market_total_billion"),
        "sectors": sectors,
        "double_high": double_high,
        "meta": today.get("meta"),
    }
