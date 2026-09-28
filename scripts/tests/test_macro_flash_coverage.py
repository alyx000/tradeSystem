"""集中回归：覆盖守恒、全量证据、归档代重推与无副作用。"""
import datetime as dt
import json

import pytest

from services.macro_flash import collector, formatter, service, summary
from services.macro_flash.filter import FlashCandidate

START = dt.datetime(2026, 7, 22, 20)
END = dt.datetime(2026, 7, 23, 20)
CONFIG = {"macro_flash": {"keywords": {"货币政策": ["央行"], "财政债券": ["国债"]}}}


def item(i, text, important=0, time="2026-07-23 10:00:00"):
    return {"id": str(i), "time": time, "important": important, "data": {"content": text}}


def plan(candidates, **kwargs):
    return formatter.build_push_plan(
        candidates, window_start=START, window_end=END, source_status="complete",
        raw_count=100, window_count=50, topic_order=["货币政策", "财政债券"],
        archive_hint="data/runs/macro-flash/2026-07-23/digest.md", **kwargs)


def run(tmp_path, items, **kwargs):
    kwargs.setdefault("no_push", True)
    return service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path,
                       collect_fn=lambda *_: collector.CollectResult(
                           status="complete", items=items, raw_count=100), **kwargs)


def test_coverage_accounts_for_caps_nonimportant_and_zero_topics():
    cands = [FlashCandidate(item(i, "央行", 1), "货币政策") for i in range(12)]
    cands += [FlashCandidate(item("ordinary", "央行"), "货币政策")]
    cands += [FlashCandidate(item(f"other{i}", "其他", 1), "其他要闻") for i in range(6)]
    md, cov = plan(cands)
    assert (cov["raw_count"], cov["window_count"], cov["matched_count"],
            cov["selected_count"], cov["omitted_count"]) == (100, 50, 19, 11, 8)
    assert cov["topics"]["财政债券"] == dict(matched_count=0, selected_count=0, omitted_count=0)
    assert cov["topics"]["其他要闻"]["selected_count"] == 3
    assert "matched=19；selected=11；omitted=8" in md


def test_budget_counts_actual_whole_blocks_even_in_fallback():
    cands = [FlashCandidate(item("small", "央行短文本"), "货币政策")]
    cands += [FlashCandidate(item(i, "国债" * 100), "财政债券") for i in range(60)]
    md, cov = plan(cands)
    assert len(md.encode()) <= 18000
    assert cov["selection_mode"] == "full_fallback"
    assert cov["selected_count"] == 1
    assert cov["omitted_count"] == cov["budget_omitted_count"] == 60
    assert "## 财政债券" not in md and "selected=1；omitted=60" in md
    assert md.count("- **") == cov["selected_count"]


def test_budget_overflow_fails_closed_without_sending(monkeypatch):
    monkeypatch.setattr(formatter, "PUSH_BODY_MAX_BYTES", 10)
    with pytest.raises(ValueError, match="拒绝发送"):
        plan([])


def test_summary_uses_all_raw_even_unmatched_unimportant_and_full_content(tmp_path):
    items = [item("m", "央行公告", 1),
             item("industry", "半导体订单增长", time="2026-07-22 23:00:00"),
             item("stock", "某公司公告 " + "说明" * 150 + " 600000.SH"),
             item("number", "成交金额600000元")]
    out = run(tmp_path, items)
    m = out.manifest
    assert m["matched_count"] == 1 and m["window_count"] == 4 and m["raw_count"] == 100
    s = m["summary"]
    assert s["input_count"] == 4 and s["input"] == "flash_raw.json.items"
    assert s["source_dates"] == ["2026-07-22", "2026-07-23"]
    industry = s["categories"]["产业趋势"]["evidence"]
    assert [(x["id"], x["source_date"], x["source"]) for x in industry] == [
        ("industry", "2026-07-22", "金十快讯")]
    stock = s["categories"]["核心个股"]["evidence"]
    assert [x["id"] for x in stock] == ["stock"]
    assert "600000.SH" in stock[0]["text"]
    assert "半导体订单增长" in out.digest_md
    assert s["optional_sources"]["research-digest"]["coverage"] == "not_collected"
    assert s["unclassified_count"] == 1


@pytest.mark.parametrize("status", ["source_failed", "partial_window_truncated", "schema_drift", "pagination_stalled"])
def test_partial_and_failed_evidence_keep_original_status_and_error(tmp_path, status):
    push = []
    out = service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path,
                      collect_fn=lambda *_: collector.CollectResult(
                          status=status, error="timeout", items=[item("i", "半导体产业消息")], raw_count=7),
                      push_fn=lambda _, md: push.append(md) or True)
    assert out.manifest["summary"]["coverage"] == status
    assert out.manifest["summary"]["categories"]["产业趋势"]["coverage"] == status
    assert out.manifest["summary"]["error"] == "timeout"
    assert status in push[0] and "timeout" in push[0]
    assert out.exit_code == service.EXIT_CODES[status]


