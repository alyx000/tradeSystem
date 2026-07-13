"""tail-scan 四维事实卡编排 + 粗权重分。

粗分仅用于 PK 强池截断与破平，**不进 PK prompt**（PK 只喂 [事实] 字段）。
单维度取数失败只降级该维度（标 *_status=missing/source_failed），不中断整批。
"""
from __future__ import annotations

import datetime as _dt

from services.tail_scan import constants as C
from services.tail_scan import indicators as ind
from services.volume_concentration import repo as vc_repo
from services.volume_concentration.aggregator import UNCLASSIFIED


def _bare(code: str) -> str:
    return str(code or "").split(".")[0].strip()


def _prev_trade_date(registry, date: str) -> str:
    """上一交易日(T-1)；解析失败保守退回 date（不因日历源抖动打崩概念维度）。"""
    try:
        from utils.trade_date import get_prev_trade_date
        prev = get_prev_trade_date(registry, date)
        return prev or date
    except Exception:
        return date


def _main_sectors(conn, date: str, top_k: int) -> tuple[set, bool]:
    """T-1 主线申万二级 Top-K（当日缺失回退最近一日）。刻意副本，口径同 board_break/trend_leader。

    取数失败降级不中断整批，与 _index_context 等其他维度同款 try/except 对称。
    """
    try:
        rec = vc_repo.get_concentration(conn, date)
        degraded = False
        if rec is None:
            degraded = True
            recent = vc_repo.get_recent_concentration(conn, date, 1)
            rec = recent[-1] if recent else None
        auto = []
        if rec and rec.get("sector_summary"):
            auto = [s["industry"] for s in rec["sector_summary"]
                    if s.get("industry") != UNCLASSIFIED][:top_k]
        return set(auto), degraded
    except Exception:
        # DB 失败或表缺失，降级空集 + degraded=True（不中断整批）
        return set(), True


def _hot_concepts(registry, concept_date: str, top_m: int) -> tuple[dict, str]:
    """概念资金流 Top-M → {概念名: net_amount_yi} + 成分反查 {bare_code: [概念名]}。

    `concept_date` 由调用方解析为 **上一交易日(T-1)**：`moneyflow_cnt_ths` 是盘后日频，
    盘中用当日 date 调恒返空(codex 门2 高危：概念维度一直静默为死)，故须传 T-1。
    返回 (concept_map_by_code, status)：
      - source_failed：资金流取数失败/空 → concept_map 空
      - member_failed：资金流成功但 get_ths_member 失败 → 成分未知，不能当"确定无命中"(codex 门2 中)
      - ok：两步都成功
    """
    r = registry.call("get_concept_moneyflow_ths", concept_date)
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        return {}, "source_failed"
    rows = sorted(r.data, key=lambda x: x.get("net_amount_yi") or x.get("net_amount") or 0,
                  reverse=True)[:top_m]
    names = [row.get("name") for row in rows if row.get("name")]
    mr = registry.call("get_ths_member", concept_date, names)
    if not (getattr(mr, "success", False) and isinstance(mr.data, list)):
        # 资金流拿到了 Top-M，但成分反查失败：成分归属未知，返回 member_failed，
        # 让下游知道 in_hot_concept=False 是"未知"而非"确定不在概念内"。
        return {}, "member_failed"
    by_code: dict[str, list] = {}
    for m in mr.data:
        cn = _bare(m.get("con_code") or "")           # 成分股代码字段=con_code（tushare_provider.py:731）
        concept = m.get("index_name")                  # 概念名字段=index_name（非 concept_name/name）
        if cn and concept in names:
            by_code.setdefault(cn, []).append(concept)
    return by_code, "ok"


