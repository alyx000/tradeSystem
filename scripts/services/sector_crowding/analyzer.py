"""拥挤度纯函数：占比/区间涨幅/滚动分位/双高信号。全部无 IO，历史序列由调用方传入。"""
from __future__ import annotations

import math
from decimal import Decimal

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

# 每日板块标签（申万 L2）：
# - 半年线/年线沿用交易体系的 144/233 个交易快照斐波那契均线代理；
# - 价量共振是「存在性」标签：最近 10 个交易快照日内，close 与 amount 同日严格突破
#   此前 20 个交易快照日高点。严格大于可避免横盘等值被误记为新高。
HALF_YEAR_MA_WINDOW = 144
YEAR_MA_WINDOW = 233
RESONANCE_LOOKBACK_DAYS = 10
RESONANCE_BREAKOUT_WINDOW = 20
RESONANCE_RULE = "close_and_amount_strictly_above_prior_window_highs"
LABEL_HISTORY_DAYS = max(
    HALF_YEAR_MA_WINDOW,
    YEAR_MA_WINDOW,
    RESONANCE_BREAKOUT_WINDOW + RESONANCE_LOOKBACK_DAYS,
)


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


def sector_label_definitions() -> dict:
    """板块标签公开口径。API、报告与测试共用，避免文案和计算参数漂移。"""
    return {
        "half_year_ma_window": HALF_YEAR_MA_WINDOW,
        "year_ma_window": YEAR_MA_WINDOW,
        "resonance_lookback_days": RESONANCE_LOOKBACK_DAYS,
        "resonance_breakout_window": RESONANCE_BREAKOUT_WINDOW,
        "resonance_rule": RESONANCE_RULE,
        "window_unit": "trading_snapshot_days",
    }


def empty_sector_labels(date: str, status: str = "missing_snapshot") -> dict:
    """标签 API 空壳；缺快照与快照内无 L2 用 status 区分。"""
    return {
        "date": date,
        "available": False,
        "status": status,
        "definitions": sector_label_definitions(),
        "summary": {
            "total_l2": 0,
            "missing_l2_count": 0,
            "above_half_year_ma": 0,
            "above_year_ma": 0,
            "recent_resonance": 0,
            "year_and_resonance": 0,
            "half_year_ma_insufficient": 0,
            "year_ma_insufficient": 0,
            "resonance_insufficient": 0,
        },
        "items": [],
    }


def _label_series_by_code(records: list[dict]) -> dict:
    """{L2 code: {close_by_date, amount_by_date}}，只保留有限数值。"""
    out: dict = {}
    for rec in records:
        rec_date = rec["date"]
        for sector in _dedup_sectors(rec.get("sectors")):
            code = sector.get("code")
            if sector.get("level") != "L2" or not code:
                continue
            entry = out.setdefault(code, {"close_by_date": {}, "amount_by_date": {}})
            if _finite(sector.get("close")):
                entry["close_by_date"][rec_date] = float(sector["close"])
            if _finite(sector.get("amount_billion")):
                entry["amount_by_date"][rec_date] = float(sector["amount_billion"])
    return out


def _l2_by_code(record: dict) -> dict:
    """单快照 L2 code→行；重复码沿用全模块的末条修正语义。"""
    return {
        sector.get("code"): sector
        for sector in _dedup_sectors(record.get("sectors"))
        if sector.get("level") == "L2" and sector.get("code")
    }


def _trusted_expected_l2_codes(
    record: dict,
    observed_codes: set[str],
) -> set[str] | None:
    """读取目标日可信 as-of L2 宇宙；元数据与实际快照不一致即拒绝信任。"""
    meta = record.get("meta")
    if not isinstance(meta, dict):
        return None
    raw_codes = meta.get("l2_expected_codes")
    if meta.get("l2_universe_complete") is not True or not isinstance(raw_codes, list):
        return None
    normalized_codes: list[str] = []
    for code in raw_codes:
        if not isinstance(code, str) or not code or code != code.strip():
            return None
        normalized_codes.append(code)
    if len(set(normalized_codes)) != len(normalized_codes):
        return None
    codes = set(normalized_codes)
    expected_count = meta.get("l2_expected_count")
    observed_count = meta.get("l2_observed_count")
    if (
        not codes
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or not isinstance(observed_count, int)
        or isinstance(observed_count, bool)
        or expected_count != len(codes)
        or observed_count != len(observed_codes)
        or expected_count != observed_count
        or codes != observed_codes
    ):
        return None
    return codes


