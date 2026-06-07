"""research_digest.huibo：慧博候选解析、预筛、Antigravity 隔离、清理。"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

from services.research_digest import huibo


def test_parse_hot_report_rows_normalizes_fields():
    rows = [
        {
            "报告名称": "国金证券-氢能与燃料电池行业：电力缺口催生刚性替代-260603",
            "报告评级": "买入（首次）",
            "作者": "姚遥\n唐雪琪",
            "大小": "1,886 K",
            "页数": "25页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "阅读链接": "https://example.test/r",
            "PDF路径": "/tmp/report.pdf",
        }
    ]

    out = huibo.parse_hot_report_rows(rows)

    assert out[0].title.startswith("国金证券-氢能")
    assert out[0].rating == "买入（首次）"
    assert out[0].authors == ["姚遥", "唐雪琪"]
    assert out[0].size_kb == 1886
    assert out[0].pages == 25
    assert out[0].date == "2026-06-03"
    assert out[0].category == "行业分析"
    assert out[0].read_url == "https://example.test/r"
    assert out[0].pdf_path == "/tmp/report.pdf"


def test_report_id_disambiguates_same_title_without_urls():
    rows = [
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "时间": "2026-06-03", "分类": "行业分析"},
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "时间": "2026-06-03", "分类": "行业分析"},
    ]

    out = huibo.parse_hot_report_rows(rows)

    assert len(out) == 2
    assert out[0].report_id != out[1].report_id


def test_prescreen_prioritizes_first_coverage_and_strong_hints():
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-普通周报：行业数据跟踪", "报告评级": "增持", "页数": "18页", "时间": "2026-06-03", "分类": "行业分析"},
        {"报告名称": "B证券-机器人行业深度：重点关注国产替代", "报告评级": "", "页数": "36页", "时间": "2026-06-03", "分类": "行业分析"},
        {"报告名称": "C证券-某公司首次覆盖：AI算力新方向", "报告评级": "买入（首次）", "页数": "24页", "时间": "2026-06-03", "分类": "公司调研"},
    ])

    picked = huibo.prescreen_candidates(candidates, reader_cap=2)

    assert [p.candidate.title[0] for p in picked] == ["C", "B"]
    assert "首次覆盖" in picked[0].reasons
    assert "强提示词" in picked[1].reasons


def test_prescreen_limits_duplicate_topics():
    rows = [
        {"报告名称": f"券商{i}-MLCC行业深度报告：国产替代推进", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"}
        for i in range(5)
    ]
    rows.append({"报告名称": "券商X-商业航天行业深度：产业链重估", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"})

    picked = huibo.prescreen_candidates(huibo.parse_hot_report_rows(rows), reader_cap=10, per_topic_cap=2)

    assert sum("MLCC" in p.candidate.title for p in picked) == 2
    assert any("商业航天" in p.candidate.title for p in picked)


def test_parse_html_extracts_download_link_and_derives_pdf_url():
    html = """
    <table>
      <tr><th>编号</th><th>报告名称</th></tr>
      <tr>
        <td>1</td>
        <td>国信证券-基础化工行业：英伟达Rubin架构发布</td>
        <td></td>
        <td>
          <a href="/hiborClientDownload/Download/Index?downloadType=r&docType=2&did=abc">阅读</a>
          <a href="/hiborClientDownload/Download/Index?downloadType=d&docType=2&did=abc">下载</a>
        </td>
        <td>优于大市</td><td>杨林</td><td>3,171 K</td><td>47页</td><td>2026-06-02</td>
      </tr>
      <tr>
        <td>2</td>
        <td>国金证券-氢能与燃料电池行业：电力缺口催生刚性替代</td>
        <td></td>
        <td>
          <a href="/hiborClientDownload/Download/Index?downloadType=r&docType=2&did=def">阅读</a>
        </td>
        <td>买入（首次）</td><td>姚遥</td><td>1,886 K</td><td>25页</td><td>2026-06-03</td>
      </tr>
    </table>
    """

    out = huibo.parse_hot_report_html(html, category="行业分析", base_url="https://sys.hibor.com.cn/redian/HotReport")

    assert len(out) == 2
    assert out[0].read_url.startswith("https://sys.hibor.com.cn/hiborClientDownload/Download/Index")
    assert "downloadType=d" in out[0].download_url
    assert "linkType=pdf" in out[0].download_url
    assert "downloadType=d" in out[1].download_url
    assert "linkType=pdf" in out[1].download_url


def test_hot_report_api_maps_rows_and_builds_pdf_urls(monkeypatch):
    url = (
        "https://sys.hibor.com.cn/redian/HotReport?"
        "abc=ABC&def=DEF&vidd=3&keyy=KEY&xyz=XYZ&op=0"
    )
    captured = {}

    def post_json(api_url, data, headers=None):
        captured["api_url"] = api_url
        captured["data"] = data
        captured["headers"] = headers
        return [
            {
                "DocTitle": "国信证券-基础化工行业：英伟达Rubin架构发布",
                "Comment": "优于大市",
                "DocAuthor": "<a>杨林</a>",
                "DocSize": 3246655,
                "DocPages": 47,
                "uptime": "2026-06-02",
                "DocType": 2,
                "didMi": "sPqRnMpRzQtQmO",
                "DocDegree": "1",
            },
            {
                "DocTitle": "中信证券-策略专题：政策预期修复",
                "Comment": "",
                "DocAuthor": "张三",
                "DocSize": 1024,
                "DocPages": 30,
                "uptime": "2026-06-03",
                "DocType": 4,
                "didMi": "strategyDid",
            },
            {
                "DocTitle": "华泰证券-某公司首次覆盖：新材料平台",
                "Comment": "买入（首次）",
                "DocAuthor": "李四",
                "DocSize": 2048,
                "DocPages": 22,
                "uptime": "2026-06-04",
                "DocType": 1,
                "didMi": "companyDid",
            },
        ]

    monkeypatch.setattr(huibo, "_post_json", post_json)

    out = huibo._fetch_hot_report_api(url, date="2026-06-06", window_days=5)

    assert captured["api_url"] == "https://sys.hibor.com.cn/redian/HotReport/GetList"
    assert captured["data"]["Starttime"] == "2026-06-02"
    assert captured["data"]["Endtime"] == "2026-06-06"
    assert captured["data"]["unameMi"] == "ABC"
    assert [c.category for c in out] == ["行业分析", "投资策略", "公司调研"]
    assert out[0].authors == ["杨林"]
    assert out[0].size_kb == 3171
    assert out[0].pages == 47
    assert out[0].read_url
    assert "downloadType=r" in out[0].read_url
    assert "downloadType=d" in out[0].download_url
    assert "did=sPqRnMpRzQtQmO" in out[0].download_url
    assert "linkType=pdf" in out[0].download_url


def test_url_source_prefers_hot_report_api_without_downloading_all_pdfs(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    url = (
        "https://sys.hibor.com.cn/redian/HotReport?"
        "abc=ABC&def=DEF&vidd=3&keyy=KEY&xyz=XYZ&op=0"
    )
    fetched_bytes = []
    fetched_text = []

    def post_json(api_url, data, headers=None):
        return [
            {
                "DocTitle": "国信证券-基础化工行业：英伟达Rubin架构发布",
                "Comment": "优于大市",
                "DocAuthor": "<a>杨林</a>",
                "DocSize": 3246655,
                "DocPages": 47,
                "uptime": "2026-06-02",
                "DocType": 2,
                "didMi": "sPqRnMpRzQtQmO",
                "DocDegree": "1",
            },
        ]

    def fetch_text(url, headers=None):
        fetched_text.append(url)
        return "<html></html>"

    def fetch_bytes(url, headers=None):
        fetched_bytes.append(url)
        return b"%PDF-1.4\nfixture"

    monkeypatch.delenv("HUIBO_HOT_REPORT_JSON", raising=False)
    monkeypatch.setenv("HUIBO_HOT_REPORT_URL", url)
    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))
    monkeypatch.setattr(huibo, "_post_json", post_json)
    monkeypatch.setattr(huibo, "_fetch_text", fetch_text)
    monkeypatch.setattr(huibo, "_fetch_bytes", fetch_bytes)

    candidates, texts = huibo.build_source_from_env("desktop_terminal")(None, "2026-06-06", 5)

    assert texts == {}
    assert len(candidates) == 1
    assert candidates[0].title.startswith("国信证券")
    assert "downloadType=d" in candidates[0].download_url
    assert candidates[0].pdf_path == ""
    assert fetched_text == []
    assert fetched_bytes == []
    assert not raw_dir.exists()


def test_snapshot_source_attaches_pdf_from_dir_and_copies_to_raw(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    raw_dir = tmp_path / "raw"
    pdf_dir.mkdir()
    title = "A证券-机器人行业深度：重点推荐产业链"
    local_pdf = pdf_dir / (huibo._safe_filename(title) + ".pdf")
    local_pdf.write_bytes(b"%PDF-1.4\nfixture")
    snapshot = tmp_path / "hot.json"
    snapshot.write_text(json.dumps({
        "rows": [
            {"报告名称": title, "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_REPORT_PDF_DIR", str(pdf_dir))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))

    candidates, texts = huibo.build_source_from_env("desktop_terminal")(None, "2026-06-03", 5)

    assert texts == {}
    assert len(candidates) == 1
    copied = raw_dir / f"{candidates[0].report_id}.pdf"
    assert candidates[0].pdf_path == str(copied)
    assert copied.exists()


def test_snapshot_source_derives_download_url_without_downloading_all_pdfs(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    snapshot = tmp_path / "hot.json"
    read_url = "https://sys.hibor.com.cn/hiborClientDownload/Download/Index?downloadType=r&docType=2&did=abc"
    snapshot.write_text(json.dumps({
        "rows": [
            {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "阅读链接": read_url, "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    fetched = []

    def fetch_bytes(url, headers=None):
        fetched.append(url)
        return b"%PDF-1.4\nfixture"

    monkeypatch.setenv("HUIBO_HOT_REPORT_JSON", str(snapshot))
    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))
    monkeypatch.setattr(huibo, "_fetch_bytes", fetch_bytes)

    candidates, _ = huibo.build_source_from_env("desktop_terminal")(None, "2026-06-03", 5)

    assert len(candidates) == 1
    assert fetched == []
    assert "downloadType=d" in candidates[0].download_url
    assert "linkType=pdf" in candidates[0].download_url
    assert candidates[0].pdf_path == ""
    assert not raw_dir.exists()


def test_run_huibo_digest_downloads_pdf_after_prescreen(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    read_url = "https://sys.hibor.com.cn/hiborClientDownload/Download/Index?downloadType=r&docType=2&did=abc"
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "阅读链接": read_url, "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"},
    ])
    fetched = []

    def fetch_bytes(url, headers=None):
        fetched.append(url)
        return b"%PDF-1.4\nfixture"

    def runner(role, payload):
        if role == "report_reader":
            return {"industry": "机器人", "key_points": ["产业链升温"], "mentioned_stocks": [], "read_score": 80}
        return {}

    monkeypatch.setenv("HUIBO_RAW_DIR", str(raw_dir))
    monkeypatch.setattr(huibo, "_fetch_bytes", fetch_bytes)

    out = huibo.run_huibo_digest(
        candidates,
        report_texts={},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path / "summaries",
        reader_cap=1,
    )

    assert fetched
    assert "downloadType=d" in fetched[0]
    assert "linkType=pdf" in fetched[0]
    assert out.reader_results[0]["pdf_path"] == str(raw_dir / f"{candidates[0].report_id}.pdf")
    assert (raw_dir / f"{candidates[0].report_id}.pdf").read_bytes().startswith(b"%PDF")


def test_role_runner_blocks_pdf_file_reference_when_explicitly_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HUIBO_ALLOW_EXTERNAL_PDF_LLM", "0")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    calls = []

    def prompt_runner(prompt, payload):
        calls.append((prompt, payload))
        return {"ok": True}

    runner = huibo.build_role_runner(prompt_runner)
    out = runner("report_reader", {"report_pdf_path": str(pdf), "report_text": "预览文本"})

    assert out is None
    assert calls == []


def test_role_runner_uses_pdf_file_reference_for_reader_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HUIBO_ALLOW_EXTERNAL_PDF_LLM", raising=False)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    seen = {}

    def prompt_runner(prompt, payload):
        raise AssertionError("report_reader should use dedicated PDF runner")

    def pdf_runner(payload):
        seen["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(huibo, "_run_antigravity_pdf_reader", pdf_runner)
    runner = huibo.build_role_runner(prompt_runner)
    out = runner("report_reader", {"report_pdf_path": str(pdf), "report_text": "预览文本"})

    assert out == {"ok": True}
    assert seen["payload"]["report_pdf_path"] == str(pdf)


def test_antigravity_pdf_reader_grants_pdf_directory(tmp_path, monkeypatch):
    import subprocess

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    seen = {}

    class R:
        returncode = 0
        stdout = '{"industry":"材料","read_score":80}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return R()

    monkeypatch.setenv("ANTIGRAVITY_BIN", "agy")
    monkeypatch.setattr(subprocess, "run", fake_run)

    out = huibo._run_antigravity_pdf_reader({
        "report_pdf_path": str(pdf),
        "metadata": {"title": "测试报告"},
    })

    assert out == {"industry": "材料", "read_score": 80}
    assert "--add-dir" in seen["cmd"]
    assert str(tmp_path) in seen["cmd"]
    assert "--dangerously-skip-permissions" in seen["cmd"]


def test_parse_json_object_extracts_json_after_terminal_noise():
    text = "Warning: terminal\n{\"industry\":\"基础化工\",\"read_score\":95}\n"
    assert huibo._parse_json_object(text) == {"industry": "基础化工", "read_score": 95}


def test_reader_called_once_per_candidate_and_aggregators_use_only_reader_json(tmp_path):
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"%PDF-1.4\nfixture")
    pdf_b.write_bytes(b"%PDF-1.4\nfixture")
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf_a)},
        {"报告名称": "B证券-算力行业首次覆盖：重点关注材料", "报告评级": "买入（首次）", "页数": "28页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf_b)},
    ])
    texts = {c.report_id: f"原始正文不得进入聚合 {c.title}" for c in candidates}
    calls = []

    def runner(role, payload):
        calls.append((role, payload))
        if role == "report_reader":
            return {
                "industry": payload["metadata"]["category"],
                "key_points": [f"观点:{payload['metadata']['title'][:2]}"],
                "mentioned_stocks": [{"name": "测试股", "source": payload["metadata"]["title"]}],
                "read_score": 80,
            }
        if role == "industry_aggregator":
            assert "report_text" not in json.dumps(payload, ensure_ascii=False)
            assert "原始正文不得进入聚合" not in json.dumps(payload, ensure_ascii=False)
            return {"industries": [{"industry": "机器人", "viewpoint": "共识"}]}
        if role == "trend_aggregator":
            assert "report_text" not in json.dumps(payload, ensure_ascii=False)
            return {"changes": ["机器人升温"]}
        if role == "ranker":
            dumped = json.dumps(payload, ensure_ascii=False)
            assert "report_text" not in dumped
            assert "原始正文不得进入聚合" not in dumped
            return {"recommendations": [{"title": payload["reports"][0]["title"], "reason": "最值得读"}]}
        raise AssertionError(role)

    out = huibo.run_huibo_digest(
        candidates,
        report_texts=texts,
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path,
    )

    assert [r for r, _ in calls].count("report_reader") == 2
    assert [r for r, _ in calls].count("industry_aggregator") == 1
    assert [r for r, _ in calls].count("trend_aggregator") == 1
    assert [r for r, _ in calls].count("ranker") == 1
    assert len(out.recommendations) <= 2
    assert out.reader_results[0]["reader"]["viewpoint"].startswith("观点")
    assert (tmp_path / "2026-06-03.json").exists()


def test_report_readers_run_concurrently_before_aggregation(tmp_path):
    pdfs = []
    rows = []
    for i in range(3):
        pdf = tmp_path / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        pdfs.append(pdf)
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-机器人行业深度：重点推荐产业链{i}",
            "报告评级": "推荐",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    candidates = huibo.parse_hot_report_rows(rows)
    lock = threading.Lock()
    active = 0
    max_active = 0
    completed_readers = 0
    aggregation_seen_completed = []

    def runner(role, payload):
        nonlocal active, max_active, completed_readers
        if role == "report_reader":
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
                completed_readers += 1
            return {
                "industry": "机器人",
                "key_points": [payload["metadata"]["title"]],
                "mentioned_stocks": [],
                "read_score": 80,
            }
        if role in {"industry_aggregator", "trend_aggregator", "ranker"}:
            with lock:
                aggregation_seen_completed.append(completed_readers)
            return {}
        raise AssertionError(role)

    huibo.run_huibo_digest(
        candidates,
        report_texts={},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path,
        reader_cap=3,
        reader_concurrency=3,
    )

    assert max_active > 1
    assert aggregation_seen_completed
    assert all(n == 3 for n in aggregation_seen_completed)


def test_huibo_llm_fail_fast_after_first_reader_failure(tmp_path):
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"%PDF-1.4\nfixture")
    pdf_b.write_bytes(b"%PDF-1.4\nfixture")
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf_a)},
        {"报告名称": "B证券-算力行业深度：重点推荐材料", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf_b)},
    ])
    calls = []

    def runner(role, payload):
        calls.append(role)
        return None

    out = huibo.run_huibo_digest(
        candidates,
        report_texts={c.report_id: "正文" for c in candidates},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path,
    )

    assert calls == ["report_reader"]
    assert len(out.reader_results) == 2
    assert out.industry_summary == {}
    assert out.trend_summary == {}
    assert out.recommendations == []


def test_rank_recommendations_excludes_reader_failures():
    rows = [
        {
            "report_id": "failed",
            "title": "失败研报",
            "prescreen_score": 99,
            "reader": {"error": "reader_failed", "read_score": 0},
        },
        {
            "report_id": "ok",
            "title": "成功研报",
            "prescreen_score": 1,
            "reader": {"read_score": 10, "viewpoint": "已读观点"},
        },
    ]

    out = huibo._rank_recommendations(rows, recommend_cap=2)

    assert [item["report_id"] for item in out] == ["ok"]


def test_huibo_candidates_without_pdf_are_not_recommended(tmp_path):
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"},
    ])
    calls = []

    def runner(role, payload):
        calls.append(role)
        return {"read_score": 100}

    out = huibo.run_huibo_digest(
        candidates,
        report_texts={candidates[0].report_id: "正文"},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path,
    )

    assert calls == []
    assert out.reader_results[0]["reader"]["error"] == "missing_pdf"
    assert out.recommendations == []


def test_normalize_recommendations_filters_before_applying_cap():
    result = {
        "recommendations": [
            {"reason": "缺标题"},
            {"title": "有效研报1", "reason": "理由1"},
            {"title": "有效研报2", "reason": "理由2"},
        ]
    }

    out = huibo._normalize_recommendations(result, recommend_cap=2)

    assert [item["title"] for item in out] == ["有效研报1", "有效研报2"]


def test_normalize_recommendations_keeps_only_successfully_read_reports():
    result = {
        "recommendations": [
            {"report_id": "failed", "title": "失败研报", "reason": "ranker 误选"},
            {"report_id": "made_up", "title": "臆造研报", "reason": "ranker 臆造"},
            {"report_id": "ok", "title": "成功研报", "reason": "已成功阅读"},
        ]
    }
    reader_results = [
        {
            "report_id": "failed",
            "title": "失败研报",
            "reader": {"error": "reader_failed"},
        },
        {
            "report_id": "ok",
            "title": "成功研报",
            "reader": {"read_score": 90},
        },
    ]

    out = huibo._normalize_recommendations(
        result,
        recommend_cap=2,
        reader_results=reader_results,
    )

    assert [item["report_id"] for item in out] == ["ok"]


def test_normalize_reader_result_drops_unapproved_extra_fields():
    out = huibo._normalize_reader_result({
        "industry": "半体",
        "key_points": ["AI数据中用电与产业链升温�"],
        "mentioned_stocks": [{
            "name": "测试股�",
            "source": "图表",
            "source_page": "第12页",
            "source_section": "核心观点",
        }],
        "read_score": 90,
        "report_text": "不应进入聚合的原文",
        "full_text": "不应写入 summary",
    })

    assert out["industry"] == "半导体"
    assert out["viewpoint"] == "AI数据中心用电与产业链升温"
    assert out["mentioned_stocks"] == [{
        "name": "测试股",
        "viewpoint": "",
        "source": "图表 / 第12页 / 核心观点",
    }]
    assert "report_text" not in out
    assert "full_text" not in out


def test_normalize_reader_result_converts_read_score_to_int():
    out = huibo._normalize_reader_result({
        "industry": "机器人",
        "key_points": ["观点"],
        "read_score": "85分",
    })

    assert out["read_score"] == 85


def test_concurrent_reader_stops_scheduling_after_repeated_failed_batches(tmp_path):
    topics = ["MLCC", "AI算力", "机器人", "商业航天", "创新药", "固态电池", "光模块", "PCB", "CCL", "SiC", "新材料", "低空经济"]
    rows = []
    for i, topic in enumerate(topics):
        pdf = tmp_path / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-{topic}行业深度：重点推荐产业链",
            "报告评级": "推荐",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    candidates = huibo.parse_hot_report_rows(rows)
    calls = []

    def runner(role, payload):
        calls.append(payload["metadata"]["title"])
        return None

    out = huibo.run_huibo_digest(
        candidates,
        report_texts={},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path / "summaries",
            reader_cap=12,
            reader_concurrency=4,
        )

    assert len(calls) < len(candidates)
    assert len(out.reader_results) == len(candidates)
    assert all(r["reader"]["error"] == "reader_failed" for r in out.reader_results)


def test_concurrent_reader_does_not_trip_on_sparse_pdf_failures(tmp_path):
    topics = ["MLCC", "AI算力", "机器人", "商业航天", "创新药", "固态电池", "光模块", "PCB"]
    rows = []
    for i, topic in enumerate(topics):
        pdf = tmp_path / f"{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        rows.append({
            "报告名称": f"{chr(65 + i)}证券-{topic}行业深度：重点推荐产业链",
            "报告评级": "推荐",
            "页数": "20页",
            "时间": "2026-06-03",
            "分类": "行业分析",
            "PDF路径": str(pdf),
        })
    candidates = huibo.parse_hot_report_rows(rows)
    calls = []

    def runner(role, payload):
        title = payload["metadata"]["title"]
        calls.append(title)
        if title.startswith(("A证券", "B证券")):
            return None
        return {
            "industry": "机器人",
            "key_points": ["健康候选成功读取"],
            "mentioned_stocks": [],
            "read_score": 80,
        }

    out = huibo.run_huibo_digest(
        candidates,
        report_texts={},
        llm_runner=runner,
        date="2026-06-03",
        summary_dir=tmp_path / "summaries",
        reader_cap=8,
        reader_concurrency=2,
    )

    assert len(calls) == len(candidates)
    assert sum(1 for r in out.reader_results if not r["reader"].get("error")) == 6


def test_cleanup_removes_expired_files_and_dry_run_does_not_delete(tmp_path):
    raw = tmp_path / "raw"
    summaries = tmp_path / "summaries"
    raw.mkdir()
    summaries.mkdir()
    old_raw = raw / "old.txt"
    new_raw = raw / "new.txt"
    old_sum = summaries / "2025-01-01.json"
    new_sum = summaries / "2026-06-03.json"
    for p in (old_raw, new_raw, old_sum, new_sum):
        p.write_text("x", encoding="utf-8")
    now = datetime(2026, 6, 6, 12, 0)
    old_raw_ts = (now - timedelta(days=31)).timestamp()
    old_summary_ts = (now - timedelta(days=181)).timestamp()
    new_ts = (now - timedelta(days=5)).timestamp()
    for p in (old_raw, old_sum):
        p.touch()
        p.stat()
    import os
    os.utime(old_raw, (old_raw_ts, old_raw_ts))
    os.utime(old_sum, (old_summary_ts, old_summary_ts))
    os.utime(new_raw, (new_ts, new_ts))
    os.utime(new_sum, (new_ts, new_ts))

    preview = huibo.cleanup_storage(raw, summaries, raw_retention_days=30, summary_retention_days=180, now=now, dry_run=True)
    assert old_raw in preview.raw_files and old_sum in preview.summary_files
    assert old_raw.exists() and old_sum.exists()

    deleted = huibo.cleanup_storage(raw, summaries, raw_retention_days=30, summary_retention_days=180, now=now, dry_run=False)
    assert old_raw in deleted.raw_files and old_sum in deleted.summary_files
    assert not old_raw.exists()
    assert not old_sum.exists()
    assert new_raw.exists() and new_sum.exists()


def test_safe_filename_never_returns_empty_stem():
    assert huibo._safe_filename("!!!") == "untitled"


def test_download_counts_invalid_existing_pdf_path_as_missing(tmp_path):
    from argparse import Namespace
    from workflows import huibo_helper

    item = huibo.PrescreenedCandidate(
        candidate=huibo.parse_hot_report_rows([
            {
                "报告名称": "A证券-机器人行业深度：重点推荐产业链",
                "时间": "2026-06-03",
                "分类": "行业分析",
                "PDF路径": str(tmp_path / "missing.pdf"),
            },
        ])[0],
        score=1,
        reasons=[],
        topic_key="机器人",
    )
    prescreened = tmp_path / "prescreened.json"
    out = tmp_path / "downloaded.json"
    prescreened.write_text(json.dumps([huibo_helper._prescreened_json(item)], ensure_ascii=False), encoding="utf-8")

    result = huibo_helper._cmd_download(Namespace(
        prescreened=str(prescreened),
        raw_dir=str(tmp_path / "raw"),
        out=str(out),
    ))

    assert result["downloaded_count"] == 0
    assert result["missing_pdf_count"] == 1
