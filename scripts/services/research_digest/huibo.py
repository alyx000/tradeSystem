"""慧博研报深读增强：候选解析、预筛、Antigravity 隔离阅读、聚合和清理。

本模块只处理结构化候选与报告文本，不直接绑定慧博终端 UI。慧博终端 / 官方 API / 测试
fixture 都应先转换成 HuiboCandidate + report_text，再交给这里。
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HuiboTerminalUrlUnavailable(RuntimeError):
    """Raised when production requires the live Huibo terminal URL but it cannot be read."""


IMPORTANT_CATEGORIES = {"行业分析", "公司调研", "投资策略", "港美研究", "金融工程", "新股研究"}
LOW_VALUE_CATEGORIES = {"债券研究", "期货研究", "基金频道", "外汇研究", "晨会早刊", "机构资讯"}

DEPTH_TERMS = ("深度", "专题", "首次覆盖", "行业策略", "中期策略", "产业链", "系列报告", "跟踪报告")
PENALTY_TERMS = ("周报", "日报", "早报", "晨会", "点评", "简评")
FIRST_RATING_TERMS = ("首次覆盖", "首次评级", "买入（首次）", "增持（首次）", "推荐（首次）")
POSITIVE_RATING_TERMS = ("买入", "增持", "推荐", "优于大市", "强于大市", "看好", "领先大市")
STRONG_HINT_TERMS = ("重点关注", "重点跟踪", "重点推荐", "核心推荐", "首推", "建议关注")
TOPIC_TERMS = (
    "MLCC", "AI算力", "算力", "机器人", "商业航天", "创新药", "固态电池", "光模块",
    "玻璃基板", "碳化硅", "SiC", "PCB", "CCL", "人工智能", "新材料", "低空经济",
)


@dataclass(frozen=True)
class HuiboCandidate:
    title: str
    rating: str = ""
    authors: list[str] = field(default_factory=list)
    size_kb: int | None = None
    pages: int | None = None
    date: str = ""
    category: str = ""
    read_url: str = ""
    download_url: str = ""
    pdf_path: str = ""
    hot_rank: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def report_id(self) -> str:
        locator = (
            _stable_report_locator_from_raw(self.raw)
            or _stable_report_locator_from_url(self.read_url)
            or _stable_report_locator_from_url(self.download_url)
            or str(self.raw.get("_row_index") or self.hot_rank or "")
        )
        key = "|".join([self.date, self.title, locator])
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PrescreenedCandidate:
    candidate: HuiboCandidate
    score: float
    reasons: list[str]
    topic_key: str
    pdf_download: dict[str, Any] = field(default_factory=dict)


@dataclass
class HuiboDigest:
    date: str
    prescreened: list[PrescreenedCandidate]
    reader_results: list[dict[str, Any]]
    industry_summary: dict[str, Any] = field(default_factory=dict)
    trend_summary: dict[str, Any] = field(default_factory=dict)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "prescreened": [
                {
                    "candidate": asdict(p.candidate),
                    "score": p.score,
                    "reasons": p.reasons,
                    "topic_key": p.topic_key,
                    "pdf_download": p.pdf_download,
                }
                for p in self.prescreened
            ],
            "reader_results": self.reader_results,
            "industry_summary": self.industry_summary,
            "trend_summary": self.trend_summary,
            "recommendations": self.recommendations,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class CleanupResult:
    raw_files: list[Path]
    summary_files: list[Path]
    dry_run: bool


def _stable_report_locator_from_raw(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    nested = raw.get("raw")
    if not isinstance(nested, dict):
        nested = {}
    parts: list[str] = []
    primary_found = False
    for key in ("DId", "DocName"):
        value = raw.get(key)
        if value in (None, ""):
            value = nested.get(key)
        value_text = str(value).strip() if value not in (None, "") else ""
        if value_text:
            parts.append(f"{key}={value_text}")
            primary_found = True
    for key in (("DocType",) if primary_found else ("didMi", "did", "DocType")):
        value = raw.get(key)
        if value in (None, ""):
            value = nested.get(key)
        value_text = str(value).strip() if value not in (None, "") else ""
        if value_text:
            parts.append(f"{key}={value_text}")
    return "|".join(parts)


def _stable_report_locator_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    stable_keys = ("did", "docType", "degree", "baogaotype", "fromtype", "linkType")
    stable_parts = [f"path={parsed.path}"]
    for key in stable_keys:
        value = params.get(key)
        if value:
            stable_parts.append(f"{key}={value}")
    return "|".join(stable_parts) if len(stable_parts) > 1 else url


def parse_hot_report_rows(rows: list[dict[str, Any]]) -> list[HuiboCandidate]:
    """把慧博热点研报表格行转成统一候选。兼容中文列名和测试/接口风格英文列名。"""
    out: list[HuiboCandidate] = []
    for idx, row in enumerate(rows or [], 1):
        title = _clean_str(_get(row, "报告名称", "title", "name"))
        if not title:
            continue
        hot_rank = _to_int(_get(row, "编号", "rank")) or idx
        read_url = _clean_str(_get(row, "阅读链接", "read_url", "url"))
        download_url = _clean_str(_get(row, "下载链接", "download_url"))
        if not download_url:
            download_url = _derive_pdf_download_url(read_url)
        raw = dict(row)
        raw.setdefault("_row_index", idx)
        out.append(HuiboCandidate(
            title=title,
            rating=_clean_str(_get(row, "报告评级", "rating")),
            authors=_split_authors(_get(row, "作者", "authors")),
            size_kb=_parse_size_kb(_get(row, "大小", "size", "size_kb")),
            pages=_parse_pages(_get(row, "页数", "pages")),
            date=_normalize_date(_get(row, "时间", "date")),
            category=_clean_str(_get(row, "分类", "category")),
            read_url=read_url,
            download_url=_ensure_pdf_download_url(download_url),
            pdf_path=_clean_str(_get(row, "PDF路径", "pdf_path", "local_pdf_path", "pdf")),
            hot_rank=hot_rank,
            raw=raw,
        ))
    return out


def parse_hot_report_html(text: str, *, category: str = "", base_url: str = "") -> list[HuiboCandidate]:
    """从慧博热点列表 HTML 粗解析表格。主要作为 desktop_terminal URL fallback。"""
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.I | re.S)
        if len(cells) < 6:
            continue
        plain = [_strip_html(c) for c in cells]
        if any(h in plain[0] for h in ("编号", "报告名称")):
            continue
        row = {
            "编号": plain[0],
            "报告名称": plain[1] if len(plain) > 1 else "",
            "报告评级": plain[4] if len(plain) > 4 else "",
            "作者": plain[5] if len(plain) > 5 else "",
            "大小": plain[6] if len(plain) > 6 else "",
            "页数": plain[7] if len(plain) > 7 else "",
            "时间": plain[8] if len(plain) > 8 else "",
            "分类": category,
        }
        links = _extract_links(tr, base_url=base_url)
        if links:
            read_link = next((href for text_, href in links if "阅读" in text_), links[0][1])
            download_link = next((href for text_, href in links if "下载" in text_), "")
            row["阅读链接"] = read_link
            if download_link:
                row["下载链接"] = download_link
        rows.append(row)
    return parse_hot_report_rows(rows)


def build_source_from_env(mode: str):
    if mode == "off":
        return None
    if mode == "official_api":
        return _official_api_source
    if mode == "desktop_terminal":
        return _desktop_terminal_source
    return None


def prescreen_candidates(
    candidates: list[HuiboCandidate],
    *,
    reader_cap: int = 20,
    per_topic_cap: int = 3,
    preview_texts: dict[str, str] | None = None,
) -> list[PrescreenedCandidate]:
    """轻量预筛：只用列表元数据 + 可选首页/核心观点/目录轻扫文本，决定派给 Antigravity 的候选池。"""
    scored = [_score_candidate(c, preview_texts or {}) for c in candidates or []]
    scored.sort(key=lambda p: (-p.score, p.candidate.hot_rank or 9999, p.candidate.title))

    kept: list[PrescreenedCandidate] = []
    topic_counts: dict[str, int] = {}
    for item in scored:
        n = topic_counts.get(item.topic_key, 0)
        if n >= per_topic_cap:
            continue
        kept.append(item)
        topic_counts[item.topic_key] = n + 1
        if len(kept) >= reader_cap:
            break
    return kept


def run_huibo_digest(
    candidates: list[HuiboCandidate],
    *,
    report_texts: dict[str, str],
    llm_runner,
    date: str,
    summary_dir: str | Path,
    reader_cap: int = 20,
    recommend_cap: int = 2,
    lookback_days: int = 5,
    reader_concurrency: int = 1,
) -> HuiboDigest:
    """对预筛候选逐篇 reader，再独立做行业聚合、趋势聚合和 Top 推荐。"""
    summary_path = Path(summary_dir)
    prescreened = prescreen_candidates(candidates, reader_cap=reader_cap, preview_texts=report_texts)
    hydrated_prescreened: list[PrescreenedCandidate] = []
    reader_results: list[dict[str, Any]] = []
    for item in prescreened:
        c = _ensure_pdf_available(item.candidate, os.getenv("HUIBO_RAW_DIR", "data/reports/huibo/raw"))
        item = PrescreenedCandidate(candidate=c, score=item.score, reasons=item.reasons, topic_key=item.topic_key)
        hydrated_prescreened.append(item)

    llm_available = llm_runner is not None
    concurrency = max(1, int(reader_concurrency or 1))
    if concurrency <= 1:
        for item in hydrated_prescreened:
            row, ok = _run_single_reader(item, report_texts, llm_runner if llm_available else None)
            if not ok and llm_available and row["reader"].get("error") == "reader_failed":
                logger.warning("[research-digest] 慧博 reader 首次失败，停止本批次后续 LLM 调用并降级")
                llm_available = False
            reader_results.append(row)
    else:
        reader_results = [None] * len(hydrated_prescreened)  # type: ignore[list-item]
        max_workers = min(concurrency, len(hydrated_prescreened) or 1)
        consecutive_failed_batches = 0
        logger.info("[research-digest] 慧博 reader 并发启动: candidates=%d concurrency=%d",
                    len(hydrated_prescreened), max_workers)
        for start in range(0, len(hydrated_prescreened), max_workers):
            batch = hydrated_prescreened[start:start + max_workers]
            if llm_available is False or consecutive_failed_batches >= 2:
                logger.warning("[research-digest] 慧博 reader 连续整批失败，停止投递后续 LLM 调用并降级")
                for offset, item in enumerate(batch):
                    reader_results[start + offset] = _reader_row(item, _reader_error(item.candidate, "reader_failed"))
                continue
            batch_reader_failed = 0
            batch_success = 0
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="huibo-reader") as pool:
                future_to_index = {
                    pool.submit(_run_single_reader, item, report_texts, llm_runner): start + idx
                    for idx, item in enumerate(batch)
                }
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        row, ok = future.result()
                    except Exception as exc:  # noqa: BLE001
                        item = hydrated_prescreened[idx]
                        logger.warning("[research-digest] 慧博 reader 并发任务异常 %s: %s", item.candidate.title, exc)
                        row, ok = _reader_row(item, _reader_error(item.candidate, "reader_failed")), False
                    if not ok and row["reader"].get("error") == "reader_failed":
                        batch_reader_failed += 1
                    elif ok:
                        batch_success += 1
                    reader_results[idx] = row
            if batch_reader_failed == len(batch) and batch_success == 0:
                consecutive_failed_batches += 1
            else:
                consecutive_failed_batches = 0
        reader_results = [r for r in reader_results if r is not None]

    has_reader_json = any(not (r.get("reader") or {}).get("error") for r in reader_results)
    aggregate_payload = {"date": date, "reports": reader_results}
    industry_summary = (
        _safe_llm_call(llm_runner, "industry_aggregator", aggregate_payload)
        if llm_available and has_reader_json else {}
    )
    industry_summary = industry_summary or {}
    trend_payload = {
        "date": date,
        "lookback_days": lookback_days,
        "history": _load_summary_history(summary_path, date, lookback_days),
        "today": {"reports": reader_results, "industry_summary": industry_summary},
    }
    trend_summary = (
        _safe_llm_call(llm_runner, "trend_aggregator", trend_payload)
        if llm_available and has_reader_json else {}
    )
    trend_summary = trend_summary or {}
    rank_payload = {
        "date": date,
        "recommend_cap": recommend_cap,
        "reports": reader_results,
        "industry_summary": industry_summary,
        "trend_summary": trend_summary,
    }
    rank_result = _safe_llm_call(llm_runner, "ranker", rank_payload) if llm_available and has_reader_json else {}
    rank_result = rank_result or {}
    recommendations = _normalize_recommendations(
        rank_result,
        recommend_cap,
        reader_results=reader_results,
    )
    if not recommendations:
        recommendations = _rank_recommendations(reader_results, recommend_cap=recommend_cap)

    digest = HuiboDigest(
        date=date,
        prescreened=hydrated_prescreened,
        reader_results=reader_results,
        industry_summary=industry_summary,
        trend_summary=trend_summary,
        recommendations=recommendations,
    )
    summary_path.mkdir(parents=True, exist_ok=True)
    (summary_path / f"{date}.json").write_text(
        json.dumps(digest.to_jsonable(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return digest


def _run_single_reader(
    item: PrescreenedCandidate,
    report_texts: dict[str, str],
    llm_runner,
) -> tuple[dict[str, Any], bool]:
    c = item.candidate
    has_pdf = bool(c.pdf_path) and Path(c.pdf_path).expanduser().exists()
    payload = {
        "metadata": _candidate_metadata(c, item),
        "report_text": report_texts.get(c.report_id, ""),
        "report_pdf_path": c.pdf_path,
    }
    result = _safe_llm_call(llm_runner, "report_reader", payload) if llm_runner is not None and has_pdf else None
    if result is None:
        if not has_pdf:
            logger.warning("[research-digest] 慧博候选缺少原始 PDF，跳过 reader: %s", c.title)
            result = _reader_error(c, "missing_pdf")
        else:
            result = _reader_error(c, "reader_failed")
        return _reader_row(item, result), False
    return _reader_row(item, _normalize_reader_result(result)), True


def _reader_error(c: HuiboCandidate, error: str) -> dict[str, Any]:
    return {
        "industry": c.category or "未分类",
        "viewpoint": "",
        "mentioned_stocks": [],
        "read_score": 0,
        "error": error,
    }


def _reader_row(item: PrescreenedCandidate, result: dict[str, Any]) -> dict[str, Any]:
    c = item.candidate
    return {
        "report_id": c.report_id,
        "title": c.title,
        "institution": _infer_institution(c.title),
        "date": c.date,
        "huibo_list_time": c.date,
        "date_source": "huibo_hot_report_time",
        "category": c.category,
        "rating": c.rating,
        "pdf_path": c.pdf_path,
        "prescreen_score": item.score,
        "prescreen_reasons": item.reasons,
        "pdf_download": item.pdf_download,
        "reader": result,
    }


def build_role_runner(prompt_runner):
    """把现有 narrator runner(prompt, payload) 包成慧博 runner(role, payload)。

    report_reader 优先通过 Antigravity CLI 的 @file 语法读取 PDF；report_text 仅作为预筛摘录和
    PDF 不可用时的兜底。
    """
    if prompt_runner is None:
        return None

    def runner(role: str, payload: dict[str, Any]):
        runner.last_diagnostics = None
        prompt = _build_role_prompt(role)
        pdf_path = _clean_str(payload.get("report_pdf_path")) if role == "report_reader" else ""
        if pdf_path:
            if not _allow_external_pdf_llm():
                logger.warning("[research-digest] HUIBO_ALLOW_EXTERNAL_PDF_LLM 未启用，跳过外部 Antigravity PDF 阅读")
                return None
            result = _run_antigravity_pdf_reader(payload)
            runner.last_diagnostics = getattr(_run_antigravity_pdf_reader, "last_diagnostics", None)
            return result
        result = prompt_runner(prompt, payload)
        runner.last_diagnostics = getattr(prompt_runner, "last_diagnostics", None)
        return result

    runner.last_diagnostics = None
    return runner


def cleanup_storage(
    raw_dir: str | Path,
    summary_dir: str | Path,
    *,
    raw_retention_days: int = 30,
    summary_retention_days: int = 180,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """清理过期原始文件和结构化摘要。dry_run 只返回候选删除列表。"""
    now = now or datetime.now()
    raw_cutoff = now - timedelta(days=raw_retention_days)
    summary_cutoff = now - timedelta(days=summary_retention_days)
    raw_files = _expired_files(Path(raw_dir), raw_cutoff)
    summary_files = _expired_files(Path(summary_dir), summary_cutoff)
    if not dry_run:
        for p in raw_files + summary_files:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("[research-digest] 慧博清理失败 %s: %s", p, exc)
    return CleanupResult(raw_files=raw_files, summary_files=summary_files, dry_run=dry_run)


def _official_api_source(_registry, date: str, window_days: int):
    base = os.getenv("HUIBO_API_BASE_URL", "").strip()
    if not base:
        logger.info("[research-digest] HUIBO_API_BASE_URL 未配置，跳过慧博 official_api")
        return [], {}
    params = urllib.parse.urlencode({"date": date, "window_days": window_days})
    url = f"{base.rstrip('/')}/reports?{params}"
    headers = {}
    token = os.getenv("HUIBO_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = _fetch_json(url, headers=headers)
    rows = payload.get("rows") or payload.get("reports") or []
    candidates = parse_hot_report_rows(rows)
    candidates = _attach_pdf_paths(
        candidates,
        payload=payload,
        pdf_dir=os.getenv("HUIBO_REPORT_PDF_DIR", "").strip(),
        raw_dir=os.getenv("HUIBO_RAW_DIR", "data/reports/huibo/raw"),
    )
    texts = _texts_from_payload(payload, candidates)
    return candidates, texts


def _desktop_terminal_source(_registry, date: str, window_days: int):
    """读取慧博终端可访问 URL/快照。

    代码层不直接控制 GUI；终端自动化或官方接口只需把热点列表 JSON/URL 交给这里。
    """
    snapshot = os.getenv("HUIBO_HOT_REPORT_JSON", "").strip()
    if snapshot:
        payload = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        rows = payload.get("rows") or payload.get("reports") or []
        candidates = parse_hot_report_rows(rows)
        candidates = _attach_pdf_paths(
            candidates,
            payload=payload,
            pdf_dir=os.getenv("HUIBO_REPORT_PDF_DIR", "").strip(),
            raw_dir=os.getenv("HUIBO_RAW_DIR", "data/reports/huibo/raw"),
        )
        return candidates, _texts_from_payload(payload, candidates)

    refresh_from_app = os.getenv("HUIBO_REFRESH_URL_FROM_APP", "").strip().lower() in {"1", "true", "yes", "on"}
    if refresh_from_app:
        url = _hot_report_url_from_terminal_app()
        if not url:
            raise HuiboTerminalUrlUnavailable(
                "HUIBO_REFRESH_URL_FROM_APP 已启用，但未能读取慧博终端当前 URL；"
                "已拒绝回退到可能过期的 HUIBO_HOT_REPORT_URL"
            )
    else:
        url = os.getenv("HUIBO_HOT_REPORT_URL", "").strip()
    if not url:
        logger.info("[research-digest] HUIBO_HOT_REPORT_URL/JSON 未配置，跳过慧博 desktop_terminal")
        return [], {}
    candidates = _fetch_hot_report_api(url, date=date, window_days=window_days)
    if not candidates:
        html_text = _fetch_text(url)
        candidates = parse_hot_report_html(html_text, base_url=url)
    candidates = _attach_pdf_paths(
        candidates,
        payload={},
        pdf_dir=os.getenv("HUIBO_REPORT_PDF_DIR", "").strip(),
        raw_dir=os.getenv("HUIBO_RAW_DIR", "data/reports/huibo/raw"),
    )
    texts = _texts_from_dir(os.getenv("HUIBO_REPORT_TEXT_DIR", "").strip(), candidates)
    return candidates, texts


def _hot_report_url_from_terminal_app() -> str:
    """Best-effort macOS Accessibility read of the active Huibo terminal URL."""
    if os.getenv("HUIBO_REFRESH_URL_FROM_APP", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return ""
    if os.getenv("HUIBO_DISABLE_TERMINAL_URL_READ", "").strip().lower() in {"1", "true", "yes", "on"}:
        return ""
    if getattr(os, "uname", None) is None or os.uname().sysname != "Darwin":
        return ""
    script = Path(__file__).with_name("huibo_current_urls.py")
    if not script.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("[research-digest] 慧博终端当前 URL 读取失败：%s", exc)
        return ""
    for line in result.stdout.splitlines():
        url = _normalize_huibo_hot_report_url(line.strip())
        if url:
            logger.info("[research-digest] 使用慧博终端当前页面刷新热点研报 URL")
            return url
    if result.stderr.strip():
        logger.debug("[research-digest] 慧博终端 URL 读取 stderr: %s", result.stderr.strip()[:200])
    return ""


def _normalize_huibo_hot_report_url(url: str) -> str:
    """Normalize a Huibo terminal/report URL into the HotReport list URL."""
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    if not host.endswith("hibor.com.cn"):
        return ""
    qs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    required = ("abc", "def", "vidd", "keyy", "xyz")
    if not all(qs.get(k) for k in required):
        return ""
    params = [(k, qs[k]) for k in required]
    params.append(("op", qs.get("op") or "0"))
    return "https://sys.hibor.com.cn/redian/HotReport?" + urllib.parse.urlencode(params)


def _fetch_hot_report_api(url: str, *, date: str, window_days: int) -> list[HuiboCandidate]:
    """调用慧博热点研报页面背后的 GetList 接口。

    终端页面静态 HTML 只包含 JS 模板，真实列表由 `/redian/HotReport/GetList` 返回。
    """
    parsed = urllib.parse.urlsplit(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    required = ("abc", "def", "vidd", "keyy", "xyz", "op")
    if not all(qs.get(k) for k in required):
        return []

    start_date = _window_start_date(date, window_days)
    endpoint = urllib.parse.urljoin(url, "/redian/HotReport/GetList")
    data = {
        "selType": "",
        "selChildType": "",
        "selPublish": "",
        "Starttime": start_date,
        "Endtime": date,
        "type": "100",
        "Ishour": "0",
        "unameMi": qs["abc"],
        "def": qs["def"],
        "vidd": qs["vidd"],
        "keyy": qs["keyy"],
        "xyz": qs["xyz"],
        "op": qs["op"],
        "url": "/redian/HotReport/Report",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": url,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        payload = _post_json(endpoint, data, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research-digest] 慧博 GetList 接口失败，回落 HTML 解析: %s", exc)
        return []

    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("reports") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return parse_hot_report_rows(_hot_report_api_rows(rows, page_url=url))


def _hot_report_api_rows(rows: list[dict[str, Any]], *, page_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        title = _clean_str(row.get("DocTitle"))
        did = _clean_str(row.get("didMi") or row.get("DId") or row.get("did"))
        doc_type = _clean_str(row.get("DocType"))
        if not title or not did:
            continue
        size_bytes = _to_int(row.get("DocSize"))
        out.append({
            "编号": idx,
            "报告名称": title,
            "报告评级": _clean_str(row.get("Comment")),
            "作者": _strip_html(_clean_str(row.get("DocAuthor"))),
            "大小": int(round(size_bytes / 1024)) if size_bytes is not None else "",
            "页数": row.get("DocPages") or "",
            "时间": row.get("uptime") or row.get("UpdateTime") or "",
            "分类": _doc_type_category(doc_type),
            "阅读链接": _build_huibo_report_url(page_url, row, download_type="r"),
            "下载链接": _build_huibo_report_url(page_url, row, download_type="d"),
            "raw": row,
        })
    return out


def _build_huibo_report_url(page_url: str, row: dict[str, Any], *, download_type: str) -> str:
    page = urllib.parse.urlsplit(page_url)
    qs = dict(urllib.parse.parse_qsl(page.query, keep_blank_values=True))
    did = _clean_str(row.get("didMi") or row.get("DId") or row.get("did"))
    params = [
        ("downloadType", download_type),
        ("docType", _clean_str(row.get("DocType"))),
        ("abc", qs.get("abc", "")),
        ("def", qs.get("def", "")),
        ("vidd", qs.get("vidd", "")),
        ("keyy", qs.get("keyy", "")),
        ("xyz", qs.get("xyz", "")),
        ("did", did),
        ("degree", _clean_str(row.get("DocDegree"))),
        ("baogaotype", "2"),
        ("fromtype", "11"),
        ("linkType", "pdf"),
    ]
    base = f"{page.scheme or 'https'}://{page.netloc or 'sys.hibor.com.cn'}"
    return base + "/hiborClientDownload/Download/Index?" + urllib.parse.urlencode(params)


def _doc_type_category(doc_type: Any) -> str:
    mapping = {
        "1": "公司调研",
        "2": "行业分析",
        "4": "投资策略",
        "8": "新股研究",
        "9": "港美研究",
        "13": "金融工程",
    }
    return mapping.get(_clean_str(doc_type), "")


def _window_start_date(date: str, window_days: int) -> str:
    try:
        end = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return date
    days = max(1, int(window_days or 1))
    return (end - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _score_candidate(c: HuiboCandidate, preview_texts: dict[str, str]) -> PrescreenedCandidate:
    text = f"{c.title}\n{c.rating}\n{preview_texts.get(c.report_id, '')}"
    reasons: list[str] = []
    score = 0.0
    if c.category in IMPORTANT_CATEGORIES:
        score += 10
        reasons.append("重点分类")
    elif c.category in LOW_VALUE_CATEGORIES:
        score -= 8
        reasons.append("低优先分类")

    if any(t in c.title for t in DEPTH_TERMS):
        score += 10
        reasons.append("深度/专题")
    if any(t in c.title for t in PENALTY_TERMS):
        score -= 6
        reasons.append("短周期报告")

    if any(t in c.rating or t in c.title for t in FIRST_RATING_TERMS):
        score += 30
        reasons.append("首次覆盖")
    elif any(t in c.rating for t in POSITIVE_RATING_TERMS):
        score += 8
        reasons.append("偏多评级")

    if any(t in text for t in STRONG_HINT_TERMS):
        score += 14
        reasons.append("强提示词")

    if c.hot_rank:
        score += max(0, 30 - c.hot_rank) / 3
    if c.pages is not None:
        if c.pages < 8:
            score -= 8
            reasons.append("页数过短")
        elif 10 <= c.pages <= 80:
            score += 4
            reasons.append("页数适中")
        elif c.pages > 100:
            score -= 4
            reasons.append("超长报告")

    return PrescreenedCandidate(
        candidate=c,
        score=round(score, 2),
        reasons=reasons,
        topic_key=_topic_key(c.title),
    )


def _rank_recommendations(reader_results: list[dict[str, Any]], *, recommend_cap: int) -> list[dict[str, Any]]:
    def score(r: dict[str, Any]) -> float:
        return _fallback_score(r)["total_score"]

    def explanation(r: dict[str, Any]) -> dict[str, Any]:
        payload = _fallback_score(r)
        reader = r.get("reader") or {}
        quality = reader.get("quality") if isinstance(reader.get("quality"), dict) else {}
        return {
            "ranker": "fallback",
            "read_score": payload["read_score"],
            "prescreen_score": payload["prescreen_score"],
            "quality_penalty": payload["quality_penalty"],
            "total_score": payload["total_score"],
            "prescreen_reasons": list(r.get("prescreen_reasons") or []),
            "quality_issues": list(quality.get("issues") or []),
        }

    usable = [r for r in reader_results if not (r.get("reader") or {}).get("error")]
    ranked = sorted(usable, key=lambda r: (-score(r), str(r.get("title") or "")))
    out = []
    for r in ranked[:recommend_cap]:
        reader = r.get("reader") or {}
        out.append({
            "report_id": r.get("report_id"),
            "title": r.get("title"),
            "institution": r.get("institution"),
            "date": r.get("date"),
            "huibo_list_time": r.get("huibo_list_time") or r.get("date"),
            "category": r.get("category"),
            "reason": reader.get("recommend_reason") or reader.get("viewpoint") or "综合评分靠前",
            "score": round(score(r), 2),
            "source": f"{r.get('institution') or ''}".strip(),
            "ranking_explanation": explanation(r),
        })
    return out


def _fallback_score(r: dict[str, Any]) -> dict[str, float]:
    reader = r.get("reader") or {}
    try:
        read_score = float(reader.get("read_score") or 0)
    except (TypeError, ValueError):
        read_score = 0.0
    prescreen_score = float(r.get("prescreen_score") or 0)
    quality = reader.get("quality") if isinstance(reader.get("quality"), dict) else {}
    quality_penalty = -10.0 if quality.get("status") == "warning" else 0.0
    return {
        "read_score": read_score,
        "prescreen_score": prescreen_score,
        "quality_penalty": quality_penalty,
        "total_score": read_score + prescreen_score + quality_penalty,
    }

def _normalize_reader_result(result: dict[str, Any]) -> dict[str, Any]:
    key_points = result.get("key_points")
    if isinstance(key_points, list):
        points = [_clean_str(p) for p in key_points if _clean_str(p)]
    else:
        points = []
    stocks = []
    for item in result.get("mentioned_stocks") or []:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"))
        if not name:
            continue
        stocks.append({
            "name": name,
            "viewpoint": _clean_str(item.get("viewpoint")),
            "source": _stock_source(item),
        })
    viewpoint = _clean_str(result.get("viewpoint")) or ("；".join(points[:3]) if points else "")
    recommend_reason = _clean_str(result.get("recommend_reason")) or (points[0] if points else "")
    return {
        "industry": _clean_str(result.get("industry")),
        "viewpoint": viewpoint,
        "key_points": points[:3],
        "mentioned_stocks": stocks,
        "recommend_reason": recommend_reason,
        "pdf_report_date": _normalize_date(result.get("pdf_report_date") or result.get("report_date")),
        "read_score": _to_int(result.get("read_score")) or 0,
    }


def _normalize_recommendations(
    result: dict[str, Any],
    recommend_cap: int,
    *,
    reader_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    recs = result.get("recommendations") if isinstance(result, dict) else None
    if not isinstance(recs, list):
        return []
    allowed_ids, allowed_titles = _successful_reader_keys(reader_results)
    out: list[dict[str, Any]] = []
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        report_id = _clean_str(rec.get("report_id"))
        title = _clean_str(rec.get("title"))
        if not title:
            continue
        if reader_results is not None and not (
            (report_id and report_id in allowed_ids) or title in allowed_titles
        ):
            continue
        out.append({
            "report_id": report_id,
            "title": title,
            "institution": _clean_str(rec.get("institution")),
            "date": _clean_str(rec.get("date")),
            "huibo_list_time": _clean_str(rec.get("huibo_list_time")) or _clean_str(rec.get("date")),
            "category": _clean_str(rec.get("category")),
            "reason": _clean_str(rec.get("reason")) or "综合评分靠前",
            "score": rec.get("score"),
            "source": _clean_str(rec.get("source")),
        })
        if len(out) >= recommend_cap:
            break
    return out


def _successful_reader_keys(reader_results: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    allowed_ids: set[str] = set()
    allowed_titles: set[str] = set()
    for row in reader_results or []:
        if (row.get("reader") or {}).get("error"):
            continue
        report_id = _clean_str(row.get("report_id"))
        title = _clean_str(row.get("title"))
        if report_id:
            allowed_ids.add(report_id)
        if title:
            allowed_titles.add(title)
    return allowed_ids, allowed_titles


def _candidate_metadata(c: HuiboCandidate, item: PrescreenedCandidate) -> dict[str, Any]:
    return {
        "report_id": c.report_id,
        "title": c.title,
        "institution": _infer_institution(c.title),
        "rating": c.rating,
        "authors": c.authors,
        "pages": c.pages,
        "date": c.date,
        "huibo_list_time": c.date,
        "date_source": "huibo_hot_report_time",
        "category": c.category,
        "prescreen_score": item.score,
        "prescreen_reasons": item.reasons,
    }


def _safe_llm_call(llm_runner, role: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if llm_runner is None:
        return None
    try:
        result = llm_runner(role, payload)
    except Exception as exc:  # noqa: BLE001
        try:
            llm_runner.last_diagnostics = {
                "reason": "exception",
                "message": str(exc),
            }
        except Exception:  # noqa: BLE001
            pass
        logger.warning("[research-digest] 慧博 %s LLM 失败: %s", role, exc)
        return None
    return result if isinstance(result, dict) else None


def _texts_from_payload(payload: dict[str, Any], candidates: list[HuiboCandidate]) -> dict[str, str]:
    raw = payload.get("texts") or payload.get("report_texts") or {}
    if not isinstance(raw, dict):
        return {}
    by_title = {c.title: c.report_id for c in candidates}
    out: dict[str, str] = {}
    for key, value in raw.items():
        rid = key if key in {c.report_id for c in candidates} else by_title.get(key)
        if rid and value:
            out[rid] = str(value)
    return out


def _texts_from_dir(root: str, candidates: list[HuiboCandidate]) -> dict[str, str]:
    if not root:
        return {}
    base = Path(root)
    if not base.exists():
        return {}
    out = {}
    for c in candidates:
        for name in (f"{c.report_id}.txt", _safe_filename(c.title) + ".txt"):
            path = base / name
            if path.exists():
                out[c.report_id] = path.read_text(encoding="utf-8")
                break
    return out


def _attach_pdf_paths(
    candidates: list[HuiboCandidate],
    *,
    payload: dict[str, Any],
    pdf_dir: str,
    raw_dir: str,
) -> list[HuiboCandidate]:
    mapped = _pdfs_from_payload(payload, candidates)
    dir_pdfs = _pdfs_from_dir(pdf_dir, candidates)
    out: list[HuiboCandidate] = []
    for c in candidates:
        pdf_path = c.pdf_path or mapped.get(c.report_id) or dir_pdfs.get(c.report_id)
        if pdf_path:
            pdf_path = _copy_pdf_to_raw(pdf_path, c, raw_dir)
        out.append(replace(c, pdf_path=pdf_path or ""))
    return out


def _ensure_pdf_available(c: HuiboCandidate, raw_dir: str) -> HuiboCandidate:
    candidate, _diagnostics = _ensure_pdf_available_with_diagnostics(c, raw_dir)
    return candidate


def _ensure_pdf_available_with_diagnostics(c: HuiboCandidate, raw_dir: str) -> tuple[HuiboCandidate, dict[str, Any]]:
    if c.pdf_path and Path(c.pdf_path).expanduser().exists():
        return c, {"status": "ok", "reason": "existing_pdf", "pdf_path": c.pdf_path}
    archived = Path(raw_dir) / f"{c.report_id}.pdf"
    if archived.exists():
        return replace(c, pdf_path=str(archived)), {
            "status": "ok",
            "reason": "existing_raw_pdf",
            "pdf_path": str(archived),
        }
    if not _allow_direct_pdf_download():
        return c, {
            "status": "missing",
            "reason": "terminal_pdf_missing",
            "message": (
                "desktop_terminal 模式只使用慧博终端实际下载/导出的本地 PDF；"
                "请将慧博终端下载目录配置为 HUIBO_REPORT_PDF_DIR，或在候选中提供 PDF路径。"
            ),
        }
    if c.download_url:
        pdf_path, diagnostics = _download_pdf_with_diagnostics(c, raw_dir)
        if pdf_path:
            return replace(c, pdf_path=pdf_path), diagnostics
        return c, diagnostics
    return c, {"status": "missing", "reason": "missing_download_url", "message": "candidate has no download_url"}


def _allow_direct_pdf_download() -> bool:
    return os.getenv("HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD", "").strip().lower() in {"1", "true", "yes", "on"}


def _pdfs_from_payload(payload: dict[str, Any], candidates: list[HuiboCandidate]) -> dict[str, str]:
    raw = payload.get("pdfs") or payload.get("report_pdfs") or {}
    if not isinstance(raw, dict):
        return {}
    by_title = {c.title: c.report_id for c in candidates}
    ids = {c.report_id for c in candidates}
    out: dict[str, str] = {}
    for key, value in raw.items():
        rid = key if key in ids else by_title.get(key)
        if rid and value:
            out[rid] = str(value)
    return out


def _pdfs_from_dir(root: str, candidates: list[HuiboCandidate]) -> dict[str, str]:
    if not root:
        return {}
    base = Path(root).expanduser()
    if not base.exists():
        return {}
    out: dict[str, str] = {}
    for c in candidates:
        for name in (f"{c.report_id}.pdf", _safe_filename(c.title) + ".pdf"):
            path = base / name
            if path.exists():
                out[c.report_id] = str(path)
                break
    return out


def _copy_pdf_to_raw(pdf_path: str, c: HuiboCandidate, raw_dir: str) -> str:
    src = Path(pdf_path).expanduser()
    if not src.exists() or src.suffix.lower() != ".pdf":
        return ""
    dest_dir = Path(raw_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{c.report_id}.pdf"
    if src.resolve() == dest.resolve():
        return str(dest)
    if not dest.exists():
        shutil.copy2(src, dest)
    return str(dest)


def _download_pdf(c: HuiboCandidate, raw_dir: str) -> str:
    pdf_path, _diagnostics = _download_pdf_with_diagnostics(c, raw_dir)
    return pdf_path


def _download_pdf_with_diagnostics(c: HuiboCandidate, raw_dir: str) -> tuple[str, dict[str, Any]]:
    dest_dir = Path(raw_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{c.report_id}.pdf"
    if dest.exists():
        return str(dest), {"status": "ok", "reason": "existing_pdf", "pdf_path": str(dest)}
    try:
        headers = _pdf_download_headers(c)
        data, meta = _fetch_bytes_with_meta(c.download_url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research-digest] 慧博 PDF 下载失败 %s: %s", c.title, exc)
        return "", {
            "status": "missing",
            "reason": "request_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
    if not data.startswith(b"%PDF"):
        final_url = str(meta.get("final_url") or "")
        content_type = str(meta.get("content_type") or "")
        reason = "hibor_mb404" if "/mb404" in final_url.lower() else "not_pdf_response"
        logger.warning(
            "[research-digest] 慧博下载结果不是 PDF %s: reason=%s status=%s content_type=%s final_url=%s",
            c.title,
            reason,
            meta.get("http_status"),
            content_type,
            final_url,
        )
        return "", {
            "status": "missing",
            "reason": reason,
            "message": "download response is not a PDF",
            **meta,
        }
    dest.write_bytes(data)
    return str(dest), {"status": "ok", "reason": "downloaded", "pdf_path": str(dest), **meta}


def _pdf_download_headers(c: HuiboCandidate) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    }
    if c.read_url:
        headers["Referer"] = c.read_url
    return headers


def _fetch_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = _fetch_text(url, headers=headers)
    parsed = json.loads(data)
    return parsed if isinstance(parsed, dict) else {}


def _post_json(url: str, data: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    final_headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    final_headers.update(headers or {})
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=final_headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 慧博用户配置终端地址
        text = resp.read().decode("utf-8", errors="replace")
    return json.loads(text)


def _fetch_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - user-configured internal endpoint
        return resp.read().decode("utf-8", errors="replace")


def _fetch_bytes(url: str, *, headers: dict[str, str] | None = None) -> bytes:
    data, _meta = _fetch_bytes_with_meta(url, headers=headers)
    return data


def _fetch_bytes_with_meta(url: str, *, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, Any]]:
    final_headers = {"User-Agent": "Mozilla/5.0"}
    final_headers.update(headers or {})
    req = urllib.request.Request(url, headers=final_headers)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - 慧博用户配置下载链接
        data = resp.read()
        meta = {
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type", ""),
            "final_url": resp.geturl(),
            "byte_count": len(data),
        }
        return data, meta


def _allow_external_pdf_llm() -> bool:
    return os.getenv("HUIBO_ALLOW_EXTERNAL_PDF_LLM", "1").strip().lower() not in {"0", "false", "no", "n"}


def _run_antigravity_pdf_reader(payload: dict[str, Any]) -> dict[str, Any] | None:
    _run_antigravity_pdf_reader.last_diagnostics = None
    pdf_path = Path(_clean_str(payload.get("report_pdf_path"))).expanduser().resolve()
    metadata = payload.get("metadata") or {}
    title = _clean_str(metadata.get("title"))
    from utils.antigravity_diagnostics import build_diagnostics
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    timeout = config.timeout_seconds
    from services.research_digest import narrator as _narrator

    log_file = _narrator._next_antigravity_log_file()  # noqa: SLF001
    prompt = (
        "请读取这个PDF研报，只输出JSON，不要markdown。字段："
        "title, pdf_report_date(研报首页/封面报告日期，YYYY-MM-DD，找不到留空), "
        "industry, viewpoint, key_points(数组最多3条), recommend_reason, read_score(0-100), "
        "mentioned_stocks(数组，每项 name, viewpoint, source, source_page, source_section)。"
        "mentioned_stocks 的 viewpoint 只能写该个股在研报中的独立观点；"
        "如果只是可比公司、客户、供应商、数据引用来源，viewpoint 必须留空，把关系写到 source。"
        "source_page/source_section 尽量给页码或章节；不要输出目标价、买入卖出、仓位或价格预测。"
        f"候选标题：{title}。PDF：@{pdf_path}"
    )
    cmd = build_prompt_command(
        config,
        prompt,
        add_dirs=[str(pdf_path.parent)],
        skip_permissions=True,
        log_file=str(log_file) if log_file else None,
    )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        _run_antigravity_pdf_reader.last_diagnostics = build_diagnostics(
            stdout="",
            stderr=f"timeout after {timeout}s",
            log_file=log_file,
            reason="timeout",
        )
        logger.warning("[research-digest] 慧博 PDF reader antigravity 超时 %ds，降级", timeout)
        return None
    except OSError as exc:
        _run_antigravity_pdf_reader.last_diagnostics = build_diagnostics(
            stdout="",
            stderr=str(exc),
            log_file=log_file,
            reason="startup_failed",
        )
        logger.warning("[research-digest] 慧博 PDF reader antigravity 启动失败(%s)，降级", exc)
        return None
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.returncode != 0:
        _run_antigravity_pdf_reader.last_diagnostics = build_diagnostics(
            stdout=stdout,
            stderr=stderr,
            log_file=log_file,
            returncode=result.returncode,
        )
        logger.warning("[research-digest] 慧博 PDF reader antigravity returncode=%s，降级", result.returncode)
        return None
    if not stdout.strip():
        _run_antigravity_pdf_reader.last_diagnostics = build_diagnostics(
            stdout=stdout,
            stderr=stderr,
            log_file=log_file,
            reason="empty_stdout",
        )
        logger.warning(
            "[research-digest] 慧博 PDF reader antigravity stdout 为空，降级: %s",
            _run_antigravity_pdf_reader.last_diagnostics.get("message")
            or _run_antigravity_pdf_reader.last_diagnostics.get("reason"),
        )
        return None
    parsed = _parse_json_object(stdout)
    if parsed is None:
        _run_antigravity_pdf_reader.last_diagnostics = build_diagnostics(
            stdout=stdout,
            stderr=stderr,
            log_file=log_file,
            reason="parse_failed",
        )
    return parsed


_run_antigravity_pdf_reader.last_diagnostics = None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1 and t[:nl].strip().lower() in ("json", ""):
            t = t[nl + 1:]
    try:
        parsed = json.loads(t)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(t[start:i + 1])
                    return parsed if isinstance(parsed, dict) else None
                except Exception:
                    return None
    return None


def _extract_links(fragment: str, *, base_url: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", fragment, flags=re.I | re.S):
        attrs, body = m.groups()
        href_m = re.search(r"href=[\"']([^\"']+)[\"']", attrs, flags=re.I)
        if not href_m:
            continue
        label = _strip_html(body)
        href = _normalize_url(html.unescape(href_m.group(1)), base_url=base_url)
        out.append((label, href))
    if out:
        return out
    hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", fragment, flags=re.I)
    return [("", _normalize_url(html.unescape(h), base_url=base_url)) for h in hrefs]


def _normalize_url(url: str, *, base_url: str = "") -> str:
    if not url:
        return ""
    if base_url:
        return urllib.parse.urljoin(base_url, url)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://sys.hibor.com.cn" + url
    return url


def _derive_pdf_download_url(read_url: str) -> str:
    if not read_url:
        return ""
    parsed = urllib.parse.urlsplit(read_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not query:
        return ""
    params = dict(query)
    if "downloadType" not in params and "downloadtype" not in {k.lower() for k in params}:
        return ""
    new_query: list[tuple[str, str]] = []
    has_link_type = False
    for key, value in query:
        lower = key.lower()
        if lower == "downloadtype":
            new_query.append((key, "d"))
        elif lower == "linktype":
            new_query.append((key, "pdf"))
            has_link_type = True
        else:
            new_query.append((key, value))
    if not has_link_type:
        new_query.append(("linkType", "pdf"))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(new_query)))


def _ensure_pdf_download_url(url: str) -> str:
    if not url:
        return ""
    derived = _derive_pdf_download_url(url)
    if derived:
        return derived
    return url


def _strip_html(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def _safe_filename(text: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", text).strip("._-")[:120]
    return stem or "untitled"


def _build_role_prompt(role: str) -> str:
    if role == "report_reader":
        return (
            "你是研报阅读 agent。只阅读输入的一篇研报 PDF，输出 JSON object。字段："
            "industry, viewpoint, key_points, mentioned_stocks, recommend_reason, read_score。"
            "mentioned_stocks 每项包含 name, viewpoint, source, source_page, source_section。"
            "viewpoint 只能写该个股在研报中的独立观点；如果只是可比公司、客户、供应商、"
            "数据引用来源，viewpoint 必须留空，把关系写到 source。必须尽量标明来源页码或章节；"
            "如同时给了 report_text，它只是预筛摘录，最终结论必须以 PDF 为准。"
            "不得输出买卖建议、目标价或价格预测。"
        )
    if role == "industry_aggregator":
        return (
            "你是行业研报聚合 agent。只读取输入的 reader JSON，不接触原始全文。"
            "输出 JSON object，字段 industries。每个行业包含 industry, viewpoint, consensus, divergence, sources。"
            "不得新增未在 reader JSON 出现的观点或个股。"
        )
    if role == "trend_aggregator":
        return (
            "你是研报热点变化聚合 agent。只读取最近窗口 summary JSON 和今日 reader JSON。"
            "输出 JSON object，字段 changes，为字符串数组。只描述热点变化，不给买卖建议。"
        )
    if role == "ranker":
        return (
            "你是研报推荐排序 agent。只读取 reader JSON、行业聚合 JSON 和热点变化 JSON。"
            "输出 JSON object，字段 recommendations，为数组，最多 recommend_cap 篇。每项包含 "
            "report_id, title, institution, date, category, reason, score, source。"
            "推荐理由只解释为什么值得阅读，不给买卖建议。"
        )
    return "只输出 JSON object，不要 markdown。"


def _load_summary_history(summary_dir: Path, date: str, lookback_days: int) -> list[dict[str, Any]]:
    if not summary_dir.exists():
        return []
    files = sorted(summary_dir.glob("*.json"), reverse=True)
    out = []
    for path in files:
        if path.stem >= date:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(data)
        if len(out) >= lookback_days:
            break
    return list(reversed(out))


def _expired_files(root: Path, cutoff: datetime) -> list[Path]:
    if not root.exists():
        return []
    expired = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        if mtime < cutoff:
            expired.append(p)
    return sorted(expired)


def _get(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return ""


def _clean_str(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ").replace("\ufffd", "")
    text = re.sub(r"\s+", " ", text).strip()
    return _repair_common_text(text)


def _repair_common_text(text: str) -> str:
    """修复 OCR/LLM 常见破损词，保持保守，只处理确定性高的短语。"""
    replacements = {
        "AI数据中用电": "AI数据中心用电",
        "数据中用电": "数据中心用电",
        "数据中 心": "数据中心",
        "半体": "半导体",
        "半 导体": "半导体",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _stock_source(item: dict[str, Any]) -> str:
    parts = []
    for key in ("source", "source_page", "source_section"):
        text = _clean_str(item.get(key))
        if text and text not in parts:
            parts.append(text)
    return " / ".join(parts)


def _split_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[\n,，、\s]+", _clean_str(value))
    return [str(p).strip() for p in parts if str(p).strip()]


def _parse_size_kb(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    text = _clean_str(value).replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    n = float(m.group(1))
    if "M" in text.upper():
        n *= 1024
    return int(round(n))


def _parse_pages(value: Any) -> int | None:
    return _to_int(value)


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    m = re.search(r"\d+", _clean_str(value).replace(",", ""))
    return int(m.group(0)) if m else None


def _normalize_date(value: Any) -> str:
    text = _clean_str(value)
    if not text:
        return ""
    m = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if not m:
        return text[:10]
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def _topic_key(title: str) -> str:
    for term in TOPIC_TERMS:
        if term in title:
            return term
    parts = re.split(r"[-：:，,——]", title)
    for part in parts[1:]:
        cleaned = re.sub(r"(行业|公司|深度|专题|报告|研究|系列|\d+)", "", part).strip()
        if cleaned:
            return cleaned[:12]
    return title[:12]


def _infer_institution(title: str) -> str:
    if "-" in title:
        return title.split("-", 1)[0].strip()
    return ""
