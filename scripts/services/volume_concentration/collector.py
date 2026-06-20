"""成交额 top20 采集 + 申万行业打标 + 组装 record(read-through 读库优先,缺则重拉)。"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from db import queries
from services import concept_tags

from .aggregator import UNCLASSIFIED, aggregate_sectors

# 区间涨幅排名(独立于 Top20 集中度):成交额前 50 → 算 5/10/20 日涨幅 → 按板块排名。
TOP_VOLUME_UNIVERSE_N = 50
GAIN_PERIODS = (5, 10, 20)
# 区间回看自然日跨度:覆盖 20 日榜所需的 21 根 bar(≈ 30 个交易日,留足停牌冗余)。
GAIN_LOOKBACK_DAYS = 45
# 题材榜降噪:只对同花顺资金净流入 Top-M 热概念出榜(否则一只热票的几十个概念标签全冒头,真实数据
# 实测 91 个题材且头部冗余)。M=15 给复盘较全的热题材视野。
HOT_CONCEPT_TOP_M = 15


def _coerce_stock_list(raw) -> list:
    """daily_market.top_volume_stocks 三态归一:list 直用 / JSON str 解析 / 其它→[]。

    get_daily_market 返回原始 TEXT 不自动 json.loads(queries.py:797),故必须在此解析。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def load_top20(conn, registry, date: str, top_n: int = 20, refetch: bool = False) -> list[dict]:
    """read-through 取当日 top20:先读 daily_market.top_volume_stocks(命中即用,零重拉),
    缺失/空则 registry.call('get_top_volume_stocks') 自愈重拉(dec-1 C 方案)。

    refetch=True:跳过读库,强制重拉 provider —— 用于回填历史(库里 top_volume_stocks
    可能是某次换算 fix 之前采集的陈旧值,read-through 命中即用会灌坏数据)。
    """
    if not refetch:
        row = queries.get_daily_market(conn, date)
        if row is not None:
            stocks = _coerce_stock_list(row.get("top_volume_stocks"))
            if stocks:
                return stocks
    result = registry.call("get_top_volume_stocks", date, top_n)
    return result.data if (result.success and result.data) else []


def _fetch_market_total(registry, date: str):
    """单拉两市成交额(get_market_volume,自带降级);失败返 (None, None) 不阻断。"""
    result = registry.call("get_market_volume", date)
    if result.success and result.data:
        return result.data.get("total_billion"), result.source
    return None, None


def build_record(conn, registry, date: str, top_n: int = 20, refetch: bool = False) -> dict | None:
    """编排当日集中度 record:read-through 取 top20 → 申万打标 → market_total 单拉 →
    聚合 → 组装。无 top20 数据(非交易日/源全挂)返 None,由上层不写库不推送。

    refetch=True 透传给 load_top20,强制重拉绕过陈旧缓存(回填历史用)。
    """
    stocks = load_top20(conn, registry, date, top_n, refetch=refetch)
    if not stocks:
        return None

    labeled = label_industries(stocks, registry)
    market_total, market_total_source = _fetch_market_total(registry, date)
    agg = aggregate_sectors(labeled["stocks"])

    return {
        "date": date,
        "top_n": agg["top_n"],
        "total_amount_billion": agg["total_amount_billion"],
        "market_total_billion": market_total,
        "stocks": labeled["stocks"],
        "sector_summary": agg["sector_summary"],
        "source": {
            "industry_source": labeled["industry_source"],
            "industry_coverage": agg["industry_coverage"],
            "market_total_source": market_total_source,
        },
    }


