"""拥挤度纯函数：占比/区间涨幅/滚动分位/双高信号。全部无 IO，历史序列由调用方传入。"""
from __future__ import annotations

import math

SHARE_WARN_PCT = 30.0      # 交易拥挤提示线
SHARE_EXTREME_PCT = 40.0   # 历史极值区（2020-21 白酒 ~42 / 本轮电子 47）
GAIN_WINDOWS = (5, 20, 60)
HIGH_PCTILE = 90.0
MIN_PCTILE_SAMPLES = 60    # 历史样本(含当日)不足 60 个交易日不出分位


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def compute_share_pct(amount_billion, market_total_billion) -> float | None:
    if not (_finite(amount_billion) and _finite(market_total_billion)) or market_total_billion <= 0:
        return None
    return round(amount_billion / market_total_billion * 100, 2)


def interval_gain(bars: list, n: int, end_date: str) -> float | None:
    """bars 升序 (date, close)。末根日期必须等于 end_date（防节假日/陈旧数据冒充当日）。

    窗口按 bar 索引回数（非交易日历距离）：假设行业指数 close 无缺失日；若历史存在
    缺 close 被跳过的日子，窗口会向前偏移（指数极少缺 close，接受该假设）。"""
    if len(bars) < n + 1 or bars[-1][0] != end_date:
        return None
    base, last = bars[-1 - n][1], bars[-1][1]
    if not (_finite(base) and _finite(last)) or base <= 0:
        return None
    gain = round((last / base - 1) * 100, 2)
    return gain if math.isfinite(gain) else None


def rolling_percentile(history: list, current) -> float | None:
    """current 在 history+current 中的分位(0-100,最大值=100)。样本不足 MIN_PCTILE_SAMPLES → None。"""
    samples = [v for v in history if _finite(v)]
    if not _finite(current) or len(samples) + 1 < MIN_PCTILE_SAMPLES:
        return None
    below = sum(1 for v in samples if v <= current) + 1  # +1 计入 current 自身
    return round(below / (len(samples) + 1) * 100, 1)


def _series_by_code(records: list[dict]) -> dict:
    """{(level, code): {"bars": [(date, close)], "shares": [float], "name": str}}。
    按 (level, code) 键隔离，L1/L2 永不掺混（spec 事故级用例）。"""
    out: dict = {}
    for rec in records:
        for s in rec.get("sectors") or []:
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
        base, last = bars[i - n][1], bars[i][1]
        if _finite(base) and _finite(last) and base > 0:
            g = (last / base - 1) * 100
            if math.isfinite(g):
                out.append(round(g, 2))
    return out


def build_view(records: list[dict], date: str) -> dict | None:
    """从升序历史快照现算当日视图（分位/涨幅/双高）。末行必须是目标日。"""
    if not records or records[-1]["date"] != date:
        return None
    today = records[-1]
    series = _series_by_code(records)
    sectors, double_high = [], []
    for s in today.get("sectors") or []:
        key = (s.get("level"), s.get("code"))
        ent = series.get(key, {"bars": [], "shares": []})
        row = dict(s)
        # 分位的 history 剔除当日值（当日有值时恒为序列末元素）
        hist_shares = ent["shares"][:-1] if _finite(s.get("share_pct")) else ent["shares"]
        row["share_pctile"] = rolling_percentile(hist_shares, s.get("share_pct"))
        for n in GAIN_WINDOWS:
            row[f"gain_{n}d"] = interval_gain(ent["bars"], n, date)
        gain_hist = _gain_history(ent["bars"], 20)
        row["gain_pctile_20d"] = rolling_percentile(gain_hist[:-1] if gain_hist else [],
                                                    row["gain_20d"])
        sectors.append(row)
        if (row["share_pctile"] is not None and row["share_pctile"] >= HIGH_PCTILE
                and row["gain_pctile_20d"] is not None and row["gain_pctile_20d"] >= HIGH_PCTILE):
            double_high.append(row)
    return {
        "date": date,
        "market_total_billion": today.get("market_total_billion"),
        "sectors": sectors,
        "double_high": double_high,
        "meta": today.get("meta"),
    }
