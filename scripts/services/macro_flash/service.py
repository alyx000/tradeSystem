"""macro-flash 编排:锁 → 采集 → 筛选 → 原子归档 → manifest → 推送。

manifest.json 是唯一 run receipt;所有写盘走临时文件 + os.replace 原子替换;
同日已有 complete manifest 默认幂等跳过(--force-refresh / --repush 显式覆盖)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from services.macro_flash import collector, formatter
from services.macro_flash import filter as flash_filter

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_LOOKBACK_HOURS = 24
WINDOW_END_TIME = time(16, 30)  # --date 补跑时窗口终点(工作日档);周日档补跑用 --lookback-hours 调整
SCHEMA_VERSION = 1
CANDIDATE_TEXT_LIMIT = 120
REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = REPO_ROOT / "data" / "runs" / "macro-flash"

EXIT_CODES = {
    collector.STATUS_COMPLETE: 0,
    "skipped_existing": 0,
    "repushed": 0,
    "repush_missing": 2,
    collector.STATUS_FAILED: 3,
    collector.STATUS_TRUNCATED: 4,
    collector.STATUS_STALLED: 5,
    collector.STATUS_DRIFT: 6,
    "lock_contention": 7,
    "push_failed": 8,   # 采集 complete 但推送失败:非零让 launchd 日志可见,--repush 补推
    "run_error": 1,     # 编排层意外异常(collector 契约外)
}


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    manifest: Optional[dict] = None
    digest_md: Optional[str] = None
    push_md: Optional[str] = None


def resolve_window(date_str: Optional[str], lookback_hours: int,
                   now: Optional[datetime] = None):
    """窗口终点:--date 指定日取 16:30,否则取当前时刻;均为 naive 上海时间。"""
    now_sh = (now or datetime.now(TZ)).replace(tzinfo=None)
    end = (datetime.combine(date.fromisoformat(date_str), WINDOW_END_TIME)
           if date_str else now_sh)
    return end - timedelta(hours=lookback_hours), end


def read_manifest(date_str: str, base_dir: Optional[Path] = None) -> Optional[dict]:
    path = Path(base_dir or BASE_DIR) / date_str / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def doctor() -> dict:
    """live 探测金十:拉 1 页并校验必需字段。部署验证与日常排障入口。"""
    import requests
    try:
        page = collector._fetch_page(requests.Session(), "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    sample = page[0] if page else {}
    data = sample.get("data") or {}
    return {
        "ok": True,
        "rows": len(page),
        "required_fields_ok": bool(sample.get("id") and sample.get("time")
                                   and (data.get("content") or data.get("title"))),
        "sample_time": sample.get("time"),
    }


def run(config: dict, *, date_str: Optional[str] = None,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        dry_run: bool = False, no_push: bool = False,
        force_refresh: bool = False, repush: bool = False,
        base_dir: Optional[Path] = None,
        collect_fn: Optional[Callable] = None,
        push_fn: Optional[Callable[[str, str], bool]] = None,
        now: Optional[datetime] = None) -> RunOutcome:
    base = Path(base_dir) if base_dir else BASE_DIR
    collect = collect_fn or collector.collect_window
    push = push_fn if push_fn is not None else _push_dingtalk

    window_start, window_end = resolve_window(date_str, lookback_hours, now)
    run_date = window_end.date().isoformat()
    day_dir = base / run_date
    manifest_path = day_dir / "manifest.json"
    digest_path = day_dir / "digest.md"
    title = f"宏观快讯速读 · {run_date}"

    existing = read_manifest(run_date, base)

    if repush:
        if not existing or not digest_path.exists():
            print(f"{run_date} 无既有归档,无法 --repush", file=sys.stderr)
            return RunOutcome(status="repush_missing", exit_code=EXIT_CODES["repush_missing"])
        digest_md = digest_path.read_text(encoding="utf-8")
        push_md = formatter.build_push_markdown(digest_md, _rel(digest_path))
        existing["push_status"] = "success" if push(title, push_md) else "failed"
        existing["pushed_at"] = _now_iso()
        _atomic_write(manifest_path, _dumps(existing))
        return RunOutcome(status="repushed", exit_code=0, manifest=existing, push_md=push_md)

    if (existing and existing.get("source_status") == collector.STATUS_COMPLETE
            and not force_refresh):
        print(f"{run_date} 已有 complete 归档,幂等跳过(--force-refresh 重采 / --repush 重推)。")
        return RunOutcome(status="skipped_existing", exit_code=0, manifest=existing)

    keywords = flash_filter.load_keyword_config(config)

    if dry_run:
        result = collect(window_start, window_end)
        candidates = flash_filter.filter_items(result.items, keywords)
        digest_md = formatter.build_digest_markdown(
            candidates, window_start=window_start, window_end=window_end,
            source_status=result.status, raw_count=result.raw_count,
            topic_order=list(keywords))
        print(digest_md)
        return RunOutcome(status=result.status,
                          exit_code=EXIT_CODES.get(result.status, 1),
                          digest_md=digest_md)

    day_dir.mkdir(parents=True, exist_ok=True)
    lock_path = day_dir / ".lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"{run_date} 已有进行中的 run({lock_path});确认为残留锁可删除后重试。",
              file=sys.stderr)
        return RunOutcome(status="lock_contention", exit_code=EXIT_CODES["lock_contention"])
    try:
        result = collect(window_start, window_end)
        candidates = flash_filter.filter_items(result.items, keywords)
        digest_md = formatter.build_digest_markdown(
            candidates, window_start=window_start, window_end=window_end,
            source_status=result.status, raw_count=result.raw_count,
            topic_order=list(keywords))
        raw_payload = _dumps({
            "schema_version": SCHEMA_VERSION,
            "window_start": str(window_start), "window_end": str(window_end),
            "items": result.items,
        })
        _atomic_write(day_dir / "flash_raw.json", raw_payload)
        _atomic_write(digest_path, digest_md)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_date": run_date,
            "window_start": str(window_start),
            "window_end": str(window_end),
            "lookback_hours": lookback_hours,
            "source_status": result.status,
            "error": result.error,
            "raw_count": result.raw_count,
            "dropped_count": result.dropped_count,
            "matched_count": len(candidates),
            "pages": result.pages,
            "files": {
                "flash_raw": {"path": "flash_raw.json", "sha256": _sha256(raw_payload)},
                "digest": {"path": "digest.md", "sha256": _sha256(digest_md)},
            },
            "candidates": [_candidate_row(c) for c in candidates],
            "push_status": "skipped",
            "pushed_at": None,
            "generated_at": _now_iso(),
        }
        push_md = None
        if not no_push:
            push_md = (formatter.build_status_push(
                           result.status, window_start=window_start,
                           window_end=window_end, error=result.error)
                       if result.status == collector.STATUS_FAILED
                       else formatter.build_push_markdown(digest_md, _rel(digest_path)))
            manifest["push_status"] = "success" if push(title, push_md) else "failed"
            manifest["pushed_at"] = _now_iso()
        exit_code = EXIT_CODES.get(result.status, 1)
        if exit_code == 0 and manifest["push_status"] == "failed":
            exit_code = EXIT_CODES["push_failed"]  # 归档成功推送失败:不静默,同日补推走 --repush
        manifest["exit_code"] = exit_code
        _atomic_write(manifest_path, _dumps(manifest))
        return RunOutcome(status=result.status, exit_code=exit_code,
                          manifest=manifest, digest_md=digest_md, push_md=push_md)
    except Exception as exc:  # noqa: BLE001 — 编排层意外异常也落 manifest,兑现"唯一 run receipt"
        logger.exception("[macro-flash] run 意外异常")
        err_manifest = {
            "schema_version": SCHEMA_VERSION, "run_date": run_date,
            "window_start": str(window_start), "window_end": str(window_end),
            "lookback_hours": lookback_hours,
            "source_status": "run_error", "error": str(exc),
            "push_status": "skipped", "pushed_at": None,
            "exit_code": EXIT_CODES["run_error"], "generated_at": _now_iso(),
        }
        _atomic_write(manifest_path, _dumps(err_manifest))
        return RunOutcome(status="run_error", exit_code=EXIT_CODES["run_error"],
                          manifest=err_manifest)
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def _candidate_row(cand) -> dict:
    data = cand.item.get("data") or {}
    text = (data.get("title") or data.get("content") or "")[:CANDIDATE_TEXT_LIMIT]
    return {"id": cand.item.get("id"), "time": cand.item.get("time"),
            "topic": cand.topic, "important": bool(cand.item.get("important")),
            "text": text}


def _push_dingtalk(title: str, content: str) -> bool:
    """与 volume_watch 同模式:token/secret 由 pusher.initialize() 从 env 读。"""
    from pushers.dingtalk_pusher import DingTalkPusher
    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[macro-flash] DingTalk pusher 未启用(缺 env),跳过推送")
        return False
    ok = pusher.send_markdown(title=title, content=content)
    logger.info("[macro-flash] 推送 %s", "成功" if ok else "失败")
    return ok


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")