def _window_start(date: str, days: int) -> str:
    return (_dt.datetime.strptime(date, "%Y-%m-%d") - _dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _teacher_hits(conn, date: str, lookback_days: int) -> list[dict]:
    """近 N 日老师观点（复用 string_yang.mainline._teacher_notes 同款 SQL）。

    取数失败降级空列表，与 _main_sectors/_index_context 等其他维度同款 try/except 对称。
    """
    try:
        start = _window_start(date, lookback_days)
        cur = conn.execute(
            "SELECT id, date, title, core_view, key_points, sectors "
            "FROM teacher_notes WHERE date >= ? AND date <= ? ORDER BY date DESC, id DESC LIMIT 50",
            (start, date))
        rows = []
        for r in cur.fetchall():
            rows.append({"title": r[2] or "", "core_view": r[3] or "",
                         "key_points": r[4] or "", "sectors": r[5] or ""})
        return rows
    except Exception:
        return []


def _teacher_mentions(blob: str, name: str, industry: str) -> bool:
    """老师观点是否提及该票名或所属行业（子串命中）。"""
    if name and name in blob:
        return True
    return bool(industry) and industry in blob


def _index_context(conn, ref_date: str) -> tuple[str, str]:
    """大势：market_timing_signal 表中 **≤ ref_date(T-1)** 的最近一批信号摘要（缺失降级空串）。

    注意：market-timing **不是 registry capability**（无 provider 注册），必须直接读表。
    绑 ref_date（codex 门2）：不取库内"最新"，而是取 ≤ref_date 的最近一日，避免 `--date`
    历史回放或同日已落库时读到未来信号。摘要成 "指数名:涨跌% [phase]" 紧凑串供 LLM 参考。
    """
    from services.market_timing import repo as mt_repo
    try:
        # 多取几日再按 ref_date 过滤（一日约6指数，×5 足够覆盖到最近一个有信号的交易日）
        rows = mt_repo.list_signals(conn, limit=C.MARKET_TIMING_FETCH_LIMIT * 5)
    except Exception:
        return "", "missing"
    rows = [r for r in rows if (r.get("trade_date") or "") <= ref_date]
    if not rows:
        return "", "missing"
    latest = max(r.get("trade_date") for r in rows)
    parts = []
    for r in rows:
        if r.get("trade_date") != latest:
            continue
        name = r.get("index_name") or r.get("index_code") or ""
        chg = r.get("change_pct")
        phase = r.get("bottom_phase") or r.get("phase") or ""
        seg = f"{name}:{chg}%" + (f"[{phase}]" if phase else "")
        parts.append(seg)
    return (f"{latest} " + " ".join(parts)).strip(), ("ok" if parts else "missing")


def _calendar(date: str) -> str:
    d = _dt.datetime.strptime(date, "%Y-%m-%d")
    tags = []
    if d.day >= C.MONTH_END_START_DAY or d.day <= C.MONTH_START_END_DAY:
        tags.append("月末月初")
    if d.month in C.EARNINGS_SEASON_MONTHS:
        tags.append("财报季窗口")
    return "/".join(tags)


def build_fact_cards(conn, registry, scan_result: dict, *, params: dict) -> list[dict]:
    date = params["date"]
    cands = scan_result.get("candidates") or []
    top_k = params.get("main_sector_top_k", C.MAIN_SECTOR_TOP_K)
    top_m = params.get("concept_top_m", C.CONCEPT_TOP_M)
    gain_n = params.get("gain_window", C.GAIN_WINDOW)
    high_n = params.get("high_window", C.HIGH_WINDOW)

    # T-1 统一上下文（codex 门2）：主线/概念/大势都是盘后日频，须绑 prev_date，否则盘中恒空、
    # 且 `--date` 历史回放会读到同日/未来数据(看未来排序)。解析失败保守退回 date。
    prev_date = _prev_trade_date(registry, date)
    main_sectors, ms_degraded = _main_sectors(conn, prev_date, top_k)
    concept_map, concept_status = _hot_concepts(registry, prev_date, top_m)
    # 老师观点是本地人工录入(非行情)，按 date 回看窗口，不涉看未来。
    teacher = _teacher_hits(conn, date, params.get("teacher_lookback", C.TEACHER_LOOKBACK_DAYS))
    teacher_blob = " ".join(f"{h['title']} {h['core_view']} {h['key_points']} {h['sectors']}" for h in teacher)
    index_ctx, index_status = _index_context(conn, prev_date)
    cal = _calendar(date)

    # 个股→申万二级映射（修 in_main_sector：scan 候选不带行业，必须补映射才能判主线归属）。
    # get_stock_sw_industry_map 是真实 registry capability，返回 {ts_code: {name, sw_l2}}，申万口径。
    ir = registry.call("get_stock_sw_industry_map")
    industry_map = ir.data if getattr(ir, "success", False) and isinstance(ir.data, dict) else {}

    # 候选集内相对强弱：按涨幅降序名次（最票 proxy）
    order = sorted(cands, key=lambda c: -(c.get("pct_chg") or 0))
    rank_of = {c["code"]: i for i, c in enumerate(order, start=1)}

    start = _window_start(date, C.LOOKBACK_NATURAL_DAYS)

    cards = []
    for cand in cands:
        code, name = cand["code"], cand.get("name", "")
        bare = _bare(code)
        r = registry.call("get_stock_daily_range", code, start, date)
        history_ok = getattr(r, "success", False) and isinstance(r.data, list)
        bars = r.data if history_ok else []          # 失败→空,但用 history_status 区分"没取到"vs"真没数据"
        closes = [b.get("close") for b in bars if b.get("close") is not None]
        highs = [b.get("high") for b in bars if b.get("high") is not None]
        live = cand.get("price")

        # 申万二级行业（补映射后 main_sector 才真正生效）
        industry = (industry_map.get(code) or {}).get("sw_l2", "")

        # 量能倍数（单位对齐关键）：sina amount_yi=亿元；tushare daily.amount=**千元**。
        # 统一到元：live=amount_yi*1e8，prev=prev_amount*1e3 → vol_ratio=amount_yi*1e5/prev_amount。
        # 注意这是 14:30 半日累计额 vs T-1 全日额，故 vol_ratio≈0.7 已相当于追平昨日全日量能节奏。
        prev_amount = bars[-1].get("amount") if bars else None
        ud = ind.up_days(bars) if bars else None   # 无历史→None(未知)，不伪装成"连涨0天"(codex 门2)
        vol_ratio = None
        if prev_amount:
            try:
                vol_ratio = round(cand.get("amount_yi", 0) * 1e5 / prev_amount, 2)
            except (TypeError, ZeroDivisionError):
                vol_ratio = None

        cards.append({
            "code": code, "name": name, "pct_chg": cand.get("pct_chg"),
            "price": live, "amount_yi": cand.get("amount_yi"),
            "is_limit_up": cand.get("is_limit_up"), "close_pos": cand.get("close_pos"),
            "amplitude": cand.get("amplitude"),
            # —— 逻辑 ——
            "in_main_sector": bool(industry) and industry in main_sectors,
            "main_sector_status": "missing" if (ms_degraded and not main_sectors) else "ok",
            "main_sector_degraded": ms_degraded,
            "in_hot_concept": bare in concept_map,
            "concept_names": concept_map.get(bare, []),
            "concept_status": concept_status,
            "teacher_hit": _teacher_mentions(teacher_blob, name, industry),
            # —— 三位一体 ——
            "rank_in_pool": rank_of.get(code),
            "index_context": index_ctx, "index_status": index_status,
            # —— 节奏 ——
            "gain5": ind.gain_nd(closes, live, gain_n),
            "ma_above": ind.above_all_ma(live, closes),
            "up_days": ud,
            "history_status": "ok" if history_ok else "source_failed",
            # 首次放量加速代理：半日额已追平昨日全日节奏(vol_ratio>=FIRST_SURGE_VOL_RATIO_MIN)
            # 且非高位连涨(up_days<=FIRST_SURGE_UP_DAYS_MAX)。ud=None(历史缺失)时不成立。
            # 半日 vs 全日本质不可完全对齐(无分时数据)，vol_ratio 作为 [事实] 一并喂 LLM 自行判读。
            "first_surge": (vol_ratio is not None and vol_ratio >= C.FIRST_SURGE_VOL_RATIO_MIN
                             and ud is not None and ud <= C.FIRST_SURGE_UP_DAYS_MAX),
            "vol_ratio": vol_ratio,
            # —— 节点 ——
            "dist_to_high": ind.dist_to_high(live, highs, high_n),
            "broke_high": ind.broke_prior_high(live, highs, high_n),
            "calendar": cal,
        })
    return cards


def _coarse_score(card: dict) -> float:
    s = 0.0
    if card.get("in_main_sector"):
        s += C.W_LOGIC_MAIN
    if card.get("in_hot_concept"):
        s += C.W_LOGIC_CONCEPT
    if card.get("teacher_hit"):
        s += C.W_LOGIC_TEACHER
    rank = card.get("rank_in_pool")
    if rank is not None and rank <= C.TRINITY_TOP_RANK:
        s += C.W_TRINITY_TOP
    if card.get("first_surge"):
        s += C.W_RHYTHM_FIRST
    if card.get("ma_above"):
        s += C.W_RHYTHM_MA
    if card.get("broke_high"):
        s += C.W_NODE_BREAK
    if card.get("is_limit_up") and (card.get("close_pos") or 0) >= C.TAIL_STRONG_CLOSE_POS_MIN:
        s += C.W_TAIL_STRONG
    return s


def score_all(cards: list[dict]) -> list[dict]:
    scored = [{**c, "total": _coarse_score(c)} for c in cards]
    scored.sort(key=lambda c: (-c["total"], c.get("code", "")))
    for i, c in enumerate(scored, start=1):
        c["rank_score"] = i
    return scored
