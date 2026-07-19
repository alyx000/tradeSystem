"""sector_crowding 采集层：sw_daily 行业行情 + 两市总额守卫 + 资金流代理三路。"""
from __future__ import annotations

import logging

# 复用 volume-watch 已实战校准的三段守卫常量（只 import 常量不改其文件）
from services.volume_concentration.collector import (
    MARKET_SZ_SH_RATIO_FLOOR,
    MARKET_TOTAL_DROP_WARN_RATIO,
    MARKET_TOTAL_FLOOR_BILLION,
)

from . import repo

logger = logging.getLogger(__name__)

# sw_daily amount 单位换算除数 → 亿元。2026-07-18 真机实测校准:L1 amount 总和
# 265,411,140(万元)÷10000 ≈ 2.65 万亿,与当日全市场量级吻合;与 get_sector_rankings
# 的 amount/10000 口径一致。
AMOUNT_TO_BILLION = 10000.0


def fetch_sector_daily(provider, date: str) -> dict | None:
    """当日申万 L1+L2 快照。L1 缺失且 parent_map 可靠才合成（meta 标 synthesized）。

    sw_daily 实测含 L3 与"申万50"等特殊指数,不在 L1/L2 码表内的行必须过滤(防混级双计)。
    """
    d = date.replace("-", "")
    try:
        df = provider.pro.sw_daily(trade_date=d)
    except Exception as e:
        logger.warning("[sector-crowding] sw_daily 失败: %s", e)
        return None
    if df is None or df.empty:
        return None
    l1_codes = provider._ensure_sw_l1_codes() or set()
    l2_codes = provider._ensure_sw_l2_codes() or set()
    sectors, has_l1 = [], False
    for row in df.to_dict("records"):
        code = row.get("ts_code")
        level = "L1" if code in l1_codes else ("L2" if code in l2_codes else None)
        if level is None:
            continue
        amount = row.get("amount")
        sectors.append({
            "code": code, "name": row.get("name"), "level": level,
            "close": row.get("close"),
            "amount_billion": round(amount / AMOUNT_TO_BILLION, 2) if amount is not None else None,
        })
        has_l1 = has_l1 or level == "L1"
    if not sectors:
        return None
    if has_l1:
        l1_status = "native"
    else:
        parent_map = provider._ensure_sw_l1_parent_map() or {}
        if parent_map:
            l1_status = "synthesized"
            sectors.extend(synthesize_l1(sectors, parent_map))
        else:
            l1_status = "missing"  # 映射不可靠禁止合成（spec v2 严重1）
    return {"sectors": sectors, "meta": {"l1_status": l1_status, "source": "tushare:sw_daily"}}


def synthesize_l1(l2_sectors: list[dict], parent_map: dict) -> list[dict]:
    """L2 成交额按 parent_code 归并成 L1。close 不可加总 → None（斜率维度缺席）。

    daily 与 backfill 共用同一分支逻辑(Explore review 中1):回填不合成会导致合成 L1
    永无历史序列 → 分位/双高对 L1 长期失效。"""
    agg: dict = {}
    for s in l2_sectors:
        if s.get("level") != "L2" or s.get("amount_billion") is None:
            continue
        parent = parent_map.get(s.get("code"))
        if not parent:
            continue
        ent = agg.setdefault(parent, {"code": parent, "name": parent, "level": "L1",
                                      "close": None, "amount_billion": 0.0})
        ent["amount_billion"] = round(ent["amount_billion"] + s["amount_billion"], 2)
    return list(agg.values())


def fetch_market_total(conn, registry, date: str):
    """两市总额（get_market_volume）+ 三段守卫；prev 基准读本任务自己的表。

    prev 刻意不走 volume_concentration 的同名查询:两表覆盖日期不同(本表回填后历史更长、
    对方守卫失败日落 NULL 的日期集合也不同),跨表基准会随对方任务故障漂移。"""
    result = registry.call("get_market_volume", date)
    if not (result.success and result.data):
        return None, None
    data = result.data
    total = data.get("total_billion")
    if total is None or total < MARKET_TOTAL_FLOOR_BILLION:
        if total is not None:
            logger.warning("[sector-crowding] %s 两市成交额 %.0f 亿低于绝对地板 %.0f 亿,落 NULL(source=%s)",
                           date, total, MARKET_TOTAL_FLOOR_BILLION, result.source)
        return None, None
    sh, sz = data.get("shanghai_billion"), data.get("shenzhen_billion")
    if sh is not None and sz is not None and sh > 0 and sz < sh * MARKET_SZ_SH_RATIO_FLOOR:
        logger.warning("[sector-crowding] %s 深市腿 %.0f 亿 < 沪市腿 %.0f 亿×%.1f,疑口径退化,落 NULL(source=%s)",
                       date, sz, sh, MARKET_SZ_SH_RATIO_FLOOR, result.source)
        return None, None
    prev = repo.get_latest_market_total_before(conn, date)
    if prev and total < prev * (1 - MARKET_TOTAL_DROP_WARN_RATIO):
        logger.warning("[sector-crowding] %s 两市成交额 %.0f 亿较前值 %.0f 亿骤降逾 %.0f%%,请人工复核(仅告警,照常落库)",
                       date, total, prev, MARKET_TOTAL_DROP_WARN_RATIO * 100)
    return total, result.source


