"""七天横盘不创新低候选的 LLM 两两循环赛。"""
from __future__ import annotations

import itertools
import json
import logging
import math
import time

from services.no_new_low import constants as C
from services.recommend.formatter import REDLINE_KEYWORDS

logger = logging.getLogger(__name__)

_FACT_FIELDS = (
    "code", "name", "anchor_date", "latest_date", "base_range_pct",
    "max_close_deviation_pct", "abs_window_return_pct", "volume_ratio", "bar_count",
)

_PROMPT = (
    "你是七天横盘不创新低形态的两两评审员。所有候选均已通过机械筛选：基准 K 线后连续 7 根"
    "有效 K 线的前复权最低价没有跌破基准，且 8 根 K 线总振幅、收盘最大偏离、期末净漂移均"
    "通过横盘硬门。请逐一完成 pairs 中的每一场两两比较，只根据 cards 里的[事实]判断横盘收敛"
    "质量、低点守稳和量能平稳。总振幅更窄、收盘偏离更小、期末净漂移更小应优先；不得把区间"
    "上涨、低点大幅抬高或所谓相对强度当作优势。必须恰好覆盖全部 pairs，不得缺场、"
    "重复或添加池外股票。只输出 JSON："
    "{\"matches\":[{\"a\":\"代码\",\"b\":\"代码\",\"winner\":\"胜者代码\","
    "\"reason\":\"不超过80字\"}]}。不得给买卖建议、仓位建议或价格预测。"
)


