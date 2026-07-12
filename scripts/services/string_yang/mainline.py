"""串阳首阴主线融合判断。

LLM 只裁决「哪些已提供的申万二级/同花顺概念属于主线」，不直接选择个股。
个股仍必须由 scanner 的首阴机械条件过滤。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from services.concept_tags import build_stock_concept_map
from services.string_yang import constants as C
from services.volume_concentration import repo as vc_repo
from services.volume_concentration.aggregator import UNCLASSIFIED
from utils.antigravity_diagnostics import diag_reason

logger = logging.getLogger(__name__)


@dataclass
class MainlineJudgment:
    date: str
    main_sectors: list[str]
    main_concepts: list[str] = field(default_factory=list)
    status: str = "fallback"
    degraded: bool = False
    confidence: float | None = None
    watch_only: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)
    volume_sectors: list[dict[str, Any]] = field(default_factory=list)
    hot_concepts: list[dict[str, Any]] = field(default_factory=list)
    teacher_notes: list[dict[str, Any]] = field(default_factory=list)
    stock_concept_map: dict[str, set[str]] = field(default_factory=dict)

    def public_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "main_sectors": self.main_sectors,
            "main_concepts": self.main_concepts,
            "confidence": self.confidence,
            "watch_only": self.watch_only,
            "evidence": self.evidence,
            "volume_sectors": self.volume_sectors,
            "hot_concepts": self.hot_concepts,
            "teacher_notes": self.teacher_notes,
            "source_errors": self.source_errors,
        }


def judge_mainline(
    conn: sqlite3.Connection,
    registry,
    date: str,
    *,
    top_k: int,
    top_concepts: int = C.DEFAULT_TOP_CONCEPTS,
    teacher_lookback_days: int = C.TEACHER_LOOKBACK_DAYS,
    use_llm: bool = False,
    llm_runner=None,
) -> MainlineJudgment:
    volume_sectors, degraded = _volume_sectors(conn, date, top_k)
    teacher_notes = _teacher_notes(conn, date, teacher_lookback_days)
    stock_concept_map: dict[str, set[str]] = {}
    hot_concepts: list[dict[str, Any]] = []
    source_errors: list[str] = []

    if use_llm:
        ranked_concepts, concept_ok = _ranked_concept_rows(registry, date)
        if not concept_ok:
            source_errors.append("concept_flow")
        else:
            prefetch_names = [row["name"] for row in ranked_concepts[:_concept_prefetch_limit(top_concepts)]]
            stock_concept_map, member_count, ok = build_stock_concept_map(
                registry,
                date,
                concept_names=prefetch_names,
            )
            if not ok:
                source_errors.append("ths_member")
            hot_concepts, coverage_ok = _filter_hot_concept_rows(
                ranked_concepts,
                top_concepts,
                member_count,
            )
            if ok and not coverage_ok:
                source_errors.append("concept_coverage")

    fallback = MainlineJudgment(
        date=date,
        main_sectors=[row["industry"] for row in volume_sectors],
        main_concepts=[],
        status="disabled" if not use_llm else "fallback",
        degraded=degraded,
        volume_sectors=volume_sectors,
        hot_concepts=hot_concepts,
        teacher_notes=teacher_notes,
        stock_concept_map=stock_concept_map,
        source_errors=source_errors.copy(),
    )

    if not use_llm:
        return fallback

    runner = llm_runner or build_antigravity_runner()
    payload = {
        "date": date,
        "volume_sectors": volume_sectors,
        "hot_concepts": hot_concepts,
        "teacher_notes": teacher_notes,
    }
    try:
        raw = runner(_build_prompt(), payload)
        if raw is None and diag_reason(runner) != "timeout":
            # 非超时失败重试 1 次（对齐 board_break/pk 的抗抖动策略）；超时不重试
            # ——重试大概率再超时，反而拖长盘后任务链。
            raw = runner(_build_prompt(), payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[string-yang] mainline LLM 异常，降级成交额主线: %s", exc)
        fallback.status = "llm_fallback"
        fallback.source_errors.append("llm_error")
        return fallback

    if raw is None:
        # 调用层失败（超时/启动失败/非零返回码/空输出/不可解析）——与「LLM 判无有效
        # 主线」是两回事，分别贴标签：本分支曾被误记 llm_mainline_empty，导致连续多日
        # 降级却查不到真实失败原因（诊断详情已由 runner 落 ANTIGRAVITY_LOG_DIR 日志）。
        logger.warning("[string-yang] mainline LLM 调用失败，降级成交额主线: %s", diag_reason(runner) or "unknown")
        fallback.status = "llm_fallback"
        fallback.source_errors.append("llm_call_failed")
        return fallback

    parsed = _parse_json(raw) if isinstance(raw, str) else raw
    decision = _normalize_llm_decision(parsed, volume_sectors, hot_concepts)
    if not decision["main_sectors"] and not decision["main_concepts"]:
        # LLM 正常返回但裁决为空/全部无效 → 真「无有效主线」
        fallback.status = "llm_fallback"
        fallback.source_errors.append("llm_mainline_empty")
        return fallback

    return MainlineJudgment(
        date=date,
        main_sectors=decision["main_sectors"],
        main_concepts=decision["main_concepts"],
        status="llm",
        degraded=degraded,
        confidence=decision["confidence"],
        watch_only=decision["watch_only"],
        evidence=decision["evidence"],
        source_errors=source_errors,
        volume_sectors=volume_sectors,
        hot_concepts=hot_concepts,
        teacher_notes=teacher_notes,
        stock_concept_map=stock_concept_map,
    )


def _volume_sectors(conn: sqlite3.Connection, date: str, top_k: int) -> tuple[list[dict[str, Any]], bool]:
    rec = vc_repo.get_concentration(conn, date)
    degraded = False
    if rec is None:
        degraded = True
        recent = vc_repo.get_recent_concentration(conn, date, 1)
        rec = recent[-1] if recent else None
    rows: list[dict[str, Any]] = []
    for item in (rec or {}).get("sector_summary") or []:
        industry = item.get("industry")
        if not industry or industry == UNCLASSIFIED:
            continue
        rows.append({
            "industry": str(industry),
            "amount_billion": item.get("amount_billion"),
            "amount_share": item.get("amount_share"),
            "stock_count": item.get("stock_count"),
        })
        if len(rows) >= top_k:
            break
    return rows, degraded


def _teacher_notes(conn: sqlite3.Connection, date: str, lookback_days: int) -> list[dict[str, Any]]:
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT id, date, title, core_view, key_points, sectors, raw_content
           FROM teacher_notes
           WHERE date >= ? AND date <= ?
           ORDER BY date DESC, id DESC
           LIMIT ?""",
        (start, date, C.TEACHER_NOTES_LIMIT),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        sectors = _json_list(row["sectors"])
        snippets = []
        for key in ("core_view", "key_points", "raw_content"):
            text = _trim(row[key], C.TEACHER_SNIPPET_CHARS)
            if text:
                snippets.append(text)
        out.append({
            "id": row["id"],
            "date": row["date"],
            "title": row["title"],
            "sectors": sectors,
            "snippets": snippets[:3],
        })
    return out


