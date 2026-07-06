"""JS 慧博研报 workflow：状态日志、断点续跑、reader 并发边界。"""
from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
from pathlib import Path

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "research-digest-workflow.mjs"


@pytest.fixture(autouse=True)
def _disable_real_dingtalk(monkeypatch):
    monkeypatch.delenv("DINGTALK_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("DINGTALK_WEBHOOK_SECRET", raising=False)


def test_js_workflow_runs_and_resumes_without_rereading(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    rows = []
    for i in range(3):
        pdf = pdf_dir / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-机器人行业深度：重点推荐产业链{i}",
            "报告评级": "买入（首次）",
            "作者": "张三",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys, time\n"
        f"pathlib.Path({str(calls)!r}).open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "time.sleep(0.05)\n"
        "print(json.dumps({'industry':'机器人','key_points':['产业链升温'],'mentioned_stocks':[{'name':'测试股','source':'fake'}],'read_score':88}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "3",
        "--reader-concurrency", "3",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=True)
    assert "[workflow] done" in result.stdout

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["read"]["status"] == "done"
    assert state["stages"]["publish"]["status"] == "skipped"
    assert state["stages"]["publish"]["result"]["reason"] == "publish_not_requested"
    assert state["options"]["huiboRefreshUrlFromApp"] == "1"
    assert state["options"]["huiboReportPdfDir"].endswith("/Downloads")
    assert len(list((run_dir / "reader").glob("*.json"))) == 3
    assert (run_dir / "report.md").exists()
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"event":"stage_start"' in events
    assert '"stage":"read"' in events
    assert '"event":"finalize_agent_skip"' in events
    assert '"role":"industry_aggregator"' in events
    summary_events = [json.loads(line) for line in events.splitlines() if '"event":"workflow_summary"' in line]
    assert summary_events[-1]["published"] is False
    assert summary_events[-1]["pushed"] is False
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 3

    subprocess.run([*cmd, "--resume"], text=True, capture_output=True, check=True)
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 3


def test_js_workflow_absolutizes_relative_huibo_pdf_dir(tmp_path, monkeypatch):
    title = "方正证券-电新行业新技术系列报告~玻璃基板专题1：AI算力引领封装升级-260621"
    pdf_dir = tmp_path / "rel-pdfs"
    pdf_dir.mkdir()
    safe_stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", title).strip("._-")[:120]
    (pdf_dir / f"{safe_stem}.pdf").write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": title,
                "报告评级": "",
                "作者": "张三",
                "页数": "20页",
                "时间": "2026-06-21",
                "分类": "行业分析",
                "raw": {"DId": 5131866, "DocName": "202606210826522290.pdf", "DocType": 2},
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'industry':'电子','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    repo_root = WORKFLOW.parents[2]
    monkeypatch.setenv("HUIBO_REPORT_PDF_DIR", os.path.relpath(pdf_dir, repo_root))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True, cwd=tmp_path)

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["options"]["huiboReportPdfDir"] == str(pdf_dir)
    assert state["stages"]["download"]["result"]["downloaded_count"] == 1
    assert state["stages"]["read"]["result"]["scheduled_count"] == 1