def _parse_json_object(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _fact_payload(card: dict) -> dict:
    return {field: card.get(field) for field in _FACT_FIELDS}


def _filter_reason(reason: str) -> str:
    text = str(reason or "")
    for keyword in REDLINE_KEYWORDS:
        if keyword in text:
            logger.warning("[no-new-low pk] reason 命中红线 '%s'，已替换", keyword)
            return "（理由已按红线过滤）"
    text = text[: C.PK_REASON_MAX_CHARS]
    return text or "（无理由）"


def _safe_call(llm_runner, payload):
    if llm_runner is None:
        return None, "runner_unavailable"
    if hasattr(llm_runner, "last_diagnostics"):
        try:
            llm_runner.last_diagnostics = None
        except Exception:
            pass
    try:
        text = llm_runner(_PROMPT, payload)
    except Exception:
        logger.warning("[no-new-low pk] runner 调用异常", exc_info=True)
        return None, "runner_exception"
    diagnostics = getattr(llm_runner, "last_diagnostics", None) or {}
    return text, diagnostics.get("reason")


def _parse_tournament(text: str, pairs: list[tuple[str, str]]) -> list[dict] | None:
    value = _parse_json_object(text)
    raw_matches = value.get("matches") if value else None
    if not isinstance(raw_matches, list) or len(raw_matches) != len(pairs):
        return None

    expected = {frozenset(pair): pair for pair in pairs}
    parsed: dict[frozenset, dict] = {}
    for item in raw_matches:
        if not isinstance(item, dict):
            return None
        a, b, winner, reason = item.get("a"), item.get("b"), item.get("winner"), item.get("reason")
        pair_key = frozenset((a, b)) if isinstance(a, str) and isinstance(b, str) else frozenset()
        if len(pair_key) != 2 or pair_key not in expected or pair_key in parsed:
            return None
        if not isinstance(winner, str) or winner not in pair_key or not isinstance(reason, str):
            return None
        canonical_a, canonical_b = expected[pair_key]
        parsed[pair_key] = {
            "a": canonical_a, "b": canonical_b, "winner": winner,
            "reason": _filter_reason(reason), "state": "valid",
        }
    if len(parsed) != len(expected):
        return None
    return [parsed[frozenset(pair)] for pair in pairs]


def _evidence_key(card: dict) -> tuple:
    """胜场/直接交手之后的事实破平；不含股票代码。"""
    try:
        volume_ratio = float(card.get("volume_ratio"))
    except (TypeError, ValueError):
        volume_ratio = None
    volume_stability = (
        -abs(volume_ratio - 1.0)
        if volume_ratio is not None and math.isfinite(volume_ratio)
        else float("-inf")
    )
    return (
        -round(float(card.get("base_range_pct") or 0.0), 6),
        -round(float(card.get("max_close_deviation_pct") or 0.0), 6),
        -round(float(card.get("abs_window_return_pct") or 0.0), 6),
        round(volume_stability, 6),
        round(float(card.get("today_amount_yi") or 0.0), 6),
    )


def _build_order(pool: list[str], card_map: dict, wins: dict, matches: list[dict]) -> tuple[list[str], dict]:
    # 胜场相同时，只统计该胜场组内的直接交手胜场。这样两票同分时直接交手
    # 胜者优先；三票以上出现循环胜负时也不会制造非传递 comparator。
    internal_wins = {code: 0 for code in pool}
    for match in matches:
        if match.get("state") != "valid":
            continue
        a, b, winner = match["a"], match["b"], match["winner"]
        if wins.get(a) == wins.get(b) and winner in internal_wins:
            internal_wins[winner] += 1

    business_key = {
        code: (wins[code], internal_wins[code], _evidence_key(card_map[code]))
        for code in pool
    }
    order = sorted(pool, key=lambda code: business_key[code], reverse=True)
    ranks: dict[str, int] = {}
    rank = 0
    previous_code = None
    for position, code in enumerate(order, start=1):
        if previous_code is None:
            rank = position
        else:
            if business_key[code] != business_key[previous_code]:
                rank = position
        ranks[code] = rank
        previous_code = code
    return order, ranks


def run_pk(
    fact_cards: list[dict], llm_runner, *,
    budget_seconds: float = C.PK_BUDGET_SECONDS,
    clock=time.monotonic,
) -> dict:
    card_map: dict[str, dict] = {}
    pool: list[str] = []
    for card in fact_cards[: C.PK_POOL_MAX]:
        code = card.get("code")
        if code and code not in card_map:
            card_map[code] = card
            pool.append(code)
    if len(pool) < 2:
        return {
            "status": "skipped", "wins": {}, "order": pool, "ranks": None,
            "matches": [], "invalid": 0, "attempted": 0, "total": 0,
            "valid_ratio": 0.0, "failure_reasons": {},
        }

    pairs = list(itertools.combinations(pool, 2))
    wins = {code: 0 for code in pool}
    matches: list[dict] = []
    failure_reasons: dict[str, int] = {}
    start = clock()
    payload = {
        "cards": [_fact_payload(card_map[code]) for code in pool],
        "pairs": [{"a": a, "b": b} for a, b in pairs],
    }
    batch_attempts = 0
    failure = None
    for _ in range(2):
        if clock() - start > budget_seconds:
            failure = "budget_exceeded"
            break
        batch_attempts += 1
        text, failure = _safe_call(llm_runner, payload)
        if clock() - start > budget_seconds:
            failure = "budget_exceeded"
            break
        matches = _parse_tournament(text, pairs)
        if matches is not None:
            break
        if failure in ("timeout", "startup_failed", "runner_unavailable"):
            break
        failure = failure or "invalid_output"

    if not matches:
        failure = failure or "invalid_output"
        failure_reasons[failure] = 1
        return {
            "status": "melted", "wins": wins, "order": None, "ranks": None,
            "matches": [], "invalid": len(pairs), "attempted": 0,
            "total": len(pairs), "valid_ratio": 0.0,
            "failure_reasons": failure_reasons, "batch_attempts": batch_attempts,
        }

    for match in matches:
        wins[match["winner"]] += 1

    order, ranks = _build_order(pool, card_map, wins, matches)
    return {
        "status": "ok", "wins": wins, "order": order, "ranks": ranks,
        "matches": matches, "invalid": 0, "attempted": len(matches),
        "total": len(pairs), "valid_ratio": 1.0,
        "failure_reasons": failure_reasons, "batch_attempts": batch_attempts,
    }


def build_llm_runner():
    import os
    import subprocess
    from pathlib import Path

    from utils.antigravity_diagnostics import build_diagnostics
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    log_root = Path(os.getenv("ANTIGRAVITY_LOG_DIR", "/private/tmp/tradesystem-antigravity-logs"))
    try:
        log_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_root = None

    def runner(prompt, payload):
        runner.last_diagnostics = None
        full_prompt = prompt + "\n\n输入数据（JSON）：\n" + json.dumps(payload, ensure_ascii=False)
        log_file = None
        if log_root is not None:
            log_file = log_root / f"no-new-low-{os.getpid()}-{time.time_ns()}.log"
        cmd = build_prompt_command(config, full_prompt, log_file=str(log_file) if log_file else None)
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=config.timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr="timeout", log_file=log_file, reason="timeout")
            return None
        except OSError as exc:
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr=str(exc), log_file=log_file, reason="startup_failed")
            return None
        stdout = completed.stdout or ""
        if completed.returncode != 0:
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=completed.stderr or "", log_file=log_file,
                returncode=completed.returncode)
            return None
        if not stdout.strip():
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=completed.stderr or "", log_file=log_file,
                reason="empty_stdout")
            return None
        return stdout

    runner.last_diagnostics = None
    return runner
