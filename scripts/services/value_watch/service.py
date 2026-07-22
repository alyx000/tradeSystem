"""value-watch service 编排：采集 → 重放 → 落库 → 通知判定 → 推送 → 账本。

三档语义（spec v8）：
- 裸（persist=True, push=True）：落库 + 通过闸门的新事件推送；
- --no-push（persist=True, push=False）：落库 + 打印候选；
- --dry-run（persist=False, push=False）：内存计算不落库不推送不写账本。

推送闸门：run_target_date == resolve_latest_closed_trade_date(now)（strict 日历，
blocked=None 一律不推）；历史 --date 落库但绝不推。事件发生日不参与闸门。
"""
from __future__ import annotations

import datetime
import logging
import sqlite3

from pushers.dingtalk_pusher import DingTalkPusher
from utils.trade_date import resolve_latest_closed_trade_date

from . import collector, engine, formatter, notify, repo
from .config import (
    DRAWDOWN_TARGETS,
    HISTORY_ANCHOR_DATE,
    LOGIC_VERSION,
    SCARCITY_CODES,
)
from .weekly import aggregate_completed_weeks

logger = logging.getLogger(__name__)


def run_daily(conn: sqlite3.Connection, registry, provider, date: str, *,
              persist: bool, push: bool, now=None) -> "str | None":
    payload, all_events = _collect_and_replay(conn, registry, provider, date)

    ledger = repo.load_sent_ledger(conn) if persist else set()
    candidates = notify.select_candidates(all_events, ledger)

    if persist:
        repo.upsert_daily(conn, date, payload, LOGIC_VERSION)

    md = formatter.render_report(payload, date=date, logic_version=LOGIC_VERSION,
                                 candidates=candidates)

    if persist and push and candidates:
        latest_closed = resolve_latest_closed_trade_date(conn, registry, now=now)
        if not notify.allow_push(date, latest_closed):
            logger.info("[value-watch] 目标日 %s ≠ 最新已收盘交易日 %s,闸门拦截推送(落库照常)",
                        date, latest_closed)
        else:
            _push_candidates(conn, date, candidates, payload)
    return md


def _collect_and_replay(conn, registry, provider, date: str):
    """采集(锚定起点)→三引擎重放。单标的失败只降级该标的(source_status 标注),不中断整批。"""
    source_status: dict[str, str] = {}
    all_events: list[engine.Event] = []

    drawdown: dict[str, "dict | None"] = {}
    for code, buckets in DRAWDOWN_TARGETS.items():
        if code.endswith(".SI"):
            series = collector.fetch_sw_index_series(provider, code, HISTORY_ANCHOR_DATE, date)
        else:
            series = collector.fetch_stock_series(registry, code, HISTORY_ANCHOR_DATE, date)
        if series is None:
            source_status[code] = "source_failed"
            drawdown[code] = None
            continue
        source_status[code] = "ok"
        snap, events = engine.drawdown_events(code, _upto(series, date), buckets)
        drawdown[code] = snap
        all_events += events

    ladder = []
    positions = collector.load_ladder_positions(conn)
    stock_cache: dict[str, "list[dict] | None"] = {}
    for pos in positions:
        entry = dict(pos)
        if pos["insufficient_identity"]:
            ladder.append(entry)
            continue
        code = pos["code"]
        if code not in stock_cache:
            stock_cache[code] = collector.fetch_stock_series(
                registry, code, HISTORY_ANCHOR_DATE, date)
        series = stock_cache[code]
        if series is None:
            source_status.setdefault(code, "source_failed")
            entry["state"] = "source_failed"
            ladder.append(entry)
            continue
        source_status.setdefault(code, "ok")
        since_entry = [b for b in _upto(series, date) if b["date"] >= pos["entry_date"]]
        snap, events = engine.ladder_events(pos["position_key"], pos["name"],
                                            pos["entry_price"], since_entry)
        entry.update(snap)
        ladder.append(entry)
        all_events += events

    scarcity: dict[str, "dict | None"] = {}
    for code in SCARCITY_CODES:
        series = collector.fetch_stock_series(registry, code, HISTORY_ANCHOR_DATE, date)
        if series is None:
            scarcity[code] = None
            source_status[code] = "source_failed"
            continue
        source_status[code] = "ok"
        weeks = aggregate_completed_weeks(
            _upto(series, date), date,
            target_week_has_remaining_open_days=_week_has_remaining_open_days(conn, date))
        snap, events = engine.scarcity_replay(code, weeks)
        scarcity[code] = snap
        all_events += events

    payload = {
        "drawdown": drawdown,
        "ladder": ladder,
        "scarcity": scarcity,
        "source_status": source_status,
        "holdings_as_of": date,
    }
    return payload, all_events


def _upto(series: list[dict], date: str) -> list[dict]:
    """截断到目标日(含)——回填历史日期时不得让未来 bar 泄入重放。"""
    return [b for b in series if b["date"] <= date]


def _week_has_remaining_open_days(conn: sqlite3.Connection, date: str) -> bool:
    """目标日所在 ISO 周内是否还有严格晚于目标日的 open 日(决定该周是否算完成周)。

    日历缺失时保守返回 True(当周按未完成处理,少评估一周,不产生伪事件)——
    live contract check 实测:空库无日历行时若直接查 is_open=1 会返回 False,把
    盘中运行的当周误判为完成周(周三就出'完成周'伪 week_end)。故先判窗口内有无
    任何日历行:无行=无法确认=保守 True;有行才看其中是否有 open 日。"""
    d = datetime.date.fromisoformat(date)
    week_end_sunday = d + datetime.timedelta(days=6 - d.weekday())
    if date == week_end_sunday.isoformat():
        return False   # 周日:当周天然无剩余日
    rows = conn.execute(
        "SELECT is_open FROM trade_calendar WHERE date > ? AND date <= ?",
        (date, week_end_sunday.isoformat()),
    ).fetchall()
    if not rows:
        return True    # 日历缺失,无法确认 → 保守当未完成
    return any(r[0] for r in rows)


def _push_candidates(conn, date: str, candidates, payload) -> None:
    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[value-watch] DingTalk pusher 未启用(缺 env),跳过推送;"
                     "事件不落账本,下次运行重试")
        return
    for title, markdown, keys in formatter.render_push_messages(candidates, payload):
        ok = pusher.send_markdown(title=title, content=markdown)
        if ok:
            repo.append_sent_events(conn, date, keys)   # 成功才记账;失败下次重试
        else:
            logger.warning("[value-watch] 推送失败(不落账本,下次重试): %s", title)


def run_report(conn: sqlite3.Connection, date: "str | None") -> str:
    """只渲染已落库快照(不采集不现算);现算走 daily --dry-run。"""
    snap = repo.get_snapshot(conn, date)
    if snap is None:
        which = date or "最新"
        return f"value-watch 无快照({which});先运行 value-watch daily,或用 --dry-run 现算预览。"
    return formatter.render_report(snap["payload"], date=snap["date"],
                                   logic_version=snap["logic_version"])