CHUNK_YEARS = 4  # 回填分片窗口:7.5年≈1820行/码贴近镜像2000行静默截断上限,必须分片
TRUNCATION_ROW_FLOOR = 2000  # 单片返回行数达到该值=疑似截断(镜像单页上限)


class BackfillTruncationError(Exception):
    """单片返回 ≥2000 行=疑似静默截断,拒绝落库(memory: index_member_all 同坑)。"""


def fetch_code_history(provider, code: str, start: str, end: str) -> list[dict]:
    """按 ≤CHUNK_YEARS 年窗口分片拉单码区间日线，升序返回 {date, close, amount_billion}。"""
    out = []
    chunk_start = start
    while chunk_start <= end:
        cy = int(chunk_start[:4])
        chunk_end = min(f"{cy + CHUNK_YEARS - 1}-12-31", end)
        df = provider.pro.sw_daily(
            ts_code=code,
            start_date=chunk_start.replace("-", ""),
            end_date=chunk_end.replace("-", ""),
        )
        if df is not None and len(df) >= TRUNCATION_ROW_FLOOR:
            raise BackfillTruncationError(
                f"{code} {chunk_start}~{chunk_end} 返回 {len(df)} 行,疑似截断")
        if df is not None and not df.empty:
            for row in df.to_dict("records"):
                td = str(row.get("trade_date"))
                amount = row.get("amount")
                out.append({
                    "date": f"{td[:4]}-{td[4:6]}-{td[6:]}",
                    "close": row.get("close"),
                    "amount_billion": round(amount / AMOUNT_TO_BILLION, 2)
                    if amount is not None else None,
                })
        chunk_start = f"{cy + CHUNK_YEARS}-01-01"
    out.sort(key=lambda r: r["date"])
    return out


def fetch_proxy(registry, date: str) -> dict:
    """资金流代理三路，各自独立失败不拖垮整体。

    moneyflow 三级顺序贴 spec #7:ths→dc→akshare fund_flow。前两个 capability akshare
    也声明了 dc,registry 会自动跨 provider 降级;第三级覆盖"仅 akshare fund_flow 可用"
    的残余场景。"""
    errors: list[str] = []
    moneyflow, mf_source = None, None
    for cap in ("get_sector_moneyflow_ths", "get_sector_moneyflow_dc", "get_sector_fund_flow"):
        r = registry.call(cap, date)
        if r.success and r.data:
            moneyflow = _normalize_moneyflow(r.data)
            mf_source = r.source
            break
        errors.append(f"{cap}: {getattr(r, 'error', None) or 'no data'}")
    etf = _safe_call(registry, "get_etf_flow", date, errors)
    margin = _safe_call(registry, "get_margin_data", date, errors)
    return {"moneyflow": moneyflow, "moneyflow_source": mf_source,
            "etf": etf, "margin": margin, "errors": errors}


def _safe_call(registry, cap: str, date: str, errors: list):
    r = registry.call(cap, date)
    if r.success and r.data:
        return r.data
    errors.append(f"{cap}: {getattr(r, 'error', None) or 'no data'}")
    return None


def _normalize_moneyflow(records: list) -> list[dict]:
    """归一不同源字段形态：统一输出 {name, net_amount_yi}，脏值剔除。"""
    out = []
    for row in records:
        name = row.get("name") or row.get("industry") or ""
        val = row.get("net_amount_yi")
        if val is None:
            val = row.get("net_inflow_billion")
        try:
            val = round(float(val), 2)
        except (TypeError, ValueError):
            continue
        if name:
            out.append({"name": name, "net_amount_yi": val})
    return out