def test_js_workflow_auto_retries_failed_reader(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = pdf_dir / "0.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "作者": "张三",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "lines = calls.read_text(encoding='utf-8').splitlines() if calls.exists() else []\n"
        "calls.open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "if len(lines) == 0:\n"
        "    print('first attempt fails', file=sys.stderr)\n"
        "    raise SystemExit(7)\n"
        "print(json.dumps({'industry':'机器人','key_points':['retry ok'],'mentioned_stocks':[],'read_score':77}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "2",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["status"] == "read_done"
    assert report["attempts"] == 2
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
    assert len(list((run_dir / "reader").glob("*.json"))) == 1
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"event":"report_read_retry"' in events


def test_js_workflow_global_quota_failure_stops_reader_and_marks_outputs(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    rows = []
    for i in range(3):
        pdf = pdf_dir / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-机器人行业深度：重点推荐产业链{i}",
            "报告评级": "买入（首次）",
            "作者": "张三",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "if '--log-file' in sys.argv:\n"
        "    pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): Individual quota reached\\n', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "3",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "3",
        "--recommend-cap", "1",
        "--publish",
        "--publish-dry-run",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["llmStatus"] == "unavailable"
    assert state["llmFailureReason"] == "quota_exhausted"
    reports = list(state["reports"].values())
    assert sum(1 for report in reports if report["status"] == "failed") == 1
    assert sum(1 for report in reports if report["status"] == "skipped_llm_unavailable") == 2
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    global_failures = [event for event in events if event["event"] == "llm_global_failure"]
    assert len(global_failures) == 1
    assert global_failures[0]["reason"] == "quota_exhausted"
    assert "RESOURCE_EXHAUSTED" in global_failures[0]["message"]
    assert not [event for event in events if event["event"] == "report_read_retry"]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["antigravity"]["status"] == "unavailable"
    assert summary["meta"]["ranker"]["status"] == "fallback"
    assert summary["meta"]["ranker"]["reason"] == "quota_exhausted"
    markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Antigravity 不可用" in markdown
    assert "ranker=fallback" in markdown
    published = json.loads((run_dir / "published.json").read_text(encoding="utf-8"))
    assert published["huibo_antigravity"]["status"] == "unavailable"
    assert published["huibo_ranker"]["status"] == "fallback"


def test_js_workflow_preflight_quota_failure_skips_reader_calls(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "if '--log-file' in sys.argv:\n"
        "    pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): quota\\n', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "2",
        "--recommend-cap", "1",
        "--preflight",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["llmStatus"] == "unavailable"
    assert state["llmFailureReason"] == "quota_exhausted"
    assert state["stages"]["preflight"]["status"] == "done"
    report = next(iter(state["reports"].values()))
    assert report["status"] == "skipped_llm_unavailable"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "llm_global_failure" and event.get("stage") == "preflight" for event in events)


def test_js_workflow_preflight_timeout_does_not_wait_for_inherited_pipe(tmp_path, monkeypatch):
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-AI算力行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "作者": "张三",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    bg_pid = tmp_path / "bg.pid"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)\n"
        f"pathlib.Path({str(bg_pid)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("HUIBO_REPORT_PDF_DIR", str(tmp_path / "terminal-pdfs"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "1",
        "--recommend-cap", "1",
        "--preflight",
        "--preflight-timeout-seconds", "1",
        "--no-aggregate-llm",
    ]
    try:
        subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=30)
    finally:
        if bg_pid.exists():
            try:
                os.kill(int(bg_pid.read_text(encoding="utf-8")), signal.SIGKILL)
            except ProcessLookupError:
                pass

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    preflight = state["stages"]["preflight"]["result"]
    assert preflight["status"] == "warning"
    assert preflight["reason"] == "timeout"
    assert state["stages"]["read"]["result"]["scheduled_count"] == 0


