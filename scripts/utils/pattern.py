"""形态篇选股形态纯函数：多头排列 / MACD 零轴状态 / 5·13 均量线阳放阴缩节奏。

出处：`teacher_notes#444` 鞠磊《形态篇（第一节技术课程）· 牛市板块内选股形态标准》，
认知 `cog_3b32e660`。课程口径（板块优先前提下的个股四条件）：

1. 日线均线多头排列，短期至少 5/10/20/30/55 —— `ma_alignment`
2. MACD 零轴上方金叉，「不管是金叉还是运行，都要在零轴上方」 —— `macd_state`
3. 上涨阳线量站上 5/13 日均量线、回调阴线缩量，成组重复 —— `volume_ma_rhythm`
4. 尚未加速 —— **不在本模块**，复用 `services.trend_leader.detectors.accel_threshold`
   （加速口径 board-aware，已是单一真源，复制一份必然漂移）

「月线位置要低」同样不在本模块：课程那条是肉眼判断（是否触及历史两个高点连线），
机械化必然误判；位置由 `utils.ma_position`（5/144/233）与
`services.board_break.indicators.position_250d` 表达，且只作展示不作门槛。

本模块只输出算术事实（[事实]）；「形态干净 / 容易起爆」的解读由渲染层标 [判断]。
纯函数，供 pattern-scan 与 trend-leader / tail-scan / board-break / ma-breakout 候选卡复用。
closes 应为**前复权**序列（`utils.qfq.apply_qfq`）：未复权跨除权会把均线方向标反。
样本不足一律返回 None 不硬算（沿用 `ma_position` / `position_250d` 契约）。
"""
from __future__ import annotations

import math

# 课程原话「至少 5、10、20、30、55 这几根基本上要多头排列」。
MA_ALIGNMENT_WINDOWS = (5, 10, 20, 30, 55)

# MACD 参数与 warm-up 门槛与 board_break 同源（`MIN_BARS_INDICATOR`）：
# board_break.macd_dif 已下沉为本模块薄封装，两处若分别持有阈值必然漂移。
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MIN_BARS_MACD = 120

# 课程原话「你跟我一样去设置一下 5 和 13 均量线」——区别于 ma_breakout 的成交额 5/10 口径。
VOLUME_MA_WINDOWS = (5, 13)
DEFAULT_RHYTHM_LOOKBACK = 20


