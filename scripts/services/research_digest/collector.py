"""事实采集层：经 registry.call 取 A股/美股结构化评级，聚合 + 启发式打分；不调 LLM。

- A股：get_research_report_list（巨潮评级清单，含源 `评级变化` 方向列），按标的聚合 + 打分。
- 美股：get_us_rating_changes（yfinance，已在 provider 侧做窗口/方向/冻结过滤）。
- A股采集失败/空 → 记录不致命，返空（service 顶层据此降级，M1）。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from providers.akshare_provider import _us_eastern_now

logger = logging.getLogger(__name__)

# A股源 `评级变化` 取值（L1）：维持 / 调高 / 调低 / 首次。
# 鞠磊框架（teacher_notes#91，2026-05-10）：研报最值得关注的 #1 信号是「首次覆盖」
# （隔两三个月再被覆盖 → 多数股票位置不高），其次是评级上调；故首次覆盖权重最高。
_CN_FIRST_TOKENS = ("首次",)
_CN_UP_TOKENS = ("调高", "上调")
_CN_DOWN_TOKENS = ("调低", "下调")
_CN_FIRST_WEIGHT = 3.0
_CN_UP_WEIGHT = 1.5
_CN_DOWN_WEIGHT = -1.0
_MULTI_COVER_MIN = 3   # 多家覆盖阈值
# A股段「展示条数」与「逐股补观点条数」的统一上限：单一真源，renderer 的 A股段 import 复用，
# 避免两处各写 15 漂移导致"展示了但没补到观点"的展示项缺观点。
CN_DISPLAY_CAP = 15


def _cn_score(org_count: int, rating_changes: list[str]) -> float:
    """启发式：机构覆盖数为底；首次覆盖（鞠磊 #1）权重最高，评级上调次之，下调减权（F6，不靠 LLM）。"""
    score = float(org_count)
    for rc in rating_changes:
        if any(tok in rc for tok in _CN_FIRST_TOKENS):
            score += _CN_FIRST_WEIGHT
        elif any(tok in rc for tok in _CN_UP_TOKENS):
            score += _CN_UP_WEIGHT
        elif any(tok in rc for tok in _CN_DOWN_TOKENS):
            score += _CN_DOWN_WEIGHT
    return round(score, 2)


def _cn_signals(org_count: int, rating_changes: list[str]) -> list[str]:
    """鞠磊框架的可机械检测信号标签（供 Top3 why 与排序展示）。"""
    sig = []
    if any(any(t in rc for t in _CN_FIRST_TOKENS) for rc in rating_changes):
        sig.append("首次覆盖")
    if any(any(t in rc for t in _CN_UP_TOKENS) for rc in rating_changes):
        sig.append("评级上调")
    if org_count >= _MULTI_COVER_MIN:
        sig.append("多家覆盖")
    return sig


def _pick_viewpoint(reports: list[dict], date: str) -> dict | None:
    """从个股研报列表选一条作"简要观点"=报告标题：优先发布日=date 当日，否则最新一条（em 默认倒序）。

    返回 {title, institution, date} 或 None（无可用标题）。
    """
    if not reports:
        return None
    # 不依赖上游 em 默认倒序：显式按发布日降序排，再优先取当日、否则最新一条（稳健，不被源排序改动影响）
    ordered = sorted(reports, key=lambda r: str(r.get("date") or ""), reverse=True)
    same_day = [r for r in ordered if str(r.get("date") or "")[:10] == date]
    pick = (same_day or ordered)[0]
    title = str(pick.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "institution": str(pick.get("institution") or "").strip(),
        "date": str(pick.get("date") or "")[:10],
    }


def _enrich_cn_viewpoints(registry, items: list[dict], date: str) -> None:
    """就地为将展示的 A股标的补 `viewpoint`=最新研报标题（东财 get_research_reports，逐股一次网络调用）。

    标题属**事实层**（出处=机构报告名），采集层照取不过滤（红线「约束生成不约束取数」）；
    渲染出口再过一道红线兜底（renderer._safe_text，不 surface 目标价/操作词）。
    逐股失败 / 空 / 异常仅跳过该条 viewpoint，不中断其余、不致命。
    """
    for it in items:
        code = it.get("stock_code")
        if not code:
            continue
        try:
            # code 为巨潮 cninfo 的 6 位纯数字证券代码（无 .SH/.SZ 后缀）；provider 内 split(".")[0] 幂等兼容带后缀源
            r = registry.call("get_research_reports", code)
        except Exception as e:  # noqa: BLE001
            logger.warning("[research-digest] 观点采集异常 %s: %s", code, e)
            continue
        if not getattr(r, "success", False):
            continue
        vp = _pick_viewpoint(r.data or [], date)
        if vp:
            it["viewpoint"] = vp
            # em 报告标题常直写"首次覆盖"，而 cninfo `评级变化` 列可能漏标 → 补进 signals 置首，
            # 让鞠磊 #1 信号「首次覆盖」更全、在报告里更显著（仅补展示信号，不改 score/排序）。
            # 必须匹配完整术语"首次覆盖"，不能只判"首次"——否则"首次盈利/首次进入指数/首次实现正现金流"
            # 等业绩/事件描述会被误判为研报覆盖动作（false-positive，subagent 审查指出）。
            if "首次覆盖" in vp["title"] and "首次覆盖" not in it["signals"]:
                it["signals"].insert(0, "首次覆盖")