def _finite_num(v) -> bool:
    """是否为有限数值(拒绝 None/bool/字符串/NaN/Inf),防脏 close 击穿涨幅计算。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _interval_gain(closes: list, n: int) -> float | None:
    """区间涨幅 %:(close[-1]/close[-1-n] - 1) * 100。

    需 ≥ n+1 根 bar(否则历史不足);基准/最新 close 须为有限数值且基准 > 0,
    结果须有限(否则 None)—— 防除零 + 防 NaN/Inf 脏值污染榜单(codex 中等)。
    """
    if len(closes) < n + 1:
        return None
    base = closes[-1 - n]
    last = closes[-1]
    if not (_finite_num(base) and _finite_num(last)) or base <= 0:
        return None
    gain = round((last / base - 1) * 100, 2)
    return gain if math.isfinite(gain) else None


def enrich_interval_gains(stocks: list[dict], end_date: str, registry,
                          periods=GAIN_PERIODS, lookback_days: int = GAIN_LOOKBACK_DAYS) -> list[dict]:
    """逐股取区间日线(get_stock_daily_range),算各周期区间涨幅写回 gain_{n}d。

    以 end_date 为区间终点,回看 lookback_days 自然日;provider 失败/历史不足 → 该周期 None。
    复用 get_stock_daily_range 升序 bars 契约(最后一根=区间终点),口径同趋势主升缓涨判定。
    """
    start = (datetime.strptime(end_date, "%Y-%m-%d")
             - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    out: list[dict] = []
    none_gains = {f"gain_{n}d": None for n in periods}
    for s in stocks:
        code = s.get("code")
        bars = []
        if code:
            r = registry.call("get_stock_daily_range", code, start, end_date)
            bars = r.data if (getattr(r, "success", False) and isinstance(r.data, list)) else []
        usable = [b for b in bars if b.get("close") is not None]
        # 红线诚实:最新一根 bar 必须正好落在榜单日(end_date)且 close 为有限数值。停牌/数据延迟/
        # 部分区间会让 closes[-1] 落在 end_date 之前,脏 close(NaN/Inf/非数值)也不可信;此时算出的
        # 5/10/20 日涨幅是"陈旧窗口/脏值",冒充当日 [事实] 会误导复盘 → 全 None(该股剔出排名),宁缺毋假
        # (codex 中等:防陈旧窗口冒充当日 + 防 NaN/Inf 污染榜单)。closes 保留原位(不滤中段),
        # 由 _interval_gain 对各周期 base/last 单独校验有限性,避免删中段 bar 致窗口错位。
        if (not usable or usable[-1].get("trade_date") != end_date
                or not _finite_num(usable[-1].get("close"))):
            out.append({**s, **none_gains})
            continue
        closes = [b.get("close") for b in usable]
        gains = {f"gain_{n}d": _interval_gain(closes, n) for n in periods}
        out.append({**s, **gains})
    return out


def enrich_concepts(universe: list[dict], registry, date: str,
                    top_m: int = HOT_CONCEPT_TOP_M) -> tuple[list[dict], bool]:
    """给 universe 每只票加 `concepts`(供题材榜分组):该票概念 ∩ 资金净流入 Top-M 热概念。
    返回 (universe_with_concepts, concept_ok)。

    两步过滤:① concept_tags.build_stock_concept_map(get_ths_member 反查)+ 容器过滤(≤300);
    ② ∩ concept_tags.hot_concepts(资金 Top-M 热概念)降噪——避免一只热票的几十个概念标签全冒头。
    **concept_ok = ok and hot_ok and cov**(provider 成功且 ths_member 无部分覆盖缺失):
    上层据此决定降级回填——只在 concept_ok=False(真失败/部分覆盖)时保留旧 concepts;concept_ok=True
    但当日无可出榜题材(热概念交集为空)是**健康空**,如实置空,不能回填旧 stale 题材(codex 高:反向污染)。
    """
    cmap, mcount, ok = concept_tags.build_stock_concept_map(registry, date)
    hot, hot_ok, cov = (concept_tags.hot_concepts(registry, date, top_m, mcount)
                        if ok else (set(), False, True))
    # bool(hot):资金 moneyflow 成功但返空表(交易日静默零行)→ 无热概念 → 也算降级。否则 concept_ok 会
    # =True 走"健康空"路径抹掉旧题材(codex 高:空成功响应不得冒充健康空)。本工作流只在交易日(build_record
    # 已成功)到此,正常日 moneyflow 必有数百概念,空表=provider 抖动。trend_leader 的 hot_concepts 契约不动。
    concept_ok = ok and hot_ok and cov and bool(hot)
    out: list[dict] = []
    for s in universe:
        bare = (s.get("code") or "").split(".")[0]
        concepts = ([c for c in concept_tags.stock_real_concepts(bare, cmap, mcount) if c in hot]
                    if concept_ok else [])
        out.append({**s, "concepts": concepts})
    return out, concept_ok


def build_gain_universe(registry, date: str, top_n: int = TOP_VOLUME_UNIVERSE_N) -> list[dict]:
    """成交额前 top_n 个股 → 申万打标 → 区间涨幅,组装区间涨幅排名原始集。

    独立于 Top20 集中度(自取 top50、自标行业),无数据返 []。
    """
    res = registry.call("get_top_volume_stocks", date, top_n)
    stocks = res.data if (getattr(res, "success", False) and res.data) else []
    if not stocks:
        return []
    labeled = label_industries(stocks, registry)["stocks"]
    return enrich_interval_gains(labeled, date, registry)


def label_industries(stocks: list[dict], registry) -> dict:
    """给 top20 打申万二级行业标签 + 回填 name,三级降级:

    ① 申万成分命中 → industry=sw_l2、name 取申万(回填空 name)
    ② 缺成分(多为次新)→ 批量 get_stock_basic_batch 兜 name、industry=「未分类」
    ③ stock_basic 也无 → name 留原值(常为空)、industry=「未分类」

    返回 {stocks: 已打标列表, industry_source: 溯源字符串(供 source_json)}。
    """
    sw_result = registry.call("get_stock_sw_industry_map")
    sw_map = sw_result.data if (sw_result.success and sw_result.data) else {}
    industry_source = (
        sw_result.source if sw_result.success else f"sw_failed:{sw_result.error}"
    )

    labeled: list[dict] = []
    miss_codes: list[str] = []
    for s in stocks:
        code = s.get("code")
        entry = sw_map.get(code)
        if entry and entry.get("sw_l2"):
            labeled.append({
                **s,
                "industry": entry["sw_l2"],
                "name": entry.get("name") or s.get("name", ""),
            })
        else:
            miss_codes.append(code)
            labeled.append({**s, "industry": UNCLASSIFIED, "name": s.get("name", "")})

    if miss_codes:
        basic_result = registry.call("get_stock_basic_batch", miss_codes)
        basic_name: dict[str, str] = {}
        if basic_result.success and basic_result.data:
            for r in basic_result.data:
                c = r.get("ts_code")
                if c:
                    basic_name[c] = r.get("name") or ""
        if basic_name:
            industry_source = f"{industry_source}+stock_basic兜底"
        miss_set = set(miss_codes)
        for s in labeled:
            if s["code"] in miss_set:
                s["name"] = basic_name.get(s["code"]) or s.get("name", "")

    return {"stocks": labeled, "industry_source": industry_source}
