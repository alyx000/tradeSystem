"""下一开放日复查逐证券两融明细，原始回执归入其实际交易日。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timezone
import hashlib
import json
import logging
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection
from providers.margin_detail import summary, verified_receipt
from utils.trade_date import verified_previous_session

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _verified(row, day, as_of):
    if row["status"] not in {"partial", "success"} or row["provider"] != "tushare:margin_detail":
        return None
    try:
        raw = row["payload_json"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["payload_hash"]:
            return None
        payload = json.loads(raw)
        meta = json.loads(row["source_meta_json"])
        fetched = datetime.fromisoformat(meta["fetched_at"])
        close = datetime.fromisoformat(day + "T15:00:00").replace(tzinfo=SHANGHAI)
        if fetched.tzinfo is None or not close <= fetched <= as_of:
            return None
        if (payload["interface_name"] == "margin_detail" and payload["biz_date"] == day
                and payload["provider"] == meta["source"] == row["provider"]
                and payload["params"] == {"trade_date": day.replace("-", "")}
                and row["row_count"] == len(payload["rows"]) and meta["data"] == payload["rows"]):
            note = verified_receipt(payload["rows"], meta, day)
            state = "published" if row["status"] == "success" else "partial"
            if note and note["status"] == state:
                return payload["rows"], meta
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    return None


def _receipts(conn, day, as_of):
    if as_of.tzinfo is None:
        return []
    # 原始入库使用 SQLite datetime('now')，UTC 秒精度。
    cutoff = as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(
        "SELECT provider, status, row_count, payload_json, payload_hash, source_meta_json "
        "FROM raw_interface_payloads WHERE interface_name='margin_detail' AND stage='post_extended' "
        "AND raw_table='raw_margin_detail' AND biz_date=? AND target_date=? AND datetime(inserted_at)<=? "
        "ORDER BY inserted_at DESC, id DESC", (day, day, cutoff),
    )


def load_published_margin_detail(conn, day, *, as_of, allow_partial=False):
    """优先三市齐备；展示可另行接纳已验证部分，均须在观察时刻已取得并入库。"""
    partial = None
    for row in _receipts(conn, day, as_of):
        if row["status"] == "success":
            result = _verified(row, day, as_of)
            if result is not None:
                return result
        elif allow_partial and partial is None:
            partial = _verified(row, day, as_of)
    return partial


def _failed_attempt(conn, day, cutoff):
    records = conn.execute(
        "SELECT r.params_json,r.started_at,r.finished_at,e.created_at FROM ingest_runs r "
        "LEFT JOIN ingest_errors e ON e.run_id=r.run_id AND e.biz_date=r.biz_date "
        "AND e.interface_name=r.interface_name AND e.stage=r.stage "
        "WHERE r.interface_name='margin_detail' AND r.stage='post_extended' AND r.status='failed' "
        "AND r.biz_date=? AND r.target_date=? AND r.finished_at IS NOT NULL", (day, day),
    )
    close = datetime.fromisoformat(day + "T15:00:00").replace(tzinfo=SHANGHAI)
    for record in records:
        try:
            if json.loads(record["params_json"]) != {"trade_date": day.replace("-", "")}:
                continue
            start, end = [datetime.fromisoformat(record[key]) for key in ("started_at", "finished_at")]
            if start.tzinfo is None and end.tzinfo is None:
                # 旧运行日志记录主机本地时间，错误表由 SQLite 记 UTC 秒。用同次请求
                # 的错误行核验时区，避免将无时区或事后重跑视为当时已知的失败。
                recorded = datetime.fromisoformat(record["created_at"]).replace(tzinfo=timezone.utc)
                zones = [tz for tz in (SHANGHAI, timezone.utc)
                         if start.replace(tzinfo=tz, microsecond=0) <= recorded <= end.replace(tzinfo=tz)]
                if len(zones) != 1:
                    continue
                start, end = start.replace(tzinfo=zones[0]), end.replace(tzinfo=zones[0])
            if start <= end and close <= end <= cutoff:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def refresh_previous_margin_detail(service, observed_date, *, input_by, now=None):
    """只重查已有原始回执或经日期核验的失败审计的上一开放日。"""
    info = {"observed_date": observed_date, "fact_date": None}
    if not isinstance(input_by, str) or not input_by.strip():
        raise ValueError("逐证券两融披露重查需要 input_by")
    at = now or datetime.now(SHANGHAI)
    if at.tzinfo is None:
        return {**info, "status": "skipped", "reason": "观察时间缺少时区"}
    local = at.astimezone(SHANGHAI)
    if local.date().isoformat() != observed_date or local.time() < time(8, 30):
        return {**info, "status": "skipped", "reason": "尚未进入本次交易日披露窗口"}
    try:
        with closing(get_readonly_connection(service.db_path)) as conn:
            previous = verified_previous_session(conn, observed_date)
            if previous is None:
                return {**info, "status": "skipped", "reason": "未核验相邻开放日"}
            info["fact_date"] = previous
            rows = list(_receipts(conn, previous, at))
            if any(row["status"] == "success" and _verified(row, previous, at) is not None for row in rows):
                return {**info, "status": "already_published"}
            if not rows and not _failed_attempt(conn, previous, at):
                return {**info, "status": "skipped", "reason": "前一开放日无原始回执或已核验失败审计"}
        result = service.execute_interface("margin_detail", previous, triggered_by="system", input_by=input_by)
        state = result["status"]
        logger.info("逐证券两融明细披露重查 观察日=%s 事实日=%s 状态=%s", observed_date, previous, state)
        return {**info, "status": state, "run_id": result["run"]["run_id"]}
    except Exception as exc:
        logger.warning("逐证券两融明细披露重查未完成 观察日=%s 事实日=%s 错误=%s",
                       observed_date, info["fact_date"], type(exc).__name__)
        return {**info, "status": "failed", "reason": "披露重查未完成"}


def prepare_margin_detail_for_post(registry, observed_date, *, db_path=None, input_by="cmd_post", now=None):
    """返回报告生成时实际已入库的上一开放日摘要；任何明细缺口均独立于当日盘后主流程。"""
    from services.ingest_service import IngestService

    previous = None
    try:
        service = IngestService(db_path=db_path, registry=registry)
        check = refresh_previous_margin_detail(service, observed_date, input_by=input_by, now=now)
        previous = check["fact_date"]
        if previous is None or check["status"] == "skipped":
            return None
        as_of = now or datetime.now(SHANGHAI)
        if as_of.astimezone(SHANGHAI).date().isoformat() != observed_date:
            return None
        statuses = {"success": "published", "partial": "partial", "empty": "pending",
                    "failed": "source_failed", "already_published": "published"}
        state = statuses.get(check["status"], "source_failed")
        reasons = {"published": "重查收到三市明细", "partial": "重查只收到部分交易所明细",
                   "pending": "本次重查该事实日返回空页，尚无逐证券明细",
                   "source_failed": "本次重查未取得通过核验的来源回包"}
        attempt = dict(status=state, observed_date=observed_date, run_id=check.get("run_id"),
                       reason=check.get("reason", reasons[state]))
        with closing(get_readonly_connection(db_path)) as conn:
            stored = load_published_margin_detail(conn, previous, as_of=as_of, allow_partial=True)
        if stored is not None:
            rows, meta = stored
            result = summary(rows, meta, previous)
            if check["status"] != "already_published":
                if state == "published" and result["status"] == "partial":
                    attempt["reason"] = "本次三市回包未取代已入库的原回执，保留已核验的部分范围"
                result["latest_attempt"] = attempt
            return result
        if state not in {"pending", "source_failed"}:
            reason = "本次回包尚无在观察时点可核验的入库明细"
            state = "source_failed"
        else:
            reason = attempt["reason"]
        return dict(status=state, requested_date=previous, trade_date=None, count=None,
                    reason=reason, latest_attempt=attempt)
    except Exception as exc:
        logger.warning("盘后上一开放日两融明细未取得 观察日=%s 错误=%s", observed_date, type(exc).__name__)
        if previous is not None:
            return dict(status="source_failed", requested_date=previous, trade_date=None, count=None,
                        reason="本次未能核验逐证券明细原始回执")
    return None


def prefer_stored_margin_summary(block, day, *, rows=None, db_path=None, now=None):
    """当同一事实日的新回包缩减了原已核验的证券或字段时，保留原范围及本次结果。"""
    from providers.margin_detail import loses_disclosed_fields

    try:
        at = now or datetime.now(SHANGHAI)
        if at.tzinfo is None:
            return block
        local = at.astimezone(SHANGHAI)
        if day > local.date().isoformat():
            return block
        # 历史报告最多使用事实日结束前已到达原始库的快照。
        cutoff = min(local, datetime.combine(datetime.fromisoformat(day).date(), time.max, SHANGHAI))
        with closing(get_readonly_connection(db_path)) as conn:
            stored = load_published_margin_detail(conn, day, as_of=cutoff, allow_partial=True)
        if stored is None:
            return block
        prior_rows, meta = stored
        prior = summary(prior_rows, meta, day)
        status = block.get("status", "source_failed")
        if (status in {"published", "partial"} and block.get("trade_date") == day
                and block.get("_source") == meta["source"] and isinstance(rows, list)
                and len(rows) == block.get("count") and all(r.get("trade_date") == day.replace("-", "") for r in rows)
                and (status == "published" or prior["status"] == "partial")
                and not loses_disclosed_fields(prior_rows, rows)):
            return block
        reason = block.get("reason") or {
            "pending": "本次同日请求未取得已披露明细，沿用此前回执",
            "source_failed": "本次同日请求来源未核验，沿用此前回执",
        }.get(status, "本次回包未覆盖原已核验证券或字段，沿用此前回执")
        prior["latest_attempt"] = dict(status=status, requested_date=day, observed_date=local.date().isoformat(),
                                       fetched_at=block.get("fetched_at"), reason=reason)
        if "previous_disclosure" in block:
            prior["previous_disclosure"] = block["previous_disclosure"]
        return prior
    except Exception as exc:
        logger.warning("盘后同日两融明细回执未能对照 事实日=%s 错误=%s", day, type(exc).__name__)
        return block