def _fallback_l2_universe(
    prior_records: list[dict],
) -> tuple[set[str], dict[str, dict]]:
    """遗留快照的保守 L2 宇宙与最近可见行。

    优先继承最近一个带可信完整元数据的快照，避免合法换码后的更早历史代码重新混入。
    若整个窗口都没有可信元数据，只能取历史观察并集；这可能多报已退役代码为缺失，但不会
    因连续部分返回而逐日收缩宇宙、把来源故障误报成 ``success``。
    """
    rows_by_record: list[tuple[dict, dict[str, dict]]] = []
    latest_l2_by_code: dict[str, dict] = {}
    for record in prior_records:
        l2_by_code = _l2_by_code(record)
        rows_by_record.append((record, l2_by_code))
        latest_l2_by_code.update(l2_by_code)

    for record, l2_by_code in reversed(rows_by_record):
        trusted = _trusted_expected_l2_codes(record, set(l2_by_code))
        if trusted is not None:
            return set(trusted), latest_l2_by_code
    return set(latest_l2_by_code), latest_l2_by_code


def _ma_position_label(
    close_by_date: dict,
    snapshot_dates: list[str],
    window: int,
) -> tuple[float | None, bool | None]:
    window_dates = snapshot_dates[-window:]
    if len(window_dates) < window:
        return None, None
    closes = [close_by_date.get(d) for d in window_dates]
    if not all(_finite(v) for v in closes):
        return None, None
    # 价格以十进制文本语义比较。二进制浮点求和会把 144 个 1000.05 的均值算成
    # 1000.0499999999998，从而把“等于均线”误标为严格在线上。
    decimal_closes = [Decimal(str(value)) for value in closes]
    ma_decimal = sum(decimal_closes, Decimal(0)) / Decimal(window)
    # 返回值转回 JSON 可序列化 float；严格关系已经在 Decimal 域内确定。
    return float(ma_decimal), bool(decimal_closes[-1] > ma_decimal)


def _recent_resonance_label(
    close_by_date: dict,
    amount_by_date: dict,
    snapshot_dates: list[str],
) -> tuple[bool | None, dict | None, int | None]:
    """最近价量共振标签 + 最近一次证据 + 距目标日交易快照数。

    无事件时，只有最近 10 个交易快照日每一天都具备「当日 + 此前 20 个快照日」完整
    价量数据才返回 False；否则返回 None，避免把数据不足伪装成未发生。只要找到事件即可
    返回 True。
    """
    recent_dates = snapshot_dates[-RESONANCE_LOOKBACK_DAYS:]
    fully_evaluable = len(recent_dates) == RESONANCE_LOOKBACK_DAYS
    index_by_date = {d: i for i, d in enumerate(snapshot_dates)}
    events: list[dict] = []

    for candidate_date in recent_dates:
        i = index_by_date[candidate_date]
        if i < RESONANCE_BREAKOUT_WINDOW:
            fully_evaluable = False
            continue
        prior_dates = snapshot_dates[i - RESONANCE_BREAKOUT_WINDOW:i]
        close = close_by_date.get(candidate_date)
        amount = amount_by_date.get(candidate_date)
        prior_closes = [close_by_date.get(d) for d in prior_dates]
        prior_amounts = [amount_by_date.get(d) for d in prior_dates]
        if not (
            _finite(close)
            and _finite(amount)
            and all(_finite(v) for v in prior_closes)
            and all(_finite(v) for v in prior_amounts)
        ):
            fully_evaluable = False
            continue
        prior_close_high = max(prior_closes)
        prior_amount_high = max(prior_amounts)
        if close > prior_close_high and amount > prior_amount_high:
            events.append({
                "date": candidate_date,
                # 保留原始比较精度；展示层自行做最多 8 位的可读格式化。
                "close": close,
                "prior_close_high": prior_close_high,
                "amount_billion": amount,
                "prior_amount_high_billion": prior_amount_high,
            })

    if events:
        latest = events[-1]
        age = len(snapshot_dates) - 1 - index_by_date[latest["date"]]
        return True, latest, age
    return (False if fully_evaluable else None), None, None


