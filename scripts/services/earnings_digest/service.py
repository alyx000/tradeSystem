"""编排层：权威取数落库 → 读回 → 水位线过滤 → 标准化 → 缺口验证 → 渲染。

设计约束（方案 v4）：
- 单次权威取数：外网请求只发生在 IngestService.execute_interface（落库）与行情/
  行业辅助查询；推送内容一律从落库 payload 读回，与存档严格同批。
- 采集失败降级：execute_interface 失败只记审计不中断（读回历史 payload 继续），
  水位线过滤后自然推空 → 不推送，失败可在 ingest inspect 里追溯。
- 周日/非交易日：当日无行情 → 缺口段自然为空；新公告照常推（供周日复盘）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from db.connection import get_db
from services.ingest_service import IngestService
from utils.trade_date import get_prev_trade_date

from . import collector, expectation, gap_check, normalize, renderer
from .collector import position_hit

logger = logging.getLogger(__name__)

_FORECAST = "earnings_forecast"
_EXPRESS = "earnings_express"
# 快报对标预告区间的历史回看天数：预告（1/4/7/10 月窗口）通常先于同期快报 ≤2 个月
_FORECAST_LOOKBACK_FOR_EXPRESS_DAYS = 90
# 一致预期（口径三）：report_rc 取数回看窗 + 限流上限（per-stock 2 次外网调用，
# 只对命中票/Top 候选做，cap 防中报高峰几十只票拖垮 22:00 批任务）
_CONSENSUS_REPORT_RC_DAYS = 120
_CONSENSUS_STOCK_CAP = 40
# 全市场 daily 单次返回行数地板：A 股 ~5400 只，低于此值疑似镜像截断/部分返回。
# 缺口验证对缺行情候选静默 continue，故覆盖不足时显式渲成提示（codex review 2026-06-12）。
# 取 4000（~市场规模 74%）而非更高：当日停牌 + 未开板新股 + 长假后部分停牌可达数百只，
# 阈值留足容差只兜「大面积缺失」（截断按页丢尾部成片缺，非零散停牌），避免常态误报。
_MIN_EXPECTED_MARKET_QUOTES = 4000


@dataclass
class DigestResult:
    target_date: str
    title: str | None = None
    markdown: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    # 本次 digest 内容的已推业务键（CLI 推送成功后落 .pushed 标记，供同日重跑去重）
    pushed_announcement_keys: set[str] = field(default_factory=set)
    pushed_gap_keys: set[str] = field(default_factory=set)
    pushed_warning_keys: set[str] = field(default_factory=set)

    @property
    def has_content(self) -> bool:
        return self.markdown is not None


def _archive(ingest: IngestService, interface_name: str, target_date: str, input_by: str | None) -> None:
    # triggered_by 有 DB CHECK 约束（cli/api/system）；digest 内部带起的采集
    # 对齐 cmd_post 盘后主流程语义用 "system"，触发来源细分靠 input_by 区分
    result = ingest.execute_interface(
        interface_name, target_date, triggered_by="system", input_by=input_by,
    )
    status = result.get("status")
    if status == "failed":
        run = result.get("run") or {}
        logger.warning("[earnings-digest] %s 采集失败（已记审计，继续用历史存档）: %s",
                       interface_name, run.get("error"))


def _industry_map(registry) -> dict[str, dict] | None:
    result = registry.call("get_stock_sw_industry_map")
    if result.success and isinstance(result.data, dict):
        return result.data
    logger.warning("[earnings-digest] 申万行业映射获取失败，跳过行业聚合段: %s", result.error)
    return None


def _quotes(registry, date: str) -> tuple[list[dict], str | None]:
    """返回 (行情行, 错误)。失败 ≠ 空：非交易日是「成功返回空」，故障必须可见
    （codex review：交易日行情故障若与非交易日同貌，缺口段会静默消失装作干净空日）。"""
    result = registry.call("get_market_daily_quotes", date)
    if result.success and isinstance(result.data, list):
        return result.data, None
    logger.error("[earnings-digest] %s 行情获取失败，缺口验证将缺席: %s", date, result.error)
    return [], result.error or "unknown"


def _consensus_labels(
    registry, target_date: str, items: list[dict], position_codes: dict[str, set[str]],
    *, min_profit_wan: float,
) -> dict[str, dict]:
    """口径三：对命中票 + Top-up 候选票算券商一致预期（仅 0630 中报期）。

    每股 2 次外网调用（report_rc + income），故只覆盖决策相关小集合并 cap 限流；
    per-stock 失败/无覆盖优雅跳过（不阻塞推送）。返回 {ts_code: assess 结果}。
    """
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    rc_start = (target_dt - timedelta(days=_CONSENSUS_REPORT_RC_DAYS)).strftime("%Y-%m-%d")
    # 候选集 = 持仓/关注命中 ∪ Top-up 候选；仅中报 0630 期（H1 占比折算的适用前提）
    def _eligible(it: dict) -> bool:
        return it["end_date"].endswith("0630") and (
            position_hit(it["ts_code"], position_codes) is not None
            or normalize.is_top_up_candidate(it, min_profit_wan)
        )
    candidates = [it for it in items if _eligible(it)]
    if len(candidates) > _CONSENSUS_STOCK_CAP:
        logger.warning("[earnings-digest] 一致预期候选 %d 只超 cap %d，截断（命中优先）",
                       len(candidates), _CONSENSUS_STOCK_CAP)
        hit = [it for it in candidates if position_hit(it["ts_code"], position_codes)]
        rest = [it for it in candidates if not position_hit(it["ts_code"], position_codes)]
        candidates = (hit + rest)[:_CONSENSUS_STOCK_CAP]

    labels: dict[str, dict] = {}
    for it in candidates:
        ts_code, end_date = it["ts_code"], it["end_date"]
        year = int(end_date[:4])
        rc = registry.call("get_analyst_forecasts", ts_code, rc_start, target_date)
        inc = registry.call("get_income_history", ts_code, f"{year - 5}-01-01", f"{year - 1}-12-31")
        if not (rc.success and inc.success):
            continue  # per-stock 数据源故障 → 无标签
        verdict = expectation.assess(
            it["net_profit_mid_wan"], rc.data or [], inc.data or [], year)
        if verdict:
            labels[ts_code] = verdict
    return labels


def run_daily_digest(
    registry,
    target_date: str,
    *,
    db_path: str | None = None,
    input_by: str | None = "manual",
    top_n: int = renderer.DEFAULT_TOP_N,
    min_profit_wan: float = renderer.DEFAULT_MIN_PROFIT_WAN,
    gap_threshold_pct: float = gap_check.DEFAULT_GAP_THRESHOLD_PCT,
    gap_display_cap: int = renderer.GAP_DISPLAY_CAP,
    enable_consensus: bool = True,
    skip_archive: bool = False,
) -> DigestResult:
    """跑一次每日速报。

    :param enable_consensus: 口径三券商一致预期（每股 2 次外网调用，可关）。
    :param skip_archive: 仅测试/复盘用，跳过采集直接读历史存档。
    """
    ingest = IngestService(db_path, registry=registry)
    if not skip_archive:
        _archive(ingest, _FORECAST, target_date, input_by)
        _archive(ingest, _EXPRESS, target_date, input_by)

    prev_trade = get_prev_trade_date(registry, target_date)

    with get_db(db_path) as conn:
        # 推送增量：**近窗**payload + 水位线过滤。本批读取必须限定近几天的快照——
        # 「最近一份非空」会在当日空窗时回退到任意陈旧存档（实测回退到数月前的
        # 历史回填 payload 并整批误推），近窗边界把陈旧存档挡在"本批"之外
        recent_start = (
            datetime.strptime(target_date, "%Y-%m-%d")
            - timedelta(days=collector._SEEN_KEYS_SCAN_DAYS)
        ).strftime("%Y-%m-%d")
        fc_rows = collector.read_payload_rows_between(conn, _FORECAST, recent_start, target_date)
        ex_rows = collector.read_payload_rows_between(conn, _EXPRESS, recent_start, target_date)
        fc_new = collector.filter_new_since_watermark(
            fc_rows, collector.get_push_watermark(conn, _FORECAST, target_date))
        ex_new = collector.filter_new_since_watermark(
            ex_rows, collector.get_push_watermark(conn, _EXPRESS, target_date))

        # 同日重跑幂等兜底：水位线只跨日推进、不覆盖 target_date 当日，故再用本日
        # 已推标记剔除本次之前已推过的内容（重跑只保留增量；标记于推送成功后由 CLI 落盘）
        marker_dir = renderer.resolve_report_dir()
        pushed = collector.load_pushed(target_date, marker_dir)
        fc_new = collector.filter_unpushed(fc_new, _FORECAST, pushed["announcements"])
        ex_new = collector.filter_unpushed(ex_new, _EXPRESS, pushed["announcements"])

        # 缺口候选：跨按日 payload 区间 union（长假/周末场景）
        candidate_rows = collector.read_payload_rows_between(
            conn, _FORECAST, prev_trade, target_date)

        # 口径二① 预告区间对标：快报通常晚于预告数周发布，区间必须从**历史存档**
        # 回看（只用本批新增预告对标的话 vs_forecast 生产环境恒为 None，功能虚设）
        forecast_history_rows: list[dict] = []
        if ex_new:
            history_start = (
                datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=_FORECAST_LOOKBACK_FOR_EXPRESS_DAYS)
            ).strftime("%Y-%m-%d")
            forecast_history_rows = collector.read_payload_rows_between(
                conn, _FORECAST, history_start, target_date)

        position_codes = collector.load_position_codes(conn)

    forecast_items = normalize.normalize_forecast(fc_new)
    forecast_for_express = (
        normalize.normalize_forecast(forecast_history_rows)
        if forecast_history_rows else forecast_items
    )
    express_items = normalize.normalize_express(ex_new, forecast_for_express)

    # 缺口验证「应今日验证」的候选 = candidate_rows 落在窗口 [prev_trade, target) 的子集。
    # candidate_rows 按 biz_date 区间读取含 target 当天 ann（不该今日验证），gap_error/gap_note
    # 的「有无候选」判定必须用此过滤后子集，否则只有当天新公告时会误报缺口验证缺席（codex review）
    eligible_candidates = gap_check.eligible_window_rows(candidate_rows, prev_trade, target_date)

    today_quotes, gap_error = _quotes(registry, target_date)
    gap_hits: list[dict] = []
    gap_note: str | None = None
    if today_quotes:  # 非交易日「成功返回空」→ 缺口段自然为空（与故障路径区分）
        prev_quotes, _ = _quotes(registry, prev_trade)  # prev 失败仅缺严格缺口标注，可降级
        gap_hits = gap_check.check_gaps(
            eligible_candidates,
            today_quotes,
            prev_quotes,
            prev_trade_date=prev_trade,
            target_date=target_date,
            threshold_pct=gap_threshold_pct,
        )
        # 行情完整性地板校验：有应验证候选但全市场行情行数远低于市场规模 → 疑似截断/部分返回，
        # 缺口命中可能漏（静默 continue 看不出来），显式提示（codex review）
        if eligible_candidates and len(today_quotes) < _MIN_EXPECTED_MARKET_QUOTES:
            gap_note = f"行情仅 {len(today_quotes)} 行（疑似截断/部分返回），缺口命中可能不完整"
            logger.warning("[earnings-digest] %s %s", target_date, gap_note)
        # close 字段完整性校验（收盘定调新引入的依赖）：行数地板只看行数、不看字段——若数据源
        # close 整列漂移/改名/缺失，行数正常但 check_gaps 因无有效 close 静默跳过候选，命中或归零、
        # 或仅剩少数侥幸带 close 的票（命中列表看似权威实则漏算）。故**不以 gap_hits 是否为空为门**
        # （codex v3）：行数健康但全市场有效 close 占比过低时，无论有无命中都显式提示，不静默装空。
        elif eligible_candidates:
            valid_close = sum(
                1 for q in today_quotes
                if (c := normalize._to_float(q.get("close"))) is not None and c > 0
            )
            if valid_close < len(today_quotes) * 0.5:
                gap_note = (f"行情有效收盘价仅 {valid_close}/{len(today_quotes)} 行"
                            "（疑似收盘价字段漂移），收盘定调缺口验证可能不完整")
                logger.warning("[earnings-digest] %s %s", target_date, gap_note)
    elif gap_error is None and eligible_candidates:
        # 行情「成功返回空」与故障同样需要鉴别（codex review v2/v3）：应验证候选存在时
        # 默认可见警示，**只有确证非交易日才静默**——交易日鉴别自身降级（不可用/
        # 超时/不支持）不能成为第二个静默吞掉缺口验证的通道
        trade_check = registry.call("is_trade_day", target_date)
        if trade_check.success and trade_check.data is False:
            pass  # 确证非交易日：干净空，无需警示
        elif trade_check.success and trade_check.data:
            gap_error = "行情返回为空（交易日，疑似数据源滞后）"
        else:
            gap_error = "行情返回为空且交易日鉴别失败"
        if gap_error:
            logger.error("[earnings-digest] %s %s", target_date, gap_error)
    if gap_error and not eligible_candidates:
        gap_error = None  # 无应验证候选时缺口段本就为空，故障不影响本期内容，仅留日志

    # 同日重跑：剔除已推过的缺口命中（同日 quotes 固定、重跑结果不变，只报一次）
    gap_hits = collector.filter_unpushed_gaps(gap_hits, pushed["gaps"])

    # gap_error 单独会触发推送（在 render 的 None 判定内），同日重跑须去重防重推；但「已推过」
    # 只应抑制其**单独触发推送**，不应让它从含新内容的 digest 里消失——否则晚些到达的新公告
    # 速报会丢掉「行情故障/缺口验证缺席」提示，stats 也静默变 None（codex round2）。
    # 故 gap_error 始终传给 renderer 展示，仅用 gap_error_pushable 控制能否**单独**触发推送。
    warning_keys: set[str] = set()
    gap_error_pushable = True
    if gap_error:
        wk = collector.gap_error_marker_key(target_date)
        if wk in pushed["warnings"]:
            gap_error_pushable = False  # 已推过 → 不再单独触发推送，但仍随其它内容展示
        else:
            warning_keys.add(wk)

    consensus_labels = (
        _consensus_labels(registry, target_date, forecast_items, position_codes,
                          min_profit_wan=min_profit_wan)
        if enable_consensus and forecast_items else {}
    )

    # 申万成分 map 自带股票名（{ts_code: {name, ...}}）：有任意内容就取一次，既供 ③ 行业聚合，
    # 又给所有条目注入 name（数据源 forecast_vip/express_vip 不含名字，复用此图零额外接口）。
    # 缺成分的次新（不在 index_member_all）name 缺失 → 渲染优雅退化只显代码。
    industry_map = (
        _industry_map(registry)
        if (forecast_items or express_items or gap_hits) else None
    )
    if industry_map:
        for item in (*forecast_items, *express_items, *gap_hits):
            item["name"] = (industry_map.get(item["ts_code"]) or {}).get("name", "")

    rendered = renderer.render_digest(
        target_date=target_date,
        forecast_items=forecast_items,
        express_items=express_items,
        gap_hits=gap_hits,
        position_codes=position_codes,
        industry_map=industry_map,
        top_n=top_n,
        min_profit_wan=min_profit_wan,
        gap_display_cap=gap_display_cap,
        gap_error=gap_error,
        gap_error_pushable=gap_error_pushable,
        gap_note=gap_note,
        consensus_labels=consensus_labels,
    )

    stats = {
        "forecast_new": len(forecast_items),
        "express_new": len(express_items),
        "gap_hits": len(gap_hits),
        "gap_error": gap_error,
        "gap_note": gap_note,
        "consensus_labeled": len(consensus_labels),
        "prev_trade_date": prev_trade,
    }
    if rendered is None:
        logger.info("[earnings-digest] %s 空窗口日（无新增公告且无缺口命中），不推送", target_date)
        return DigestResult(target_date=target_date, stats=stats)
    title, markdown = rendered
    # 本次 digest 内容的已推业务键（仅在有内容时算；推送成功后由 CLI 落 .pushed 标记，供同日重跑去重）
    ann_keys = {collector.announcement_marker_key(_FORECAST, r) for r in fc_new}
    ann_keys |= {collector.announcement_marker_key(_EXPRESS, r) for r in ex_new}
    gap_keys = {collector.gap_marker_key(h) for h in gap_hits}
    return DigestResult(
        target_date=target_date, title=title, markdown=markdown, stats=stats,
        pushed_announcement_keys=ann_keys, pushed_gap_keys=gap_keys,
        pushed_warning_keys=warning_keys,
    )
