"""JS 慧博研报 workflow：状态日志、断点续跑、reader 并发边界。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "research-digest-workflow.mjs"


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
    assert len(list((run_dir / "reader").glob("*.json"))) == 3
    assert (run_dir / "report.md").exists()
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"event":"stage_start"' in events
    assert '"stage":"read"' in events
    assert '"event":"finalize_agent_skip"' in events
    assert '"role":"industry_aggregator"' in events
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 3

    subprocess.run([*cmd, "--resume"], text=True, capture_output=True, check=True)
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 3


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


def test_js_workflow_default_date_uses_asia_shanghai_day(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "报告评级": "买入（首次）",
                "页数": "20页",
                "时间": "2026-06-07",
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
    assert not (tmp_path / "runs" / "2026-06-06").exists()


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
    subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=5)

    state = json.loads((tmp_path / "runs" / "2026-06-03" / "state.json").read_text(encoding="utf-8"))
    report = next(iter(state["reports"].values()))
    assert report["status"] == "failed"
    assert report["lastError"] == "antigravity requires interactive authentication"