def test_failed_source_retains_matched_but_does_not_select_detail(tmp_path):
    out = service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path, no_push=True,
                      collect_fn=lambda *_: collector.CollectResult(
                          status="source_failed", items=[item("i", "央行", 1)], error="broken"))
    cov = out.manifest["coverage"]
    assert (cov["matched_count"], cov["selected_count"], cov["omitted_count"]) == (1, 0, 1)
    assert cov["selection_mode"] == "status_only"


def test_empty_category_is_missing_data_not_no_events():
    s = summary.build_summary([], source_status="complete")
    assert all(g["coverage"] == "missing-data" for g in s["categories"].values())
    assert "source_date=unknown" in summary.render_summary(s)
    assert "不能推断无事件" in summary.render_summary(s)


def test_repush_replays_exact_payload_and_no_push_still_records_planned_coverage(tmp_path, monkeypatch):
    first = run(tmp_path, [item("m", "央行", 1), item("i", "芯片出货")])
    day = tmp_path / "2026-07-23"
    raw_before = (day / "flash_raw.json").read_bytes()
    digest_before = (day / "digest.md").read_bytes()
    archived = json.loads(raw_before)["push_digest"]
    assert first.manifest["push_status"] == "skipped"
    assert first.manifest["selected_count"] == 1
    assert "芯片出货" in archived
    monkeypatch.setattr(formatter, "PUSH_PER_TOPIC_LIMIT", 0)
    monkeypatch.setattr(summary, "build_summary", lambda *a, **kw: pytest.fail("must not regenerate"))
    pushed = []
    out = service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path, repush=True,
                      collect_fn=lambda *_: pytest.fail("must not collect"),
                      push_fn=lambda _, md: pushed.append(md) or True)
    assert out.status == "repushed" and pushed == [archived]
    assert (day / "flash_raw.json").read_bytes() == raw_before
    assert (day / "digest.md").read_bytes() == digest_before
    assert out.manifest["coverage"] == first.manifest["coverage"]


def test_legacy_repush_derives_coverage_from_archived_topics_and_raw(tmp_path):
    run(tmp_path, [item("m", "央行", 1), item("i", "产业订单")])
    day = tmp_path / "2026-07-23"
    raw = json.loads((day / "flash_raw.json").read_text())
    del raw["push_digest"]
    raw_text = json.dumps(raw, ensure_ascii=False)
    (day / "flash_raw.json").write_text(raw_text)
    m = json.loads((day / "manifest.json").read_text())
    m["schema_version"] = 1
    m["files"]["flash_raw"]["sha256"] = service._sha256(raw_text)
    for key in ("coverage", "summary", "selected_count", "omitted_count", "topic_order"):
        m.pop(key, None)
    (day / "manifest.json").write_text(json.dumps(m))
    pushed = []
    out = service.run({"macro_flash": {"keywords": {"新主题": ["产业"]}}},
                      date_str="2026-07-23", base_dir=tmp_path, repush=True,
                      push_fn=lambda _, md: pushed.append(md) or True)
    assert out.manifest["coverage"]["topics"]["货币政策"]["selected_count"] == 1
    assert "新主题" not in pushed[0] and "产业订单" in pushed[0]


def test_dry_run_and_run_error_never_claim_delivery_or_zero_coverage(tmp_path):
    dry = run(tmp_path, [item("i", "产业订单")], dry_run=True)
    assert "产业订单" in dry.digest_md and not list(tmp_path.iterdir())
    def boom(*_):
        raise RuntimeError("unavailable")
    out = service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path, collect_fn=boom)
    assert out.status == "run_error"
    assert all(out.manifest[k] is None for k in
               ("raw_count", "matched_count", "selected_count", "omitted_count"))


def test_important_budget_counts_exclude_summary_references():
    cands = [FlashCandidate(item(f"{t}-{i}", "产业资讯" * 60, 1), f"主题{t}")
             for t in range(6) for i in range(10)]
    evidence = summary.build_summary([c.item for c in cands], source_status="complete")
    md, cov = plan(cands, summary_md=summary.render_summary(evidence, compact=True))
    assert len(md.encode()) <= 18000
    assert cov["eligible_count"] == 48
    assert 0 < cov["selected_count"] < 48
    assert md.count("- **") == cov["selected_count"]
    assert cov["matched_count"] == cov["selected_count"] + cov["omitted_count"]
    assert cov["budget_omitted_count"] == 48 - cov["selected_count"]
    assert "证据 60 条 · 展示 1 条" in md
    for topic, counts in cov["topics"].items():
        assert counts["matched_count"] == counts["selected_count"] + counts["omitted_count"]
        assert counts["selected_count"] in (0, 8)
        assert (f"## {topic}(8)" in md) == bool(counts["selected_count"])


def test_tampered_archived_push_cannot_be_repushed(tmp_path):
    run(tmp_path, [item("m", "央行", 1)])
    path = tmp_path / "2026-07-23" / "flash_raw.json"
    raw = json.loads(path.read_text())
    raw["push_digest"] = "changed"
    path.write_text(json.dumps(raw))
    out = service.run(CONFIG, date_str="2026-07-23", base_dir=tmp_path, repush=True,
                      push_fn=lambda *_: pytest.fail("must not send corrupt snapshot"))
    assert out.status == "archive_corrupt"