def _concept_prefetch_limit(top_concepts: int) -> int:
    return max(C.CONCEPT_PREFETCH_MIN, top_concepts * C.CONCEPT_PREFETCH_MULTIPLIER)


def _ranked_concept_rows(registry, date: str) -> tuple[list[dict[str, Any]], bool]:
    r = registry.call("get_concept_moneyflow_ths", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return [], False
    parsed: list[dict[str, Any]] = []
    for row in r.data:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        name = str(row["name"])
        try:
            net_amount = float(row.get("net_amount"))
        except (TypeError, ValueError):
            continue
        parsed.append({"name": name, "net_amount": net_amount})
    parsed.sort(key=lambda item: float(item["net_amount"]), reverse=True)
    return parsed, True


def _filter_hot_concept_rows(
    ranked_concepts: list[dict[str, Any]],
    top_concepts: int,
    member_count: dict[str, int],
) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    coverage_ok = True
    for item in ranked_concepts:
        item = {**item, "member_count": member_count.get(str(item.get("name")), 0)}
        mc = int(item.get("member_count") or 0)
        if mc > C.CONCEPT_MAX_MEMBERS:
            continue
        if mc <= 0:
            coverage_ok = False
            continue
        kept.append(item)
        if len(kept) >= top_concepts:
            break
    return kept, coverage_ok


def _normalize_llm_decision(
    parsed: Any,
    volume_sectors: list[dict[str, Any]],
    hot_concepts: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {"main_sectors": [], "main_concepts": [], "watch_only": [], "evidence": [], "confidence": None}
    allowed_sectors = {row["industry"] for row in volume_sectors}
    allowed_concepts = {row["name"] for row in hot_concepts}
    main_sectors = _ordered_allowed(parsed.get("main_l2"), allowed_sectors)
    main_concepts = _ordered_allowed(parsed.get("main_concepts"), allowed_concepts)
    return {
        "main_sectors": main_sectors,
        "main_concepts": main_concepts,
        "watch_only": _string_list(parsed.get("watch_only"), limit=8),
        "evidence": _string_list(parsed.get("evidence"), limit=8),
        "confidence": _confidence(parsed.get("confidence")),
    }


def _ordered_allowed(values: Any, allowed: set[str]) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if text and text in allowed and text not in out:
            out.append(text)
    return out


def _string_list(values: Any, *, limit: int) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _confidence(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, v))


def _json_list(raw: Any) -> list[str]:
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(x).strip() for x in data if isinstance(x, str) and str(x).strip()]


