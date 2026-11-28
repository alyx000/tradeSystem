"""T+1 回填仅更新上一开放日信封的反馈块，原日行情及其他业务列保持独立。"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from db.connection import get_db
from utils.post_archive import archive_lock, atomic_text, atomic_write_archive, read_archive
from utils.trade_date import verified_previous_session

logger = logging.getLogger(__name__)
FEEDBACK_KEYS = ("premium_backfill", "promotion_backfill", "popularity_backfill")
POPULARITY_COVERAGE = "popularity_backfill_coverage"
_PENDING = ".post-market-feedback.pending.json"


def source_digest(envelope):
    raw = envelope.get("raw_data")
    if not isinstance(raw, dict):
        raise ValueError("盘后归档缺少原始行情")
    inputs = [raw.get(key) for key in ("limit_up", "dragon_tiger", "top_volume_stocks")]
    content = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def _observation_day(conn, envelope, source):
    premium = envelope.get("premium_backfill")
    if envelope.get("date") != source or not isinstance(premium, dict) or premium.get("prev_date") != source:
        return None
    outcome = premium.get("trade_date")
    try:
        if date.fromisoformat(outcome).isoformat() != outcome or verified_previous_session(conn, outcome) != source:
            return None
    except (TypeError, ValueError, sqlite3.Error):
        return None
    return outcome


def checked_blocks(conn, envelope, source):
    """历史人气股列表沿用同一份归档中溢价块的明确日期。"""
    outcome = _observation_day(conn, envelope, source)
    if outcome is None:
        return {}, None
    blocks = {"premium_backfill": envelope["premium_backfill"]}
    promotion = envelope.get("promotion_backfill")
    if (isinstance(promotion, dict) and promotion.get("prev_date") == source
            and promotion.get("trade_date") == outcome):
        blocks["promotion_backfill"] = promotion
    popularity = envelope.get("popularity_backfill")
    if isinstance(popularity, list) and all(isinstance(row, dict) and row.get("code") for row in popularity):
        blocks["popularity_backfill"] = popularity
    if isinstance(envelope.get(POPULARITY_COVERAGE), dict):
        blocks[POPULARITY_COVERAGE] = envelope[POPULARITY_COVERAGE]
    return blocks, outcome


def _same_observations(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return {k: v for k, v in left.items() if k != "computed_at"} == {
            k: v for k, v in right.items() if k != "computed_at"}
    return left == right


def _observed_codes(block):
    coverage = block.get("_coverage") or {}
    return set(coverage.get("observed_codes") or [])


def _less_complete(fresh, old):
    """新档有逐股覆盖。旧档只有分组总数及展示的前十只时，作保守比较。"""
    coverage = fresh.get("_coverage") or {}
    if coverage.get("status") == "source_failed":
        return old.get("_coverage", {}).get("status") != "source_failed"
    if _observed_codes(old) - _observed_codes(fresh):
        return True
    for key, group in old.items():
        if key == "capacity_top10" or not isinstance(group, dict) or not isinstance(group.get("count"), int):
            continue
        candidate = fresh.get(key) or {}
        if (candidate.get("count") or 0) < group["count"]:
            return True
        old_details = {r["code"] for r in group.get("detail", []) if r.get("code")}
        if coverage.get("status") == "partial" and old_details - _observed_codes(fresh):
            return True
    return False


def _merge_observations(updates, previous):
    merged = {**previous, **updates}
    old = previous.get("premium_backfill")
    fresh = updates["premium_backfill"]
    if old:
        preferred = old if _less_complete(fresh, old) else fresh
        cap, old_cap = fresh.get("capacity_top10"), old.get("capacity_top10")
        if old_cap and (not cap or _observed_codes(old_cap) - _observed_codes(cap)
                        or (old_cap.get("count") or 0) > (cap.get("count") or 0)):
            cap = old_cap
        premium = dict(preferred)
        if cap is not None:
            premium["capacity_top10"] = cap
        merged["premium_backfill"] = premium
    old_rows = previous.get("popularity_backfill", [])
    rows = updates.get("popularity_backfill", [])
    by_code = {r["code"]: r for r in rows}
    if any(r["code"] not in by_code for r in old_rows):
        merged["popularity_backfill"] = old_rows
        merged.pop(POPULARITY_COVERAGE, None)
        if POPULARITY_COVERAGE in previous:
            merged[POPULARITY_COVERAGE] = previous[POPULARITY_COVERAGE]
    elif old_rows and rows:
        # 两端日期与来源已核对。同码重试偶缺开盘/收盘，应保留已观察到的字段。
        prior = {r["code"]: r for r in old_rows}
        merged["popularity_backfill"] = [
            {**row, **{key: value for key, value in prior.get(row["code"], {}).items()
                       if row.get(key) is None and value is not None}} for row in rows]
    for key, value in previous.items():
        if key in merged and _same_observations(value, merged[key]):
            merged[key] = value
    return merged


def _publish_locked(path, intent, db_path):
    """持锁读最新两端，再仅合并本次获得的事实；中断后可从意图文件重放。"""
    archived = read_archive(path)
    source = path.parent.name
    if archived.get("date") != source or source_digest(archived) != intent["source_sha256"]:
        raise ValueError(f"{source} 回填源行情已变更，请重新核验")
    updates = intent["blocks"]
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        valid, outcome = checked_blocks(conn, {"date": source, **updates}, source)
        if outcome is None or set(updates) != set(valid):
            raise ValueError("反馈块缺少严格相邻开放日证据")
        row = conn.execute("SELECT raw_data FROM daily_market WHERE date = ?", (source,)).fetchone()
        if row is None:
            raise ValueError(f"{source} 缺少 SQLite 盘后信封")
        stored = json.loads(row["raw_data"])
        if not isinstance(stored, dict) or stored.get("date") != source or source_digest(stored) != intent["source_sha256"]:
            raise ValueError(f"{source} 两端回填源行情不一致")
        merged = updates
        for current in (archived, stored):
            known, known_day = checked_blocks(conn, current, source)
            if known_day == outcome:
                merged = _merge_observations(merged, known)
        file_changed = any(archived.get(k) != v for k, v in merged.items())
        db_changed = any(stored.get(k) != v for k, v in merged.items())
        pending = path.with_name(_PENDING)
        if file_changed or db_changed:
            atomic_text(pending, json.dumps(intent, ensure_ascii=False))
        if db_changed:
            conn.execute("UPDATE daily_market SET raw_data=? WHERE date=?",
                         (json.dumps({**stored, **merged}, ensure_ascii=False), source))
        if file_changed:
            atomic_write_archive(path, {**archived, **merged})
    pending.unlink(missing_ok=True)
    return merged["premium_backfill"]


def publish_feedback(path, original, updates, db_path=None):
    intent = {"source_sha256": source_digest(original), "blocks": updates}
    with archive_lock(path):
        pending = path.with_name(_PENDING)
        if pending.is_file():
            prior = json.loads(pending.read_bytes())
            if prior["source_sha256"] == intent["source_sha256"]:
                _publish_locked(path, prior, db_path)
            # 来源变化后仅新计算可以替换旧意图，_publish_locked 会先复核两端源摘要。
        try:
            return _publish_locked(path, intent, db_path)
        except sqlite3.Error:
            # 获取写锁/连接也可能失败。保存意图，重试时重新核对日历与两端源行情。
            if not pending.exists():
                atomic_text(pending, json.dumps(intent, ensure_ascii=False))
            raise


def retry_feedback(daily_dir, db_path):
    succeeded = failed = 0
    for pending in Path(daily_dir).glob(f"*/{_PENDING}"):
        path = pending.with_name("post-market.yaml")
        try:
            with archive_lock(path):
                if not pending.is_file():
                    continue
                _publish_locked(path, json.loads(pending.read_bytes()), db_path)
            succeeded += 1
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            logger.warning("%s 次日反馈重试失败：%s", path.parent.name, exc)
            failed += 1
    return succeeded, failed


def feedback_view(conn, source, envelope, path, *, as_of=None):
    """只有最终归档视图可见后补结果；指定截至日时严格截断观察日。"""
    out = dict(envelope)
    out.pop("t1_feedback_dates", None)
    cutoff = min(as_of or date.max.isoformat(), datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    known, outcome = checked_blocks(conn, out, source)
    if cutoff > source and not all(key in known for key in FEEDBACK_KEYS) and path is not None and path.is_file():
        try:
            archived = read_archive(path)
            archived_blocks, archived_day = checked_blocks(conn, archived, source)
            if (archived_day and (not outcome or outcome == archived_day)
                    and archived_day <= cutoff and source_digest(archived) == source_digest(out)):
                outcome = archived_day
                known = {**archived_blocks, **known}
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            pass
    for key in (*FEEDBACK_KEYS, POPULARITY_COVERAGE):
        out.pop(key, None)
    if known and outcome <= cutoff:
        out.update(known)
        out["t1_feedback_dates"] = {"source_trade_date": source, "outcome_trade_date": outcome}
    return out
