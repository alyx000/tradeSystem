"""研报覆盖·申万一级行业趋势（share-of-voice 多窗口对比）。

数据底座 = `raw_interface_payloads.research_report_list`（`cmd_post` post_extended 阶段每日落库，
历史缺口用 `backfill_missing_payloads` / CLI `research-digest trend --backfill N` 回补）。
行业标注复用 `collector.load_sw_map` + `collector.sw_prefix_index` 的 6 位裸码前缀 join。

两个实测数据坑决定本模块口径（2026-07 直连 cninfo 近 15 交易日采样）：

1. **占比（份额）而非绝对篇数**：cninfo 存在月末批量脉冲（2026-06-30 单日 185 篇 vs
   日常 50-90 篇），绝对篇数会把脉冲误读成全行业升温；share of voice 天然免疫。
2. **空日鉴别**：cninfo 按日存在合法真空日（2026-06-15/17/23 均为工作日却返 0 篇）。
   空日判定依据 `raw_interface_payloads.status/row_count`（IngestService 语义：采集成功但
   无行 = 'empty'，失败不落 payload 只记 ingest_errors），不依赖采集函数返回值；
   'empty' 日与未采集日都不进有效日窗口。

红线：本模块输出全部为 [事实] 级计数聚合；趋势解读由消费方（复盘/打分）标 [判断]，
不出价位、不构成买卖建议。
"""
from __future__ import annotations

import datetime
import logging
from collections import defaultdict

from db import queries as Q
from services.research_digest.collector import UNCLASSIFIED, load_sw_map, sw_prefix_index

logger = logging.getLogger(__name__)

INTERFACE_NAME = "research_report_list"
DEFAULT_DAYS_FACTOR = 4  # 未显式指定 days 时读库份数 = recent_n×4（双窗口 2×recent_n + 空日/缺口余量）


def load_daily_industry_series(conn, prefix_index: dict, *, days: int = 30) -> dict:
    """读库最近 `days` 份日 payload → {biz_date: {行业: 篇数}}（仅含有效日，升序）。

    payload 读取与解析走 `Q.list_raw_interface_daily_payloads`（status='empty' 真空日与
    脏 payload 都返 rows=[]，此处直接跳过）。
    """
    series: dict[str, dict] = {}
    for day in Q.list_raw_interface_daily_payloads(conn, interface_name=INTERFACE_NAME, limit=days):
        if not day["rows"]:
            # status=success 且 row_count>0 却解析不出行 = 脏 payload（写入截断/损坏），
            # 与合法真空日（status=empty）不同——该日本应有效却被剔出窗口，必须留痕。
            if day["status"] == "success" and day["row_count"]:
                logger.warning("[research-trend] %s payload 损坏（status=success, row_count=%s 但无法解析行），"
                               "该日被剔出趋势窗口", day["biz_date"], day["row_count"])
            continue
        agg: dict[str, int] = defaultdict(int)
        for r in day["rows"]:
            code6 = str(r.get("stock_code") or "").strip()[:6]
            entry = prefix_index.get(code6)
            industry = (entry.get("sw_l1") if entry else "") or UNCLASSIFIED
            agg[industry] += 1
        series[day["biz_date"]] = dict(agg)
    return dict(sorted(series.items()))


