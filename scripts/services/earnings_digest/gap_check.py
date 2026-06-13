"""预告次日缺口验证：市场投票 2×2（口径一）。

「次日」= 预告后的**下一个交易日**：T 日（交易日）验证「下一交易日==T」的全部预告，
即 ann_date ∈ [上一交易日, T) 自然日区间——周五晚/周末/节假日公告统一在下一交易日
验证，不漏。候选名单从按日 payload 区间 union 读取（collector.read_payload_rows_between）。

已知简化：极少数盘前早间公告（首反应是当日）按主流盘后发布口径归入次日验证。
"""
from __future__ import annotations

from .normalize import NEGATIVE_TYPES, POSITIVE_TYPES, _to_float, normalize_forecast

DEFAULT_GAP_THRESHOLD_PCT = 2.0

# 市场投票 2×2：公告方向 × 缺口方向
VOTE_LABELS = {
    ("positive", "up"): "✅超预期确认",
    ("positive", "down"): "⚠️利好不及预期",
    ("negative", "up"): "💡利空出尽",
    ("negative", "down"): "❌暴雷确认",
}


def _direction_of_type(type_text: str) -> str | None:
    if type_text in POSITIVE_TYPES:
        return "positive"
    if type_text in NEGATIVE_TYPES:
        return "negative"
    return None


def _quote_map(rows: list[dict]) -> dict[str, dict]:
    return {str(r.get("ts_code") or ""): r for r in rows if r.get("ts_code")}


def eligible_window_rows(
    candidate_rows: list[dict], prev_trade_date: str, target_date: str
) -> list[dict]:
    """落在缺口验证窗口 ann_date ∈ [prev_trade, target) 的候选行（次日验证语义单一真源）。

    candidate_rows 按 biz_date 区间读取，含 target 当天 ann（不该今日验证）；判定「本期
    是否有应验证候选」与 check_gaps 取数必须共用此窗口规则，否则 service 用全集判 gap_error
    会在只有当天新公告时误报「缺口验证本期缺席」（codex review）。
    """
    lo = prev_trade_date.replace("-", "")
    hi = target_date.replace("-", "")
    return [
        row for row in candidate_rows
        if lo <= str(row.get("ann_date") or "").replace("-", "") < hi
    ]


def check_gaps(
    candidate_rows: list[dict],
    today_quotes: list[dict],
    prev_quotes: list[dict],
    *,
    prev_trade_date: str,
    target_date: str,
    threshold_pct: float = DEFAULT_GAP_THRESHOLD_PCT,
) -> list[dict]:
    """候选预告 × 当日行情 → 跳空命中列表（按 |缺口| 降序）。

    :param candidate_rows: raw forecast rows（含多版本，内部 normalize 取当前版本）
    :param today_quotes: T 日全市场 OHLC+pre_close（get_market_daily_quotes）
    :param prev_quotes: 上一交易日全市场行情（严格缺口判定需昨日 high/low）
    :param prev_trade_date / target_date: YYYY-MM-DD；候选窗口 [prev_trade, T)
    """
    window_rows = eligible_window_rows(candidate_rows, prev_trade_date, target_date)
    items = normalize_forecast(window_rows)
    today_map = _quote_map(today_quotes)
    prev_map = _quote_map(prev_quotes)

    hits: list[dict] = []
    for item in items:
        direction = _direction_of_type(item["type"])
        if direction is None:
            continue
        quote = today_map.get(item["ts_code"])
        if not quote:
            continue  # 停牌/无行情跳过
        open_px = _to_float(quote.get("open"))
        pre_close = _to_float(quote.get("pre_close"))
        # A 股价格恒 >0；<=0 即脏数据（兼防除零与负价产生的荒唐缺口），跳过
        if open_px is None or pre_close is None or open_px <= 0 or pre_close <= 0:
            continue
        gap_pct = (open_px - pre_close) / pre_close * 100.0
        # epsilon 防浮点误差吞掉恰好达线的缺口（实测 (10.2-10.0)/10*100 = 1.9999...）
        if abs(gap_pct) < threshold_pct - 1e-9:
            continue
        gap_direction = "up" if gap_pct > 0 else "down"

        # 严格缺口：今低 > 昨高（向上）/ 今高 < 昨低（向下）——更强信号
        prev_quote = prev_map.get(item["ts_code"]) or {}
        prev_high = _to_float(prev_quote.get("high"))
        prev_low = _to_float(prev_quote.get("low"))
        today_high = _to_float(quote.get("high"))
        today_low = _to_float(quote.get("low"))
        strict = False
        if gap_direction == "up" and today_low is not None and prev_high is not None:
            strict = today_low > prev_high
        elif gap_direction == "down" and today_high is not None and prev_low is not None:
            strict = today_high < prev_low

        # 一字板：全天单一价（容差防浮点表示差异；行情价两位小数，1e-9 远小于最小价位）
        one_word = (
            today_high is not None
            and today_low is not None
            and abs(today_high - today_low) <= 1e-9
        )

        hits.append({
            **item,
            "gap_pct": round(gap_pct, 2),
            "gap_direction": gap_direction,
            "vote_label": VOTE_LABELS[(direction, gap_direction)],
            "strict_gap": strict,
            "one_word_board": one_word,
        })

    hits.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
    return hits
