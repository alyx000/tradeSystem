"""volume_concentration 编排:daily(采集→落库→趋势→渲染)/ trend(只读渲染)/
build_trend_payload(只读 → 前端集中度趋势图 API 载荷)。"""
from __future__ import annotations

import logging

from . import collector, formatter, ranking, repo, trend
from .aggregator import UNCLASSIFIED

logger = logging.getLogger(__name__)

TREND_DAYS = 30
_STACK_TOP_K = 8           # 堆叠面积图保留的行业数(其余并入「其他」)
_RETENTION_MIN_STREAK = 2  # 连续在榜入快照的最小天数
_RETENTION_TOP_N = 12      # 连续在榜快照截断(限制载荷)


def _has_gain_coverage(universe) -> bool:
    """universe 是否能产出**用户可见的排名**——直接以最终展示契约为判据:
    跑 build_sector_gain_ranking 后,任一周期存在 max_gain 非 None 的板块即为"成功刷新"。

    用最终排名(而非"≥1 非 None 涨幅"代理)判定,一并覆盖所有降级路径——空 universe /
    全 None 涨幅 / 全『未分类』(申万映射失败但价格成功)——这些都派生出空榜单,
    视为降级、保留库内既有 gain_universe 不抹(codex 高:幂等,失败重跑不删既有可见榜单)。
    """
    if not universe:
        return False
    ranked = ranking.build_sector_gain_ranking(universe)
    return any(
        sector.get("max_gain") is not None
        for period_rows in ranked.values()
        for sector in period_rows
    )


def run_daily(conn, registry, date: str, trend_days: int = TREND_DAYS,
              persist: bool = True, refetch: bool = False) -> str | None:
    """daily 模式:read-through 采集 + 申万打标 → (落库) → 读最近 N 日算趋势 → 渲染 Markdown。

    无 top20 数据(非交易日/源全挂)→ collector 返 None,本函数直接返 None,
    上层据此不写库不推送(dec-8 非交易日兜底)。
    persist=False(CLI --dry-run 预览):不落库;趋势用「库里历史 + 内存中今日 record」拼,
    保证预览含当日而不污染真实库。
    refetch=True(CLI --refetch 回填历史):强制重拉 top20,绕过 daily_market 陈旧缓存。
    """
    record = collector.build_record(conn, registry, date, refetch=refetch)
    if record is None:
        return None

    # 区间涨幅排名原始集(成交额前50 → 5/10/20 日涨幅):独立 ancillary 特性,随 record 落库 + 进报告。
    # fail-closed —— 任何取数/解析异常(脏 bar / 非数值 close / provider 抖动)都降级并记日志,
    # 绝不拖垮既有 Top20 集中度日报的落库+渲染+推送(codex 中等:防可选排名路径在 save 前抛错回归既有 daily 工作流)。
    # 成功判据 = 新 universe 有"可用排名覆盖"(≥1 只票 ≥1 周期算出有限涨幅),而非仅"非空列表":
    # top50 取数成功但全部 get_stock_daily_range 失败/陈旧/脏值时,enrich 仍逐票返行但 gain 全 None,
    # 这种"非空全 None"无排名价值。仅在有覆盖时才覆盖落库;否则(空/全 None/异常)视为降级,
    # **保留库内已成功落库的 gain_universe**,避免同日降级重跑把有效榜单抹成空
    # (codex 高:Top20 read-through 成功但 top50/日线 provider 抖动的重跑不得删既有榜单;幂等)。
    gain_universe = None
    try:
        gain_universe = collector.build_gain_universe(registry, date)
    except Exception as exc:  # noqa: BLE001 — 边界吞异常是有意的(隔离 ancillary 失败,保住主日报)
        logger.warning("[volume-watch] 区间涨幅排名取数失败,降级(不阻断集中度日报): %s", exc)
    if _has_gain_coverage(gain_universe):
        record["gain_universe"] = gain_universe
    elif persist:
        prev = repo.get_concentration(conn, date)  # 降级 → 保留前次已落库榜单,幂等不抹
        record["gain_universe"] = (prev.get("gain_universe") if prev else None) or []
    else:
        record["gain_universe"] = []  # dry-run 降级:无库可保,留空(预览诚实略过该段)

    if persist:
        repo.save_concentration(conn, record)
        recent = repo.get_recent_concentration(conn, date, trend_days)
    else:
        recent = repo.get_recent_concentration(conn, date, trend_days)
        recent = [r for r in recent if r["date"] != date] + [record]  # 拼入内存今日
        # 截到窗口:与真跑(先落库再 LIMIT trend_days)同窗,避免 dry-run 多出一天致 CR3 分位/留存基准漂移(codex 中等)
        recent = sorted(recent, key=lambda r: r["date"])[-trend_days:]

    trend_result = trend.compute_trend(recent)
    return formatter.format_daily_report(record, trend_result)


def run_trend(conn, date: str, days: int = TREND_DAYS) -> str:
    """trend 模式:只读最近 N 日已落库快照,渲染趋势(不采集、不落库、不推送)。"""
    recent = repo.get_recent_concentration(conn, date, days)
    if not recent:
        return "暂无集中度数据(需先跑 volume-watch daily 累积)。"
    record = recent[-1]  # 最新一日作报告主体
    trend_result = trend.compute_trend(recent)
    return formatter.format_daily_report(record, trend_result)


