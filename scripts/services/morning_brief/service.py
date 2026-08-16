"""morning-brief 编排：窗口解析 → 金十/公告/隔夜行情采集 → 分类 → 状态合并。

状态语义:
- source_failed: 金十(核心新闻源)请求失败;报告仍含其余可用段,推告警版,CLI 非零退出
- partial: 金十非 complete(截断/停滞/漂移) 或 公告失败/截断 或 任一隔夜标的失败
  或 上一交易日无法核实(窗口锚点存疑);正常推送,缺口在报告头显式列出
- complete: 全部段落无缺口
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

from services.macro_flash import collector as flash_collector
from services.macro_flash import filter as flash_filter
from services.morning_brief import classify
from services.morning_brief import constants as C

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Shanghai")

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "source_failed"

TIME_FMT = "%Y-%m-%d %H:%M:%S"

_PREV_SCAN_DAYS = 15  # 与 utils.trade_date 同口径,覆盖 A 股长假


@dataclass
class BriefResult:
    status: str
    date: str
    payload: dict = field(default_factory=dict)
    error: Optional[str] = None


def _resolve_prev_trade_date(registry, today: str, conn=None) -> "tuple[str, list[str]]":
    """核实型上一交易日解析:DB 日历优先、provider 兜底,任何未核实降级都显式返回 gap。

    utils.trade_date.get_prev_trade_date 在日历源失败时静默回退「昨天」——周一/长假后
    会把早报窗口锚到周日/假日,静默缩窗漏掉周末披露(codex 门2 high)。这里改为:
    - 候选日在 DB trade_calendar 或 provider 可证明为交易日 → 采用,已核实
    - 扫描中任何候选日 DB/provider 双双无法判定 → 结果仍可用但标 gap(锚点可能过宽)
    - 15 天内无一可证明 → 回退昨天 + gap(锚点未核实,窗口可能缩窄)
    """
    gaps: List[str] = []
    scan_unproven = False
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    for delta in range(1, _PREV_SCAN_DAYS + 1):
        candidate = (today_dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        verdict: Optional[bool] = None
        if conn is not None:
            try:
                from db import queries as Q
                verdict = Q.is_trade_day_from_db(conn, candidate)
            except Exception:  # noqa: BLE001 — DB 缺表等按未知处理,走 provider
                verdict = None
        if verdict is None:
            r = registry.call("is_trade_day", candidate)
            if r.success and r.data is not None:
                verdict = bool(r.data)
        if verdict is True:
            if scan_unproven:
                gaps.append("上一交易日核实中部分日期日历不可判，窗口锚点可能过宽")
            return candidate, gaps
        if verdict is None:
            scan_unproven = True
    fallback = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    gaps.append("上一交易日无法核实（日历源失败），窗口按昨日锚定，可能缩窗漏掉假期披露")
    return fallback, gaps


def resolve_windows(registry, date_str: Optional[str],
                    now: Optional[datetime] = None, conn=None) -> dict:
    """窗口终点:--date 指定日取 08:00;否则取当前时刻,但当日 08:00 后钳制回 08:00
    (launchd 延迟触发/手动补跑不得扩窗,保证同日报告窗口确定;codex 门2 medium)。
    起点锚在核实过的上一交易日。"""
    now_sh = (now or datetime.now(TZ)).replace(tzinfo=None)
    if date_str:
        end = datetime.combine(date_cls.fromisoformat(date_str), C.WINDOW_END_TIME)
    else:
        cutoff = datetime.combine(now_sh.date(), C.WINDOW_END_TIME)
        end = cutoff if now_sh >= cutoff else now_sh
    target_date = end.date().isoformat()
    prev_trade, gaps = _resolve_prev_trade_date(registry, target_date, conn)
    prev_day = date_cls.fromisoformat(prev_trade)
    return {
        "date": target_date,
        "prev_trade_date": prev_trade,
        "news_window": (datetime.combine(prev_day, C.NEWS_WINDOW_START_TIME), end),
        "ann_window": (datetime.combine(prev_day, C.ANN_WINDOW_START_TIME), end),
        "gaps": gaps,
    }


def _collect_overnight(registry) -> dict:
    """隔夜行情快照:逐标的失败隔离,单标的失败只标缺不中断。"""
    indices: List[tuple] = []
    for key, label in C.OVERNIGHT_INDICES:
        r = registry.call("get_global_index", key)
        indices.append((label, r.data if r.success else {"error": r.error}))
    r = registry.call("get_us_tickers_overnight", ["HXC"])
    if r.success and r.data and isinstance(r.data.get("HXC"), dict):
        us_china: Optional[dict] = r.data["HXC"]
    else:
        us_china = {"error": (r.error if not r.success else "无数据")}
    commodities: List[tuple] = []
    for key, label in C.OVERNIGHT_COMMODITIES:
        r = registry.call("get_commodity", key)
        commodities.append((label, r.data if r.success else {"error": r.error}))
    return {"indices": indices, "us_china": us_china, "commodities": commodities}


def build_brief(config: dict, registry, *, date_str: Optional[str] = None,
                now: Optional[datetime] = None, conn=None,
                collect_fn: Optional[Callable] = None) -> BriefResult:
    """单一路径组装:金十失败与否共用同一 payload 流程,末尾统一判 status
    (source_failed 早返回分支曾手工重复 payload 且漏统计行情缺口,门1 finding)。"""
    keywords = classify.load_news_keywords(config)
    windows = resolve_windows(registry, date_str, now, conn)
    news_start, news_end = windows["news_window"]
    ann_start, ann_end = windows["ann_window"]
    target_date = windows["date"]
    gaps: List[str] = list(windows["gaps"])

    # 金十快讯（核心新闻源）
    collect = collect_fn or flash_collector.collect_window
    flash_result = collect(news_start, news_end)
    flash_failed = flash_result.status == flash_collector.STATUS_FAILED
    if flash_failed:
        gaps.append(f"金十快讯源失败：{flash_result.error}")
        candidates: List = []
    else:
        if flash_result.status != flash_collector.STATUS_COMPLETE:
            gaps.append(f"金十快讯窗口不完整（{flash_result.status}），要闻可能有缺")
        candidates = flash_filter.filter_items(flash_result.items, keywords)

    # 隔夜行情
    overnight = _collect_overnight(registry)
    failed_quotes = [
        label for label, info in (
            overnight["indices"] + overnight["commodities"]
            + [("纳斯达克中国金龙", overnight["us_china"])])
        if not info or "error" in info
    ]
    if failed_quotes:
        gaps.append(f"隔夜行情缺口：{'、'.join(failed_quotes)}")

    # 全市场公告
    announcements = _collect_announcements(registry, ann_start, ann_end, gaps)

    if flash_failed:
        status = STATUS_FAILED
    elif gaps:
        status = STATUS_PARTIAL
    else:
        status = STATUS_COMPLETE
    payload = {
        "date": target_date,
        "status": status,
        "gaps": gaps,
        # 补跑档标记:隔夜行情源(PGJ/实时商品)不支持严格 as-of,--date 回看时可能混入
        # 晚于目标窗口的场次(实测周四补跑周五窗口 PGJ 已含周四美股收盘);实时 08:00 档
        # "最新已收盘场次"即正确隔夜场次不受影响,故只做诚实标注不做日期化改造
        "backfill": bool(date_str),
        "news_window": windows["news_window"],
        "ann_window": windows["ann_window"],
        "overnight": overnight,
        "news": {
            "candidates": candidates,
            "topic_order": list(keywords),
            "source_status": flash_result.status,
            "raw_count": flash_result.raw_count,
        },
        "announcements": announcements,
    }
    return BriefResult(status=status, date=target_date, payload=payload,
                       error=flash_result.error if flash_failed else None)


def _collect_announcements(registry, start: datetime, end: datetime,
                           gaps: List[str]) -> dict:
    r = registry.call("get_market_announcements_range",
                      start.strftime(TIME_FMT), end.strftime(TIME_FMT))
    if not r.success or not isinstance(r.data, dict):
        gaps.append(f"公告源失败：{r.error}")
        return {"grouped": {}, "status": "failed", "raw_count": 0, "error": r.error}
    data = r.data
    if data.get("status") == "truncated":
        gaps.append("公告采集触达预算被截断（部分结果）")
    grouped = classify.classify_announcements(data.get("items") or [])
    return {
        "grouped": grouped,
        "status": data.get("status"),
        "raw_count": data.get("raw_count", 0),
        "error": None,
    }
