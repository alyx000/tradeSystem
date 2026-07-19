"""sector_crowding 采集层：sw_daily 行业行情 + 两市总额守卫 + 资金流代理三路。"""
from __future__ import annotations

import logging
import math

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


def _finite_num(v) -> bool:
    """有限数值守卫:pandas 缺值即 NaN 浮点,is None 挡不住;NaN 落库会写成非标 JSON token。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _amount_billion(amount) -> float | None:
    """amount(万元)→亿元的单点换算(daily 与 backfill 共用,防口径分叉);非有限值 → None。"""
    return round(amount / AMOUNT_TO_BILLION, 2) if _finite_num(amount) else None


def _clean_close(close) -> float | None:
    """close 非有限值置 None:NaN 经 json.dumps 会落成非标 token,严格 JSON 消费端直接炸。"""
    return close if _finite_num(close) else None


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
    if not l1_codes:
        # 码表拉取失败被惰性缓存为空集(进程内不重试,仓库既有模式):原生 L1 行会被过滤、
        # 走合成降级(name=code/close 缺席)。留日志便于排障,daily 单次进程影响面小。
        logger.warning("[sector-crowding] L1 码表为空(拉取失败?),原生 L1 行将被过滤并降级合成")
    sectors = []
    for row in df.to_dict("records"):
        code = row.get("ts_code")
        level = "L1" if code in l1_codes else ("L2" if code in l2_codes else None)
        if level is None:
            continue
        sectors.append({
            "code": code, "name": row.get("name"), "level": level,
            "close": _clean_close(row.get("close")),
            "amount_billion": _amount_billion(row.get("amount")),
        })
    if not sectors:
        return None
    sectors, l1_status = resolve_l1(sectors, provider._ensure_sw_l1_parent_map)
    return {"sectors": sectors, "meta": {"l1_status": l1_status, "source": "tushare:sw_daily"}}


def resolve_l1(sectors: list[dict], parent_map_getter) -> tuple[list[dict], str]:
    """L1 状态机单一真源（daily 与 backfill 共用）:native / synthesized / missing。

    映射不可靠(getter 返回空)时禁止合成(spec v2 严重1:合成路径条件启用)。"""
    if any(s.get("level") == "L1" for s in sectors):
        return sectors, "native"
    parent_map = parent_map_getter() or {}
    if parent_map:
        return sectors + synthesize_l1(sectors, parent_map), "synthesized"
    return sectors, "missing"


def synthesize_l1(l2_sectors: list[dict], parent_map: dict) -> list[dict]:
    """L2 成交额按 parent_code 归并成 L1。close 不可加总 → None（斜率维度缺席）。

    daily 与 backfill 共用同一分支逻辑(Explore review 中1):回填不合成会导致合成 L1
    永无历史序列 → 分位/双高对 L1 长期失效。"""
    agg: dict = {}
    for s in l2_sectors:
        if s.get("level") != "L2" or not _finite_num(s.get("amount_billion")):
            continue  # NaN 参与加总会把整个合成 L1 毒成 NaN(单行脏值放大为整行业缺席)
        parent = parent_map.get(s.get("code"))
        if not parent:
            continue
        ent = agg.setdefault(parent, {"code": parent, "name": parent, "level": "L1",
                                      "close": None, "amount_billion": 0.0})
        ent["amount_billion"] += s["amount_billion"]
    for ent in agg.values():
        ent["amount_billion"] = round(ent["amount_billion"], 2)  # 出循环一次 round,免截断误差累积
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
    # NaN 前置拦截:三段守卫全是 < 比较,NaN 比较恒 False 会穿透全部守卫、绕过
    # missing_data 标注渲染出 "nan 亿"(memory:降级链"成功但含脏值"事故同型)
    if total is not None and not _finite_num(total):
        logger.warning("[sector-crowding] %s 两市成交额为非有限值(%r),落 NULL(source=%s)",
                       date, total, result.source)
        return None, None
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
# 单码瞬时超时重试:~330 次请求里镜像随机掉 1 个(实测两轮各废一整轮 15 分钟),
# 重试吸收瞬时抖动;重试穷尽仍失败才记 codes_failed 触发 fail-closed(保底语义不变)
CODE_FETCH_RETRIES = 3
CODE_FETCH_RETRY_SLEEP_SECONDS = 2.0
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
                # pandas 列含缺值会整列 int→float64:str() 出 "20200101.0"/"nan",
                # 直接切片会批量生成畸形日期键落库且永不与 daily 行对齐 → 跳行留日志
                if len(td) != 8 or not td.isdigit():
                    logger.warning("[sector-crowding backfill] %s 畸形 trade_date %r,跳过该行",
                                   code, td)
                    continue
                out.append({
                    "date": f"{td[:4]}-{td[4:6]}-{td[6:]}",
                    "close": _clean_close(row.get("close")),
                    "amount_billion": _amount_billion(row.get("amount")),
                })
        chunk_start = f"{cy + CHUNK_YEARS}-01-01"
    out.sort(key=lambda r: r["date"])
    return out


def _fetch_code_history_with_retry(provider, code: str, start: str, end: str) -> list[dict]:
    """fetch_code_history 加瞬时故障重试。截断异常不重试(数据问题非网络问题)。"""
    import time

    last_exc: Exception | None = None
    for attempt in range(1, CODE_FETCH_RETRIES + 1):
        try:
            return fetch_code_history(provider, code, start, end)
        except BackfillTruncationError:
            raise
        except Exception as e:
            last_exc = e
            if attempt < CODE_FETCH_RETRIES:
                logger.info("[sector-crowding backfill] %s 第 %d 次失败(%s),%.0fs 后重试",
                            code, attempt, e, CODE_FETCH_RETRY_SLEEP_SECONDS)
                time.sleep(CODE_FETCH_RETRY_SLEEP_SECONDS)
    raise last_exc  # type: ignore[misc]


def fetch_history_by_date(provider, start: str, end: str) -> tuple[dict, list[str]]:
    """回填阶段①（采集层）:码表枚举 → 逐码分片拉取 → 按日期聚合。

    单码失败记账继续;截断异常向上抛不吞(疑似截断宁可整体失败也不落半截)。
    回填行 name 暂存 code(sw_daily 区间接口不保证带 name),report 渲染以当日行为准。"""
    l1 = provider._ensure_sw_l1_codes() or set()
    l2 = provider._ensure_sw_l2_codes() or set()
    if not l1 or not l2:
        # 码表空集=拉取失败(真机实测 L1=31/L2=134 恒非空)。不抛错会静默写出半截历史,
        # 且这些日期随后被 get_existing_dates 判"已有"锁死,重跑无法自愈 → 整体中止
        raise RuntimeError(
            f"sector-crowding backfill: 申万码表为空(L1={len(l1)}/L2={len(l2)}),疑拉取失败,中止回填")
    by_date: dict = {}
    codes_failed: list[str] = []
    for code, level in [(c, "L1") for c in sorted(l1)] + [(c, "L2") for c in sorted(l2)]:
        try:
            bars = _fetch_code_history_with_retry(provider, code, start, end)
        except BackfillTruncationError:
            raise
        except Exception as e:
            logger.warning("[sector-crowding backfill] %s 失败(重试 %d 次后): %s",
                           code, CODE_FETCH_RETRIES, e)
            codes_failed.append(code)
            continue
        for bar in bars:
            by_date.setdefault(bar["date"], []).append(
                {"code": code, "name": code, "level": level,
                 "close": bar["close"], "amount_billion": bar["amount_billion"]})
    return by_date, codes_failed


def fetch_proxy(registry, date: str) -> dict:
    """资金流代理三路，各自独立失败不拖垮整体。

    moneyflow 三级顺序贴 spec #7:ths→dc→akshare fund_flow。前两个 capability akshare
    也声明了 dc,registry 会自动跨 provider 降级;第三级覆盖"仅 akshare fund_flow 可用"
    的残余场景。"""
    errors: list[str] = []
    moneyflow, mf_source = None, None
    for cap in ("get_sector_moneyflow_ths", "get_sector_moneyflow_dc", "get_sector_fund_flow"):
        r = _try_call(registry, cap, date, errors)
        if r is not None:
            moneyflow, mf_source = _normalize_moneyflow(r.data), r.source
            break
    etf_r = _try_call(registry, "get_etf_flow", date, errors)
    margin_r = _try_call(registry, "get_margin_data", date, errors)
    return {"moneyflow": moneyflow, "moneyflow_source": mf_source,
            "etf": _normalize_etf(etf_r.data) if etf_r else None,
            "margin": _clean_margin(margin_r.data) if margin_r else None, "errors": errors}


def _normalize_etf(records: list) -> list[dict]:
    """ETF 代理归一(与 moneyflow 对称):数值字段非有限即置 None,防 NaN 落库/渲染 +nan 亿份。"""
    out = []
    for row in records or []:
        out.append({
            "code": row.get("code"), "name": row.get("name"),
            "total_shares_billion": row.get("total_shares_billion")
            if _finite_num(row.get("total_shares_billion")) else None,
            "shares_change_billion": row.get("shares_change_billion")
            if _finite_num(row.get("shares_change_billion")) else None,
        })
    return out


def _clean_margin(data: dict | None) -> dict | None:
    """两融代理清洗:白名单重建输出,数值字段非有限置 None,主值非有限整体置 None。

    不原样透传外部 dict:嵌套字段(exchanges 等)含 NaN 会随 proxy_json 落成非标 JSON
    (codex 门2 轮2 中);未消费字段一律不带。"""
    if not isinstance(data, dict) or not _finite_num(data.get("total_rzrqye_yi")):
        return None
    return {
        "trade_date": data.get("trade_date"),
        "requested_date": data.get("requested_date"),
        "market_scope": data.get("market_scope"),
        "total_rzrqye_yi": data["total_rzrqye_yi"],
        "total_rzye_yi": data.get("total_rzye_yi")
        if _finite_num(data.get("total_rzye_yi")) else None,
        "total_rqye_yi": data.get("total_rqye_yi")
        if _finite_num(data.get("total_rqye_yi")) else None,
    }


def _try_call(registry, cap: str, date: str, errors: list):
    """registry.call 薄封装:成功返 result 对象,失败按统一格式记账返 None。"""
    r = registry.call(cap, date)
    if r.success and r.data:
        return r
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
            val = float(val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue  # float("nan")/inf 不抛异常但会毒化排序比较并落成非标 JSON
        if name:
            out.append({"name": name, "net_amount_yi": round(val, 2)})
    return out