def build_sector_labels(records: list[dict], date: str) -> dict | None:
    """从目标日及其历史快照现算申万 L2 每日标签；不落派生值。

    records 必须按交易快照日升序且末行等于目标日。窗口严格按快照序列对齐；单板块缺任一
    快照日的 close/amount 时返回数据不足，而不是用更早有效观察向前补齐。
    """
    if not records or records[-1]["date"] != date:
        return None
    snapshot_dates = [rec["date"] for rec in records]
    today_record = records[-1]
    today_l2_by_code = _l2_by_code(today_record)

    # 新采集/回填写入可信 as-of 宇宙，允许合法新增、退出和换码；遗留/手工快照
    # 无可信元数据时继承最近可信宇宙；全程无可信元数据才取历史观察并集。不能只用
    # 上一快照，否则连续缺同一码时第二天会把残缺清单误报为 success。
    trusted_expected_codes = _trusted_expected_l2_codes(
        today_record,
        set(today_l2_by_code),
    )
    fallback_expected_codes, latest_prior_l2_by_code = _fallback_l2_universe(
        records[:-1]
    )
    expected_codes = (
        set(trusted_expected_codes)
        if trusted_expected_codes is not None
        else fallback_expected_codes
    )
    expected_codes.update(today_l2_by_code)
    if not expected_codes:
        return empty_sector_labels(date, status="missing_l2")

    missing_expected_codes = sorted(expected_codes - set(today_l2_by_code))
    label_universe = dict(today_l2_by_code)
    for code in missing_expected_codes:
        label_universe[code] = latest_prior_l2_by_code.get(
            code,
            {"code": code, "name": code, "level": "L2"},
        )

    series = _label_series_by_code(records)
    items = []
    for code, sector in label_universe.items():
        present_on_target = code in today_l2_by_code
        entry = series.get(code, {"close_by_date": {}, "amount_by_date": {}})
        if present_on_target:
            half_year_ma, above_half_year_ma = _ma_position_label(
                entry["close_by_date"], snapshot_dates, HALF_YEAR_MA_WINDOW
            )
            year_ma, above_year_ma = _ma_position_label(
                entry["close_by_date"], snapshot_dates, YEAR_MA_WINDOW
            )
            recent_resonance, last_resonance, resonance_age = _recent_resonance_label(
                entry["close_by_date"], entry["amount_by_date"], snapshot_dates
            )
        else:
            half_year_ma = above_half_year_ma = None
            year_ma = above_year_ma = None
            recent_resonance = last_resonance = resonance_age = None
        items.append({
            "code": code,
            "name": sector.get("name") or code,
            "present_on_target": present_on_target,
            "close": (
                sector.get("close")
                if present_on_target and _finite(sector.get("close"))
                else None
            ),
            "amount_billion": (
                sector.get("amount_billion")
                if present_on_target and _finite(sector.get("amount_billion"))
                else None
            ),
            "half_year_ma": half_year_ma,
            "above_half_year_ma": above_half_year_ma,
            "year_ma": year_ma,
            "above_year_ma": above_year_ma,
            "recent_price_volume_resonance": recent_resonance,
            "last_resonance": last_resonance,
            "resonance_age_snapshot_days": resonance_age,
        })

    # 年线+共振优先，其次近期共振、年线上、半年线上；同组按中文名稳定排序。
    items.sort(key=lambda item: (
        not bool(item["above_year_ma"] and item["recent_price_volume_resonance"]),
        not bool(item["recent_price_volume_resonance"]),
        not bool(item["above_year_ma"]),
        not bool(item["above_half_year_ma"]),
        str(item["name"]),
    ))
    summary = {
        "total_l2": len(items),
        "missing_l2_count": len(missing_expected_codes),
        "above_half_year_ma": sum(item["above_half_year_ma"] is True for item in items),
        "above_year_ma": sum(item["above_year_ma"] is True for item in items),
        "recent_resonance": sum(
            item["recent_price_volume_resonance"] is True for item in items
        ),
        "year_and_resonance": sum(
            item["above_year_ma"] is True
            and item["recent_price_volume_resonance"] is True
            for item in items
        ),
        "half_year_ma_insufficient": sum(
            item["above_half_year_ma"] is None for item in items
        ),
        "year_ma_insufficient": sum(item["above_year_ma"] is None for item in items),
        "resonance_insufficient": sum(
            item["recent_price_volume_resonance"] is None for item in items
        ),
    }
    return {
        "date": date,
        "available": bool(today_l2_by_code),
        "status": (
            "missing_l2"
            if not today_l2_by_code
            else ("partial" if missing_expected_codes else "success")
        ),
        "definitions": sector_label_definitions(),
        "summary": summary,
        "items": items,
    }


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
        # 分位仍使用完整历史；每日标签只需要自身最长窗口，避免报告路径重复扫描约 1900 日。
        "sector_labels": build_sector_labels(records[-LABEL_HISTORY_DAYS:], date),
        "meta": today.get("meta"),
    }