def _trim(raw: Any, limit: int) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return text[:limit]


def _build_prompt() -> str:
    return (
        "你是A股盘后主线判断助手。请只根据输入中的三类证据判断当前主线："
        "1) 成交额集中度申万二级候选；2) 同花顺概念资金分支候选；3) 老师观点原文摘录。"
        "你只能从输入 volume_sectors.industry 中选择 main_l2，只能从 hot_concepts.name 中选择 main_concepts；"
        "不能新增行业或概念，不能选择个股，不能给买卖建议、价位、仓位或预测。"
        "输出 JSON，不要 markdown："
        "{\"main_l2\":[\"...\"],\"main_concepts\":[\"...\"],\"watch_only\":[\"...\"],"
        "\"evidence\":[\"...\"],\"confidence\":0.0}"
    )


def _string_yang_log_file():
    """生成本次调用的独立诊断日志路径（ANTIGRAVITY_LOG_DIR，同 board_break/pk 范式）。

    string-yang 每晚只调 1-2 次 LLM，逐调用 mkdir 无 pk 那种 66 场循环赛的开销顾虑，
    不拆 root/文件名两步。目录创建失败返回 None（诊断降级为无日志，不阻断主流程）。
    """
    import os
    import time
    from pathlib import Path

    root = Path(os.getenv("ANTIGRAVITY_LOG_DIR", "/private/tmp/tradesystem-antigravity-logs"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[string-yang] antigravity log dir 创建失败(%s): %s", root, exc)
        return None
    return root / f"string-yang-{os.getpid()}-{int(time.time() * 1000)}.log"


def build_antigravity_runner():
    """构造主线裁决 LLM runner（对齐 board_break/pk 的诊断范式）。

    失败（超时/启动失败/非零返回码/空输出/输出不可解析）→ 返回 None 并设
    `runner.last_diagnostics`（含 reason），日志落 ANTIGRAVITY_LOG_DIR 供事后定位
    ——旧实现不落日志不捕 stderr，连续多日降级却无从排查真实失败原因。

    tech-debt（defer）：本 runner + `_string_yang_log_file` 与 `board_break/pk.py`、
    `research_digest/narrator.py`、`huibo.py` 的 LLM runner 四处同构（timeout/OSError/
    returncode/empty_stdout 四分支 + build_diagnostics + 独立日志文件）。收敛到共享
    `invoke_llm_cli` 的 defer 项见 `board_break/pk.py:_board_break_log_root` 注释所指
    task-s4-report.md「defer」节；第 5 处出现前应统一回收，勿再复制。
    """
    from utils.antigravity_diagnostics import build_diagnostics
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    timeout = config.timeout_seconds

    def runner(prompt: str, payload: dict[str, Any]):
        runner.last_diagnostics = None
        full_prompt = prompt + "\n\n输入证据 JSON：\n" + json.dumps(payload, ensure_ascii=False)
        log_file = _string_yang_log_file()
        cmd = build_prompt_command(config, full_prompt, log_file=str(log_file) if log_file else None)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr=f"timeout after {timeout}s", log_file=log_file, reason="timeout")
            logger.warning("[string-yang] mainline LLM 调用超时 %ds", timeout)
            return None
        except OSError as e:
            # CLI 缺失/不可执行等启动级失败；不扩到裸 Exception（那会吞编程错误）
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr=str(e), log_file=log_file, reason="startup_failed")
            logger.warning("[string-yang] mainline LLM 启动失败(%s)", e)
            return None
        stdout = (result.stdout or "")
        if result.returncode != 0:
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=result.stderr or "", log_file=log_file, returncode=result.returncode)
            logger.warning("[string-yang] mainline LLM returncode=%s", result.returncode)
            return None
        if not stdout.strip():
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=result.stderr or "", log_file=log_file, reason="empty_stdout")
            logger.warning("[string-yang] mainline LLM stdout 为空")
            return None
        parsed = _parse_json(stdout)
        if parsed is None:
            # returncode==0 且有输出但提不出 JSON：同属调用质量失败（重试可能救回）
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=result.stderr or "", log_file=log_file, reason="unparseable_output")
            logger.warning("[string-yang] mainline LLM 输出不可解析为 JSON")
            return None
        return parsed

    runner.last_diagnostics = None
    return runner


def _parse_json(text: str | None):
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        newline = t.find("\n")
        if newline != -1 and t[:newline].strip().lower() in ("json", ""):
            t = t[newline + 1 :]
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    for idx in range(start, len(t)):
        if t[idx] == "{":
            depth += 1
        elif t[idx] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start : idx + 1])
                except Exception:
                    return None
    return None
