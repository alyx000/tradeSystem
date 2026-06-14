"""趋势主升漏斗编排（盘后 EOD 只读扫描 → 持久化观察池）。

数据流：涨停列表(get_limit_up_list) → 映射申万二级(get_stock_sw_industry_map)
→ ∩ 主线池(daily_volume_concentration Top-K ∪ --sectors) → 拉区间 OHLCV(get_stock_daily_range)
→ 检测器 → 入池/维护/退池(pool)。

两遍：
- Pass 1 发现：主线∩涨停 且 首次涨停加速 + 缓涨 → 入池（**不含贴MA5**：涨停日必远离 MA5）。
- Pass 2 维护：在池 active 股 → 趋势破坏则退池；否则 touch 并记回踩/见顶信号（贴MA5/缩量阴线买点/远离MA5）。

红线：只读观察清单，标 [判断]，不出价位、不写计划层。registry.call 走 capability，不耦合具体 provider。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from services.trend_leader import constants as C
from services.trend_leader import detectors as D
from services.trend_leader import pool
from services.volume_concentration import repo as vc_repo
from services.volume_concentration.aggregator import UNCLASSIFIED
from utils.price_limit import is_dual_board


def _lookback_start(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=C.RANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def _main_sectors(conn: sqlite3.Connection, date: str, top_k: int, sectors) -> tuple[set, bool]:
    """主线池 = 当日成交额集中度 Top-K 申万二级 ∪ 手工 --sectors；当日缺失回退最近一日（走 vc_repo）。"""
    rec = vc_repo.get_concentration(conn, date)
    degraded = False
    if rec is None:
        degraded = True
        recent = vc_repo.get_recent_concentration(conn, date, 1)  # <= date 的最近一条
        rec = recent[-1] if recent else None
    auto = []
    if rec and rec.get("sector_summary"):
        auto = [s["industry"] for s in rec["sector_summary"] if s.get("industry") != UNCLASSIFIED][:top_k]
    return set(auto) | set(sectors or []), degraded


def _bars(registry, code: str, start: str, date: str) -> list[dict]:
    r = registry.call("get_stock_daily_range", code, start, date)
    return r.data if getattr(r, "success", False) and isinstance(r.data, list) else []


def _break_reason(trend_detail: dict) -> str:
    if trend_detail.get("below_ma10"):
        return "收盘跌破MA10"
    if trend_detail.get("two_day_break"):
        return "连续2日跌破MA5且跌幅扩大"
    return "趋势破坏"


def _clean_code(raw) -> str | None:
    """规范个股代码为非空字符串；None/空/nan/非字符串(如 int 600552) 统一处理 → None 或规范串。
    防 provider/schema drift 用非字符串 code 击穿 .split() 行级防御。"""
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s and s.lower() != "nan" else None


def _dual_board_accelerators(registry, date: str) -> tuple[list[dict], bool]:
    """双创(20cm) 涨幅≥15% 的加速票（鞠磊「20cm 涨15%+」即加速，不必全 20% 涨停）。

    全市场日涨跌（get_market_daily_changes）→ 过滤 双创前缀 + pct_chg≥阈值。返回 ([{code,name}], ok)；
    含全涨停(20%)的票也会进，与涨停榜按裸码去重。provider 失败 → ([], False)，由上层记 source_errors。
    """
    r = registry.call("get_market_daily_changes", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return [], False
    out = []
    for row in r.data:
        # 行级防御：单条脏行（非 dict / 缺码 / pct 非数）跳过，不让全市场 feed 一条坏数据
        # 崩掉整个日跑（result 级失败已兜底，行级也必须容错；对齐监管采集的防御解析）。
        if not isinstance(row, dict):
            continue
        code = _clean_code(row.get("ts_code") or row.get("code"))
        if not code:
            continue
        try:
            pct = float(row.get("pct_chg"))
        except (TypeError, ValueError):
            continue
        if is_dual_board(code) and pct >= C.ACCEL_DUAL_BOARD_MIN_PCT:
            out.append({"code": code, "name": row.get("name", "")})
    return out, True


def _main_concepts(registry, date: str, top_m: int, member_count: dict,
                   max_members: int | None = None) -> tuple[set, bool, bool]:
    """主线分支 = 同花顺概念资金净流入 Top-M（鞠磊「主线或其分支」）。

    先按成员数闸过滤「容器概念」再排序：融资融券(3845)/深股通(1738)/华为概念(1009) 这类资格类
    标签覆盖几千只票，聚合净流入天然霸榜，但绝非鞠磊式窄分支（大硅片/MLCC/CPO 仅几十到一两百只）。
    0522 真实数据验证 cap=300 干净分离。
    返回 (Top-M 概念名集合, ok, coverage_ok)；取数失败 → (set(), False, True)。

    coverage_ok=False 表示：净流入排在「入选窗口内」（比第 top_m 名更热）的概念里出现 member_count==0
    ——健康日 0522 实测前排概念成员数均充足（386/386 命中），故热概念无成员强烈指向 ths_member
    success 但分页/部分截断丢了该概念成员（本仓库 tushare 批量接口有静默截断前科），属静默部分覆盖
    缺失，上层据此记 concept_coverage 警示（门2 codex M2'）。容器概念(>cap)被剔不算覆盖缺失。

    name 与 member_count 跨两个独立接口（concept_moneyflow_ths.name / ths_member.index_name）靠
    字符串相等匹配——这是安全的：两接口概念名同源于 THS ths_index 概念母表，0522 实测 386/386
    moneyflow 概念在 ths_member 里逐字命中（member_count=0 者 0 个），命名分歧静默漏网面=0；境外
    成员（CAT.N 等）也实测不会把任何窄概念从 <cap 顶到 >=cap。故不需额外做概念名归一/对账。
    （cap<=0 会让 `0<x<=cap` 恒 False → main_concepts 全空；当前 cap 由常量固定为正，无 CLI/config
    覆盖入口，不可达；若将来加 --concept-max-members 类 flag，须在入口校验为正。）

    max_members=None 时读 C.CONCEPT_MAX_MEMBERS（默认参数会在 import 时绑定，使 monkeypatch 失效，
    故用哨兵在调用时取值）。
    """
    cap = C.CONCEPT_MAX_MEMBERS if max_members is None else max_members
    r = registry.call("get_concept_moneyflow_ths", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return set(), False, True
    parsed = []
    for row in r.data:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not name:
            continue
        try:                                            # 身份过后再解析数值（提前短路垃圾行）
            amt = float(row.get("net_amount"))
        except (TypeError, ValueError):
            continue
        parsed.append((str(name), amt))
    parsed.sort(key=lambda x: x[1], reverse=True)
    kept: list[str] = []
    coverage_ok = True
    for name, _ in parsed:                              # 净流入降序逐个判，填满 top_m 即停
        mc = member_count.get(name, 0)
        if mc > cap:                                    # 容器概念，正常剔除（非覆盖缺失）
            continue
        if mc == 0:                                     # 排在入选窗口内却无成员 → 疑似部分覆盖缺失
            coverage_ok = False
            continue
        kept.append(name)
        if len(kept) >= top_m:
            break
    return set(kept), True, coverage_ok


def _stock_concept_map(registry, date: str) -> tuple[dict, dict, bool]:
    """个股 → 同花顺概念 反向映射 {裸码: set(概念名)} + 概念成员数 {概念名: 去重个股数}。

    成员数供 _main_concepts 过滤容器概念。返回 (map, member_count, ok)；失败 → ({}, {}, False)
    由上层记 source_errors。含美股/全球成员（con_code 如 CAT.N），裸码归一后 A 股候选查不到则不命中，无害。
    """
    r = registry.call("get_ths_member", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return {}, {}, False
    out: dict[str, set] = {}
    member_count: dict[str, int] = {}                   # 概念 → 去重个股数（每只票每概念计一次）
    for row in r.data:
        if not isinstance(row, dict):
            continue
        code = _clean_code(row.get("con_code"))
        concept = row.get("index_name")
        if code and concept:
            concept = str(concept)
            s = out.setdefault(code.split(".")[0], set())  # 键=裸码，与候选查询口径一致
            if concept not in s:                        # 同一票同概念去重后才计数，与 set 语义一致
                s.add(concept)
                member_count[concept] = member_count.get(concept, 0) + 1
    return out, member_count, True


def run_daily(conn: sqlite3.Connection, registry, date: str, *,
              sectors=None, top_k: int = C.DEFAULT_TOP_K_SECTORS, range_start: str | None = None,
              main_line: str = "l2", top_concepts: int = C.DEFAULT_TOP_CONCEPTS) -> dict:
    main_sectors, degraded = _main_sectors(conn, date, top_k, sectors)
    start = range_start or _lookback_start(date)

    lu = registry.call("get_limit_up_list", date)
    lu_ok = getattr(lu, "success", False)
    _raw_limit = (lu.data or {}).get("stocks", []) if lu_ok else []
    # 行级防御：涨停榜可能 stocks=None / 混入非 dict / 缺码 / 非字符串码(int) 脏行 → 规整为
    # code 已规范化的合法 dict 列表，否则后续 limit_bares/candidate_map 的 .split 崩整个日跑。
    limit_stocks = []
    if isinstance(_raw_limit, list):
        for s in _raw_limit:
            if isinstance(s, dict):
                _c = _clean_code(s.get("code"))
                if _c:
                    limit_stocks.append({**s, "code": _c})
    sw = registry.call("get_stock_sw_industry_map")
    sw_ok = getattr(sw, "success", False) and isinstance(sw.data, dict)
    sw_map = sw.data if sw_ok else {}
    # AkShare 降级回裸码(600552)、Tushare sw_map 键是 ts_code(600552.SH)；建裸码副索引兜底匹配，
    # 否则降级源的涨停股会因 sw_map miss → 未分类 → 被主线交集静默漏掉。
    sw_by_bare = {k.split(".")[0]: v for k, v in sw_map.items()}

    # 概念分支主线（鞠磊「主线或其分支」）：main_line=l2+concept 时主线门放宽为
    # 二级∈主线 OR 概念∩主线概念。默认 l2 不取概念（行为不变）。
    concept_mode = main_line == "l2+concept"
    # 候选准入：l2 模式必须 sw 可用（否则无法判二级主线，全挡）；concept 模式下概念分支门与 sw_map
    # 无关（main_concepts 源自 concept_moneyflow、concept_map 源自 ths_member），故 sw 挂了仍放候选
    # 进来靠概念分支匹配，不被无关的 sw 故障静默全挡（门2 codex M1）。
    include_candidates = sw_ok or concept_mode

    # 候选源扩并：双创(20cm) 涨幅≥15% 的加速票（鞠磊「20cm 涨15%+」）。
    dual_accel, dual_ok = ([], True)
    if include_candidates:
        dual_accel, dual_ok = _dual_board_accelerators(registry, date)

    main_concepts, concept_ok, coverage_ok = (set(), True, True)
    concept_map, ths_ok = ({}, True)
    if concept_mode:
        # 先取成员 map（含成员数），再用成员数闸过滤容器概念后取净流入 Top-M。
        concept_map, member_count, ths_ok = _stock_concept_map(registry, date)
        main_concepts, concept_ok, coverage_ok = _main_concepts(
            registry, date, top_concepts, member_count)

    # 发现链路 provider 失败显式记账：否则「链路断了」会伪装成「今日无候选」，运营无法区分。
    source_errors = []
    if not lu_ok:
        source_errors.append("limit_up")
    if not sw_ok:
        source_errors.append("sw_map")
    if not dual_ok:
        source_errors.append("market_changes")
    if concept_mode:
        if not concept_ok:
            source_errors.append("concept_flow")
        if not ths_ok:
            source_errors.append("ths_member")
        elif concept_ok and not coverage_ok:
            # ths/concept 取数都报成功，但「入选窗口内的热概念」出现 member_count==0 → ths_member
            # success 但空/部分截断（本仓库 tushare 批量接口有静默截断前科），概念分支会静默漏热分支；
            # 显式记账，避免「链路部分降级」被误读为「今日无概念候选」（门2 codex M2/M2'）。容器概念被剔
            # 不触发（coverage_ok 不受 >cap 影响），故容器-only 日不误报。
            source_errors.append("concept_coverage")

    summary = {
        "date": date, "limit_up": len(limit_stocks), "main_sectors": sorted(main_sectors),
        "degraded_main": degraded, "candidates": 0,
        "entered": [], "refreshed": [], "exited": [], "in_pool_signals": [],
        "data_errors": [], "source_errors": source_errors,
    }

    # 涨停裸码集：用于区分入池触发类型（涨停 vs 双创15%加速），写入 signal_json 供池/报告辨识。
    limit_bares = {(s.get("code") or "").split(".")[0] for s in (limit_stocks if include_candidates else [])}

    # 合并候选：涨停 ∪ 双创@15%，按裸码去重（涨停源字段更全，后写覆盖）。
    candidate_map: dict[str, dict] = {}
    for st in dual_accel:
        b = (st.get("code") or "").split(".")[0]
        if b:
            candidate_map[b] = st
    for st in (limit_stocks if include_candidates else []):
        b = (st.get("code") or "").split(".")[0]
        if b:
            candidate_map[b] = st

    entered_codes = set()
    # Pass 1 — 发现：主线∩(涨停∪双创加速) + 首次加速 + 缓涨。l2 模式 sw 映射不可用时跳过发现（无法
    # 可靠映射二级主线，全挡；失败已记 source_errors）；concept 模式 sw 挂仍靠概念分支放行（sw_l2=未分类）。
    # Pass2 维护照常。
    for st in candidate_map.values():
        code_raw = st.get("code")
        bare = code_raw.split(".")[0]          # 池内唯一身份（裸码），防 ts_code/裸码建重复 active
        sw_entry = sw_map.get(code_raw) or sw_by_bare.get(bare) or {}
        sw_l2 = sw_entry.get("sw_l2", UNCLASSIFIED)
        # 已知限制（defer，门2 codex M1'，触发条件=将默认翻到 l2+concept 时补修复路径）：concept 模式
        # 下若 sw_map 故障，经概念分支入池的票 sw_l2 落「未分类」；Pass2 维护(pool.touch)只更 last_signal
        # 不更 sw_l2，故 sw 恢复后该票 sw_l2 仍滞留「未分类」直到退池。当前可接受：① renderer 仍标
        # 「未分类·分支:概念名」分支信号不丢；② 默认 l2 不触发（l2 下 sw 故障直接全挡无入池）；③ sw_map
        # 故障本就罕见。翻默认前应在 Pass2 加：active 行 sw_l2==未分类 且当日 sw 可用时回填真实二级。
        # 主线门：二级∈主线 OR（concept 模式下）个股概念∩主线概念。branch_concepts 记命中分支供报告标注。
        branch_concepts = sorted(concept_map.get(bare, set()) & main_concepts) if concept_mode else []
        if sw_l2 not in main_sectors and not branch_concepts:
            continue
        # candidates = 主线∩涨停（进入检测阶段的数量），与 entered（过检入池）故意分开：
        # 二者之差 = 漏斗的检测器过滤层，是设计意图而非统计错误。
        summary["candidates"] += 1
        bars = _bars(registry, bare, start, date)
        if not bars:                            # 行情拉取失败/空：记错误，不误判为"无信号"
            summary["data_errors"].append(bare)
            continue
        first, fd = D.is_first_limit_up_acceleration(bars, bare, today_accelerated=True)
        gentle, gd = D.is_gentle_rise(bars, bare)
        if not (first and gentle):
            continue
        name = st.get("name") or sw_entry.get("name", "")
        # 入池触发：涨停（主板/双创全板）vs 双创15%加速（未到全涨停）。first_limit_date 列名沿用，
        # 语义已泛化为「首次加速日」（board-aware）；trigger 落 signal_json 让两类入池可辨识。
        trigger = "涨停" if bare in limit_bares else "双创15%加速"
        res = pool.record(conn, code=bare, name=name, sw_l2=sw_l2,
                          first_limit_date=date, date=date,
                          signal_json={"first_limit": fd, "gentle": gd, "entry_trigger": trigger,
                                       "branch_concepts": branch_concepts})
        entered_codes.add(bare)
        if res in ("entered", "refreshed"):     # 据实汇报，stale(更早日期 no-op) 不报转换
            summary[res].append(bare)

    # Pass 2 — 维护：在池 active（未在 Pass1 处理）→ 退池 / 记信号
    for r in pool.list_pool(conn, status="active"):
        code = r["code"]
        if code in entered_codes:
            continue
        bars = _bars(registry, code, start, date)
        if not bars:                            # 行情缺失：不 touch 推进 last_seen/days、不退池，记错误
            summary["data_errors"].append(code)
            continue
        broken, bd = D.is_trend_broken(bars)
        if broken:
            if pool.mark_exited(conn, code, date=date, reason=_break_reason(bd)):
                summary["exited"].append(code)   # 据实汇报：旧日期 no-op 不报退出
            continue
        shrink, sd = D.is_volume_shrink_pullback(bars)
        near, nd = D.is_near_ma5(bars)
        far, fdd = D.is_far_from_ma5(bars)
        # 维护会整体重写 last_signal_json → 显式带上入池归因，否则次日就丢失辨识：
        # entry_trigger（涨停/双创15%）+ branch_concepts（经哪条概念分支入主线）。后者尤其在 sw_map
        # 故障期入池的「未分类」票上必须保留，否则维护一过就无法解释该票为何入池（门2 codex M1''）。
        prev_sig = r.get("last_signal") or {}
        pool.touch(conn, code, date=date,
                   signal_json={"shrink_pullback": sd, "near_ma5": nd, "overheat": fdd,
                                "trend": bd, "entry_trigger": prev_sig.get("entry_trigger"),
                                "branch_concepts": prev_sig.get("branch_concepts") or []})
        summary["in_pool_signals"].append({
            "code": code, "shrink_pullback_buy": shrink, "near_ma5": near, "overheat": far,
        })

    return summary