def _finite(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ma(values: list[float | None], n: int, end: int | None = None) -> float | None:
    """末端 end（不含）向前 n 个值的均值；越界或含非有限值 → None。"""
    end = len(values) if end is None else end
    start = end - n
    if n <= 0 or start < 0 or end > len(values):
        return None
    window = values[start:end]
    if any(v is None for v in window):
        return None
    return sum(window) / n


def ma_alignment(closes: list, windows: tuple = MA_ALIGNMENT_WINDOWS) -> dict:
    """均线多头排列：MA(w1) > MA(w2) > ... 严格递减。

    返回 `{"aligned": bool|None, "values": {"ma5": float|None, ...},
            "broken_at": "maA>maB"|None, "state": "ok"|"insufficient"}`。

    严格大于是**设计选择**（与 `ma_position` 的「站上」同口径）：均线恰好相等
    （全平盘、长期停牌补同值）并未形成排列，判 False 属保守方向。
    任一窗口样本不足 → `aligned=None` 且 `state="insufficient"`，**不得退化成 False**——
    「未知」和「已确认不排列」在筛选里是两种结论，折叠会让次新股被误报为形态破坏。
    短窗口的 value 仍照常给出，供渲染层展示已知部分。
    """
    vals = [_finite(c) for c in (closes or [])]
    ordered = tuple(windows or ())
    values: dict[str, float | None] = {}
    for n in ordered:
        values[f"ma{n}"] = _ma(vals, n)

    if not ordered or any(values[f"ma{n}"] is None for n in ordered):
        return {"aligned": None, "values": values, "broken_at": None, "state": "insufficient"}

    for shorter, longer in zip(ordered, ordered[1:]):
        if not values[f"ma{shorter}"] > values[f"ma{longer}"]:
            return {
                "aligned": False,
                "values": values,
                "broken_at": f"ma{shorter}>ma{longer}",
                "state": "ok",
            }
    return {"aligned": True, "values": values, "broken_at": None, "state": "ok"}


def _ema_series(values: list[float], n: int) -> list[float]:
    """EMA 全序列，seed=首个值（对齐 pandas `adjust=False`，与 board_break 历史口径一致）。"""
    k = 2.0 / (n + 1)
    out = [values[0]]
    for px in values[1:]:
        out.append(out[-1] * (1 - k) + px * k)
    return out


def macd_state(closes: list) -> dict:
    """MACD(12,26,9) 末根状态：DIF / DEA / 柱 / 零轴位置 / 当日金叉。

    返回 `{"dif","dea","hist","above_zero","golden_cross","zero_axis_bullish","state"}`。

    - `above_zero`：DIF 与 DEA **同时** > 0。只看 DIF 会把「DIF 刚翻正但 DEA 仍在零下」
      的纠结段算作零上，与课程「零下金叉难成主升浪」的本意相悖。
    - `golden_cross`：当日 DIF > DEA 且前一日 DIF <= DEA。
    - `zero_axis_bullish`：`above_zero and dif >= dea`——课程原话「不管是金叉还是运行，
      都要在零轴上方」，故**零上运行（非当日金叉）同样达标**，这是实际筛选用的那个字段。
    - 样本 < MIN_BARS_MACD 或序列含非有限值 → 全 None + `state="insufficient"`。
    """
    vals = [_finite(c) for c in (closes or [])]
    none_result = {
        "dif": None, "dea": None, "hist": None, "above_zero": None,
        "golden_cross": None, "zero_axis_bullish": None, "state": "insufficient",
    }
    if len(vals) < MIN_BARS_MACD or any(v is None for v in vals):
        return none_result

    ema_fast = _ema_series(vals, MACD_FAST)
    ema_slow = _ema_series(vals, MACD_SLOW)
    dif_series = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea_series = _ema_series(dif_series, MACD_SIGNAL)

    dif, dea = dif_series[-1], dea_series[-1]
    prev_dif, prev_dea = dif_series[-2], dea_series[-2]
    if not all(math.isfinite(v) for v in (dif, dea, prev_dif, prev_dea)):
        return none_result

    above_zero = dif > 0 and dea > 0
    golden_cross = dif > dea and prev_dif <= prev_dea
    return {
        "dif": dif,
        "dea": dea,
        "hist": (dif - dea) * 2,  # 通达信口径 MACD 柱 = 2×(DIF-DEA)
        "above_zero": above_zero,
        "golden_cross": golden_cross,
        "zero_axis_bullish": above_zero and dif >= dea,
        "state": "ok",
    }


def volume_ma_rhythm(
    bars: list[dict],
    windows: tuple = VOLUME_MA_WINDOWS,
    lookback: int = DEFAULT_RHYTHM_LOOKBACK,
) -> dict:
    """5/13 日均量线下的「阳线放量 / 阴线缩量」节奏统计。

    bar 需含 `open` / `close` / `vol`，按 trade_date **升序**，最后一根=最新。
    返回各计数与比率 + `groups`；样本不足 → 计数全 None + `state="insufficient"`。

    口径（课程）：
    - 阳线（close > open）「放量」= 当日量**同时**站上 5 日与 13 日均量线。
    - 阴线（close < open）「缩量」= 当日量 < 5 日均量线（课程只要求回调缩量，
      取更灵敏的短均量线；用 13 日会把温和回调也算成缩量）。
    - 平盘（close == open）既非阳也非阴，两边都不计——十字星不该被算作任一方向的证据。
    - `groups`：放量阳在前、缩量阴在后的完整「阳放→阴缩」组数，即课程说的「做了好几组」。
      只有缩量阴而无前置放量阳不成组。

    成交量口径用 `vol`（成交量）而非 `amount`（成交额），与课程一致；
    `ma_breakout` 的成交额 5/10 均线是**另一套口径**，故意不复用，改这里不影响那边。
    """
    bars = bars or []
    ordered = tuple(windows or ())
    max_window = max(ordered) if ordered else 0
    required = lookback + max_window - 1
    none_result = {
        "yang_total": None, "yang_above_count": None, "yang_above_ratio": None,
        "yin_total": None, "yin_shrink_count": None, "yin_shrink_ratio": None,
        "groups": None, "state": "insufficient",
    }
    if lookback <= 0 or not ordered or len(bars) < required:
        return none_result

    vols = [_finite(b.get("vol")) for b in bars]
    yang_total = yang_above = yin_total = yin_shrink = groups = 0
    pending_yang = False

    for idx in range(len(bars) - lookback, len(bars)):
        o, c, v = (_finite(bars[idx].get("open")), _finite(bars[idx].get("close")), vols[idx])
        if o is None or c is None or v is None:
            return none_result
        mas = [_ma(vols, n, idx) for n in ordered]  # 只用**当日之前**的量算均量线，避免自含
        if any(m is None for m in mas):
            return none_result

        if c > o:
            yang_total += 1
            if all(v > m for m in mas):
                yang_above += 1
                pending_yang = True
        elif c < o:
            yin_total += 1
            if v < mas[0]:
                yin_shrink += 1
                if pending_yang:
                    groups += 1
                    pending_yang = False
            else:
                # 放量阴线 = 节奏翻转，必须打断待成组的放量阳（门2 medium）：
                # 否则「放量阳→放量阴→缩量阴」会被计成一组完整节奏，把已经变脏的
                # 量价节奏报成「做了好几组」。不放量的阳线与平盘不打断——课程只要求
                # 「大部分阳线」站上均量线，且十字星不是方向性的翻转证据。
                pending_yang = False

    return {
        "yang_total": yang_total,
        "yang_above_count": yang_above,
        "yang_above_ratio": (yang_above / yang_total) if yang_total else None,
        "yin_total": yin_total,
        "yin_shrink_count": yin_shrink,
        "yin_shrink_ratio": (yin_shrink / yin_total) if yin_total else None,
        "groups": groups,
        "state": "ok",
    }
