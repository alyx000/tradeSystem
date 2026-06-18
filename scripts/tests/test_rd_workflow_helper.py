"""慧博 JS workflow 的 Python JSON helper 边界测试。"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace


HELPER = Path(__file__).resolve().parents[1] / "workflows" / "huibo_helper.py"


def _load_helper_module():
    spec = importlib.util.spec_from_file_location("huibo_helper_under_test", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_should_run_on_trade_day_or_pre_trade_day(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER)")
    conn.executemany(
        "INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)",
        [
            ("2026-06-05", 1),
            ("2026-06-06", 0),
            ("2026-06-07", 0),
            ("2026-06-08", 1),
            ("2026-06-13", 0),
            ("2026-06-14", 0),
        ],
    )
    conn.commit()
    conn.close()
    env = {**os.environ, "TRADE_DB_PATH": str(db_path)}

    trade_day = _run_helper(["should-run", "--date", "2026-06-05"], cwd=Path.cwd(), env=env)
    pre_trade_day = _run_helper(["should-run", "--date", "2026-06-07"], cwd=Path.cwd(), env=env)
    quiet_day = _run_helper(["should-run", "--date", "2026-06-13"], cwd=Path.cwd(), env=env)

    assert trade_day["should_run"] is True
    assert trade_day["reason"] == "trade_day"
    assert pre_trade_day["should_run"] is True
    assert pre_trade_day["reason"] == "pre_trade_day"
    assert quiet_day["should_run"] is False
    assert quiet_day["reason"] == "not_trade_or_pre_trade_day"


def test_resolve_date_uses_current_trade_or_pre_trade_day(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER)")
    conn.executemany(
        "INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)",
        [
            ("2026-06-12", 1),
            ("2026-06-13", 0),
            ("2026-06-14", 0),
            ("2026-06-15", 1),
            ("2026-06-16", 1),
            ("2026-06-20", 0),
            ("2026-06-21", 0),
            ("2026-06-22", 1),
        ],
    )
    conn.commit()
    conn.close()
    env = {**os.environ, "TRADE_DB_PATH": str(db_path)}

    trade_day = _run_helper(["resolve-date", "--date", "2026-06-15"], cwd=Path.cwd(), env=env)
    pre_trade_day = _run_helper(["resolve-date", "--date", "2026-06-14"], cwd=Path.cwd(), env=env)
    quiet_day = _run_helper(["resolve-date", "--date", "2026-06-20"], cwd=Path.cwd(), env=env)

    assert trade_day["date"] == "2026-06-15"
    assert trade_day["source"] == "trade_calendar_db"
    assert pre_trade_day["date"] == "2026-06-14"
    assert pre_trade_day["source"] == "trade_calendar_db"
    assert quiet_day["date"] == "2026-06-16"
    assert quiet_day["source"] == "prev_trade_calendar_db"


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
    assert downloaded[0]["pdf_download"]["status"] == "ok"

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


def test_finalize_marks_antigravity_unavailable_and_ranker_fallback(tmp_path, monkeypatch):
    helper = _load_helper_module()
    run_dir = tmp_path / "run"
    reader_dir = run_dir / "reader"
    summary_dir = tmp_path / "summaries"
    reader_dir.mkdir(parents=True)
    markdown_path = run_dir / "report.md"
    events_path = run_dir / "events.jsonl"

    candidate = helper.huibo.HuiboCandidate(
        title="A证券-机器人行业深度：重点推荐产业链",
        rating="买入（首次）",
        pages=20,
        date="2026-06-03",
        category="行业分析",
    )
    item = helper.huibo.PrescreenedCandidate(
        candidate=candidate,
        score=88,
        reasons=["首次覆盖"],
        topic_key="机器人",
    )
    prescreened_path = run_dir / "downloaded.json"
    helper._write_json(prescreened_path, [helper._prescreened_json(item)])

    def fail_runner():
        raise AssertionError("finalize must not build Antigravity runner when unavailable")

    monkeypatch.setattr(helper.narrator, "build_antigravity_runner", fail_runner)
    args = SimpleNamespace(
        date="2026-06-03",
        prescreened=str(prescreened_path),
        reader_dir=str(reader_dir),
        summary_dir=str(summary_dir),
        markdown_out=str(markdown_path),
        events_path=str(events_path),
        recommend_cap=1,
        lookback_days=5,
        no_llm=False,
        antigravity_status="unavailable",
        antigravity_reason="quota_exhausted",
        antigravity_message="RESOURCE_EXHAUSTED (code 429): Individual quota reached",
        antigravity_log_file="/tmp/agy.log",
    )

    result = helper._cmd_finalize(args)

    assert result["llm_status"] == "unavailable"
    assert result["ranker_status"] == "fallback"
    summary = json.loads((summary_dir / "2026-06-03.json").read_text(encoding="utf-8"))
    assert summary["meta"]["antigravity"]["status"] == "unavailable"
    assert summary["meta"]["ranker"]["status"] == "fallback"
    assert summary["meta"]["ranker"]["reason"] == "quota_exhausted"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Antigravity 不可用" in markdown
    assert "ranker=fallback" in markdown
    events = events_path.read_text(encoding="utf-8")
    assert '"event":"finalize_agent_skip"' in events
    assert '"reason":"antigravity_unavailable"' in events


def test_finalize_promotes_agent_quota_to_antigravity_unavailable(tmp_path, monkeypatch):
    helper = _load_helper_module()
    run_dir = tmp_path / "run"
    reader_dir = run_dir / "reader"
    summary_dir = tmp_path / "summaries"
    reader_dir.mkdir(parents=True)
    markdown_path = run_dir / "report.md"
    events_path = run_dir / "events.jsonl"

    candidate = helper.huibo.HuiboCandidate(
        title="A证券-机器人行业深度：重点推荐产业链",
        rating="买入（首次）",
        pages=20,
        date="2026-06-03",
        category="行业分析",
    )
    item = helper.huibo.PrescreenedCandidate(
        candidate=candidate,
        score=88,
        reasons=["首次覆盖"],
        topic_key="机器人",
    )
    prescreened_path = run_dir / "downloaded.json"
    helper._write_json(prescreened_path, [helper._prescreened_json(item)])
    (reader_dir / f"{candidate.report_id}.json").write_text(json.dumps({
        "industry": "机器人",
        "key_points": ["ok"],
        "mentioned_stocks": [],
        "read_score": 80,
    }, ensure_ascii=False), encoding="utf-8")

    class QuotaRunner:
        last_diagnostics = None

        def __call__(self, _role, _payload):
            self.last_diagnostics = {
                "reason": "quota_exhausted",
                "message": "RESOURCE_EXHAUSTED (code 429)",
                "log_file": "/tmp/agy.log",
            }
            return None

    monkeypatch.setattr(helper.narrator, "build_antigravity_runner", lambda: object())
    monkeypatch.setattr(helper.huibo, "build_role_runner", lambda _runner: QuotaRunner())
    args = SimpleNamespace(
        date="2026-06-03",
        prescreened=str(prescreened_path),
        reader_dir=str(reader_dir),
        summary_dir=str(summary_dir),
        markdown_out=str(markdown_path),
        events_path=str(events_path),
        recommend_cap=1,
        lookback_days=5,
        no_llm=False,
        antigravity_status="ok",
        antigravity_reason="",
        antigravity_message="",
        antigravity_log_file="",
    )

    result = helper._cmd_finalize(args)

    assert result["llm_status"] == "unavailable"
    summary = json.loads((summary_dir / "2026-06-03.json").read_text(encoding="utf-8"))
    assert summary["meta"]["antigravity"]["status"] == "unavailable"
    assert summary["meta"]["antigravity"]["reason"] == "quota_exhausted"
    assert summary["meta"]["ranker"]["status"] == "fallback"
    assert summary["meta"]["ranker"]["reason"] == "quota_exhausted"
    agents = summary["meta"]["agents"]
    assert agents["industry_aggregator"]["status"] == "failed"
    assert agents["trend_aggregator"]["status"] == "skipped"
    assert agents["ranker"]["status"] == "skipped"
    assert "Antigravity 不可用" in markdown_path.read_text(encoding="utf-8")


def test_finalize_no_llm_reports_llm_status_off(tmp_path):
    helper = _load_helper_module()
    run_dir = tmp_path / "run"
    reader_dir = run_dir / "reader"
    summary_dir = tmp_path / "summaries"
    reader_dir.mkdir(parents=True)
    markdown_path = run_dir / "report.md"
    candidate = helper.huibo.HuiboCandidate(
        title="A证券-机器人行业深度：重点推荐产业链",
        rating="买入（首次）",
        pages=20,
        date="2026-06-03",
        category="行业分析",
    )
    item = helper.huibo.PrescreenedCandidate(candidate=candidate, score=88, reasons=["首次覆盖"], topic_key="机器人")
    prescreened_path = run_dir / "downloaded.json"
    helper._write_json(prescreened_path, [helper._prescreened_json(item)])
    (reader_dir / f"{candidate.report_id}.json").write_text(json.dumps({
        "industry": "机器人",
        "key_points": ["ok"],
        "mentioned_stocks": [],
        "read_score": 80,
    }, ensure_ascii=False), encoding="utf-8")
    args = SimpleNamespace(
        date="2026-06-03",
        prescreened=str(prescreened_path),
        reader_dir=str(reader_dir),
        summary_dir=str(summary_dir),
        markdown_out=str(markdown_path),
        events_path=None,
        recommend_cap=1,
        lookback_days=5,
        no_llm=True,
        antigravity_status="ok",
        antigravity_reason="",
        antigravity_message="",
        antigravity_log_file="",
    )

    result = helper._cmd_finalize(args)

    assert result["llm_status"] == "off"
    summary = json.loads((summary_dir / "2026-06-03.json").read_text(encoding="utf-8"))
    assert summary["meta"]["antigravity"]["status"] == "off"


def test_finalize_quality_audit_excludes_redline_reader_and_explains_fallback(tmp_path):
    helper = _load_helper_module()
    run_dir = tmp_path / "run"
    reader_dir = run_dir / "reader"
    summary_dir = tmp_path / "summaries"
    reader_dir.mkdir(parents=True)
    markdown_path = run_dir / "report.md"
    bad = helper.huibo.HuiboCandidate(
        title="A证券-机器人行业深度：重点推荐产业链",
        rating="买入（首次）",
        pages=20,
        date="2026-06-03",
        category="行业分析",
    )
    good = helper.huibo.HuiboCandidate(
        title="B证券-机器人行业深度：重点推荐产业链",
        rating="买入（首次）",
        pages=20,
        date="2026-06-03",
        category="行业分析",
    )
    items = [
        helper.huibo.PrescreenedCandidate(candidate=bad, score=90, reasons=["首次覆盖"], topic_key="机器人"),
        helper.huibo.PrescreenedCandidate(candidate=good, score=70, reasons=["首次覆盖"], topic_key="机器人"),
    ]
    prescreened_path = run_dir / "downloaded.json"
    helper._write_json(prescreened_path, [helper._prescreened_json(item) for item in items])
    (reader_dir / f"{bad.report_id}.json").write_text(json.dumps({
        "industry": "机器人",
        "viewpoint": "目标价 100 元，建议买入",
        "key_points": ["目标价 100 元"],
        "mentioned_stocks": [{"name": "测试股", "viewpoint": "目标价 100 元", "source": "正文"}],
        "recommend_reason": "目标价上行",
        "read_score": 99,
    }, ensure_ascii=False), encoding="utf-8")
    (reader_dir / f"{good.report_id}.json").write_text(json.dumps({
        "industry": "机器人",
        "viewpoint": "产业链景气改善",
        "key_points": ["产业链景气改善"],
        "mentioned_stocks": [{"name": "好公司", "viewpoint": "受益产业链景气", "source": "第 3 页"}],
        "recommend_reason": "观点清晰且有来源",
        "read_score": 70,
    }, ensure_ascii=False), encoding="utf-8")
    args = SimpleNamespace(
        date="2026-06-03",
        prescreened=str(prescreened_path),
        reader_dir=str(reader_dir),
        summary_dir=str(summary_dir),
        markdown_out=str(markdown_path),
        events_path=None,
        recommend_cap=1,
        lookback_days=5,
        no_llm=True,
        antigravity_status="ok",
        antigravity_reason="",
        antigravity_message="",
        antigravity_log_file="",
    )

    helper._cmd_finalize(args)

    summary = json.loads((summary_dir / "2026-06-03.json").read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in summary["reader_results"]}
    assert rows[bad.title]["reader"]["error"] == "quality_failed"
    assert "redline_terms" in rows[bad.title]["reader"]["quality"]["issues"]
    rec = summary["recommendations"][0]
    assert rec["title"] == good.title
    assert rec["ranking_explanation"]["ranker"] == "fallback"
    assert rec["ranking_explanation"]["read_score"] == 70
    assert rec["ranking_explanation"]["prescreen_score"] == 70


def test_publish_carries_antigravity_and_ranker_meta(tmp_path, monkeypatch):
    helper = _load_helper_module()
    markdown = tmp_path / "report.md"
    markdown.write_text("# 研报速读 · 2026-06-03\n\nfixture\n", encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "meta": {
            "antigravity": {"status": "unavailable", "reason": "quota_exhausted"},
            "ranker": {"status": "fallback", "reason": "quota_exhausted"},
            "llm_agent_attempted_count": 1,
            "llm_agent_skipped_count": 2,
            "llm_agent_used_count": 0,
        }
    }, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "published.json"
    out_root = tmp_path / "reports"

    def fail_push(*_args, **_kwargs):
        raise AssertionError("no-push publish must not call DingTalk")

    monkeypatch.setattr(helper, "_push_to_dingtalk", fail_push)
    args = SimpleNamespace(
        date="2026-06-03",
        markdown=str(markdown),
        huibo_summary=str(summary),
        include_base_digest=False,
        out_root=str(out_root),
        out=str(out),
        dry_run=False,
        no_push=True,
    )

    payload = helper._cmd_publish(args)

    assert payload["huibo_antigravity"]["status"] == "unavailable"
    assert payload["huibo_ranker"]["status"] == "fallback"
    assert payload["llm_agent_attempted_count"] == 1
    assert payload["llm_agent_skipped_count"] == 2
    assert payload["llm_agent_used_count"] == 0
    published = json.loads(out.read_text(encoding="utf-8"))
    assert published["huibo_antigravity"]["reason"] == "quota_exhausted"
    assert published["huibo_ranker"]["reason"] == "quota_exhausted"


def test_publish_records_false_when_dingtalk_push_fails(tmp_path, monkeypatch):
    helper = _load_helper_module()
    markdown = tmp_path / "report.md"
    markdown.write_text("# 研报速读 · 2026-06-03\n\nfixture\n", encoding="utf-8")
    out = tmp_path / "published.json"

    monkeypatch.setattr(helper, "_push_to_dingtalk", lambda *_args, **_kwargs: False)
    args = SimpleNamespace(
        date="2026-06-03",
        markdown=str(markdown),
        huibo_summary=None,
        include_base_digest=False,
        out_root=str(tmp_path / "reports"),
        out=str(out),
        dry_run=False,
        no_push=False,
    )

    payload = helper._cmd_publish(args)

    assert payload["pushed"] is False
    published = json.loads(out.read_text(encoding="utf-8"))
    assert published["pushed"] is False


def test_publish_include_base_digest_merges_base_and_huibo_markdown(tmp_path, monkeypatch):
    helper = _load_helper_module()
    markdown = tmp_path / "report.md"
    markdown.write_text(
        "# 研报速读 · 2026-06-03\n\n"
        "> 窗口 2026-06-03（最近交易日）｜A股 0 标的覆盖 ｜ 美股 0 条评级变动\n\n"
        "## 🏆 Top3 最值得读\n"
        "- 今日两市均无符合条件的评级变动\n\n"
        "## 📚 慧博深读 Top1\n"
        "1. **慧博报告**\n\n"
        "## 🇨🇳 A股机构评级\n"
        "- 今日 A股无研报评级数据\n\n"
        "---\n"
        "> 本报告基于公开机构评级数据整理，不构成任何买卖建议，不预测价格目标。\n",
        encoding="utf-8",
    )
    out = tmp_path / "published.json"
    out_root = tmp_path / "reports"

    class FakeDigest:
        title = "研报速读 · 2026-06-03"
        markdown = (
            "# 研报速读 · 2026-06-03\n\n"
            "> 窗口 2026-06-03（最近交易日）｜A股 1 标的覆盖 ｜ 美股 0 条评级变动\n\n"
            "## 🏆 Top3 最值得读\n"
            "- 基础研报\n\n"
            "## 🇨🇳 A股机构评级\n"
            "- 基础A股\n\n"
            "---\n"
            "> 本报告基于公开机构评级数据整理，不构成任何买卖建议，不预测价格目标。\n"
        )

    monkeypatch.setattr(helper, "_render_base_digest", lambda args: FakeDigest())
    args = SimpleNamespace(
        date="2026-06-03",
        markdown=str(markdown),
        huibo_summary=None,
        include_base_digest=True,
        out_root=str(out_root),
        out=str(out),
        dry_run=False,
        no_push=True,
    )

    payload = helper._cmd_publish(args)

    rendered = Path(payload["markdown"]).read_text(encoding="utf-8")
    assert "基础研报" in rendered
    assert "慧博深读 Top1" in rendered
    assert rendered.count("> 窗口 2026-06-03") == 1
    assert rendered.count("> 本报告基于公开机构评级数据整理") == 1
    assert "今日两市均无符合条件的评级变动" not in rendered
    assert payload["base_digest_error"] == ""
    assert payload["base_digest_duration_ms"] >= 0


def test_publish_include_base_digest_omits_empty_huibo_base_sections(tmp_path, monkeypatch):
    helper = _load_helper_module()
    markdown = tmp_path / "report.md"
    markdown.write_text(
        "# 研报速读 · 2026-06-03\n\n"
        "> 窗口 2026-06-03（最近交易日）｜A股 0 标的覆盖 ｜ 美股 0 条评级变动\n\n"
        "## 🏆 Top3 最值得读\n"
        "- 今日两市均无符合条件的评级变动\n"
        "> ranker=fallback：no_successful_reader；慧博 Top 推荐不是 ranker agent 生成。\n\n"
        "## 🇨🇳 A股机构评级\n"
        "- 今日 A股无研报评级数据\n\n"
        "## 🇺🇸 美股评级变动\n"
        "- 今日美股无符合条件的评级变动\n\n"
        "---\n"
        "> 本报告基于公开机构评级数据整理，不构成任何买卖建议，不预测价格目标。\n",
        encoding="utf-8",
    )
    out = tmp_path / "published.json"
    out_root = tmp_path / "reports"

    class FakeDigest:
        title = "研报速读 · 2026-06-03"
        markdown = (
            "# 研报速读 · 2026-06-03\n\n"
            "> 窗口 2026-06-03（最近交易日）｜A股 1 标的覆盖 ｜ 美股 0 条评级变动\n\n"
            "## 🏆 Top3 最值得读\n"
            "- 基础研报\n\n"
            "## 🇨🇳 A股机构评级\n"
            "- 基础A股\n\n"
            "---\n"
            "> 本报告基于公开机构评级数据整理，不构成任何买卖建议，不预测价格目标。\n"
        )

    monkeypatch.setattr(helper, "_render_base_digest", lambda args: FakeDigest())
    args = SimpleNamespace(
        date="2026-06-03",
        markdown=str(markdown),
        huibo_summary=None,
        include_base_digest=True,
        out_root=str(out_root),
        out=str(out),
        dry_run=False,
        no_push=True,
    )

    payload = helper._cmd_publish(args)

    rendered = Path(payload["markdown"]).read_text(encoding="utf-8")
    assert "基础研报" in rendered
    assert "ranker=fallback" in rendered
    assert rendered.count("> 窗口 2026-06-03") == 1
    assert "今日两市均无符合条件的评级变动" not in rendered
    assert "今日 A股无研报评级数据" not in rendered
    assert "今日美股无符合条件的评级变动" not in rendered


def test_publish_records_base_digest_error_without_blocking_huibo_publish(tmp_path, monkeypatch):
    helper = _load_helper_module()
    markdown = tmp_path / "report.md"
    markdown.write_text("# 研报速读 · 2026-06-03\n\n## 📚 慧博深读 Top1\n1. **慧博报告**\n", encoding="utf-8")
    out = tmp_path / "published.json"
    out_root = tmp_path / "reports"

    def fail_base_digest(_args):
        raise RuntimeError("base provider unavailable")

    monkeypatch.setattr(helper, "_render_base_digest", fail_base_digest)
    args = SimpleNamespace(
        date="2026-06-03",
        markdown=str(markdown),
        huibo_summary=None,
        include_base_digest=True,
        out_root=str(out_root),
        out=str(out),
        dry_run=False,
        no_push=True,
    )

    payload = helper._cmd_publish(args)

    assert payload["base_digest_included"] is False
    assert "base provider unavailable" in payload["base_digest_error"]
    assert payload["base_digest_duration_ms"] >= 0
    rendered = Path(payload["markdown"]).read_text(encoding="utf-8")
    assert "慧博深读 Top1" in rendered
