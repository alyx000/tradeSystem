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


def _main_sectors(conn, date: str, top_k: int) -> tuple[set, bool]:
    """T-1 主线申万二级 Top-K（当日缺失回退最近一日）。刻意副本，口径同 board_break/trend_leader。"""
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


def _hot_concepts(registry, date: str, top_m: int) -> tuple[dict, str]:
    """T-1 概念资金流 Top-M → {概念名: net_amount_yi} + 成分反查 {bare_code: [概念名]}。

    返回 (concept_map_by_code, status)；source_failed 时 concept_map 为空。
    """
    r = registry.call("get_concept_moneyflow_ths", date)
    if not getattr(r, "success", False) or not isinstance(r.data, list):
        return {}, "source_failed"
    rows = sorted(r.data, key=lambda x: x.get("net_amount_yi") or x.get("net_amount") or 0,
                  reverse=True)[:top_m]
    names = [row.get("name") for row in rows if row.get("name")]
    mr = registry.call("get_ths_member", date, names)
    member_map = mr.data if getattr(mr, "success", False) and isinstance(mr.data, list) else []
    by_code: dict[str, list] = {}
    for m in member_map:
        cn = _bare(m.get("con_code") or "")           # 成分股代码字段=con_code（tushare_provider.py:731）
        concept = m.get("index_name")                  # 概念名字段=index_name（非 concept_name/name）
        if cn and concept in names:
            by_code.setdefault(cn, []).append(concept)
    return by_code, "ok"


def _teacher_hits(conn, date: str, lookback_days: int) -> list[dict]:
    """近 N 日老师观点（复用 string_yang.mainline._teacher_notes 同款 SQL）。"""
    start = (_dt.datetime.strptime(date, "%Y-%m-%d")
             - _dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT id, date, title, core_view, key_points, sectors "
        "FROM teacher_notes WHERE date >= ? AND date <= ? ORDER BY date DESC, id DESC LIMIT 50",
        (start, date))
    rows = []
    for r in cur.fetchall():
        rows.append({"title": r[2] or "", "core_view": r[3] or "",
                     "key_points": r[4] or "", "sectors": r[5] or ""})
    return rows


def _teacher_mentions(hits: list[dict], name: str, industry: str) -> bool:
    """老师观点是否提及该票名或所属行业（子串命中）。"""
    blob = " ".join(f"{h['title']} {h['core_view']} {h['key_points']} {h['sectors']}" for h in hits)
    if name and name in blob:
        return True
    return bool(industry) and industry in blob


def _index_context(conn) -> tuple[str, str]:
    """大势：market_timing_signal 表最近一批（≤T-1）信号摘要（缺失降级为空串）。

    注意：market-timing **不是 registry capability**（无 provider 注册），必须直接读表。
    取库内最新一日全指数行，摘要成 "指数名:涨跌% [phase]" 紧凑串供 LLM 参考。
    """
    from services.market_timing import repo as mt_repo
    try:
        rows = mt_repo.list_signals(conn, limit=6)   # 最近一批（一日约6指数）
    except Exception:
        return "", "missing"
    if not rows:
        return "", "missing"
    latest = rows[0].get("trade_date")
    parts = []
    for r in rows:
        if r.get("trade_date") != latest:
            break
        name = r.get("index_name") or r.get("index_code") or ""
        chg = r.get("change_pct")
        phase = r.get("bottom_phase") or r.get("phase") or ""
        seg = f"{name}:{chg}%" + (f"[{phase}]" if phase else "")
        parts.append(seg)
    return (f"{latest} " + " ".join(parts)).strip(), ("ok" if parts else "missing")


def _calendar(date: str) -> str:
    d = _dt.datetime.strptime(date, "%Y-%m-%d")
    tags = []
    if d.day >= 25 or d.day <= 3:
        tags.append("月末月初")
    if d.month in (1, 4, 7, 10):
        tags.append("财报季窗口")
    return "/".join(tags)