def _classified_shares(record: dict) -> dict:
    """某日各行业占 top_n 比重(%,round1),排除「未分类」。"""
    return {s["industry"]: round(s.get("share_in_top_n", 0) * 100, 1)
            for s in record.get("sector_summary", [])
            if s.get("industry") and s["industry"] != UNCLASSIFIED}


def _stack_sector_keys(day_shares_list: list[dict]) -> list[str]:
    """跨窗口按累计占比取前 K 行业(排未分类),并列按行业名升序;有残量(尾部/未分类)则附「其他」。
    入参为各日已算好的 classified shares(避免重复扫描 sector_summary)。"""
    cumulative: dict = {}
    for shares in day_shares_list:
        for ind, pct in shares.items():
            cumulative[ind] = cumulative.get(ind, 0.0) + pct
    ranked = sorted(cumulative.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [ind for ind, _ in ranked[:_STACK_TOP_K]]
    # 任一日 top 之和 < 100(存在尾部行业或未分类)→ 需要「其他」桶
    has_other = any(round(100.0 - sum(shares.get(k, 0.0) for k in top), 1) > 0
                    for shares in day_shares_list)
    return top + (["其他"] if has_other else [])


def _series_point(record: dict, day_shares: dict, keys: list[str]) -> dict:
    """单日 series 点:cr3 / 头部成交额 / 占两市% / 各 sector_key 占比(缺补 0,其他=100-Σtop)。
    cr3 用 trend._cr3(record) 直算(非从已 round 的 day_shares 反推),与报告/钉钉同口径。"""
    mt = record.get("market_total_billion")
    total = record.get("total_amount_billion")
    top_keys = [k for k in keys if k != "其他"]
    sectors = {k: day_shares.get(k, 0.0) for k in top_keys}
    if "其他" in keys:
        other = round(100.0 - sum(sectors.values()), 1)
        sectors["其他"] = other if other > 0 else 0.0  # 钳非负:浮点 overshoot 致 -0.0/微负,堆叠图不容负带
    return {
        "date": record["date"],
        "date_short": record["date"][5:],  # YYYY-MM-DD → MM-DD(全仓库硬约束 YYYY-MM-DD)
        "cr3": trend._cr3(record, UNCLASSIFIED),
        "total_amount_billion": total,
        "market_share_pct": round(total / mt * 100, 2) if mt else None,
        "sectors": sectors,
    }


def _snapshot(records: list[dict], trend_result: dict) -> dict:
    """最新日快照:连续在榜(streak≥2,截断)+ 异动(新进带行业/涨跌,退出仅名称)。"""
    retention = [{"name": r["name"] or r["code"], "streak": r["streak"]}
                 for r in trend_result["stock_retention"]
                 if r["streak"] >= _RETENTION_MIN_STREAK][:_RETENTION_TOP_N]
    meta_by_code = {s.get("code"): s for s in (records[-1].get("stocks") or [])}
    rot = trend_result["stock_rotation"]
    new = []
    for x in rot["new"]:
        s = meta_by_code.get(x["code"], {})
        new.append({"name": x.get("name") or x["code"],
                    "industry": s.get("industry") or "",  # 显式 null 时 .get(default) 不生效,归一化为 ""(诚实契约 string)
                    "change_pct": s.get("change_pct")})
    dropped = [{"name": x.get("name") or x["code"]} for x in rot["dropped"]]
    return {"date": records[-1]["date"], "retention": retention,
            "rotation": {"new": new, "dropped": dropped}}


def build_sector_gain_ranking_payload(conn, date: str) -> dict:
    """只读:产出某交易日「成交额前50 板块区间涨幅排名」载荷(供复盘网站 API)。

    读 daily_volume_concentration[date].gain_universe → 调纯函数派生三档排名。
    无记录/旧记录(gain_universe 空)→ 返空壳(三档空列表),前端展示空态不报错。
    """
    record = repo.get_concentration(conn, date)
    universe = record.get("gain_universe") if record else None
    if not universe:
        return {"date": date, "rankings": {"5d": [], "10d": [], "20d": []}}
    return {"date": date, "rankings": ranking.build_sector_gain_ranking(universe)}


def build_trend_payload(conn, days: int = TREND_DAYS, end_date: str | None = None) -> dict:
    """只读:产出前端集中度趋势图载荷(series 逐日 + sector_keys 堆叠键 + snapshot 最新日快照)。

    end_date=None(默认)→ 取库内最新 N 日(不依赖墙钟);传入则取 <= end_date 的最近 N 日(测试确定性)。
    series 逐日直算(cr3/占比/占两市);compute_trend 仅用于 snapshot(留存/异动)。无数据返空壳。
    """
    days = max(1, days)  # service 自防:days<=0 会令 SQLite LIMIT 负数退化为全量(CLI/直调也安全)
    records = (repo.get_recent_concentration(conn, end_date, days) if end_date
               else repo.get_latest_concentration(conn, days))
    if not records:
        return {"requested_days": days, "series": [], "sector_keys": [], "snapshot": None}
    day_shares_list = [_classified_shares(r) for r in records]   # 每日只算一次,供 keys + series 复用
    keys = _stack_sector_keys(day_shares_list)
    series = [_series_point(r, ds, keys) for r, ds in zip(records, day_shares_list)]
    snapshot = _snapshot(records, trend.compute_trend(records))
    return {"requested_days": days, "series": series, "sector_keys": keys, "snapshot": snapshot}
