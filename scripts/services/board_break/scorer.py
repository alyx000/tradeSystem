"""断板反包打分层（方式一：加权规则打分）：公告分类器 + 事实卡 + 八维度加权打分。

层次划分：
- `classify_announcement`：单条公告标题 → 类别（否定/减持/增持/定增/业绩/利好/利空/中性）。
- `build_fact_card` / `build_fact_cards`：把筛选层候选 + 各数据源结果聚合成单票 [事实] 卡
  （不做打分，只做取数/分类/指标计算的编排；单维度取数失败只降级该维度，不中断整批）。
- `score_candidate` / `score_all`：纯函数，只读 fact_card 字段打分，产出结构化 evidence
  （status ∈ ok/no_event/neutral/missing/source_failed，detail 永不为空）。

业绩接口实际选择（见 task-s2-report.md 详述）：`get_earnings_forecast/express(date)`
内建 lookback 窗口默认仅 3 自然日（服务 earnings-digest 场景），非本维度所需的 90 自然日。
复用 `earnings_digest` CLI 同款「调用前临时覆盖 `EARNINGS_LOOKBACK_DAYS` env、finally 还原」
模式把窗口扩到 `ANN_WINDOW_DAYS=90`，全市场只调一次（不逐候选重复调用），
不新增 provider 方法、不改其默认行为、不影响其他消费方。
"""
from __future__ import annotations

import os

from services.board_break import constants as C
from services.board_break.indicators import apply_qfq, gain_10d, macd_dif, position_250d
from services.board_break.scanner import bare_code, window_start
from services.earnings_digest.normalize import (
    NEGATIVE_TYPES,
    POSITIVE_TYPES,
    normalize_express,
    normalize_forecast,
)

_ANN_EVENT_KEYS = ("increase", "placement", "reduce", "good", "bad")


# ---------------------------------------------------------------------------
# 公告分类器
# ---------------------------------------------------------------------------

def classify_announcement(title: str) -> str:
    """标题 → 类别，优先级：排除词→否定>减持>增持/回购>定增>业绩>利好>利空>中性。"""
    text = title or ""
    if any(kw in text for kw in C.KW_EXCLUDE):
        return "neutral"
    if any(kw in text for kw in C.KW_NEGATE):
        return "negate"
    if any(kw in text for kw in C.KW_REDUCE):
        return "reduce"
    if any(kw in text for kw in C.KW_INCREASE):
        return "increase"
    if any(kw in text for kw in C.KW_PLACEMENT):
        return "placement"
    if any(kw in text for kw in C.KW_EARNINGS):
        return "earnings"
    if any(kw in text for kw in C.KW_GOOD):
        return "good"
    if any(kw in text for kw in C.KW_BAD):
        return "bad"
    return "neutral"


# ---------------------------------------------------------------------------
# 事实卡构建
# ---------------------------------------------------------------------------

def _holder_event_title(row: dict, direction: str) -> str:
    """增减持事件标题：「{股东} 增持/减持 {N万股}」——原始拼接 DE6375000股 不可读（真跑校准修）。"""
    name = str(row.get("holder_name") or "").strip() or "未知股东"
    action = "增持" if direction == "increase" else "减持"
    vol = row.get("change_vol")
    try:
        vol_txt = f"{float(vol) / 10000:.1f}万股" if vol is not None else "股数未知"
    except (TypeError, ValueError):
        vol_txt = "股数未知"
    return f"{name} {action} {vol_txt}"


def _norm_date(value) -> str:
    """事件日期归一为 `YYYY-MM-DD`：兼容 tushare `holder_trade` 的 `YYYYMMDD`
    与 akshare 公告的 `YYYY-MM-DD` 两种输入格式；空值原样返回空串。
    """
    # 显式区分 None 与假值 0：`value or ""` 会把数字 0（合法值，非"空"）误当空串，
    # 这里只对 None 归一为空串，其余一律 str() 后再 strip。
    s = ("" if value is None else str(value)).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _result_rows(result) -> tuple[list, bool]:
    """从 DataResult-like 对象取 (rows, ok)；None 或失败均判失败（调用方永远传 DataResult
    或 None，不存在裸 list 入参，故不再区分"success 缺失即视为裸 list 成功"分支）。
    """
    if result is None or not getattr(result, "success", False):
        return [], False
    data = getattr(result, "data", None)
    return (data if isinstance(data, list) else []), True