def build_industry_trend(series: dict, *, recent_n: int = 5) -> dict:
    """纯函数：近 recent_n 个有效日 vs 前 recent_n 个有效日的行业覆盖占比对比。

    - 窗口按**有效日**取（空日/未采集日不占窗口位），窗口实际日期由
      recent_days / prior_days 披露，消费方据此判断置信度。
    - prior 窗口数据不足时照算：prior_total=0 时 delta_pp 置 None（无对照，不伪造 0 变化）。
    """
    valid_dates = sorted(series)
    recent_dates = valid_dates[-recent_n:]
    prior_dates = valid_dates[-2 * recent_n:-recent_n]

    def _bucket(dates: list[str]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for d in dates:
            for ind, n in series[d].items():
                counts[ind] += n
        return counts

    recent, prior = _bucket(recent_dates), _bucket(prior_dates)
    recent_total, prior_total = sum(recent.values()), sum(prior.values())
    items = []
    for ind in set(recent) | set(prior):
        rc, pc = recent.get(ind, 0), prior.get(ind, 0)
        rs = rc / recent_total * 100 if recent_total else 0.0
        ps = pc / prior_total * 100 if prior_total else 0.0
        items.append({
            "industry": ind,
            "recent_count": rc,
            "recent_share": round(rs, 1),
            "prior_count": pc,   # render 不用，但保留在 --json 契约里供 agent 消费
            "prior_share": round(ps, 1),
            "delta_pp": round(rs - ps, 1) if prior_total else None,
        })
    # 未分类桶恒排最后（与 collector.aggregate_by_industry 同语义）；主序=近窗篇数
    items.sort(key=lambda x: (
        x["industry"] == UNCLASSIFIED, -x["recent_count"], -(x["delta_pp"] or 0.0), x["industry"],
    ))
    return {
        "recent_days": recent_dates,
        "prior_days": prior_dates,
        "recent_total": recent_total,
        "prior_total": prior_total,
        "items": items,
    }


def run_trend_report(conn, registry, *, days: int | None = None, recent_n: int = 5) -> dict:
    """服务入口：sw 行业标注 + 读库日序列 + 趋势聚合（CLI 只负责 wiring 与打印）。

    days 缺省 = recent_n × DEFAULT_DAYS_FACTOR；sw 映射失败降级全「未分类」（不致命）。
    """
    sw_map = load_sw_map(registry)
    if not sw_map:
        logger.warning("[research-trend] 申万行业映射不可用，本次全部归入「未分类」")
    prefix_index = sw_prefix_index(sw_map)
    series = load_daily_industry_series(
        conn, prefix_index, days=days or recent_n * DEFAULT_DAYS_FACTOR)
    # 双窗口需要 2×recent_n 个有效日；days 走缺省时空日/缺口可能吃掉配额，自动扩容重读一次
    if days is None and len(series) < 2 * recent_n:
        series = load_daily_industry_series(
            conn, prefix_index, days=recent_n * DEFAULT_DAYS_FACTOR * 3)
    if len(series) < 2 * recent_n:
        logger.warning("[research-trend] 有效日不足（%d < 2×%d），prior 窗口不完整，"
                       "Δpp 口径不对称或缺失（窗口实际日期见 recent_days/prior_days）",
                       len(series), recent_n)
    return build_industry_trend(series, recent_n=recent_n)


def backfill_missing_payloads(conn, registry, backfill_days: int) -> dict:
    """回补最近 backfill_days 个交易日中库内缺失的 payload，返回 {done, skipped, failed, scanned}。

    - 交易日取自库内日历游标（`get_prev_trade_date_from_db`，先 ensure 一次今年日历），
      日历缺口处提前停止（scanned < backfill_days 即为披露）。
    - 幂等完成态 = **非空 success**：empty 日（含合法真空日）每轮重采——cninfo 迟到回填是
      常态（实测同日上午 0 篇、下午 65 篇），empty 不是最终态；重采安全性由接口的
      preserve_nonempty_on_empty=True 保证（非空不被瞬时空窗抹掉，empty→非空允许升级）。
      真空日重采的代价 = 每轮几次多余接口调用，可接受。
    - 逐日独立 try/except，单日失败跳过不中断。
    """
    from services.ingest_service import IngestService
    from utils.trade_date import ensure_trade_calendar

    # 回补窗口可能跨年（如 1 月初回补 30 交易日触及去年 12 月），按窗口涉及的年份分别 ensure，
    # 否则游标在 1 月 1 日边界撞到日历缺口会静默截断
    today = datetime.date.today()
    approx_start = today - datetime.timedelta(days=backfill_days * 2 + 14)
    for year in sorted({today.year, approx_start.year}):
        try:
            ensure_trade_calendar(conn, registry, year=year)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[research-trend] %d 年交易日历确保失败（以库内已有日历继续）: %s", year, exc)

    # 从明天往回游标，取「≤今天的最近 backfill_days 个交易日」（含今天，若今天是交易日）
    target_dates: list[str] = []
    cursor = (today + datetime.timedelta(days=1)).isoformat()
    for _ in range(backfill_days):
        prev = Q.get_prev_trade_date_from_db(conn, cursor)
        if prev is None:
            break
        target_dates.append(prev)
        cursor = prev
    if len(target_dates) < backfill_days:
        # 日历缺口/全空时 scanned < 目标数，必须留痕——否则「回补完成 0/0/0」看着像成功
        logger.warning("[research-trend] 库内交易日历仅覆盖 %d/%d 个目标交易日"
                       "（日历缺失或采集失败），未覆盖的日期本次不会回补",
                       len(target_dates), backfill_days)

    # 完成态判定必须与趋势消费同一口径（最新快照 MAX(id) + 可解析非空行），否则
    # 「旧非空+新empty」或「success 但 payload 损坏」的日会被误判完成、永久跳过无法自愈
    existing: set[str] = set()
    if target_dates:
        stored = Q.raw_interface_biz_dates(
            conn, interface_name=INTERFACE_NAME, since=min(target_dates))
        if stored:
            existing = {
                day["biz_date"]
                for day in Q.list_raw_interface_daily_payloads(
                    conn, interface_name=INTERFACE_NAME, limit=len(stored))
                if day["rows"] and day["biz_date"] in stored
            }
    # 写库必须与传入 conn 同一库：IngestService 自建连接，不传 db_path 会写默认库，
    # 非默认库调用方（测试/多库）会出现「统计 done、payload 却写进另一库」
    db_row = conn.execute("PRAGMA database_list").fetchone()
    svc = IngestService(db_path=db_row[2] if db_row and db_row[2] else None, registry=registry)
    done = skipped = failed = 0
    for date_str in target_dates:
        if date_str in existing:
            skipped += 1
            continue
        try:
            run = svc.execute_interface(INTERFACE_NAME, date_str, triggered_by="cli")
        except Exception as exc:  # noqa: BLE001
            run = None
            logger.warning("[research-trend] 回补 %s 异常（跳过）: %s", date_str, exc)
        # provider/网络失败不抛异常，而是正常返回 status='failed'（不落 payload 只记
        # ingest_errors）——必须按返回 status 计数，否则失败被算成"新采"
        if run and run.get("status") in ("success", "empty"):
            done += 1
        else:
            failed += 1
            if run is not None:
                logger.warning("[research-trend] 回补 %s 未落库（status=%s）",
                               date_str, run.get("status"))
    return {"done": done, "skipped": skipped, "failed": failed, "scanned": len(target_dates)}


def render_trend_md(trend: dict, *, top_cap: int = 10) -> str:
    """趋势 markdown 表（全 [事实] 计数；不含任何操作建议词）。"""
    rd, pd_ = trend["recent_days"], trend["prior_days"]
    if not rd:
        return ("**研报覆盖·行业趋势**：暂无有效采集日"
                "（等待 cmd_post 逐日积累，或先执行 `research-digest trend --backfill N` 回补）。")
    header = (
        f"**研报覆盖·行业趋势**（近{len(rd)}有效日 {rd[0]}~{rd[-1]} 共{trend['recent_total']}篇"
        + (f" vs 前{len(pd_)}有效日 共{trend['prior_total']}篇" if pd_ else "；无前窗口对照")
        + "；占比=窗口内份额）"
    )
    lines = [header, "", "| 行业 | 近窗篇数 | 占比% | 前窗占比% | Δpp |", "|---|---|---|---|---|"]
    for it in trend["items"][:top_cap]:
        delta = f"{it['delta_pp']:+.1f}" if it["delta_pp"] is not None else "—"
        lines.append(
            f"| {it['industry']} | {it['recent_count']} | {it['recent_share']:.1f} "
            f"| {it['prior_share']:.1f} | {delta} |"
        )
    hidden = len(trend["items"]) - top_cap
    if hidden > 0:
        lines.append(f"| …还有 {hidden} 个行业 |  |  |  |  |")
    return "\n".join(lines)