def test_js_workflow_reset_llm_status_allows_retry_after_global_failure(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    rows = []
    for i in range(2):
        pdf = pdf_dir / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-机器人行业深度：重点推荐产业链{i}",
            "报告评级": "买入（首次）",
            "作者": "张三",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    mode = tmp_path / "mode.txt"
    mode.write_text("quota", encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"mode = pathlib.Path({str(mode)!r}).read_text(encoding='utf-8').strip()\n"
        "calls.open('a', encoding='utf-8').write(json.dumps({'mode': mode, 'argv': sys.argv}, ensure_ascii=False)+'\\n')\n"
        "if mode == 'quota':\n"
        "    if '--log-file' in sys.argv:\n"
        "        pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): quota\\n', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'industry':'机器人','key_points':['reset ok'],'mentioned_stocks':[],'read_score':81}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "2",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "2",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)
    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["llmStatus"] == "unavailable"

    mode.write_text("ok", encoding="utf-8")
    subprocess.run([*cmd, "--retry-failed", "--reset-llm-status"], text=True, capture_output=True, check=True)

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["llmStatus"] == "ok"
    assert all(report["status"] == "read_done" for report in state["reports"].values())
    call_modes = [json.loads(line)["mode"] for line in calls.read_text(encoding="utf-8").splitlines()]
    assert call_modes == ["quota", "ok", "ok"]
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "llm_status_reset" for event in events)


def test_js_workflow_retry_failed_refreshes_publish_after_finalize(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    mode = tmp_path / "mode.txt"
    mode.write_text("quota", encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"mode = pathlib.Path({str(mode)!r}).read_text(encoding='utf-8').strip()\n"
        "calls.open('a', encoding='utf-8').write(mode+'\\n')\n"
        "if mode == 'quota':\n"
        "    if '--log-file' in sys.argv:\n"
        "        pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): quota\\n', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok after reset'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "2",
        "--recommend-cap", "1",
        "--publish",
        "--publish-dry-run",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)
    run_dir = tmp_path / "runs" / "2026-06-03"
    first_published = json.loads((run_dir / "published.json").read_text(encoding="utf-8"))
    assert first_published["huibo_antigravity"]["status"] == "unavailable"

    mode.write_text("ok", encoding="utf-8")
    subprocess.run([*cmd, "--retry-failed", "--reset-llm-status"], text=True, capture_output=True, check=True)

    refreshed = json.loads((run_dir / "published.json").read_text(encoding="utf-8"))
    assert refreshed["huibo_antigravity"]["status"] == "ok"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    publish_starts = [event for event in events if event["event"] == "stage_start" and event["stage"] == "publish"]
    assert len(publish_starts) == 2


def test_js_workflow_resume_refreshes_publish_when_previous_push_failed(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    base_cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--publish-out-root", str(tmp_path / "reports"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
        "--publish",
    ]
    subprocess.run([*base_cmd, "--publish-dry-run"], text=True, capture_output=True, check=True)
    run_dir = tmp_path / "runs" / "2026-06-03"
    first_published = json.loads((run_dir / "published.json").read_text(encoding="utf-8"))
    assert first_published["dry_run"] is True
    assert first_published["pushed"] is False

    subprocess.run([*base_cmd, "--resume"], text=True, capture_output=True, check=True)

    refreshed = json.loads((run_dir / "published.json").read_text(encoding="utf-8"))
    assert refreshed["dry_run"] is False
    assert refreshed["pushed"] is False
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    publish_starts = [event for event in events if event["event"] == "stage_start" and event["stage"] == "publish"]
    assert len(publish_starts) == 2


def test_js_workflow_writes_workflow_summary_event(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
        "--publish",
        "--publish-dry-run",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summaries = [event for event in events if event["event"] == "workflow_summary"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert isinstance(summary["invocation_id"], str) and summary["invocation_id"]
    assert summary["llm_status"] == "ok"
    assert summary["reader_success_count"] == 1
    assert summary["reader_failed_count"] == 0
    assert summary["reader_skipped_count"] == 0
    assert summary["ranker_status"] == "fallback"
    assert summary["include_base_digest"] is False
    assert summary["base_digest_included"] is False
    assert summary["published"] is True
    assert summary["summary"].endswith("summary.json")
    assert summary["markdown"].endswith("report.md")
    assert all(event.get("invocation_id") == summary["invocation_id"] for event in events)
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["currentInvocation"]["id"] == summary["invocation_id"]
    report_md = (run_dir / "run_report.md").read_text(encoding="utf-8")
    assert "## Stages" in report_md
    assert "read | done" in report_md
    assert "reader_success_count | 1" in report_md


def test_js_workflow_finalize_global_failure_updates_state_and_summary_event(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "lines = calls.read_text(encoding='utf-8').splitlines() if calls.exists() else []\n"
        "calls.open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "if len(lines) == 0:\n"
        "    print(json.dumps({'industry':'机器人','key_points':['reader ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n"
        "else:\n"
        "    if '--log-file' in sys.argv:\n"
        "        pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): quota\\n', encoding='utf-8')\n"
        "    raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["llmStatus"] == "unavailable"
    assert state["llmFailureReason"] == "quota_exhausted"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["antigravity"]["status"] == "unavailable"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "llm_global_failure" and event.get("stage") == "finalize" for event in events)
    workflow_summary = next(event for event in events if event["event"] == "workflow_summary")
    assert workflow_summary["llm_status"] == "unavailable"


def test_js_workflow_parse_failure_is_per_report_not_global(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    rows = []
    for i in range(2):
        pdf = pdf_dir / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-机器人行业深度：重点推荐产业链{i}",
            "报告评级": "买入（首次）",
            "作者": "张三",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "lines = calls.read_text(encoding='utf-8').splitlines() if calls.exists() else []\n"
        "calls.open('a', encoding='utf-8').write(json.dumps(sys.argv, ensure_ascii=False)+'\\n')\n"
        "if len(lines) == 0:\n"
        "    print('not json')\n"
        "else:\n"
        "    print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "2",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state.get("llmStatus", "ok") == "ok"
    statuses = [report["status"] for report in state["reports"].values()]
    assert statuses.count("failed") == 1
    assert statuses.count("read_done") == 1
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert not [event for event in events if event["event"] == "llm_global_failure"]
    parse_errors = [
        event for event in events
        if event["event"] == "report_read_error" and event.get("reason") == "parse_failed"
    ]
    assert len(parse_errors) == 1


def test_js_workflow_default_date_uses_trade_or_pre_trade_day(tmp_path, monkeypatch):
    db_path = tmp_path / "trade.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER)")
    conn.executemany(
        "INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)",
        [("2026-06-05", 1), ("2026-06-06", 0), ("2026-06-07", 0), ("2026-06-08", 1)],
    )
    conn.commit()
    conn.close()
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-05",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WORKFLOW_NOW_ISO", "2026-06-06T22:42:00.000Z")
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    assert (tmp_path / "runs" / "2026-06-07" / "state.json").exists()
    assert not (tmp_path / "runs" / "2026-06-05").exists()


def test_js_workflow_rejects_date_without_value(tmp_path):
    result = subprocess.run(
        [
            "node", str(WORKFLOW), "daily",
            "--date",
            "--run-root", str(tmp_path / "runs"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "--date requires YYYY-MM-DD" in result.stderr


def test_js_workflow_fails_fast_when_huibo_terminal_url_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("HUIBO_HOT_REPORT_JSON", raising=False)
    monkeypatch.setenv(
        "HUIBO_HOT_REPORT_URL",
        "https://sys.hibor.com.cn/redian/HotReport?abc=STALE&def=STALE&vidd=3&keyy=STALE&xyz=STALE&op=0",
    )
    monkeypatch.setenv("HUIBO_REFRESH_URL_FROM_APP", "1")
    monkeypatch.setenv("HUIBO_DISABLE_TERMINAL_URL_READ", "1")
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)

    assert result.returncode != 0
    assert "huibo_terminal_url_unavailable" in result.stderr
    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["collect"]["status"] == "failed"
    assert "huibo_terminal_url_unavailable" in state["stages"]["collect"]["error"]
    assert "publish" not in state["stages"]


def test_js_workflow_uses_rebuilt_llm_pdf_copy_on_resume(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = pdf_dir / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "作者": "张三",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, re, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "prompt = sys.argv[sys.argv.index('--prompt') + 1]\n"
        "match = re.search(r'@([^。]+)', prompt)\n"
        "pdf_path = match.group(1) if match else ''\n"
        "payload = {'argv': sys.argv, 'pdf_path': pdf_path, 'exists': pathlib.Path(pdf_path).exists()}\n"
        "calls.open('a', encoding='utf-8').write(json.dumps(payload, ensure_ascii=False)+'\\n')\n"
        "print(json.dumps({'industry':'机器人','key_points':['copy ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    raw_dir = tmp_path / "raw"
    llm_input_dir = tmp_path / "llm-input"
    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(raw_dir),
        "--summary-dir", str(tmp_path / "summaries"),
        "--llm-input-dir", str(llm_input_dir),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    first_call = json.loads(calls.read_text(encoding="utf-8").splitlines()[0])
    assert first_call["exists"] is True
    assert str(llm_input_dir) in first_call["pdf_path"]
    assert str(raw_dir) not in first_call["pdf_path"]
    assert "--add-dir" in first_call["argv"]
    assert str(llm_input_dir / "2026-06-03") in first_call["argv"]
    assert "--dangerously-skip-permissions" in first_call["argv"]
    assert first_call["argv"].index("--add-dir") < first_call["argv"].index("--prompt")
    assert first_call["argv"].index("--dangerously-skip-permissions") < first_call["argv"].index("--prompt")

    run_dir = tmp_path / "runs" / "2026-06-03"
    reader_path = next((run_dir / "reader").glob("*.json"))
    reader_path.unlink()
    for copied_pdf in llm_input_dir.glob("**/*.pdf"):
        copied_pdf.unlink()

    subprocess.run([*cmd, "--resume"], text=True, capture_output=True, check=True)

    calls_payloads = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert len(calls_payloads) == 2
    assert calls_payloads[1]["exists"] is True
    assert calls_payloads[1]["pdf_path"] == calls_payloads[0]["pdf_path"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["pdfPath"]
    assert report["llmPdfPath"] == calls_payloads[1]["pdf_path"]


def test_js_workflow_collect_uses_cli_raw_dir_without_env(tmp_path, monkeypatch):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(source_pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, re, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "prompt = sys.argv[sys.argv.index('--prompt') + 1]\n"
        "pdf_path = re.search(r'@([^。]+)', prompt).group(1)\n"
        "calls.open('a', encoding='utf-8').write(pdf_path+'\\n')\n"
        "print(json.dumps({'industry':'机器人','key_points':['raw dir ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    raw_dir = tmp_path / "cli-raw"
    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.delenv("HUIBO_RAW_DIR", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(raw_dir),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["status"] == "read_done"
    assert Path(report["pdfPath"]).is_relative_to(raw_dir)
    assert next(raw_dir.glob("*.pdf")).exists()
    used_pdf = calls.read_text(encoding="utf-8").strip()
    assert Path(used_pdf).name == Path(report["pdfPath"]).name


def test_js_workflow_cleans_only_run_owned_llm_input_subdir(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, re, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "prompt = sys.argv[sys.argv.index('--prompt') + 1]\n"
        "pdf_path = re.search(r'@([^。]+)', prompt).group(1)\n"
        "calls.open('a', encoding='utf-8').write(pdf_path+'\\n')\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)
    llm_base = tmp_path / "llm-base"
    llm_base.mkdir()
    keep_file = llm_base / "keep.txt"
    keep_file.write_text("do not remove", encoding="utf-8")

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--llm-input-dir", str(llm_base),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    assert keep_file.exists()
    used_pdf = calls.read_text(encoding="utf-8").strip()
    assert str(llm_base) in used_pdf
    assert not Path(used_pdf).exists()


def test_js_workflow_marks_copy_failure_as_report_failure(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "raise SystemExit('antigravity should not run when pdf copy fails')\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)
    llm_input_file = tmp_path / "llm-input-is-file"
    llm_input_file.write_text("not a dir", encoding="utf-8")

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--llm-input-dir", str(llm_input_file),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "2",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=True)
    assert "[workflow] done" in result.stdout

    run_dir = tmp_path / "runs" / "2026-06-03"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["status"] == "failed"
    assert report["attempts"] == 2
    assert "EEXIST" in report["lastError"] or "not a directory" in report["lastError"].lower()
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"event":"report_read_error"' in events
    assert '"event":"report_read_retry"' in events


def test_retry_failed_reuses_completed_upstream_stages_without_explicit_resume(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    calls = tmp_path / "antigravity-calls.jsonl"
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).open('a', encoding='utf-8').write('call\\n')\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    base_cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(base_cmd, text=True, capture_output=True, check=True)
    subprocess.run([*base_cmd, "--retry-failed", "--reader-max-attempts", "2"], text=True, capture_output=True, check=True)
    subprocess.run([*base_cmd, "--retry-failed", "--reader-max-attempts", "2"], text=True, capture_output=True, check=True)

    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "2026-06-03" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    skipped = [e["stage"] for e in events if e["event"] == "stage_skip"]
    assert {"collect", "prescreen", "download"}.issubset(set(skipped))
    assert sum(1 for e in events if e["event"] == "stage_start" and e["stage"] == "collect") == 1
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2


def test_js_workflow_keeps_missing_pdf_when_llm_unavailable(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    missing_pdf = tmp_path / "missing.pdf"
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            },
            {
                "报告名称": "B证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(missing_pdf),
            },
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if '--log-file' in sys.argv:\n"
        "    pathlib.Path(sys.argv[sys.argv.index('--log-file') + 1]).write_text('RESOURCE_EXHAUSTED (code 429): quota\\n', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "2",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    state = json.loads((tmp_path / "runs" / "2026-06-03" / "state.json").read_text(encoding="utf-8"))
    statuses = {report["title"]: report["status"] for report in state["reports"].values()}
    assert statuses["A证券-机器人行业深度：重点推荐产业链"] == "failed"
    assert statuses["B证券-机器人行业深度：重点推荐产业链"] == "missing_pdf"


def test_js_workflow_publish_passes_include_base_digest(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'industry':'机器人','key_points':['ok'],'mentioned_stocks':[],'read_score':80}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
        "--publish",
        "--publish-dry-run",
        "--include-base-digest",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "2026-06-03" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    publish = next(event for event in events if event["event"] == "stage_end" and event["stage"] == "publish")
    assert publish["result"]["include_base_digest"] is True


def test_js_workflow_fails_fast_on_antigravity_auth_prompt(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "print('Opening authentication page in your browser. Do you want to continue? [Y/n]: ', flush=True)\n"
        "time.sleep(20)\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ANTIGRAVITY_BIN", str(fake_agy))
    cmd = [
        "node", str(WORKFLOW), "daily",
        "--date", "2026-06-03",
        "--run-root", str(tmp_path / "runs"),
        "--raw-dir", str(tmp_path / "raw"),
        "--summary-dir", str(tmp_path / "summaries"),
        "--reader-cap", "1",
        "--reader-concurrency", "1",
        "--reader-max-attempts", "1",
        "--recommend-cap", "1",
        "--no-aggregate-llm",
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=10)

    state = json.loads((tmp_path / "runs" / "2026-06-03" / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["status"] == "failed"
    assert report["lastError"] == "antigravity requires interactive authentication"
