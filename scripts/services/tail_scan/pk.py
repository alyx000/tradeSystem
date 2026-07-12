"""tail-scan PK 层：LLM 两两循环赛（复用 board_break 引擎范式）。

parse_verdict / build_llm_runner 直接复用 board_break（generic）。run_pk 引擎与
board_break 同构（预算熔断 + 无效场率熔断 + 胜场/粗分/裸码破平），仅事实字段/prompt
不同。跨服务 PK 引擎共享抽取列为 tech-debt（与 board_break.pk 同批 defer）。
"""
from __future__ import annotations

import itertools
import logging
import time

from services.board_break.pk import build_llm_runner, parse_verdict  # 复用 generic 积木
from services.recommend.formatter import REDLINE_KEYWORDS
from services.tail_scan import constants as C

logger = logging.getLogger(__name__)

# 喂 LLM 的 [事实] 字段（不含粗权重分/价位）
_FACT_FIELDS = (
    "code", "name", "pct_chg", "amount_yi", "is_limit_up", "close_pos", "amplitude",
    "in_main_sector", "in_hot_concept", "concept_names", "teacher_hit",
    "rank_in_pool", "index_context",
    "gain5", "ma_above", "up_days", "first_surge", "vol_ratio",
    "dist_to_high", "broke_high", "calendar",
)

_PROMPT = (
    "你是尾盘强势股评审员。下面给出两只个股（A/B）的[事实]卡（不含任何加权分数），"
    "请仅依据事实，从逻辑、三位一体、节奏、节点四个维度判断谁更支持尾盘介入（相对强弱）。"
    "只输出 JSON：{\"winner\": \"A\"|\"B\", \"reason\": \"<=60字\"}，不要输出其它文字，"
    "不给出具体买卖建议，不给出价位预测，不出仓位。"
)


def _scan_redline(text: str) -> str | None:
    for kw in REDLINE_KEYWORDS:
        if kw in (text or ""):
            return kw
    return None


def _filter_reason(reason: str) -> str:
    hit = _scan_redline(reason)
    if hit:
        logger.warning("[tail-scan pk] reason 命中红线 '%s'，已替换", hit)
        reason = "(理由已按红线过滤)"
    reason = reason[: C.PK_REASON_MAX_CHARS]
    return reason if reason else "(无理由)"


def _payload(card_a, card_b):
    def one(c):
        return {k: c.get(k) for k in _FACT_FIELDS}
    return {"A": one(card_a), "B": one(card_b)}


def _play_match(card_a, card_b, runner):
    try:
        text = runner(_PROMPT, _payload(card_a, card_b))
    except Exception:
        logger.warning("[tail-scan pk] runner 异常，重试一次", exc_info=True)
        try:
            text = runner(_PROMPT, _payload(card_a, card_b))
        except Exception:
            return None, None
    verdict = parse_verdict(text)
    if verdict is None:
        return None, None
    winner = card_a["code"] if verdict["winner"] == "A" else card_b["code"]
    return winner, _filter_reason(verdict["reason"])


def _pool(cards, score_map):
    seen, codes = set(), []
    for c in cards:
        code = c.get("code")
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    if len(codes) <= C.PK_POOL_MAX:
        return codes, []
    ranked = sorted(codes, key=lambda x: (-score_map.get(x, 0.0), x))
    return ranked[: C.PK_POOL_MAX], sorted(ranked[C.PK_POOL_MAX:])


def run_pk(fact_cards, scored, llm_runner, *, budget_seconds=180.0, clock=time.monotonic):
    card_map = {}
    for c in fact_cards:
        card_map.setdefault(c.get("code"), c)
    score_map = {s.get("code"): s.get("total", 0.0) for s in scored}
    pool, excluded = _pool(fact_cards, score_map)
    if len(pool) < 2:
        return {"status": "skipped", "wins": {}, "ranks": None, "matches": [],
                "invalid": 0, "attempted": 0, "valid_ratio": 0.0, "excluded": excluded}

    pairs = list(itertools.combinations(sorted(pool), 2))
    wins = {c: 0 for c in pool}
    matches, invalid = [], 0
    start, melted = clock(), False
    for a, b in pairs:
        if clock() - start > budget_seconds:
            melted = True
            break
        winner, reason = _play_match(card_map[a], card_map[b], llm_runner)
        if winner is None:
            invalid += 1
            matches.append({"a": a, "b": b, "winner": None, "reason": None, "state": "invalid"})
        else:
            wins[winner] += 1
            matches.append({"a": a, "b": b, "winner": winner, "reason": reason, "state": "valid"})

    attempted = len(matches)
    valid_ratio = (attempted - invalid) / attempted if attempted else 0.0
    if melted or (attempted and invalid / attempted > 0.5):
        return {"status": "melted", "wins": wins, "ranks": None, "matches": matches,
                "invalid": invalid, "attempted": attempted, "valid_ratio": valid_ratio,
                "excluded": excluded}
    ordered = sorted(pool, key=lambda c: (-wins[c], -score_map.get(c, 0.0), c))
    ranks = {c: i for i, c in enumerate(ordered, start=1)}
    return {"status": "ok", "wins": wins, "ranks": ranks, "matches": matches,
            "invalid": invalid, "attempted": attempted, "valid_ratio": valid_ratio,
            "excluded": excluded}
