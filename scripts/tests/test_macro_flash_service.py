"""service 单测:窗口/幂等/锁/manifest/推送分支,全 mock 注入。"""
import datetime as dt
import json

from services.macro_flash import collector, service

NOW = dt.datetime(2026, 7, 23, 16, 30, tzinfo=service.TZ)
CONFIG = {"macro_flash": {"keywords": {"货币政策": ["央行"]}}}


def _ok_collect(window_start, window_end):
    return collector.CollectResult(
        status=collector.STATUS_COMPLETE, raw_count=10, pages=1,
        items=[{"id": "a", "time": "2026-07-23 10:00:00", "important": 0,
                "data": {"content": "央行公开市场操作", "title": ""}}])


def _failed_collect(window_start, window_end):
    return collector.CollectResult(status=collector.STATUS_FAILED, error="timeout")


class PushSpy:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok
    def __call__(self, title, content):
        self.calls.append((title, content))
        return self.ok


def _run(tmp_path, **kw):
    kw.setdefault("collect_fn", _ok_collect)
    kw.setdefault("push_fn", PushSpy())
    kw.setdefault("now", NOW)
    return service.run(CONFIG, base_dir=tmp_path, **kw)


def test_resolve_window_default_24h():
    start, end = service.resolve_window(None, 24, now=NOW)
    assert end == dt.datetime(2026, 7, 23, 16, 30)   # naive 上海时间
    assert start == dt.datetime(2026, 7, 22, 16, 30)


def test_resolve_window_with_date():
    start, end = service.resolve_window("2026-07-20", 24, now=NOW)
    assert end == dt.datetime(2026, 7, 20, 16, 30)


def test_normal_run_writes_three_files_and_pushes(tmp_path):
    push = PushSpy()
    out = _run(tmp_path, push_fn=push)
    assert out.exit_code == 0
    day = tmp_path / "2026-07-23"
    assert (day / "manifest.json").exists()
    assert (day / "flash_raw.json").exists()
    assert (day / "digest.md").exists()
    assert not (day / ".lock").exists()        # 锁已释放
    m = json.loads((day / "manifest.json").read_text())
    assert m["source_status"] == "complete"
    assert m["push_status"] == "success"
    assert m["candidates"][0]["id"] == "a"     # 入库确认流可读候选
    assert len(push.calls) == 1


def test_dry_run_no_files_no_push(tmp_path):
    push = PushSpy()
    out = _run(tmp_path, dry_run=True, push_fn=push)
    assert out.exit_code == 0
    assert not (tmp_path / "2026-07-23").exists()
    assert push.calls == []


def test_dry_run_previews_even_when_complete_exists(tmp_path):
    # 先落一个当日 complete 归档
    _run(tmp_path, no_push=True)
    # 再 dry-run:即使已 complete,也应打印预览而非幂等跳过,且不写文件
    push = PushSpy()
    out = _run(tmp_path, dry_run=True, push_fn=push)
    assert out.status != "skipped_existing"
    assert out.digest_md is not None      # 预览有产出
    assert push.calls == []               # dry-run 不推送


def test_no_push_archives_only(tmp_path):
    push = PushSpy()
    _run(tmp_path, no_push=True, push_fn=push)
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "skipped"
    assert push.calls == []


def test_source_failed_writes_manifest_and_status_push(tmp_path):
    push = PushSpy()
    out = _run(tmp_path, collect_fn=_failed_collect, push_fn=push)
    assert out.exit_code == service.EXIT_CODES[collector.STATUS_FAILED]
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["source_status"] == "source_failed"
    assert "source_failed" in push.calls[0][1]  # 降级提示而非伪装空速读


def test_idempotent_skip_on_complete(tmp_path):
    _run(tmp_path)
    push2 = PushSpy()
    out2 = _run(tmp_path, push_fn=push2)
    assert out2.status == "skipped_existing"
    assert push2.calls == []


def test_force_refresh_recollects(tmp_path):
    _run(tmp_path)
    push2 = PushSpy()
    out2 = _run(tmp_path, force_refresh=True, push_fn=push2)
    assert out2.status == "complete"
    assert len(push2.calls) == 1


def test_repush_only_pushes_existing_digest(tmp_path):
    _run(tmp_path, no_push=True)
    push2 = PushSpy()
    calls = {"n": 0}
    def no_collect(a, b):
        calls["n"] += 1
        return _ok_collect(a, b)
    out2 = _run(tmp_path, repush=True, push_fn=push2, collect_fn=no_collect)
    assert out2.status == "repushed"
    assert calls["n"] == 0                     # 不重采
    assert len(push2.calls) == 1
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "success"


def test_repush_without_archive_errors(tmp_path):
    out = _run(tmp_path, repush=True)
    assert out.exit_code == 2


def test_repush_refuses_non_complete_manifest(tmp_path):
    """repush 只支持 complete 归档:source_failed 日不能被重推误抹成 exit_code=0。"""
    _run(tmp_path, collect_fn=_failed_collect, no_push=True)
    push = PushSpy()
    out = _run(tmp_path, repush=True, push_fn=push)
    assert out.status == "repush_not_complete"
    assert out.exit_code == 2
    assert push.calls == []


def test_repush_refuses_on_sha_mismatch(tmp_path):
    """撕裂写(旧 manifest + 新内容)导致 sha 不符时,repush 必须 fail-closed 拒绝。"""
    _run(tmp_path, no_push=True)
    (tmp_path / "2026-07-23" / "digest.md").write_text("TAMPERED", encoding="utf-8")
    push = PushSpy()
    out = _run(tmp_path, repush=True, push_fn=push)
    assert out.status == "archive_corrupt"
    assert out.exit_code == 9
    assert push.calls == []