def _select_earnings(rows: list[dict]) -> tuple[str, dict] | None:
    """同报告期 express 优先于 forecast；跨报告期取 ann_date 最近一期。"""
    combined: dict[str, dict] = {}
    for row in rows:
        end_date = row.get("end_date", "")
        combined.setdefault(end_date, {})[row.get("kind")] = row
    if not combined:
        return None
    best_kind, best_row, best_ann = None, None, ""
    for kinds in combined.values():
        kind = "express" if "express" in kinds else "forecast"
        row = kinds[kind]
        ann = row.get("ann_date") or ""
        if best_row is None or ann >= best_ann:
            best_kind, best_row, best_ann = kind, row, ann
    return (best_kind, best_row) if best_row is not None else None


def _earnings_direction_and_label(kind: str, row: dict) -> tuple[str | None, str | None]:
    """业绩方向 + 展示标签一并推导（forecast/express 两路 kind 分发只做一次，
    避免 direction/label 各自重复分发导致口径漂移）。"""
    if kind == "forecast":
        t = row.get("type") or ""
        direction = "good" if t in POSITIVE_TYPES else ("bad" if t in NEGATIVE_TYPES else None)
        return direction, (t or None)
    # express 无离散 type，用归母净利同比符号判方向
    yoy = row.get("yoy_dedu_np")
    if yoy is None:
        return None, "快报（同比未知）"
    direction = "good" if yoy > 0 else ("bad" if yoy < 0 else None)
    return direction, f"快报归母净利同比{yoy:+.1f}%"


def build_fact_card(
    cand: dict, *, main_sectors, ann_result, holder_result, earnings_rows, adj_factors,
    main_sector_degraded: bool = False,
) -> dict:
    """单票聚合 [事实]：主线板块归属 + 公告分类 + 增减持事件 + 业绩类型 + 前复权指标。"""
    code = cand.get("code", "")
    bars = cand.get("bars") or []
    t_date = cand.get("date")

    # —— 主线板块 ——（main_sectors=None 视为该维度整体缺失；空 set 视为"已知但不在池"）。
    # main_sector_degraded=True 时回退集合仍照常参与打分（对齐 trend_leader「回退值照用+
    # 显式标注」口径），不再拿 None 整体抹掉该维度——只有回退集合本身也为空才判缺失。
    main_sector_status = "missing" if main_sectors is None else "ok"
    in_main_sector = bool(main_sectors) and cand.get("industry") in main_sectors

    # —— 公告分类 ——
    ann_rows, ann_ok = _result_rows(ann_result)
    ann_status = "ok" if ann_ok else "source_failed"
    ann_events: dict[str, list] = {k: [] for k in _ANN_EVENT_KEYS}
    ann_titles: list[str] = []
    if ann_ok:
        for row in ann_rows:
            title = row.get("title", "")
            category = classify_announcement(title)
            if category in ann_events:
                ann_events[category].append({"date": _norm_date(row.get("ann_date", "")), "title": title})
            if title:
                ann_titles.append(title)

    # —— 增减持（holder_result 优先，精确到方向+数量；不可用回退公告分类结果）——
    holder_rows, holder_ok = _result_rows(holder_result)
    if holder_ok:
        holder_status = "ok"
        holder_source = "stk_holdertrade"
        ann_events["increase"] = []
        ann_events["reduce"] = []
        for row in holder_rows:
            in_de = str(row.get("in_de") or "").upper()
            direction = "increase" if in_de == "IN" else ("reduce" if in_de == "DE" else None)
            if direction:
                ann_events[direction].append({
                    "date": _norm_date(row.get("ann_date", "")),
                    # 可读性（Task 4.3 真跑校准）：方向码翻译+股数万股化,依据须人可核对
                    "title": _holder_event_title(row, direction),
                })
    else:
        holder_status = ann_status  # 回退公告分类：状态跟随公告数据源
        holder_source = "announcement"

    ann_titles = [t[: C.FACT_CARD_ANN_CHARS] for t in ann_titles[: C.FACT_CARD_ANN_MAX]]

    # —— 业绩 ——（earnings_rows=None 表示全市场业绩窗口取数整体失败）
    earnings_direction = None
    if earnings_rows is None:
        earnings_status = "source_failed"
        earnings_type = None
    else:
        rows_for_code = [r for r in earnings_rows if bare_code(r.get("ts_code")) == code]
        selected = _select_earnings(rows_for_code)
        if selected is None:
            earnings_status = "no_event"
            earnings_type = None
        else:
            kind, row = selected
            earnings_direction, earnings_type = _earnings_direction_and_label(kind, row)
            earnings_status = "ok" if earnings_direction else "neutral"

    # —— 前复权 + 指标（复权因子失败 → gain10/MACD/position 三维度整体缺失；
    # apply_qfq 自身已守卫 `not bars or not factors` 返回 None，外层无需重复判断）——
    adjusted = apply_qfq(bars, adj_factors)
    if adjusted is None:
        gain10, gain10_status = None, "missing"
        dif, dif_status = None, "missing"
        position = {"value": None, "state": "missing", "bar_count": len(bars)}
    else:
        closes = [b.get("close") for b in adjusted]
        gain10 = gain_10d(closes)
        gain10_status = "missing" if gain10 is None else "ok"

        if t_date and adjusted[-1].get("trade_date") != t_date:
            dif, dif_status = None, "missing"  # 末根非 T 日：口径不可信，不硬算
        else:
            dif = macd_dif(closes)
            dif_status = "missing" if dif is None else "ok"

        position = position_250d(adjusted)

    return {
        "code": code,
        "name": cand.get("name", ""),
        "limit_times": cand.get("limit_times"),
        "pct_chg": cand.get("pct_chg"),
        "close": cand.get("close"),
        "ref_price": cand.get("ref_price"),
        "industry": cand.get("industry", ""),
        "in_main_sector": in_main_sector,
        "main_sector_status": main_sector_status,
        "main_sector_degraded": main_sector_degraded,
        "ann_status": ann_status,
        "ann_events": ann_events,
        "ann_titles": ann_titles,
        "holder_status": holder_status,
        "holder_source": holder_source,
        "earnings_status": earnings_status,
        "earnings_type": earnings_type,
        "earnings_direction": earnings_direction,
        "gain10": gain10,
        "gain10_status": gain10_status,
        "dif": dif,
        "dif_status": dif_status,
        "position_value": position["value"],
        "position_state": position["state"],
        "position_bar_count": position["bar_count"],
    }