def collect_cn(registry, date: str) -> list[dict]:
    """A股研报评级清单 → 按标的聚合（机构数/评级/评级变化）+ 打分，并为 Top 标的补真实研报观点。失败不致命返空。"""
    res = registry.call("get_research_report_list", date)
    if not getattr(res, "success", False):
        logger.warning("[research-digest] A股研报采集失败: %s", getattr(res, "error", None))
        return []
    rows = res.data or []
    by_code: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        e = by_code.setdefault(code, {
            "stock_code": code, "stock_name": "",
            "institutions": [], "ratings": [], "rating_changes": [], "_seen_rc": set(),
        })
        if not e["stock_name"] and r.get("stock_name"):  # L1：取首个非空名（首行可能空）
            e["stock_name"] = str(r["stock_name"]).strip()
        inst = str(r.get("institution") or "").strip()
        if inst:
            e["institutions"].append(inst)
        if r.get("rating"):
            e["ratings"].append(str(r["rating"]).strip())
        rc = str(r.get("rating_change") or "").strip()
        if rc:
            key = (inst, rc)  # M3：同机构同方向去重，防重复源行重复叠加 score
            if key not in e["_seen_rc"]:
                e["_seen_rc"].add(key)
                e["rating_changes"].append(rc)
    items = []
    for code, e in by_code.items():
        orgs = sorted(set(e["institutions"]))
        item = {
            "market": "A",
            "stock_code": code,
            "stock_name": e["stock_name"],
            "org_count": len(orgs),
            "institutions": orgs,
            "ratings": e["ratings"],
            "rating_changes": e["rating_changes"],
            "score": _cn_score(len(orgs), e["rating_changes"]),
            "signals": _cn_signals(len(orgs), e["rating_changes"]),
        }
        items.append(item)
    items.sort(key=lambda x: (-x["score"], -x["org_count"], x["stock_code"]))
    _enrich_cn_viewpoints(registry, items[:CN_DISPLAY_CAP], date)  # 仅给将展示的 Top 标的补观点（控网络调用数）
    return items


# 鞠磊框架：美股 Action 映射到同一套信号标签（init=首次覆盖为 #1 信号）。
_US_ACTION_SIGNAL = {"init": "首次覆盖", "reinit": "重启覆盖", "up": "评级上调", "down": "评级下调"}


def collect_us(registry, tickers: list[str], date_window: tuple[str, str]) -> list[dict]:
    """美股评级变动（provider 已做窗口/方向/冻结过滤）。采集失败返空（不致命）。"""
    res = registry.call("get_us_rating_changes", tickers, date_window)
    if not getattr(res, "success", False):
        logger.warning("[research-digest] 美股评级采集失败: %s", getattr(res, "error", None))
        return []
    out = []
    for d in (res.data or []):
        sig = _US_ACTION_SIGNAL.get(str(d.get("action", "")).lower())
        out.append(dict(d, market="US", signals=[sig] if sig else []))
    return out


def us_date_window(lookback_days: int = 5, now_et=None) -> tuple[str, str]:
    """美股窗口 = [美东今日 - lookback_days, 美东今日]，闭区间（H2：用美东日历，非北京 --date）。

    注意跨度是 lookback_days+1 个自然日（含今日两端）；默认 5 偏宽，**有意**——
    评级变化稀疏，宁可窗口略宽安全跨越周末/单日节假日覆盖最近 1-2 个已收盘交易日，
    也不要因窗口过窄漏掉昨夜评级。windowing 在 provider 纯函数按 GradeDate 精确比较。
    """
    now = now_et or _us_eastern_now()
    end = now.date()
    start = end - timedelta(days=max(0, lookback_days))
    return (start.isoformat(), end.isoformat())