def build_fact_cards(conn, registry, scan_result: dict, *, params: dict) -> list[dict]:
    date = params["date"]
    cands = scan_result.get("candidates") or []
    top_k = params.get("main_sector_top_k", C.MAIN_SECTOR_TOP_K)
    top_m = params.get("concept_top_m", C.CONCEPT_TOP_M)
    gain_n = params.get("gain_window", C.GAIN_WINDOW)
    high_n = params.get("high_window", C.HIGH_WINDOW)

    main_sectors, ms_degraded = _main_sectors(conn, date, top_k)
    concept_map, concept_status = _hot_concepts(registry, date, top_m)
    teacher = _teacher_hits(conn, date, params.get("teacher_lookback", C.TEACHER_LOOKBACK_DAYS))
    index_ctx, index_status = _index_context(conn)
    cal = _calendar(date)

    # 个股→申万二级映射（修 in_main_sector：scan 候选不带行业，必须补映射才能判主线归属）。
    # get_stock_sw_industry_map 是真实 registry capability，返回 {ts_code: {name, sw_l2}}，申万口径。
    ir = registry.call("get_stock_sw_industry_map")
    industry_map = ir.data if getattr(ir, "success", False) and isinstance(ir.data, dict) else {}

    # 候选集内相对强弱：按涨幅降序名次（最票 proxy）
    order = sorted(cands, key=lambda c: -(c.get("pct_chg") or 0))
    rank_of = {c["code"]: i for i, c in enumerate(order, start=1)}

    start = (_dt.datetime.strptime(date, "%Y-%m-%d")
             - _dt.timedelta(days=C.LOOKBACK_NATURAL_DAYS)).strftime("%Y-%m-%d")

    cards = []
    for cand in cands:
        code, name = cand["code"], cand.get("name", "")
        bare = _bare(code)
        r = registry.call("get_stock_daily_range", code, start, date)
        bars = r.data if getattr(r, "success", False) and isinstance(r.data, list) else []
        closes = [b.get("close") for b in bars if b.get("close") is not None]
        highs = [b.get("high") for b in bars if b.get("high") is not None]
        live = cand.get("price")

        # 申万二级行业（补映射后 main_sector 才真正生效）
        industry = (industry_map.get(code) or {}).get("sw_l2", "")

        # 量能倍数（单位对齐关键）：sina amount_yi=亿元；tushare daily.amount=**千元**。
        # 统一到元：live=amount_yi*1e8，prev=prev_amount*1e3 → vol_ratio=amount_yi*1e5/prev_amount。
        # 注意这是 14:30 半日累计额 vs T-1 全日额，故 vol_ratio≈0.7 已相当于追平昨日全日量能节奏。
        prev_amount = bars[-1].get("amount") if bars else None
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
            "teacher_hit": _teacher_mentions(teacher, name, industry),
            # —— 三位一体 ——
            "rank_in_pool": rank_of.get(code),
            "index_context": index_ctx, "index_status": index_status,
            # —— 节奏 ——
            "gain5": ind.gain_nd(closes, live, gain_n),
            "ma_above": ind.above_all_ma(live, closes),
            "up_days": ind.up_days(bars),
            # 首次放量加速代理：半日额已追平昨日全日节奏(vol_ratio>=0.7) 且非高位连涨(up_days<=1)。
            # 半日 vs 全日本质不可完全对齐(无分时数据)，vol_ratio 作为 [事实] 一并喂 LLM 自行判读。
            "first_surge": (vol_ratio is not None and vol_ratio >= 0.7 and ind.up_days(bars) <= 1),
            "vol_ratio": vol_ratio,
            # —— 节点 ——
            "dist_to_high": ind.dist_to_high(live, highs, high_n),
            "broke_high": ind.broke_prior_high(live, highs, high_n),
            "market_node": index_ctx,
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
    if rank is not None and rank <= 3:
        s += C.W_TRINITY_TOP
    if card.get("first_surge"):
        s += C.W_RHYTHM_FIRST
    if card.get("ma_above"):
        s += C.W_RHYTHM_MA
    if card.get("broke_high"):
        s += C.W_NODE_BREAK
    if card.get("is_limit_up") and (card.get("close_pos") or 0) >= 0.9:
        s += C.W_TAIL_STRONG
    return s


def score_all(cards: list[dict]) -> list[dict]:
    scored = [{**c, "total": _coarse_score(c)} for c in cards]
    scored.sort(key=lambda c: (-c["total"], c.get("code", "")))
    for i, c in enumerate(scored, start=1):
        c["rank_score"] = i
    return scored