def _fetch_earnings_rows(registry, date: str) -> list[dict] | None:
    """全市场业绩窗口取数：临时覆盖 `EARNINGS_LOOKBACK_DAYS` 到 90 自然日，调用后照常还原。"""
    prev_env = os.environ.get("EARNINGS_LOOKBACK_DAYS")
    os.environ["EARNINGS_LOOKBACK_DAYS"] = str(C.EARNINGS_WINDOW_DAYS)
    try:
        forecast_result = registry.call("get_earnings_forecast", date)
        express_result = registry.call("get_earnings_express", date)
    finally:
        if prev_env is None:
            os.environ.pop("EARNINGS_LOOKBACK_DAYS", None)
        else:
            os.environ["EARNINGS_LOOKBACK_DAYS"] = prev_env

    forecast_ok = getattr(forecast_result, "success", False)
    express_ok = getattr(express_result, "success", False)
    if not forecast_ok and not express_ok:
        return None  # 两路全失败 → 业绩维度整体 source_failed

    forecast_items = normalize_forecast(forecast_result.data or []) if forecast_ok else []
    express_items = normalize_express(express_result.data or [] if express_ok else [], forecast_items)

    rows = [{**item, "kind": "forecast"} for item in forecast_items]
    rows += [{**item, "kind": "express"} for item in express_items]
    return rows


