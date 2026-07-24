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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import requests

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
    "repush_not_complete": 2,   # repush 只支持 complete 归档;非 complete 拒绝(usage error)
    "archive_corrupt": 9,       # 归档 sha 与 manifest 不符(疑似撕裂写),数据完整性错误
}


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    manifest: Optional[dict] = None
    digest_md: Optional[str] = None
    push_md: Optional[str] = None


class _LockContention(Exception):
    """目录 .lock 已被占用(另一进程持锁中)。"""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        super().__init__(str(lock_path))


@contextmanager
def _locked(day_dir: Path):
    """获取目录级 .lock(O_CREAT|O_EXCL)防并发;占用时抛 _LockContention。

    repush 与正式采集共用同一把锁(day_dir/.lock),保证 manifest.json 的
    任何写入者互斥,避免并发覆盖。
    """
    lock_path = day_dir / ".lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise _LockContention(lock_path) from None
    try:
        yield
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


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

    keywords = flash_filter.load_keyword_config(config)

    if dry_run:
        # dry-run 是纯预览:必须先于 repush/幂等跳过逻辑返回,不受既有归档影响,
        # 兑现"--dry-run 不写不推,仅打印速读"契约(即使当日已有 complete 归档)。
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

    existing = read_manifest(run_date, base)

    if repush:
        # repush 也是 manifest.json 的写入者,必须与调度 run 互斥同一把 .lock。
        # 只有从未跑过(day_dir 都不存在)才是真 missing;目录已存在则先进锁再判,
        # 避免首次 run 持锁但文件尚未落地时把并发 repush 误判为 missing(应报 lock_contention)。
        if not day_dir.exists():
            print(f"{run_date} 无既有归档,无法 --repush", file=sys.stderr)
            return RunOutcome(status="repush_missing", exit_code=EXIT_CODES["repush_missing"])
        try:
            with _locked(day_dir):
                # 持锁后重读最新落地的 manifest 与 digest,避免用锁外旧快照覆盖
                latest = read_manifest(run_date, base)
                if not latest or not digest_path.exists():
                    print(f"{run_date} 无既有归档,无法 --repush", file=sys.stderr)
                    return RunOutcome(status="repush_missing",
                                      exit_code=EXIT_CODES["repush_missing"])
                if latest.get("source_status") != collector.STATUS_COMPLETE:
                    print(f"{run_date} 归档状态 {latest.get('source_status')} 非 complete,"
                          f"不支持 --repush(仅重推完整归档;失败日请 --force-refresh 重采)。",
                          file=sys.stderr)
                    return RunOutcome(status="repush_not_complete",
                                      exit_code=EXIT_CODES["repush_not_complete"], manifest=latest)
                if not _archive_intact(latest, day_dir):
                    print(f"{run_date} 归档 sha 校验失败(疑似撕裂写),拒绝 --repush;"
                          f"请 --force-refresh 重采修复。", file=sys.stderr)
                    return RunOutcome(status="archive_corrupt",
                                      exit_code=EXIT_CODES["archive_corrupt"], manifest=latest)
                # 重推与首推同一精选口径(important 每主题限量),且用「归档代」的选择语义:
                # 主题取 manifest candidates 快照(生成时定格,不用当前词表重筛——词表改过后
                # 重筛会产出与归档不一致的内容,违反"重推既有"契约);原文按 id 回联 flash_raw
                # (上方 _archive_intact 已保证与 manifest 同代)。
                raw_payload = json.loads(
                    (day_dir / "flash_raw.json").read_text(encoding="utf-8"))
                raw_by_id = {i.get("id"): i for i in raw_payload.get("items") or []
                             if isinstance(i, dict)}
                cands = [flash_filter.FlashCandidate(
                             item=raw_by_id.get(c.get("id"))
                                  or {"id": c.get("id"), "time": c.get("time"),
                                      "important": c.get("important"),
                                      "data": {"content": c.get("text") or ""}},
                             topic=c.get("topic"))
                         for c in latest.get("candidates") or []]
                push_md = formatter.build_push_digest(
                    cands,
                    window_start=datetime.fromisoformat(latest["window_start"]),
                    window_end=datetime.fromisoformat(latest["window_end"]),
                    source_status=latest.get("source_status"),
                    raw_count=latest.get("raw_count", 0),
                    # 主题顺序同样优先归档快照;旧 manifest 无该键时退回当前词表顺序
                    topic_order=latest.get("topic_order") or list(keywords),
                    archive_hint=_rel(digest_path))
                ok = push(title, push_md)
                latest["push_status"] = "success" if ok else "failed"
                latest["pushed_at"] = _now_iso()
                # 持久化退出码:成功清除可能残留的旧 push_failed=8,失败置 8 让调用方感知
                latest["exit_code"] = 0 if ok else EXIT_CODES["push_failed"]
                _atomic_write(manifest_path, _dumps(latest))
                return RunOutcome(status="repushed", exit_code=latest["exit_code"],
                                  manifest=latest, push_md=push_md)
        except _LockContention as exc:
            print(f"{run_date} 已有进行中的 run({exc.lock_path});确认为残留锁可删除后重试。",
                  file=sys.stderr)
            return RunOutcome(status="lock_contention", exit_code=EXIT_CODES["lock_contention"])

    if (existing and existing.get("source_status") == collector.STATUS_COMPLETE
            and not force_refresh):
        if _archive_intact(existing, day_dir):
            print(f"{run_date} 已有 complete 归档,幂等跳过(--force-refresh 重采 / --repush 重推)。")
            return RunOutcome(status="skipped_existing", exit_code=0, manifest=existing)
        logger.warning("[macro-flash] %s complete manifest 与归档 sha 不符(疑似撕裂写),重新采集修复",
                       run_date)
        # 不 return,继续向下走正式采集重采修复

    day_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _locked(day_dir):
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
                    # 生成时定格主题顺序:repush 用归档代语义重建推送,不受后续词表调整影响
                    "topic_order": list(keywords),
                    "push_status": "skipped",
                    "pushed_at": None,
                    "generated_at": _now_iso(),
                }
                push_md = None
                if not no_push:
                    # 推送体用 important 精选(归档 digest.md 恒全量);源失败推降级提示
                    push_md = (formatter.build_status_push(
                                   result.status, window_start=window_start,
                                   window_end=window_end, error=result.error)
                               if result.status == collector.STATUS_FAILED
                               else formatter.build_push_digest(
                                   candidates, window_start=window_start,
                                   window_end=window_end, source_status=result.status,
                                   raw_count=result.raw_count, topic_order=list(keywords),
                                   archive_hint=_rel(digest_path),
                                   full_digest_md=digest_md))
                    manifest["push_status"] = "success" if push(title, push_md) else "failed"
                    manifest["pushed_at"] = _now_iso()
                exit_code = EXIT_CODES.get(result.status, 1)
                if exit_code == 0 and manifest["push_status"] == "failed":
                    exit_code = EXIT_CODES["push_failed"]  # 归档成功推送失败:不静默,同日补推走 --repush
                manifest["exit_code"] = exit_code
                # 提交点:所有慢速工作(采集+推送)完成后,三个最终文件连续 os.replace 落地
                # (内容文件在前、manifest 作为 commit marker 最后写),把撕裂写窗口从"横跨整个
                # 推送"收窄到三次连续 os.replace 的微秒级;推送中途崩溃则旧一代文件保持完整不被触碰。
                _atomic_write(day_dir / "flash_raw.json", raw_payload)
                _atomic_write(digest_path, digest_md)
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
    except _LockContention as exc:
        print(f"{run_date} 已有进行中的 run({exc.lock_path});确认为残留锁可删除后重试。",
              file=sys.stderr)
        return RunOutcome(status="lock_contention", exit_code=EXIT_CODES["lock_contention"])


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


def _archive_intact(manifest: Optional[dict], day_dir: Path) -> bool:
    """校验归档文件 sha256 与 manifest 记录一致(检出撕裂写:manifest 与内容文件不属同一代)。"""
    files = (manifest or {}).get("files") or {}
    for key, default_name in (("flash_raw", "flash_raw.json"), ("digest", "digest.md")):
        rec = files.get(key) or {}
        path = day_dir / rec.get("path", default_name)
        if not path.exists():
            return False
        if _sha256(path.read_text(encoding="utf-8")) != rec.get("sha256"):
            return False
    return True


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")