def test_skip_recollects_on_sha_mismatch(tmp_path):
    """撕裂写导致幂等跳过前 sha 校验失败时,必须重采修复而非静默跳过。"""
    _run(tmp_path, no_push=True)
    (tmp_path / "2026-07-23" / "digest.md").write_text("TAMPERED", encoding="utf-8")
    calls = {"n": 0}
    def counting_collect(a, b):
        calls["n"] += 1
        return _ok_collect(a, b)
    out = _run(tmp_path, collect_fn=counting_collect, no_push=True)
    assert calls["n"] == 1          # 重采修复,未跳过
    assert out.status == "complete"


def test_lock_contention_exits(tmp_path):
    day = tmp_path / "2026-07-23"
    day.mkdir(parents=True)
    (day / ".lock").touch()
    out = _run(tmp_path)
    assert out.status == "lock_contention"
    assert out.exit_code == 7


def test_push_failure_recorded_with_nonzero_exit(tmp_path):
    """采集 complete 但推送失败:manifest 记 failed 且退出码 8(launchd 可见),--repush 补推。"""
    out = _run(tmp_path, push_fn=PushSpy(ok=False))
    assert out.exit_code == service.EXIT_CODES["push_failed"]
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "failed"


def test_unexpected_error_writes_run_error_manifest(tmp_path):
    """编排层意外异常(collector 契约外):也落失败 manifest,锁释放。"""
    def boom(a, b):
        raise RuntimeError("boom")
    out = _run(tmp_path, collect_fn=boom)
    assert out.status == "run_error"
    assert out.exit_code == service.EXIT_CODES["run_error"]
    day = tmp_path / "2026-07-23"
    m = json.loads((day / "manifest.json").read_text())
    assert m["source_status"] == "run_error" and "boom" in m["error"]
    assert not (day / ".lock").exists()


def test_repush_respects_lock(tmp_path):
    """repush 与调度 run 互斥同一把 .lock:锁被占用时 repush 必须让路,不静默覆盖 manifest。"""
    _run(tmp_path, no_push=True)   # 先落一份既有归档
    day = tmp_path / "2026-07-23"
    (day / ".lock").touch()        # 模拟调度 run 正持锁进行中
    push2 = PushSpy()
    out = _run(tmp_path, repush=True, push_fn=push2)
    assert out.status == "lock_contention"
    assert out.exit_code == 7
    assert push2.calls == []


def test_source_failed_plus_push_failed_keeps_exit_3(tmp_path):
    """source_failed 时 push_failed=8 的抬升不应生效:退出码须保持 3(采集失败为主因)。"""
    out = _run(tmp_path, collect_fn=_failed_collect, push_fn=PushSpy(ok=False))
    assert out.exit_code == service.EXIT_CODES[collector.STATUS_FAILED]
    assert out.exit_code == 3
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "failed"


def test_repush_failure_sets_exit_8(tmp_path):
    """repush 推送失败必须持久化 exit_code=8,不能静默成功。"""
    _run(tmp_path, no_push=True)   # 先落一份既有归档
    out = _run(tmp_path, repush=True, push_fn=PushSpy(ok=False))
    assert out.exit_code == service.EXIT_CODES["push_failed"]
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "failed"
    assert m["exit_code"] == 8


def test_repush_success_clears_stale_exit_code(tmp_path):
    """repush 成功后应清除历史遗留的 push_failed=8,不让旧退出码残留误导后续读者。"""
    _run(tmp_path, push_fn=PushSpy(ok=False))   # 正常 run 推送失败,manifest.exit_code=8
    m0 = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m0["exit_code"] == 8
    out = _run(tmp_path, repush=True, push_fn=PushSpy(ok=True))
    assert out.exit_code == 0
    m = json.loads((tmp_path / "2026-07-23" / "manifest.json").read_text())
    assert m["push_status"] == "success"
    assert m["exit_code"] == 0


def test_repush_never_ran_is_missing(tmp_path):
    """从未跑过(day_dir 都不存在)时 repush 必须报 repush_missing,不进锁。"""
    push = PushSpy()
    out = _run(tmp_path, repush=True, push_fn=push)
    assert out.status == "repush_missing"
    assert out.exit_code == 2
    assert push.calls == []


def test_repush_during_first_run_lock_is_contention(tmp_path):
    """day_dir 已存在但 manifest/digest 尚未落地(首次 run 持锁采集中)时,并发
    repush 必须报 lock_contention 而非 repush_missing —— 目录已存在只代表
    "有 run 在进行",不代表"从未跑过"。"""
    day = tmp_path / "2026-07-23"
    day.mkdir(parents=True)
    (day / ".lock").touch()   # 模拟首次 run 持锁、尚未写出 manifest/digest
    push = PushSpy()
    out = _run(tmp_path, repush=True, push_fn=push)
    assert out.status == "lock_contention"
    assert out.exit_code == 7
    assert push.calls == []


def test_files_committed_after_push(tmp_path):
    """提交点重排验证:推送发生时,最终 flash_raw.json / digest.md 尚未落地
    (仍在临时文件阶段),推送完成后三文件才连续提交。"""
    day = tmp_path / "2026-07-23"
    seen = {}

    def checking_push(title, content):
        seen["raw_exists_at_push"] = (day / "flash_raw.json").exists()
        seen["digest_exists_at_push"] = (day / "digest.md").exists()
        seen["manifest_exists_at_push"] = (day / "manifest.json").exists()
        return True

    _run(tmp_path, push_fn=checking_push)
    assert seen["raw_exists_at_push"] is False
    assert seen["digest_exists_at_push"] is False
    assert seen["manifest_exists_at_push"] is False
    assert (day / "flash_raw.json").exists()
    assert (day / "digest.md").exists()
    assert (day / "manifest.json").exists()
