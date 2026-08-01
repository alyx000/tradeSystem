"""形态篇四条件共振检测器（纯函数）。

bar 约定：`list[dict]` 按 trade_date **升序**，最后一根=目标日；
字段至少含 `open` / `close` / `vol` / `pct_chg`。
返回 `(matched, detail)`；`detail["reason"]` 供 scanner 做 rejects 计数。

四条件全部来自 `teacher_notes#444`，缺一不可（课程原话「要符合以下这些条件」）：
1. 均线多头排列 5/10/20/30/55
2. MACD 零轴上方金叉或零上运行
3. 上涨阳线量站上 5/13 均量线（大部分）且成组出现阳放阴缩
4. 尚未加速

closes 口径：调用方须传**前复权**序列。本模块不做复权，避免与 scanner 的取数职责纠缠。
"""
from __future__ import annotations

from services.pattern_scan import constants as C
from services.trend_leader.detectors import accel_threshold
from utils.pattern import ma_alignment, macd_state, volume_ma_rhythm


def is_accelerated(
    bars: list[dict], code: str, is_st: bool | None = None,
    lookback: int = C.ACCEL_LOOKBACK_BARS,
) -> tuple[bool, dict]:
    """近 lookback 根内是否出现过加速日（含今日）。

    加速阈值复用 `trend_leader.detectors.accel_threshold`（board-aware：主板涨停 /
    双创 15%+）——加速口径必须全项目单一真源，复制一份必然漂移。
    阈值为 None（无固定涨停品种，如 ETF）→ 无法判定，返回 False 并标注，
    不拦截也不冒充「已确认未加速」。
    """
    threshold = accel_threshold(code, is_st)
    if threshold is None:
        return False, {"accel_threshold": None, "undeterminable": True}
    window = (bars or [])[-lookback:]
    hits = [b.get("trade_date") for b in window
            if b.get("pct_chg") is not None and b.get("pct_chg") >= threshold]
    return bool(hits), {
        "accel_threshold": threshold,
        "accel_dates": hits,
        "lookback": len(window),
        "undeterminable": False,
    }


def match_pattern(
    bars: list[dict],
    code: str,
    *,
    target_date: str,
    is_st: bool | None = None,
) -> tuple[bool, dict]:
    """四条件共振。任一条件样本不足即 `insufficient_history`，不得当作「不满足」。"""
    if not bars:
        return False, {"reason": "insufficient_history", "insufficient_history": True}
    last_date = bars[-1].get("trade_date")
    if last_date != target_date:
        return False, {
            "reason": "stale_last_bar",
            "last_trade_date": last_date,
            "target_date": target_date,
        }

    closes = [b.get("close") for b in bars]
    alignment = ma_alignment(closes)
    macd = macd_state(closes)
    rhythm = volume_ma_rhythm(bars, lookback=C.RHYTHM_LOOKBACK)
    accelerated, accel_detail = is_accelerated(bars, code, is_st)

    detail = {
        "alignment": alignment,
        "macd": macd,
        "rhythm": rhythm,
        "accel": accel_detail,
        "accelerated": accelerated,
        "bar_count": len(bars),
        "insufficient_history": False,
    }

    # 样本不足优先于「不满足」——两者在筛选语义上不同，折叠会让次新股被误报为形态破坏。
    if alignment["state"] != "ok" or macd["state"] != "ok" or rhythm["state"] != "ok":
        detail["reason"] = "insufficient_history"
        detail["insufficient_history"] = True
        return False, detail

    if alignment["aligned"] is not True:
        detail["reason"] = "ma_not_aligned"
        return False, detail
    if macd["zero_axis_bullish"] is not True:
        detail["reason"] = "macd_not_zero_axis"
        return False, detail

    yang_ratio = rhythm["yang_above_ratio"]
    if yang_ratio is None or yang_ratio < C.MIN_YANG_ABOVE_RATIO:
        detail["reason"] = "yang_volume_weak"
        return False, detail
    if rhythm["groups"] < C.MIN_RHYTHM_GROUPS:
        detail["reason"] = "rhythm_groups_below_min"
        return False, detail

    # 加速判定放在最后：课程的「未加速」是**空间条件**，前三条形态条件都成立、
    # 只因已加速而落选的票，对用户是有意义的信息（已错过介入窗口），
    # reason 单列便于报告里与「形态本就不干净」区分。
    if accelerated:
        detail["reason"] = "already_accelerated"
        return False, detail

    detail["reason"] = "matched"
    return True, detail