def build_fact_cards(conn, registry, result: dict) -> list[dict]:
    """编排入口：读主线板块（已随 scanner 结果附带）+ 逐票调公告/增减持/复权因子接口 +
    全市场业绩窗口一次性取数，循环 `build_fact_card`。单票单维度取数失败只降级该维度，
    不中断整批（不合格候选仍会产出 fact_card，只是相应维度标 missing/source_failed）。
    """
    date = result.get("date")
    candidates = result.get("candidates") or []
    # main_sector_degraded=True（当日集中度快照缺失、回退最近一日）时，回退集合仍照常
    # 参与打分——与 trend_leader 既有口径一致（回退值照用+显式标注），而非拿 None 把
    # 该维度整体抹成缺失。仅当回退集合本身也是空的，才无值可用、真正判缺失。
    main_sector_degraded = bool(result.get("main_sector_degraded"))
    main_sectors = set(result.get("main_sectors") or [])
    if main_sector_degraded and not main_sectors:
        main_sectors = None

    ann_window_start = window_start(date, C.ANNOUNCE_WINDOW_DAYS)
    adj_window_start = window_start(date, C.LOOKBACK_NATURAL_DAYS)
    earnings_rows = _fetch_earnings_rows(registry, date)

    cards = []
    for cand in candidates:
        code = cand["code"]
        ann_result = registry.call("get_stock_announcements", code, ann_window_start, date)
        holder_result = registry.call("get_holder_trade", code, ann_window_start, date)
        adj_result = registry.call("get_stock_adj_factor_range", code, adj_window_start, date)
        adj_factors = adj_result.data if getattr(adj_result, "success", False) else None
        card = build_fact_card(
            {**cand, "date": date},
            main_sectors=main_sectors,
            ann_result=ann_result,
            holder_result=holder_result,
            earnings_rows=earnings_rows,
            adj_factors=adj_factors,
            main_sector_degraded=main_sector_degraded,
        )
        cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# 打分（纯函数：只读 fact_card 字段，八维度加权求和 + 结构化 evidence）
# ---------------------------------------------------------------------------

def _format_event_detail(events: list, limit: int = 3) -> str:
    """事件明细拼接：「MM-DD《标题》」逐条 join（D13 spec 格式）。

    `date` 已在 `build_fact_card` 归一为 `YYYY-MM-DD`（见 `_norm_date`），
    这里只做定长切片取 `MM-DD`；若日期格式异常（非归一后的定长串）则原样保留，不硬切。
    """
    parts = []
    for e in events[:limit]:
        date = e.get("date") or ""
        mmdd = date[5:] if len(date) == 10 and date[4] == "-" else date
        title = e.get("title", "")
        parts.append(f"{mmdd}《{title}》" if mmdd else f"《{title}》")
    return "；".join(parts)


def _evidence(dimension, score, status, source, window, value, detail) -> dict:
    return {
        "dimension": dimension, "score": float(score), "status": status,
        "source": source, "window": window, "value": value, "detail": detail,
    }


def _simple_event_evidence(
    dimension, failed, events, weight, source, window, no_event_msg, failed_msg,
) -> dict:
    """通用「数据源失败/无事件/命中记分」三态 evidence（increase/placement 共用；
    reduce 需叠加位置极性、announce 需利好利空两路加总，两者更复杂仍手写不复用本 helper）。
    """
    if failed:
        return _evidence(dimension, 0.0, "source_failed", source, window, None, failed_msg)
    if events:
        detail = _format_event_detail(events)
        return _evidence(dimension, weight, "ok", source, window, len(events), detail)
    return _evidence(dimension, 0.0, "no_event", source, window, 0, no_event_msg)


