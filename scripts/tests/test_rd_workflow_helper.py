"""慧博 JS workflow 的 Python JSON helper 边界测试。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "workflows" / "huibo_helper.py"


def _run_helper(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        ["python3", str(HELPER), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_huibo_helper_collect_prescreen_download_finalize(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    raw_dir = tmp_path / "raw"
    run_dir = tmp_path / "run"
    reader_dir = run_dir / "reader"
    summary_dir = tmp_path / "summaries"
    pdf_dir.mkdir()
    reader_dir.mkdir(parents=True)
    title = "A证券-机器人行业深度：重点推荐产业链"
    (pdf_dir / "fixture.pdf").write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {
                "报告名称": title,
                "报告评级": "买入（首次）",
                "作者": "张三",
                "页数": "20页",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(pdf_dir / "fixture.pdf"),
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_REPORT_PDF_DIR", str(pdf_dir))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))

    candidates_path = run_dir / "candidates.json"
    texts_path = run_dir / "texts.json"
    prescreened_path = run_dir / "prescreened.json"
    downloaded_path = run_dir / "downloaded.json"
    markdown_path = run_dir / "report.md"
    events_path = run_dir / "events.jsonl"

    collect = _run_helper([
        "collect",
        "--date", "2026-06-03",
        "--window-days", "5",
        "--out", str(candidates_path),
        "--texts-out", str(texts_path),
    ], cwd=Path.cwd())
    assert collect["candidate_count"] == 1
    assert candidates_path.exists()

    prescreen = _run_helper([
        "prescreen",
        "--candidates", str(candidates_path),
        "--texts", str(texts_path),
        "--reader-cap", "1",
        "--out", str(prescreened_path),
    ], cwd=Path.cwd())
    assert prescreen["prescreened_count"] == 1

    download = _run_helper([
        "download",
        "--prescreened", str(prescreened_path),
        "--raw-dir", str(raw_dir),
        "--out", str(downloaded_path),
    ], cwd=Path.cwd())
    assert download["downloaded_count"] == 1
    downloaded = json.loads(downloaded_path.read_text(encoding="utf-8"))
    rid = downloaded[0]["candidate"]["report_id"]
    assert Path(downloaded[0]["candidate"]["pdf_path"]).exists()

    (reader_dir / f"{rid}.json").write_text(json.dumps({
        "industry": "机器人",
        "key_points": ["产业链升温"],
        "mentioned_stocks": [{"name": "测试股", "source": "fixture"}],
        "read_score": 80,
    }, ensure_ascii=False), encoding="utf-8")

    final = _run_helper([
        "finalize",
        "--date", "2026-06-03",
        "--prescreened", str(downloaded_path),
        "--reader-dir", str(reader_dir),
        "--summary-dir", str(summary_dir),
        "--markdown-out", str(markdown_path),
        "--events-path", str(events_path),
        "--recommend-cap", "1",
        "--lookback-days", "5",
        "--no-llm",
    ], cwd=Path.cwd())

    assert final["reader_count"] == 1
    assert final["llm_agent_count"] == 0
    assert final["recommendation_count"] == 1
    assert (summary_dir / "2026-06-03.json").exists()
    assert "慧博深读 Top1" in markdown_path.read_text(encoding="utf-8")
    events = events_path.read_text(encoding="utf-8")
    assert '"event":"finalize_agent_skip"' in events
    assert '"role":"industry_aggregator"' in events
    assert '"reason":"llm_disabled"' in events