def score_candidate(fact_card: dict) -> dict:
    """八维度加权打分：主线/增持/定增/减持(位置极性)/其他公告/业绩/涨幅过高/MACD。"""
    card = fact_card
    evidences = []

    # —— 主线板块 ——
    if card.get("main_sector_status") == "missing":
        evidences.append(_evidence(
            "main_sector", 0.0, "missing", "volume-watch", "T日", None,
            "主线板块数据缺失，维度记0"))
    else:
        industry = card.get("industry") or ""
        in_main = bool(card.get("in_main_sector"))
        score = C.W_MAIN_SECTOR if in_main else 0.0
        detail = (
            f"申万二级「{industry}」∈ 当日主线 Top-{C.MAIN_SECTOR_TOP_K}" if in_main
            else f"申万二级「{industry}」不在当日主线 Top-{C.MAIN_SECTOR_TOP_K}"
        )
        if card.get("main_sector_degraded"):
            detail += "（当日集中度缺失，回退最近一日主线）"
        evidences.append(_evidence("main_sector", score, "ok", "volume-watch", "T日", in_main, detail))

    ann_events = card.get("ann_events") or {}
    holder_status = card.get("holder_status", card.get("ann_status", "ok"))
    holder_source = card.get("holder_source", "announcement")
    window_label = f"近{C.ANNOUNCE_WINDOW_DAYS}日"

    # —— 增持/回购 ——
    increase_events = ann_events.get("increase") or []
    evidences.append(_simple_event_evidence(
        "increase", holder_status == "source_failed", increase_events, C.W_INCREASE,
        holder_source, window_label, f"{window_label}无增持/回购事件", "增减持数据源失败，维度记0"))

    # —— 定增 ——
    placement_events = ann_events.get("placement") or []
    evidences.append(_simple_event_evidence(
        "placement", card.get("ann_status") == "source_failed", placement_events, C.W_PLACEMENT,
        "announcement", window_label, f"{window_label}无定增相关公告", "公告数据源失败，维度记0"))

    # —— 减持（D12 位置极性）——
    reduce_events = ann_events.get("reduce") or []
    position_value = card.get("position_value")
    position_state = card.get("position_state")
    if holder_status == "source_failed":
        evidences.append(_evidence(
            "reduce", 0.0, "source_failed", holder_source, window_label, None,
            "增减持数据源失败，维度记0"))
    elif not reduce_events:
        evidences.append(_evidence(
            "reduce", 0.0, "no_event", holder_source, window_label, 0,
            f"{window_label}无减持公告"))
    else:
        detail_events = _format_event_detail(reduce_events)
        if position_state == "missing" or position_value is None:
            evidences.append(_evidence(
                "reduce", 0.0, "neutral", holder_source, "250日", position_value,
                f"{detail_events}；位置缺失（可得样本不足250根），按中位0计"))
        else:
            # degraded（120-249根，见 indicators.position_250d）仍按 full 同口径三档打分，
            # 只在 detail 追加样本量提示，避免静默丢失"样本不足"信息
            degraded_suffix = (
                f"（样本不足250根，按可得{card.get('position_bar_count')}根计算）"
                if position_state == "degraded" else ""
            )
            if position_value <= C.POSITION_LOW:
                evidences.append(_evidence(
                    "reduce", C.W_REDUCE_LOW, "ok", holder_source, "250日", position_value,
                    f"{detail_events}；250日分位{position_value:.0%}（低位）{degraded_suffix}"))
            elif position_value >= C.POSITION_HIGH:
                evidences.append(_evidence(
                    "reduce", C.W_REDUCE_HIGH, "ok", holder_source, "250日", position_value,
                    f"{detail_events}；250日分位{position_value:.0%}（高位）{degraded_suffix}"))
            else:
                evidences.append(_evidence(
                    "reduce", 0.0, "neutral", holder_source, "250日", position_value,
                    f"{detail_events}；250日分位{position_value:.0%}（中位，不计分）{degraded_suffix}"))

    # —— 其他重大公告（利好/利空）——
    good_events = ann_events.get("good") or []
    bad_events = ann_events.get("bad") or []
    if card.get("ann_status") == "source_failed":
        evidences.append(_evidence(
            "announce", 0.0, "source_failed", "announcement", window_label, None,
            "公告数据源失败，维度记0"))
    elif not good_events and not bad_events:
        evidences.append(_evidence(
            "announce", 0.0, "no_event", "announcement", window_label, 0,
            f"{window_label}无其他利好/利空公告"))
    else:
        score = (C.W_ANN_GOOD if good_events else 0.0) + (C.W_ANN_BAD if bad_events else 0.0)
        parts = []
        if good_events:
            parts.append("利好：" + _format_event_detail(good_events))
        if bad_events:
            parts.append("利空：" + _format_event_detail(bad_events))
        evidences.append(_evidence(
            "announce", score, "ok", "announcement", window_label,
            len(good_events) + len(bad_events), "；".join(parts)))

    # —— 业绩 ——
    earnings_status = card.get("earnings_status", "no_event")
    earnings_type = card.get("earnings_type")
    # earnings_direction 唯一推导来源是 build_fact_card._earnings_direction_and_label；
    # 此处不再重复推导，避免两处口径漂移（fixture 测试须显式给出 earnings_direction）
    earnings_direction = card.get("earnings_direction")
    earn_window = f"近{C.EARNINGS_WINDOW_DAYS}日"
    if earnings_status == "source_failed":
        evidences.append(_evidence(
            "earnings", 0.0, "source_failed", "earnings_digest", earn_window, None,
            "业绩数据源失败，维度记0"))
    elif earnings_status == "no_event":
        evidences.append(_evidence(
            "earnings", 0.0, "no_event", "earnings_digest", earn_window, None,
            f"{earn_window}无业绩预告/快报披露"))
    elif earnings_status == "neutral" or not earnings_type:
        # neutral：已选中报告期记录（forecast/express 命中），但方向未知——forecast type
        # 不在正负枚举、或 express 同比未知/为0；与"无披露"是完全不同的语义，不得混入
        # 上面 no_event 分支（此前 `no_event or not earnings_type` 合并判断会把这种
        # "已披露但方向未知"的情形误标成"无业绩预告/快报披露"）。
        label = f"业绩：{earnings_type}" if earnings_type else "已披露"
        evidences.append(_evidence(
            "earnings", 0.0, "neutral", "earnings_digest", earn_window,
            earnings_type, f"{label}但方向未知"))
    elif earnings_direction == "good":
        evidences.append(_evidence(
            "earnings", C.W_EARN_GOOD, "ok", "earnings_digest", earn_window,
            earnings_type, f"业绩：{earnings_type}"))
    elif earnings_direction == "bad":
        evidences.append(_evidence(
            "earnings", C.W_EARN_BAD, "ok", "earnings_digest", earn_window,
            earnings_type, f"业绩：{earnings_type}"))
    else:
        evidences.append(_evidence(
            "earnings", 0.0, "neutral", "earnings_digest", earn_window,
            earnings_type, f"业绩：{earnings_type}（方向中性/未知）"))

    # —— 近10日涨幅过高 ——
    gain10 = card.get("gain10")
    gain10_status = card.get("gain10_status", "missing")
    # 防御性双查：score_candidate 是纯函数容错契约，不得假设输入必经 build_fact_card
    # （未来回放/反序列化路径可能喂入 status="ok" 但 value=None 的不一致卡片）；
    # 不一致输入按 missing 降级处理，而非对 None 直接算术抛 TypeError 拖垮整批打分。
    if gain10_status == "missing" or gain10 is None:
        evidences.append(_evidence(
            "gain10", 0.0, "missing", "daily_bar", "近10日", None,
            "维度缺失：前复权失败或日线样本不足"))
    else:
        if gain10 >= C.GAIN10_HIGH:
            score = C.W_GAIN_HIGH
        elif gain10 >= C.GAIN10_MID:
            score = C.W_GAIN_MID
        else:
            score = 0.0
        evidences.append(_evidence(
            "gain10", score, "ok", "daily_bar", "近10日", gain10,
            f"近10日累计涨幅{gain10:.1f}%"))

    # —— MACD 零轴 ——
    dif = card.get("dif")
    dif_status = card.get("dif_status", "missing")
    # 防御性双查：同 gain10，见上方注释（纯函数容错契约，不假设输入必经 build_fact_card）
    if dif_status == "missing" or dif is None:
        evidences.append(_evidence(
            "macd", 0.0, "missing", "daily_bar", "T日", None,
            "维度缺失：前复权失败/样本不足120根/末根非T日"))
    else:
        score = C.W_MACD_UP if dif > 0 else 0.0
        axis = ">0（零轴上）" if dif > 0 else "<=0（零轴下）"
        evidences.append(_evidence(
            "macd", score, "ok", "daily_bar", "T日", dif, f"DIF={dif:.2f}{axis}"))

    total = sum(e["score"] for e in evidences)
    return {"total": total, "evidences": evidences}


def score_all(fact_cards: list[dict]) -> list[dict]:
    """按 total 降序排序（破平取裸码字典序，保证可复现），附 rank_score。"""
    scored = [{**card, **score_candidate(card)} for card in fact_cards]
    scored.sort(key=lambda c: (-c["total"], c.get("code", "")))
    for idx, card in enumerate(scored, start=1):
        card["rank_score"] = idx
    return scored
